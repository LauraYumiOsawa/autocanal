"""Envio dos jobs aprovados ao YouTube e sincronização do que já foi ao ar."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from autocanal import fila, grade
from autocanal.config import Config
from autocanal.fila import Estado
from autocanal.youtube import oauth, upload

log = logging.getLogger(__name__)

CUSTO_UPLOAD = 100
_MARGEM = timedelta(minutes=30)
_REAGENDAR = timedelta(hours=1)


def _somar_cota(con: sqlite3.Connection, agora: datetime, unidades: int) -> None:
    dia = agora.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    con.execute(
        "INSERT INTO cota (dia, bucket, unidades) VALUES (?, 'upload', ?)"
        " ON CONFLICT(dia, bucket) DO UPDATE SET unidades = unidades + excluded.unidades",
        (dia, unidades),
    )
    con.commit()


def _auth_off(con: sqlite3.Connection, canal: str, motivo: str) -> None:
    con.execute(
        "INSERT INTO canais_estado (canal, auth_ok, ultimo_erro) VALUES (?, 0, ?)"
        " ON CONFLICT(canal) DO UPDATE SET auth_ok = 0, ultimo_erro = excluded.ultimo_erro",
        (canal, motivo),
    )
    con.commit()


def _auth_ok(con: sqlite3.Connection, canal: str) -> bool:
    r = con.execute("SELECT auth_ok FROM canais_estado WHERE canal = ?", (canal,)).fetchone()
    return r is None or bool(r["auth_ok"])


def _reagendar(cfg: Config, con, job, agora: datetime) -> fila.Job:
    ocupados = {
        j.publish_at
        for j in fila.listar(con, canal=job.canal)
        if j.formato == job.formato and j.id != job.id and j.estado != Estado.DESCARTADO
    }
    novo = grade.proximo_slot(
        cfg.canais[job.canal], job.formato, agora + _REAGENDAR, ocupados, cfg.fuso
    )
    return fila.atualizar(con, job.id, publish_at=novo)


def publicar_aprovados(
    cfg: Config, con: sqlite3.Connection, agora: datetime,
    *, youtube_por_canal: Callable[[str], object], job_id: str | None = None,
) -> list[str]:
    enviados: list[str] = []
    canais_off: set[str] = set()
    for job in fila.listar(con, {Estado.APROVADO}):
        if job_id is not None and job.id != job_id:
            continue
        if job.canal in canais_off or not _auth_ok(con, job.canal):
            continue
        canal = cfg.canais[job.canal]
        pasta = cfg.dados / "jobs" / job.id
        try:
            meta = json.loads((pasta / "metadados.json").read_text(encoding="utf-8"))
            if not (pasta / "video.mp4").is_file():
                raise FileNotFoundError("video.mp4")
            if job.publish_at < agora + _MARGEM:
                job = _reagendar(cfg, con, job, agora)
            corpo = upload.montar_corpo(
                meta, publish_at=job.publish_at, categoria_id=canal.categoria_id,
                idioma=canal.idioma, sintetico=bool(meta.get("sintetico")),
            )
        except (OSError, ValueError, KeyError) as e:
            fila.mudar_estado(con, job.id, Estado.FALHOU, erro=f"{type(e).__name__}: {e}")
            continue
        try:
            yt = youtube_por_canal(job.canal)
            video_id = upload.enviar(yt, pasta / "video.mp4", corpo)
        except oauth.AuthExpirada as e:
            _auth_off(con, job.canal, str(e))
            canais_off.add(job.canal)
            continue
        except upload.CotaEsgotada:
            return enviados
        except upload.ErroUpload as e:
            fila.mudar_estado(con, job.id, Estado.FALHOU, erro=str(e))
            continue
        try:
            fila.atualizar(con, job.id, youtube_video_id=video_id)
            fila.mudar_estado(con, job.id, Estado.AGENDADO)
        except Exception as e:
            raise RuntimeError(
                f"upload concluído (video_id={video_id}, job={job.id}) mas o banco falhou: {e}"
            ) from e
        enviados.append(video_id)
        _somar_cota(con, agora, CUSTO_UPLOAD)
        thumb = pasta / "thumbnail.png"
        if job.formato != "short" and thumb.is_file():
            try:
                upload.definir_thumbnail(yt, video_id, thumb)
            except upload.CotaEsgotada:
                return enviados
            except upload.ErroUpload as e:
                log.warning("thumbnail de %s não enviada: %s", job.id, e)
    return enviados


def sincronizar_publicados(
    cfg: Config, con: sqlite3.Connection, agora: datetime,
    *, youtube_por_canal: Callable[[str], object],
) -> list[str]:
    """Jobs `agendado` cujo horário passou e o vídeo já está público viram `publicado`."""
    por_canal: dict[str, list[fila.Job]] = {}
    for j in fila.listar(con, {Estado.AGENDADO}):
        if j.publish_at <= agora and j.youtube_video_id:
            por_canal.setdefault(j.canal, []).append(j)
    feitos: list[str] = []
    for canal, jobs in por_canal.items():
        try:
            estados = upload.status(youtube_por_canal(canal), [j.youtube_video_id for j in jobs])
        except oauth.AuthExpirada as e:
            _auth_off(con, canal, str(e))
            continue
        except (upload.CotaEsgotada, upload.ErroUpload) as e:
            log.warning("status de %s indisponível: %s", canal, e)
            continue
        for j in jobs:
            if estados.get(j.youtube_video_id) == "public":
                fila.mudar_estado(con, j.id, Estado.PUBLICADO)
                feitos.append(j.id)
    return feitos


def youtube_real(cfg: Config) -> Callable[[str], object]:
    clientes: dict[str, object] = {}

    def por_canal(canal: str):
        if canal not in clientes:
            creds = oauth.carregar(cfg.dados / "tokens" / f"{canal}.json")
            clientes[canal] = oauth.cliente(creds)
        return clientes[canal]

    return por_canal
