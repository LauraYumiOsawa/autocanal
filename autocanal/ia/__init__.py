"""Núcleo de IA independente de provedor: escolhe o provedor da etapa, pausa e registra."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

from autocanal.config import Config
from autocanal.ia import claude_cli, metodo
from autocanal.ia.base import (
    Capacidades,
    ErroIA,
    IAIndisponivel,
    LimiteUso,
    Pedido,
    Resultado,
)

__all__ = [
    "ADAPTADORES", "Capacidades", "ErroIA", "IAIndisponivel", "LimiteUso", "METODOS", "PAUSA",
    "PRECISA", "Pedido", "Resultado", "SCRIPTS", "chamar", "faltas", "livre", "pausa", "pausar",
    "pausas", "provedor_de",
]

ADAPTADORES: dict[str, ModuleType] = {claude_cli.NOME: claude_cli}
PAUSA = timedelta(hours=1)
MAX_TURNS_PADRAO = 20
# skills do plugin que cada etapa segue como método (copiadas para <pasta>/metodo/)
METODOS: dict[str, tuple[str, ...]] = {
    "roteiro": ("yt-script",),
    "metadados": ("yt-package", "yt-seo", "yt-chapters"),
    "temas": ("yt-plan",),
}
# únicos comandos que a IA da etapa pode rodar (relativos à pasta)
SCRIPTS: dict[str, tuple[str, ...]] = {
    "roteiro": ("metodo/yt-script/hookscore.py",),
    "metadados": ("metodo/yt-package/title.py",),
}


# o que cada etapa exige do provedor (o `diagnostico` confere)
PRECISA: dict[str, Capacidades] = {
    "roteiro": Capacidades(web=True, imagens=False, scripts=True),
    "curadoria": Capacidades(web=False, imagens=True, scripts=False),
    "metadados": Capacidades(web=False, imagens=False, scripts=True),
    "revisao": Capacidades(web=True, imagens=True, scripts=False),
    "temas": Capacidades(web=True, imagens=False, scripts=False),
}


def provedor_de(cfg: Config, etapa: str) -> str:
    return cfg.ia.etapas.get(etapa, cfg.ia.padrao)


def faltas(cfg: Config, etapa: str) -> list[str]:
    """Capacidades (`web`, `imagens`, `scripts`) que o provedor da etapa não tem."""
    adaptador = ADAPTADORES.get(provedor_de(cfg, etapa))
    if adaptador is None:
        return ["sem adaptador"]
    tem, precisa = adaptador.CAPACIDADES, PRECISA[etapa]
    return [c for c in ("web", "imagens", "scripts") if getattr(precisa, c) and not getattr(tem, c)]


def _marcador(cfg: Config, provedor: str) -> Path:
    return cfg.dados / "pausa_ia" / provedor


def pausar(cfg: Config, provedor: str, motivo: str, agora: datetime | None = None) -> datetime:
    """Grava `dados/pausa_ia/<provedor>` (agora + 1 h e o motivo) e devolve o fim da pausa."""
    ate = ((agora or datetime.now(UTC)) + PAUSA).astimezone(UTC)
    arq = _marcador(cfg, provedor)
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(f"{ate.isoformat()}\n{' '.join(motivo.split())}\n", encoding="utf-8")
    return ate


def pausa(cfg: Config, provedor: str) -> tuple[datetime, str] | None:
    """(fim, motivo) da pausa do provedor (None se não houver marcador legível)."""
    try:
        linhas = _marcador(cfg, provedor).read_text(encoding="utf-8").splitlines()
        ate = datetime.fromisoformat(linhas[0].strip())
    except (OSError, ValueError, IndexError):
        return None
    ate = ate.replace(tzinfo=UTC) if ate.tzinfo is None else ate
    return ate, (linhas[1].strip() if len(linhas) > 1 else "")


def pausas(cfg: Config, agora: datetime) -> dict[str, tuple[datetime, str]]:
    """Pausas vigentes, por provedor."""
    pasta = cfg.dados / "pausa_ia"
    if not pasta.is_dir():
        return {}
    vigentes = {}
    for arq in sorted(pasta.iterdir()):
        p = pausa(cfg, arq.name)
        if p is not None and agora < p[0]:
            vigentes[arq.name] = p
    return vigentes


def livre(cfg: Config, etapa: str, agora: datetime) -> bool:
    return provedor_de(cfg, etapa) not in pausas(cfg, agora)


def _registrar(con: sqlite3.Connection, job_id: str | None, etapa: str, r: Resultado) -> None:
    con.execute(
        "INSERT INTO chamadas_ia (job_id, etapa, provedor, modelo, tokens_entrada, tokens_saida,"
        " custo_usd, turnos, duracao_s, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, etapa, r.provedor, r.modelo, r.tokens_entrada, r.tokens_saida, r.custo_usd,
         r.turnos, r.duracao_s, datetime.now(UTC).isoformat()),
    )
    con.commit()


def chamar(
    cfg: Config, con: sqlite3.Connection, job_id: str | None, etapa: str, pedido: Pedido
) -> Resultado:
    """Chama o provedor da etapa. Limite de uso e indisponibilidade pausam só esse provedor."""
    provedor = provedor_de(cfg, etapa)
    vigente = pausas(cfg, datetime.now(UTC)).get(provedor)
    if vigente is not None:
        raise LimiteUso(provedor, f"pausado até {vigente[0].isoformat()} ({vigente[1]})")
    try:
        adaptador = ADAPTADORES.get(provedor)
        if adaptador is None:
            raise IAIndisponivel(provedor, "sem adaptador para este provedor")
        try:
            metodo.preparar(pedido.pasta, cfg.plugin_dir, pedido.metodos)
        except FileNotFoundError as e:
            raise IAIndisponivel(provedor, str(e)) from None
        conf = cfg.ia.provedores[provedor]
        r = adaptador.chamar(
            pedido,
            modelo=conf.modelos.get(etapa),
            max_turns=cfg.ia.max_turns.get(etapa, MAX_TURNS_PADRAO),
            bin=conf.bin,
            raiz=cfg.raiz,
        )
    except LimiteUso:
        pausar(cfg, provedor, "limite de uso")
        raise
    except IAIndisponivel as e:
        pausar(cfg, provedor, e.msg)
        raise
    _registrar(con, job_id, etapa, r)
    return r
