"""Agendador: um passo (`tick`) do ciclo planejar → produzir → publicar → sincronizar → resumo."""
from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autocanal import fila, grade, planejador, produtor, relatorio
from autocanal.claude import LimiteUsoClaude
from autocanal.config import Config
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS

log = logging.getLogger(__name__)

HORA_RESUMO = time(7, 0)
# jobs que ainda vão ao ar sem depender de Laura (quarentena e falhou não contam)
PENDENTES = frozenset(
    {Estado.PLANEJADO, Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO, Estado.AGENDADO}
)


class TravaOcupada(Exception):
    pass


@contextmanager
def trava(caminho: Path) -> Iterator[None]:
    """Trava exclusiva de processo (`flock`, sem esperar); TravaOcupada se já estiver tomada."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(caminho, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TravaOcupada(f"trava ocupada: {caminho}") from None
        yield
    finally:
        os.close(fd)  # fechar o descritor solta a trava


@dataclass
class ResumoTick:
    pulou_por_trava: bool
    planejados: int = 0
    produzido: str | None = None
    enviados: list[str] = field(default_factory=list)
    publicados: list[str] = field(default_factory=list)
    resumo: Path | None = None


def fila_curta(cfg: Config, con: sqlite3.Connection, agora: datetime) -> bool:
    """Algum canal com nicho tem menos jobs pendentes que slots nos próximos `folga_dias` dias?"""
    fim = agora + timedelta(days=cfg.folga_dias)
    pendentes = fila.listar(con, set(PENDENTES))
    for canal in cfg.canais.values():
        if not canal.nicho.strip():
            continue
        for formato in FORMATOS:
            n_slots = len(grade.slots(canal, formato, agora, fim, cfg.fuso))
            n_jobs = sum(1 for j in pendentes if (j.canal, j.formato) == (canal.slug, formato))
            if n_jobs < n_slots:
                return True
    return False


def _claude_livre(cfg: Config, agora: datetime) -> bool:
    ate = produtor.pausa_claude_ate(cfg)
    return ate is None or agora >= ate


def _planejar_liberado(cfg: Config, agora: datetime) -> bool:
    ate = planejador.planejar_apos(cfg)
    return ate is None or agora >= ate


def tick(
    cfg: Config,
    con: sqlite3.Connection,
    agora: datetime,
    *,
    planejar_fn: Callable[[Config, sqlite3.Connection, datetime], list],
    produzir_fn: Callable[[Config, sqlite3.Connection, str], object],
    publicar_fn: Callable[[Config, sqlite3.Connection, datetime], list[str]],
    sincronizar_fn: Callable[[Config, sqlite3.Connection, datetime], list[str]],
    resumo_fn: Callable[[Config, sqlite3.Connection, date], Path],
    relogio: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ResumoTick:
    """Um passo do agendador, dentro da trava `dados/tick.lock`.

    (1) planeja se a fila estiver curta; (2) produz o próximo job; (3) envia os
    aprovados; (4) sincroniza os publicados; (5) gera o resumo do dia a partir das
    07:00 locais, uma vez. O planejamento usa `agora` (início do tick); as fases
    seguintes leem `relogio()` na hora, porque a produção pode levar dezenas de
    minutos. Planejar espera a pausa do Claude e o recuo após falha
    (`planejador.planejar_apos`); produzir espera a pausa. Erro numa fase é
    registrado no log e não impede as seguintes.
    """
    try:
        with trava(cfg.dados / "tick.lock"):
            return _passo(cfg, con, agora, relogio, planejar_fn, produzir_fn, publicar_fn,
                          sincronizar_fn, resumo_fn)
    except TravaOcupada:
        log.info("tick pulado: outro processo do autocanal está rodando")
        return ResumoTick(pulou_por_trava=True)


def _passo(cfg, con, agora, relogio, planejar_fn, produzir_fn, publicar_fn, sincronizar_fn,
           resumo_fn) -> ResumoTick:
    r = ResumoTick(pulou_por_trava=False)
    try:
        if (_claude_livre(cfg, agora) and _planejar_liberado(cfg, agora)
                and fila_curta(cfg, con, agora)):
            r.planejados = len(planejar_fn(cfg, con, agora))
    except LimiteUsoClaude:
        ate = produtor.pausar_claude(cfg, agora)
        log.warning("limite de uso do Claude ao planejar; pausa até %s", ate.isoformat())
    except Exception:
        log.exception("tick: falha ao planejar")

    try:
        if not _claude_livre(cfg, relogio()):
            log.info("Claude em pausa até %s: produção pulada",
                     produtor.pausa_claude_ate(cfg).isoformat())
        elif (job := fila.proximo_para_produzir(con)) is not None:
            r.produzido = job.id
            produzir_fn(cfg, con, job.id)
    except Exception:
        log.exception("tick: falha ao produzir")

    try:
        r.enviados = list(publicar_fn(cfg, con, relogio()))
    except Exception:
        log.exception("tick: falha ao publicar")

    try:
        r.publicados = list(sincronizar_fn(cfg, con, relogio()))
    except Exception:
        log.exception("tick: falha ao sincronizar")

    try:
        local = relogio().astimezone(ZoneInfo(cfg.fuso))
        if local.time() >= HORA_RESUMO and not relatorio.caminho_resumo(cfg, local.date()).exists():
            r.resumo = resumo_fn(cfg, con, local.date())
    except Exception:
        log.exception("tick: falha ao gerar o resumo")
    return r
