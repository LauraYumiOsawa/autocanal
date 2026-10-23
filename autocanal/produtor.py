"""Produtor: percorre as etapas do formato de um job, com checkpoint e tentativas."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from autocanal import etapas, fila
from autocanal.claude import LimiteUsoClaude
from autocanal.config import Config
from autocanal.etapas.base import PROBLEMAS_REVISAO, ErroEtapa, contexto_para
from autocanal.fila import Estado, Job
from autocanal.formatos import FORMATOS

log = logging.getLogger(__name__)

MAX_TENTATIVAS_ETAPA = 3
PAUSA_CLAUDE = timedelta(hours=1)
_ATIVOS = (Estado.PRODUZINDO, Estado.EM_REVISAO)


def _marcador_pausa(cfg: Config) -> Path:
    return cfg.dados / "pausa_claude_ate"


def pausar_claude(cfg: Config, agora: datetime | None = None) -> datetime:
    """Grava `dados/pausa_claude_ate` (agora + 1 h, ISO UTC) e devolve esse horário."""
    ate = ((agora or datetime.now(UTC)) + PAUSA_CLAUDE).astimezone(UTC)
    cfg.dados.mkdir(parents=True, exist_ok=True)
    _marcador_pausa(cfg).write_text(ate.isoformat(), encoding="utf-8")
    return ate


def pausa_claude_ate(cfg: Config) -> datetime | None:
    """Fim da pausa por limite de uso do Claude (None se não houver marcador legível)."""
    try:
        ate = datetime.fromisoformat(_marcador_pausa(cfg).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return ate.replace(tzinfo=UTC) if ate.tzinfo is None else ate


def produzir(
    cfg: Config, con: sqlite3.Connection, job_id: str, *, registro: dict | None = None
) -> Estado:
    """Roda as etapas pendentes do job e devolve o estado em que ele parou.

    Etapas já `ok` são puladas. Um erro conta uma tentativa da etapa e encerra a
    chamada (após `MAX_TENTATIVAS_ETAPA`, o job vai para `falhou`); o limite de uso do
    Claude encerra sem contar tentativa e grava a pausa de 1 h (`pausar_claude`). Se a
    revisão devolver o job para `produzindo`, o percurso recomeça na mesma chamada; se
    ela terminar sem decidir (job ainda `em_revisao`), conta como erro da etapa.
    """
    registro = etapas.REGISTRO if registro is None else registro
    job = fila.obter(con, job_id)
    if job.estado == Estado.PLANEJADO:
        job = fila.mudar_estado(con, job_id, Estado.PRODUZINDO)
    if job.estado not in _ATIVOS:
        return job.estado
    try:
        ctx = contexto_para(cfg, con, job)
    except ErroEtapa as e:
        return fila.mudar_estado(con, job_id, Estado.FALHOU, erro=str(e)).estado

    refazer = True
    while refazer:
        refazer = False
        for nome in ctx.formato.etapas:
            if fila.estado_etapa(con, job_id, nome) == "ok":
                continue
            ctx.job = fila.obter(con, job_id)
            if nome == "revisao" and ctx.job.estado == Estado.PRODUZINDO:
                ctx.job = fila.mudar_estado(con, job_id, Estado.EM_REVISAO)
            fila.marcar_etapa(con, job_id, nome, "rodando")
            try:
                etapa = registro.get(nome)
                if etapa is None:
                    raise ErroEtapa(f"etapa não implementada: {nome}")
                etapa(ctx)
                if nome == "revisao" and fila.obter(con, job_id).estado == Estado.EM_REVISAO:
                    raise ErroEtapa("revisão não decidiu")
            except LimiteUsoClaude:
                ate = pausar_claude(cfg)
                log.warning("limite de uso do Claude em %s/%s; produção pausada até %s",
                            job_id, nome, ate.isoformat())
                return fila.obter(con, job_id).estado
            except Exception as e:
                msg = str(e) or type(e).__name__
                fila.marcar_etapa(con, job_id, nome, "erro", msg)
                if fila.tentativas_etapa(con, job_id, nome) >= MAX_TENTATIVAS_ETAPA:
                    erro = f"{nome}: {msg}"
                    return fila.mudar_estado(con, job_id, Estado.FALHOU, erro=erro).estado
                return fila.obter(con, job_id).estado
            estado = fila.obter(con, job_id).estado
            if nome == "revisao" and estado == Estado.PRODUZINDO:
                # reprovada: a revisão já resetou o que refazer; não marca `ok`
                # para que ela mesma rode de novo no fim do novo percurso
                refazer = True
                break
            fila.marcar_etapa(con, job_id, nome, "ok")
            if estado not in _ATIVOS:
                return estado
    return fila.obter(con, job_id).estado


def _etapas(job: Job) -> tuple[str, ...]:
    formato = FORMATOS.get(job.formato)
    return formato.etapas if formato else ()


def _com_erro(con: sqlite3.Connection, job: Job) -> list[str]:
    return [n for n in _etapas(job) if fila.estado_etapa(con, job.id, n) == "erro"]


def preparar_refazer(cfg: Config, con: sqlite3.Connection, job_id: str) -> Job:
    """Devolve a `produzindo` um job `falhou` ou `quarentena`, para `produzir` refazê-lo.

    `falhou`: reseta as etapas com erro (as `ok` ficam). `quarentena`: zera as rodadas
    de revisão, regrava `problemas_revisao.json` com os problemas da última rodada e
    reseta da `etapa_para_refazer` dela (ou do roteiro) até a revisão.
    Outros estados voltam sem mudança. ValueError se não houver o que refazer.
    """
    job = fila.obter(con, job_id)
    if job.estado == Estado.FALHOU:
        nomes = _etapas(job)
        if nomes and all(fila.estado_etapa(con, job_id, n) == "ok" for n in nomes):
            raise ValueError(
                f"todas as etapas de {job_id} já estão concluídas: use `autocanal aprovar "
                f"{job_id}` para reenviar ou `autocanal revisar {job_id}` para revisar de novo"
            )
        fila.resetar_etapas(con, job_id, _com_erro(con, job))
        return fila.mudar_estado(con, job_id, Estado.PRODUZINDO)
    if job.estado == Estado.QUARENTENA:
        ordem = _etapas(job)
        pasta = cfg.dados / "jobs" / job_id
        try:
            ultima = json.loads(
                (pasta / f"revisao_{job.tentativas_revisao}.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            ultima = {}
        etapa = ultima.get("etapa_para_refazer") or "roteiro"
        if etapa not in ordem:
            etapa = ordem[0]
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / PROBLEMAS_REVISAO).write_text(
            json.dumps({"problemas": list(ultima.get("problemas") or [])},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        fila.resetar_etapas(
            con, job_id, list(ordem[ordem.index(etapa):ordem.index("revisao") + 1])
        )
        fila.atualizar(con, job_id, tentativas_revisao=0)
        return fila.mudar_estado(con, job_id, Estado.PRODUZINDO)
    return job


_REVISAVEIS = (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.QUARENTENA, Estado.FALHOU)


def preparar_revisao(cfg: Config, con: sqlite3.Connection, job_id: str) -> Job:
    """Reseta só a etapa `revisao`, para que `produzir` revise o job de novo.

    `quarentena` também zera as rodadas; `quarentena` e `falhou` voltam a `produzindo`
    (`falhou` reseta ainda as etapas com erro, como em `preparar_refazer`).
    ValueError nos demais estados.
    """
    job = fila.obter(con, job_id)
    if job.estado not in _REVISAVEIS:
        raise ValueError(f"não dá para revisar {job_id} no estado {job.estado.value}")
    nomes = ["revisao"]
    if job.estado == Estado.FALHOU:
        nomes += [n for n in _com_erro(con, job) if n != "revisao"]
    fila.resetar_etapas(con, job_id, nomes)
    if job.estado == Estado.QUARENTENA:
        fila.atualizar(con, job_id, tentativas_revisao=0)
    if job.estado in (Estado.QUARENTENA, Estado.FALHOU):
        return fila.mudar_estado(con, job_id, Estado.PRODUZINDO)
    return fila.obter(con, job_id)
