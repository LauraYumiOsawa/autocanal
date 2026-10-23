"""Planejador: cria jobs com temas para os slots livres da grade de cada canal."""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from autocanal import claude, fila, grade
from autocanal.config import Canal, Config
from autocanal.etapas.base import carregar_prompt, lista_md, titulos_do_canal
from autocanal.fila import Estado, Job
from autocanal.formatos import FORMATOS

log = logging.getLogger(__name__)

MARGEM = timedelta(hours=2)
SEMANA = timedelta(days=7)
FERRAMENTAS = ["Skill", "Read", "WebSearch", "WebFetch"]
N_TITULOS = 15
N_TEMAS_USADOS = 60

SugerirTemas = Callable[[Canal, str, int], list[str]]


def ocupados(con: sqlite3.Connection, canal: str, formato: str) -> set[datetime]:
    """Horários (UTC) já tomados por jobs não descartados do canal/formato."""
    return {
        j.publish_at for j in fila.listar(con, canal=canal)
        if j.formato == formato and j.estado != Estado.DESCARTADO
    }


def registrar_tema(con: sqlite3.Connection, canal: str, formato: str, tema: str) -> None:
    """Guarda em `temas` um tema que virou job (o planejador evita repeti-lo)."""
    con.execute(
        "INSERT INTO temas (canal, formato, tema, usado, criado_em) VALUES (?, ?, ?, 1, ?)",
        (canal, formato, tema, datetime.now(UTC).isoformat()),
    )
    con.commit()


def planejar(
    cfg: Config, con: sqlite3.Connection, agora: datetime, *, sugerir_temas: SugerirTemas
) -> list[Job]:
    """Cria jobs para os slots livres em [agora + 2 h, agora + folga_dias + 7 dias).

    Um pedido de temas por canal/formato com slot livre; o i-ésimo tema vai para o
    i-ésimo slot. Canal sem `nicho` é ignorado com aviso.
    """
    inicio = agora + MARGEM
    fim = agora + timedelta(days=cfg.folga_dias) + SEMANA
    criados: list[Job] = []
    for canal in cfg.canais.values():
        if not canal.nicho.strip():
            log.warning("canal %s sem nicho em canais.yaml: não planejado", canal.slug)
            continue
        for formato in FORMATOS:
            tomados = ocupados(con, canal.slug, formato)
            livres = [s for s in grade.slots(canal, formato, inicio, fim, cfg.fuso)
                      if s not in tomados]
            if not livres:
                continue
            temas = [t.strip() for t in sugerir_temas(canal, formato, len(livres)) if t.strip()]
            if len(temas) < len(livres):
                log.warning("%s/%s: %d de %d temas sugeridos; o resto fica para o próximo"
                            " planejamento", canal.slug, formato, len(temas), len(livres))
            for slot, tema in zip(livres, temas):
                criados.append(fila.criar_job(con, canal.slug, formato, tema, slot))
                registrar_tema(con, canal.slug, formato, tema)
    return criados


def _schema(n: int) -> dict:
    return {
        "type": "object",
        "required": ["temas"],
        "additionalProperties": False,
        "properties": {
            "temas": {
                "type": "array",
                "minItems": n,
                "maxItems": n,
                "items": {
                    "type": "object",
                    "required": ["tema", "angulo"],
                    "additionalProperties": False,
                    "properties": {
                        "tema": {"type": "string", "minLength": 1},
                        "angulo": {"type": "string"},
                    },
                },
            },
        },
    }


def _juntar(item: dict) -> str:
    tema, angulo = item["tema"].strip(), item["angulo"].strip()
    return f"{tema} — {angulo}" if angulo else tema


def sugerir_temas_claude(cfg: Config, con: sqlite3.Connection) -> SugerirTemas:
    """`sugerir_temas` que pede ao Claude (skill yt-plan + pesquisa web) N temas do nicho."""

    def sugerir(canal: Canal, formato: str, n: int) -> list[str]:
        usados = [r["tema"] for r in con.execute(
            "SELECT tema FROM temas WHERE canal = ? ORDER BY id DESC LIMIT ?",
            (canal.slug, N_TEMAS_USADOS),
        )]
        prompt = carregar_prompt(
            "temas",
            n=n,
            canal=canal.nome,
            nicho=canal.nicho,
            formato=FORMATOS[formato].resumo,
            titulos_recentes=lista_md(
                titulos_do_canal(cfg, con, canal.slug, N_TITULOS), "(nenhum ainda)"
            ),
            temas_usados=lista_md(usados, "(nenhum ainda)"),
        )
        pasta = cfg.dados / "estrategia"
        pasta.mkdir(parents=True, exist_ok=True)
        r = claude.chamar(
            prompt,
            _schema(n),
            FERRAMENTAS,
            pasta,
            modelo=cfg.claude.modelos.get("temas"),
            max_turns=cfg.claude.max_turns.get("temas", 15),
            claude_bin=cfg.claude.bin,
        )
        claude.registrar(con, None, "temas", r)
        return [_juntar(t) for t in r.dados["temas"]]

    return sugerir
