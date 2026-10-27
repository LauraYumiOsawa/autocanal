"""Bancada de medição: repete uma etapa de IA sobre uma cópia isolada de um job pronto.

A cópia fica em `dados/bancada/<carimbo>-<job>-<etapa>-<rótulo>/`, com banco próprio: o job real
e o banco real não mudam. Cada execução acrescenta uma linha em `dados/bancada/resultados.jsonl`.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from autocanal import etapas, fila, ia
from autocanal.config import Config
from autocanal.db import conectar
from autocanal.etapas.base import PROBLEMAS_REVISAO, contexto_para
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS
from autocanal.texto import contar_palavras

ETAPAS_BANCADA = ("roteiro", "metadados", "revisao")
RESULTADOS = "resultados.jsonl"
# Pastas (relativas ao job) que roteiro, metadados e revisão não leem: só pesam na cópia.
NAO_COPIAR = ("render", "visuais/curadoria", "visuais/flux_lote")


def _commit(raiz: Path) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=raiz,
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None


def _ignorar(origem: Path):
    """`ignore` do copytree: pula as pastas de `NAO_COPIAR` (caminhos relativos ao job)."""
    def ignorar(pasta: str, nomes: list[str]) -> set[str]:
        rel = Path(pasta).relative_to(origem)
        return {n for n in nomes if (rel / n).as_posix() in NAO_COPIAR}
    return ignorar


def _clonar_job(con_real: sqlite3.Connection, con: sqlite3.Connection, job_id: str,
                etapa: str) -> None:
    linha = dict(con_real.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    linha["estado"] = (Estado.EM_REVISAO if etapa == "revisao" else Estado.PRODUZINDO).value
    linha["tentativas_revisao"] = 0
    colunas = ", ".join(linha)
    marcas = ", ".join("?" for _ in linha)
    con.execute(f"INSERT INTO jobs ({colunas}) VALUES ({marcas})", tuple(linha.values()))
    con.commit()


def _ler(pasta: Path, nome: str) -> dict | None:
    arq = pasta / nome
    return json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None


def _metricas(etapa: str, pasta: Path, formato: str) -> dict:
    if etapa == "roteiro":
        r = _ler(pasta, "roteiro.json") or {}
        fmt = FORMATOS[formato]
        palavras = contar_palavras(" ".join(s["texto"] for s in r.get("segmentos", [])))
        conferidas = _ler(pasta, "fontes_conferidas.json")
        return {
            "palavras_ok": fmt.palavras_min <= palavras <= fmt.palavras_max,
            "nota_gancho": r.get("nota_gancho"),
            "fontes": len(r.get("fontes", [])),
            "fontes_confirmadas": None if conferidas is None else sum(
                1 for f in conferidas.get("resultado", []) if f.get("status") == "confirmada"),
        }
    if etapa == "metadados":
        m = _ler(pasta, "metadados.json") or {}
        return {"nota_titulo": m.get("nota_titulo"),
                "problemas_titulo": len(m.get("problemas_titulo", []))}
    rev = _ler(pasta, "revisao_1.json") or {}
    return {"aprovado": rev.get("aprovado"), "etapa_para_refazer": rev.get("etapa_para_refazer"),
            "problemas": len(rev.get("problemas", []))}


def rodar(
    cfg: Config,
    con_real: sqlite3.Connection,
    job_id: str,
    etapa: str,
    rotulo: str,
    *,
    modelo: str | None = None,
    esforco: str | None = None,
    primeira_versao: bool = False,
    registro=None,
    agora: datetime | None = None,
) -> dict:
    """Roda `etapa` numa cópia isolada do job e devolve (e grava) a linha de resultado.

    Com `primeira_versao`, a cópia perde `problemas_revisao.json` (o job real não muda).
    """
    if etapa not in ETAPAS_BANCADA:
        raise ValueError(f"etapa fora da bancada: {etapa} (use {', '.join(ETAPAS_BANCADA)})")
    registro = etapas.REGISTRO if registro is None else registro
    agora = agora or datetime.now(UTC)
    job_real = fila.obter(con_real, job_id)
    base = cfg.dados / "bancada" / f"{agora:%Y%m%dT%H%M%S}-{job_id}-{etapa}-{rotulo}"
    origem = cfg.dados / "jobs" / job_id
    shutil.copytree(origem, base / "jobs" / job_id, ignore=_ignorar(origem))
    if primeira_versao:  # só na cópia: sem os problemas, o roteiro parte do zero como na 1ª vez
        (base / "jobs" / job_id / PROBLEMAS_REVISAO).unlink(missing_ok=True)

    cfg_b = cfg.model_copy(update={"dados": base}, deep=True)
    prov = cfg_b.ia.provedores[ia.provedor_de(cfg_b, etapa)]
    if modelo:
        prov.modelos[etapa] = modelo
    if esforco:
        prov.esforco[etapa] = esforco
    con = conectar(base / "autocanal.db")
    try:
        _clonar_job(con_real, con, job_id, etapa)
        erro = None
        try:
            registro[etapa](contexto_para(cfg_b, con, fila.obter(con, job_id)))
        except Exception as e:  # a bancada registra a falha em vez de parar
            erro = str(e) or type(e).__name__
        soma = con.execute(
            "SELECT COUNT(*) AS n, SUM(tokens_entrada) AS ent, SUM(tokens_saida) AS sai,"
            " SUM(custo_usd) AS custo, SUM(turnos) AS turnos, SUM(duracao_s) AS dur"
            " FROM chamadas_ia"
        ).fetchone()
    finally:
        con.close()
    linha = {
        "quando": agora.isoformat(),
        "commit": _commit(cfg.raiz),
        "job": job_id,
        "etapa": etapa,
        "rotulo": rotulo,
        "modelo": modelo,
        "esforco": esforco,
        "primeira_versao": primeira_versao,
        "chamadas": soma["n"],
        "tokens_entrada": soma["ent"],
        "tokens_saida": soma["sai"],
        "custo_usd": soma["custo"],
        "turnos": soma["turnos"],
        "duracao_s": soma["dur"],
        "erro": erro,
        "metricas": None if erro else _metricas(etapa, base / "jobs" / job_id, job_real.formato),
    }
    with (cfg.dados / "bancada" / RESULTADOS).open("a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    return linha


def _media(valores: list) -> float | None:
    nums = [float(v) for v in valores if isinstance(v, (int, float)) and not isinstance(v, str)]
    return sum(nums) / len(nums) if nums else None


def _fmt(v: float | None, casas: int) -> str:
    return "—" if v is None else f"{v:.{casas}f}"


def relatorio(cfg: Config) -> list[str]:
    """Médias por etapa e rótulo (execuções com erro só entram na contagem de erros)."""
    arq = cfg.dados / "bancada" / RESULTADOS
    if not arq.exists():
        return ["(nenhuma execução na bancada)"]
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for texto in arq.read_text(encoding="utf-8").splitlines():
        if texto.strip():
            linha = json.loads(texto)
            grupos[(linha["etapa"], linha["rotulo"])].append(linha)
    saida = []
    for (etapa, rotulo), linhas in sorted(grupos.items()):
        ok = [x for x in linhas if not x.get("erro")]
        metricas: dict[str, list] = defaultdict(list)
        for x in ok:
            for k, v in (x.get("metricas") or {}).items():
                metricas[k].append(v)
        partes = [
            f"{etapa:<9} {rotulo:<10} n={len(ok)} erros={len(linhas) - len(ok)}",
            f"entrada={_fmt(_media([x['tokens_entrada'] for x in ok]), 0)}",
            f"saida={_fmt(_media([x['tokens_saida'] for x in ok]), 0)}",
            f"custo={_fmt(_media([x['custo_usd'] for x in ok]), 2)}",
            f"turnos={_fmt(_media([x['turnos'] for x in ok]), 1)}",
            f"chamadas={_fmt(_media([x['chamadas'] for x in ok]), 1)}",
            *(f"{k}={m:g}" for k, vs in sorted(metricas.items())
              if (m := _media(vs)) is not None),
        ]
        saida.append(" ".join(partes))
    return saida
