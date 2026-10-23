"""OAuth do YouTube: autorização local, tokens persistentes e cliente da API."""
from __future__ import annotations

import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

ESCOPOS = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


class AuthExpirada(Exception):
    """Token ausente, revogado ou inválido: é preciso rodar `autocanal auth`."""


def salvar(creds, token: Path) -> None:
    token = Path(token)
    if not token.parent.exists():
        token.parent.mkdir(parents=True, mode=0o700)
        token.parent.chmod(0o700)
    # grava num temporário 600 e troca de uma vez: um crash não deixa o token truncado
    tmp = token.with_name(f".{token.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(0o600)  # o modo de open() não vale para arquivo já existente
        os.replace(tmp, token)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def autorizar(
    client_secret: Path,
    destino_token: Path,
    porta: int = 8080,
    fluxo_factory=InstalledAppFlow.from_client_secrets_file,
) -> Credentials:
    fluxo = fluxo_factory(str(client_secret), ESCOPOS)
    creds = fluxo.run_local_server(port=porta, access_type="offline", prompt="consent")
    salvar(creds, destino_token)
    return creds


def carregar(token: Path, requisicao=None) -> Credentials:
    token = Path(token)
    try:
        creds = Credentials.from_authorized_user_file(str(token), ESCOPOS)
    except (OSError, ValueError) as e:
        raise AuthExpirada(f"token ausente ou inválido: {token.name}") from e
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise AuthExpirada(f"token sem refresh_token: {token.name}")
    try:
        creds.refresh(requisicao or Request())
    except RefreshError as e:
        raise AuthExpirada(str(e)) from e
    salvar(creds, token)
    return creds


def cliente(creds):
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def canal_autenticado(youtube) -> tuple[str, str]:
    """Devolve (id, título) do canal da conta autenticada."""
    itens = youtube.channels().list(part="snippet", mine=True).execute().get("items") or []
    if not itens:
        raise AuthExpirada("conta sem canal")
    return itens[0]["id"], itens[0]["snippet"]["title"]
