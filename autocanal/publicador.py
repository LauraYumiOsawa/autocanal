"""Envio dos jobs aprovados ao YouTube e sincronização do que já foi ao ar."""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from autocanal import fila, grade
from autocanal.config import Config
from autocanal.fila import Estado
from autocanal.youtube import oauth, upload

log = logging.getLogger(__name__)

CUSTO_UPLOAD = 100
_MARGEM = timedelta(hours=1)


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
    return r is not None and bool(r["auth_ok"])


def _reagendar(cfg: Config, con, job, agora: datetime) -> fila.Job:
    ocupados = {
        j.publish_at
        for j in fila.listar(con, canal=job.canal)
        if j.formato == job.formato and j.id != job.id and j.estado != Estado.DESCARTADO
    }
    novo = grade.proximo_slot(
        cfg.canais[job.canal], job.formato, agora + _MARGEM, ocupados, cfg.fuso
    )
    return fila.atualizar(con, job.id, publish_at=novo)


def _marcador_cota(cfg: Config) -> Path:
    return cfg.dados / "cota_esgotada_ate"


def cota_esgotada_ate(cfg: Config) -> datetime | None:
    """Até quando a cota diária do YouTube está esgotada (None se não houver marcador)."""
    try:
        return datetime.fromisoformat(_marcador_cota(cfg).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _cota_bloqueada(cfg: Config, agora: datetime) -> bool:
    ate = cota_esgotada_ate(cfg)
    return ate is not None and agora < ate


def _proxima_meia_noite_la(agora: datetime) -> datetime:
    la = ZoneInfo("America/Los_Angeles")
    dia = agora.astimezone(la).date() + timedelta(days=1)
    return datetime.combine(dia, time(0, 0), tzinfo=la).astimezone(UTC)


def _tratar_cota(cfg: Config, e: upload.CotaEsgotada, canal: str, canais_off: set[str], agora: datetime) -> bool:
    """Devolve True se a rodada inteira deve parar (cota diária do projeto)."""
    if e.motivo == "uploadLimitExceeded":
        log.warning("limite de uploads do canal %s atingido; pulando o canal nesta rodada", canal)
        canais_off.add(canal)
        return False
    ate = _proxima_meia_noite_la(agora)
    cfg.dados.mkdir(parents=True, exist_ok=True)
    _marcador_cota(cfg).write_text(ate.isoformat(), encoding="utf-8")
    log.warning("cota do YouTube esgotada (%s); publicação suspensa até %s", e.motivo, ate.isoformat())
    return True


def _agendar(con, job_id: str, video_id: str) -> None:
    try:
        fila.mudar_estado(con, job_id, Estado.AGENDADO)
    except Exception as e:
        raise RuntimeError(
            f"upload concluído (video_id={video_id}, job={job_id}) mas o banco falhou: {e}"
        ) from e


def publicar_aprovados(
    cfg: Config, con: sqlite3.Connection, agora: datetime,
    *, youtube_por_canal: Callable[[str], object], job_id: str | None = None,
    relogio: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    # `agora` fica por compatibilidade; margem e bloqueio de cota usam `relogio()`.
    if _cota_bloqueada(cfg, relogio()):
        log.info("cota do YouTube esgotada até %s; nada enviado", _marcador_cota(cfg).read_text().strip())
        return []
    enviados: list[str] = []
    canais_off: set[str] = set()
    for job in fila.listar(con, {Estado.APROVADO}):
        if job_id is not None and job.id != job_id:
            continue
        if job.canal in canais_off or not _auth_ok(con, job.canal):
            continue
        if job.youtube_video_id:
            # upload já feito numa rodada anterior que falhou ao gravar o estado: não reenviar
            _agendar(con, job.id, job.youtube_video_id)
            continue
        pasta = cfg.dados / "jobs" / job.id
        try:
            canal = cfg.canais.get(job.canal)
            if canal is None:
                raise KeyError(f"canal {job.canal!r} não está em config/canais.yaml")
            meta = json.loads((pasta / "metadados.json").read_text(encoding="utf-8"))
            if not (pasta / "video.mp4").is_file():
                raise FileNotFoundError("video.mp4")
            agora_real = relogio()
            if job.publish_at < agora_real + _MARGEM:
                job = _reagendar(cfg, con, job, agora_real)
            corpo = upload.montar_corpo(
                meta, publish_at=job.publish_at, categoria_id=canal.categoria_id,
                idioma=canal.idioma, sintetico=bool(meta.get("sintetico")),
                feito_para_criancas=canal.feito_para_criancas,
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
        except upload.CotaEsgotada as e:
            if _tratar_cota(cfg, e, job.canal, canais_off, relogio()):
                return enviados
            continue
        except upload.ErroUpload as e:
            fila.mudar_estado(con, job.id, Estado.FALHOU, erro=str(e))
            continue
        try:
            fila.atualizar(con, job.id, youtube_video_id=video_id)
        except Exception as e:
            raise RuntimeError(
                f"upload concluído (video_id={video_id}, job={job.id}) mas o banco falhou: {e}"
            ) from e
        _agendar(con, job.id, video_id)
        enviados.append(video_id)
        _somar_cota(con, agora, CUSTO_UPLOAD)
        thumb = pasta / "thumbnail.png"
        if job.formato != "short" and thumb.is_file():
            try:
                upload.definir_thumbnail(yt, video_id, thumb)
            except oauth.AuthExpirada as e:
                _auth_off(con, job.canal, str(e))
                canais_off.add(job.canal)
            except upload.CotaEsgotada as e:
                if _tratar_cota(cfg, e, job.canal, canais_off, relogio()):
                    return enviados
            except upload.ErroUpload as e:
                log.warning("thumbnail de %s não enviada: %s", job.id, e)
    return enviados


def _falhar_youtube(con, job, detalhe: str) -> None:
    """Marca `falhou` e limpa o id (guardado no erro) para que uma reaprovação reenvie."""
    fila.mudar_estado(con, job.id, Estado.FALHOU,
                      erro=f"youtube: {detalhe} (vídeo {job.youtube_video_id})")
    fila.atualizar(con, job.id, youtube_video_id=None)


_REJEITADOS = {"rejected", "failed", "deleted"}


def sincronizar_publicados(
    cfg: Config, con: sqlite3.Connection, agora: datetime,
    *, youtube_por_canal: Callable[[str], object],
) -> list[str]:
    """Confere todos os `agendado`: rejeitados/ausentes viram `falhou`; públicos após o horário, `publicado`."""
    por_canal: dict[str, list[fila.Job]] = {}
    for j in fila.listar(con, {Estado.AGENDADO}):
        if j.youtube_video_id:
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
            info = estados.get(j.youtube_video_id)
            if info is None:
                _falhar_youtube(con, j, "vídeo não encontrado")
            elif info.get("uploadStatus") in _REJEITADOS:
                motivo = info.get("rejectionReason") or info.get("failureReason") or ""
                _falhar_youtube(con, j, f"{info['uploadStatus']}: {motivo}")
            elif j.publish_at <= agora and info.get("privacyStatus") == "public":
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
