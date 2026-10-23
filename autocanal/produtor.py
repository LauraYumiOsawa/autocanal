"""Produtor: percorre as etapas do formato de um job, com checkpoint e tentativas."""
from __future__ import annotations

import sqlite3

from autocanal import etapas, fila
from autocanal.claude import LimiteUsoClaude
from autocanal.config import Config
from autocanal.etapas.base import ErroEtapa, contexto_para
from autocanal.fila import Estado

MAX_TENTATIVAS_ETAPA = 3
_ATIVOS = (Estado.PRODUZINDO, Estado.EM_REVISAO)


def produzir(
    cfg: Config, con: sqlite3.Connection, job_id: str, *, registro: dict | None = None
) -> Estado:
    """Roda as etapas pendentes do job e devolve o estado em que ele parou.

    Etapas já `ok` são puladas. Um erro conta uma tentativa da etapa e encerra a
    chamada (após `MAX_TENTATIVAS_ETAPA`, o job vai para `falhou`); o limite de uso do
    Claude encerra sem contar tentativa. Se a revisão devolver o job para
    `produzindo`, o percurso recomeça na mesma chamada.
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
            except LimiteUsoClaude:
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
