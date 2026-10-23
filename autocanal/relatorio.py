"""Resumo diário do autocanal (Markdown em `dados/relatorios/`) e aviso na área de trabalho."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autocanal import fila, planejador, produtor, publicador
from autocanal.config import Config
from autocanal.fila import Estado, Job

LINK = "https://youtu.be/{}"
NENHUM = "- nenhum"


def caminho_resumo(cfg: Config, dia: date) -> Path:
    return cfg.dados / "relatorios" / f"{dia.isoformat()}.md"


def _hora(cfg: Config, d: datetime) -> str:
    return d.astimezone(ZoneInfo(cfg.fuso)).strftime("%d/%m %H:%M")


def _titulo(cfg: Config, job: Job) -> str:
    arquivo = cfg.dados / "jobs" / job.id / "metadados.json"
    try:
        titulo = json.loads(arquivo.read_text(encoding="utf-8")).get("titulo")
    except (OSError, ValueError, AttributeError):
        titulo = None
    return titulo.strip() if isinstance(titulo, str) and titulo.strip() else job.tema


def _item(cfg: Config, job: Job, *extras: str) -> str:
    return "- " + " — ".join([f"`{job.id}`", _titulo(cfg, job), *extras])


def _link(job: Job) -> str:
    return LINK.format(job.youtube_video_id) if job.youtube_video_id else "(sem vídeo)"


def _no_periodo(sql: str, inicio: datetime, fim: datetime) -> tuple[str, tuple[str, str]]:
    # datas gravadas como ISO UTC (`+00:00`): a ordem das strings é a ordem do tempo
    return sql + " criado_em >= ? AND criado_em < ?", (inicio.isoformat(), fim.isoformat())


def _produzidos(cfg: Config, con: sqlite3.Connection, inicio: datetime, fim: datetime) -> list[str]:
    sql, args = _no_periodo("SELECT job_id FROM revisoes WHERE aprovado = 1 AND", inicio, fim)
    linhas = []
    for r in con.execute(sql + " ORDER BY criado_em", args):
        job = fila.obter(con, r["job_id"])
        linhas.append(_item(cfg, job, f"estado atual: {job.estado.value}"))
    return linhas


def _quarentena(cfg: Config, con: sqlite3.Connection) -> list[str]:
    linhas = []
    for job in fila.listar(con, {Estado.QUARENTENA}):
        r = con.execute(
            "SELECT motivos_json FROM revisoes WHERE job_id = ? ORDER BY rodada DESC LIMIT 1",
            (job.id,),
        ).fetchone()
        motivos = json.loads(r["motivos_json"]) if r else []
        linhas.append(_item(cfg, job))
        linhas += [f"  - {m}" for m in motivos] or ["  - (sem motivo registrado)"]
    if linhas:
        linhas += ["", "Para decidir: `autocanal aprovar <job>`, `autocanal descartar <job>` ou "
                       "`autocanal produzir <job>` (refaz com os problemas da última revisão)."]
    return linhas


def alertas(cfg: Config, con: sqlite3.Connection, agora: datetime) -> list[str]:
    """Canais sem autorização, cota do YouTube esgotada, pausa do Claude e recuo do planejador."""
    avisos: list[str] = []
    conhecidos: set[str] = set()
    for r in con.execute("SELECT canal, auth_ok, ultimo_erro FROM canais_estado ORDER BY canal"):
        canal = r["canal"]
        conhecidos.add(canal)
        if not r["auth_ok"]:
            motivo = f" ({r['ultimo_erro']})" if r["ultimo_erro"] else ""
            avisos.append(f"Canal `{canal}` sem autorização do YouTube{motivo}: envios parados;"
                          f" rode `autocanal auth {canal}`.")
    for canal in sorted({j.canal for j in fila.listar(con, {Estado.APROVADO})} - conhecidos):
        avisos.append(f"Canal `{canal}` ainda não autorizado, com vídeos aprovados esperando"
                      f" envio; rode `autocanal auth {canal}`.")
    cota = publicador.cota_esgotada_ate(cfg)
    if cota is not None and agora < cota:
        avisos.append(f"Cota do YouTube esgotada: envios suspensos até {_hora(cfg, cota)}.")
    pausa = produtor.pausa_claude_ate(cfg)
    if pausa is not None and agora < pausa:
        avisos.append(f"Claude pausado por limite de uso do plano até {_hora(cfg, pausa)}:"
                      " a produção retoma depois.")
    sistema = produtor.pausa_sistema(cfg)
    if sistema is not None and agora < sistema[0]:
        avisos.append(f"Produção pausada até {_hora(cfg, sistema[0])} por queda geral"
                      f" ({sistema[1] or 'motivo desconhecido'}): verifique o serviço.")
    recuo = planejador.planejar_apos(cfg)
    if recuo is not None and agora < recuo:
        avisos.append(f"Planejamento automático adiado até {_hora(cfg, recuo)} por falha ao"
                      " sugerir temas (detalhes no log do tick); `autocanal planejar` tenta já.")
    return avisos


def _supervisao(cfg: Config, con: sqlite3.Connection, dia: date) -> list[str]:
    # primeiro upload ≈ menor publishAt entre os jobs que chegaram a ter vídeo no YouTube
    enviados = [j.publish_at for j in fila.listar(con) if j.youtube_video_id]
    if enviados:
        primeiro = min(enviados).astimezone(ZoneInfo(cfg.fuso)).date()
        fim = primeiro + timedelta(days=cfg.revisao.supervisao_dias)
        if dia >= fim:
            return [f"Encerrada em {fim:%d/%m/%Y}: daqui em diante só a quarentena chega até você."]
        cabeca = f"Até {fim:%d/%m/%Y}, confira cada vídeo aprovado antes de ir ao ar:"
    else:
        cabeca = "Nenhum vídeo enviado ainda; confira cada vídeo aprovado antes de ir ao ar:"
    linhas = [
        _item(cfg, j, _hora(cfg, j.publish_at), _link(j), f"vetar: `autocanal vetar {j.id}`")
        for j in fila.listar(con, {Estado.AGENDADO})
    ] + [
        _item(cfg, j, "aguardando envio", f"impedir: `autocanal descartar {j.id}`")
        for j in fila.listar(con, {Estado.APROVADO})
    ]
    return [cabeca, "", *(linhas or [NENHUM])]


def _consumo(con: sqlite3.Connection, inicio: datetime, fim: datetime) -> list[str]:
    sql, args = _no_periodo(
        "SELECT COUNT(*) AS n, COALESCE(SUM(custo_usd), 0) AS custo FROM chamadas_claude WHERE",
        inicio, fim,
    )
    r = con.execute(sql, args).fetchone()
    n = r["n"]
    return [f"- {n} {'chamada' if n == 1 else 'chamadas'}, custo equivalente de"
            f" US$ {r['custo']:.2f}"]


def gerar_resumo(cfg: Config, con: sqlite3.Connection, dia: date) -> Path:
    """Escreve `dados/relatorios/<dia>.md`.

    Produzidos (aprovados pela revisão), Publicados e Consumo do Claude cobrem o dia
    anterior a `dia` (00:00–24:00 no fuso do projeto): o resumo sai de manhã. As demais
    seções mostram a situação no momento em que o resumo é gerado.
    """
    tz = ZoneInfo(cfg.fuso)
    ontem = dia - timedelta(days=1)
    inicio = datetime.combine(ontem, time(0), tzinfo=tz).astimezone(UTC)
    fim = datetime.combine(dia, time(0), tzinfo=tz).astimezone(UTC)
    agora = datetime.now(UTC)

    def lista(linhas: list[str]) -> str:
        return "\n".join(linhas) if linhas else NENHUM

    publicados = [
        _item(cfg, j, _hora(cfg, j.publish_at), _link(j))
        for j in fila.listar(con, {Estado.PUBLICADO}) if inicio <= j.publish_at < fim
    ]
    secoes = {
        "Produzidos": lista(_produzidos(cfg, con, inicio, fim)),
        "Agendados": lista([
            _item(cfg, j, _hora(cfg, j.publish_at), _link(j))
            for j in fila.listar(con, {Estado.AGENDADO})
        ]),
        "Publicados": lista(publicados),
        "Quarentena": lista(_quarentena(cfg, con)),
        "Falhas": lista([
            _item(cfg, j, j.erro or "(sem motivo registrado)")
            for j in fila.listar(con, {Estado.FALHOU})
        ]),
        "Alertas": lista([f"- {a}" for a in alertas(cfg, con, agora)]),
        "Supervisão": "\n".join(_supervisao(cfg, con, dia)),
        "Consumo do Claude": lista(_consumo(con, inicio, fim)),
    }
    texto = (
        f"# Resumo do autocanal — {dia:%d/%m/%Y}\n\n"
        f"Produzidos, Publicados e Consumo do Claude: atividade de {ontem:%d/%m/%Y}"
        f" (fuso {cfg.fuso}). As outras seções mostram a situação em {_hora(cfg, agora)}.\n"
        + "".join(f"\n## {titulo}\n\n{corpo}\n" for titulo, corpo in secoes.items())
    )
    destino = caminho_resumo(cfg, dia)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temp = destino.with_name(destino.name + ".tmp")
    temp.write_text(texto, encoding="utf-8")
    os.replace(temp, destino)  # o tick usa a existência do arquivo: nunca deixá-lo pela metade
    return destino


def notificar(titulo: str, texto: str) -> None:
    """Aviso na área de trabalho via `notify-send`; silencioso se ele não existir ou falhar."""
    exe = shutil.which("notify-send")
    if exe is None:
        return
    try:
        subprocess.run([exe, titulo, texto], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
