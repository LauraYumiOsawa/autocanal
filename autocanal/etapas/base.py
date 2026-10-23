"""Base das etapas: contexto do job, artefatos JSON e dados do canal."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from autocanal import fila
from autocanal.config import Canal, Config
from autocanal.fila import Estado, Job
from autocanal.formatos import FORMATOS, Formato
from autocanal.midia import Midia

PROBLEMAS_REVISAO = "problemas_revisao.json"
PERFIL_VOZ_PADRAO = (
    "Tom claro, direto e amigável, em português do Brasil. Frases curtas, sem jargão "
    "e sem exageros. Feche com um convite simples, sem insistir em inscrição."
)


class ErroEtapa(Exception):
    pass


@dataclass
class Contexto:
    cfg: Config
    con: sqlite3.Connection
    job: Job
    canal: Canal
    formato: Formato
    pasta: Path
    midia: Midia


def contexto_para(cfg: Config, con: sqlite3.Connection, job: Job) -> Contexto:
    canal = cfg.canais.get(job.canal)
    if canal is None:
        raise ErroEtapa(f"canal não configurado: {job.canal}")
    formato = FORMATOS.get(job.formato)
    if formato is None:
        raise ErroEtapa(f"formato desconhecido: {job.formato}")
    pasta = cfg.dados / "jobs" / job.id
    pasta.mkdir(parents=True, exist_ok=True)
    return Contexto(cfg, con, job, canal, formato, pasta, Midia(cfg.ffmpeg, cfg.ffprobe))


def ler_json(ctx: Contexto, nome: str) -> dict:
    arquivo = ctx.pasta / nome
    if not arquivo.exists():
        raise ErroEtapa(f"artefato ausente: {nome}")
    return json.loads(arquivo.read_text(encoding="utf-8"))


def gravar_json(ctx: Contexto, nome: str, dados: dict | list) -> Path:
    destino = ctx.pasta / nome
    temp = destino.with_name(destino.name + ".tmp")
    temp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, destino)
    return destino


def carregar_prompt(nome: str, **valores) -> str:
    """Lê `autocanal/prompts/<nome>.md` e preenche os campos com `str.format`."""
    modelo = (files("autocanal") / "prompts" / f"{nome}.md").read_text(encoding="utf-8")
    return modelo.format(**valores)


def lista_md(itens: list[str], vazio: str) -> str:
    """Itens como lista Markdown (`- item`), ou `vazio` se não houver nenhum."""
    return "\n".join(f"- {i}" for i in itens) if itens else vazio


def problemas_revisao(ctx: Contexto) -> list[str]:
    """Problemas apontados pela última revisão reprovada (vazio se não houve)."""
    if not (ctx.pasta / PROBLEMAS_REVISAO).exists():
        return []
    return [str(p) for p in ler_json(ctx, PROBLEMAS_REVISAO).get("problemas", [])]


def titulos_recentes(ctx: Contexto, n: int = 15) -> list[str]:
    """Títulos (de `metadados.json`) dos jobs mais recentes do canal, do mais novo ao mais velho."""
    titulos: list[str] = []
    for j in reversed(fila.listar(ctx.con, canal=ctx.job.canal)):
        if len(titulos) >= n:
            break
        if j.id == ctx.job.id or j.estado == Estado.DESCARTADO:
            continue
        arquivo = ctx.cfg.dados / "jobs" / j.id / "metadados.json"
        try:
            titulo = json.loads(arquivo.read_text(encoding="utf-8")).get("titulo")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(titulo, str) and titulo.strip():
            titulos.append(titulo.strip())
    return titulos


def perfil_voz(ctx: Contexto) -> str:
    try:
        texto = ctx.canal.perfil_voz.read_text(encoding="utf-8").strip()
    except OSError:
        texto = ""
    return texto or PERFIL_VOZ_PADRAO
