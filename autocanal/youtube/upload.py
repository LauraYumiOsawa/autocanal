"""Upload agendado, thumbnail, veto e consulta de status no YouTube."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

_RETENTAVEIS = {500, 502, 503, 504}
_RAZOES_COTA = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}


class CotaEsgotada(Exception):
    pass


class ErroUpload(Exception):
    pass


def formatar_publish_at(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def montar_corpo(
    meta: dict, *, publish_at: datetime, categoria_id: str, idioma: str, sintetico: bool
) -> dict:
    return {
        "snippet": {
            "title": meta["titulo"],
            "description": meta["descricao_final"],
            "tags": meta["tags"],
            "categoryId": categoria_id,
            "defaultLanguage": idioma,
            "defaultAudioLanguage": idioma,
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": formatar_publish_at(publish_at),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": sintetico,
        },
    }


def _razoes(e: HttpError) -> set[str]:
    try:
        erros = json.loads(e.content.decode("utf-8")).get("error", {}).get("errors", [])
        return {x.get("reason") for x in erros if isinstance(x, dict)}
    except (ValueError, AttributeError, UnicodeDecodeError):
        return set()


def _traduzir(e: HttpError) -> Exception:
    if _razoes(e) & _RAZOES_COTA:
        return CotaEsgotada(str(e))
    return ErroUpload(str(e))


def _executar(requisicao):
    try:
        return requisicao.execute()
    except HttpError as e:
        raise _traduzir(e) from e


def enviar(
    youtube,
    video: Path,
    corpo: dict,
    *,
    chunk_bytes: int = 8 * 1024 * 1024,
    max_tentativas: int = 5,
    dormir=time.sleep,
    media_factory=MediaFileUpload,
) -> str:
    media = media_factory(str(video), mimetype="video/mp4", chunksize=chunk_bytes, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=corpo, media_body=media)
    falhas = 0
    while True:
        try:
            _, resposta = req.next_chunk()
        except HttpError as e:
            if _razoes(e) & _RAZOES_COTA:
                raise CotaEsgotada(str(e)) from e
            if e.resp.status not in _RETENTAVEIS:
                raise ErroUpload(str(e)) from e
            erro: Exception = e
        except OSError as e:
            erro = e
        else:
            if resposta is not None:
                return resposta["id"]
            continue
        falhas += 1
        if falhas >= max_tentativas:
            raise ErroUpload(f"upload falhou após {falhas} tentativas: {erro}") from erro
        dormir(2 ** (falhas - 1))


def definir_thumbnail(youtube, video_id: str, imagem: Path, *, media_factory=MediaFileUpload) -> None:
    _executar(
        youtube.thumbnails().set(
            videoId=video_id, media_body=media_factory(str(imagem), mimetype="image/png")
        )
    )


def vetar(youtube, video_id: str) -> None:
    # update substitui a parte "status" inteira: manter madeForKids explícito evita 400
    _executar(
        youtube.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
            },
        )
    )


def status(youtube, ids: list[str]) -> dict[str, str]:
    saida: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        r = _executar(youtube.videos().list(part="status", id=",".join(ids[i : i + 50])))
        for item in r.get("items", []):
            saida[item["id"]] = item["status"]["privacyStatus"]
    return saida
