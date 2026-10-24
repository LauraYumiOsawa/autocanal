"""Serviços locais em Docker (ex.: Kokoro): sobe sob demanda e para depois."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import requests

from autocanal.provedores import ErroProvedor


log = logging.getLogger(__name__)


class ServicoIndisponivel(ErroProvedor):
    """O Docker não subiu o serviço: falha do sistema, não do job."""


def _compose(nome: str, raiz: Path) -> list[str]:
    return ["docker", "compose", "-f", str(raiz / "servicos" / nome / "docker-compose.yml")]


def _saudavel(url_saude: str, obter) -> bool:
    try:
        return obter(url_saude, timeout=5).status_code == 200
    except requests.RequestException:
        return False


def garantir(
    nome: str,
    raiz: Path,
    url_saude: str,
    timeout_s: int = 180,
    executar=subprocess.run,
    obter=requests.get,
) -> None:
    if _saudavel(url_saude, obter):
        return
    try:
        executar([*_compose(nome, raiz), "up", "-d"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError) as e:
        raise ServicoIndisponivel(
            f"não consegui subir o serviço {nome}: {type(e).__name__}"
        ) from None
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        if _saudavel(url_saude, obter):
            return
        time.sleep(min(2, max(0.0, limite - time.monotonic())))
    raise ErroProvedor(f"serviço {nome} não ficou saudável em {timeout_s}s ({url_saude})")


def parar(nome: str, raiz: Path, executar=subprocess.run) -> None:
    executar([*_compose(nome, raiz), "down"], check=True, capture_output=True)


def parar_kokoro(raiz: Path) -> None:
    """Para o contêiner do Kokoro (se o projeto o tiver); erros só viram aviso."""
    if not (raiz / "servicos" / "kokoro" / "docker-compose.yml").exists():
        return
    try:
        parar("kokoro", raiz)
    except Exception as e:  # noqa: BLE001 - parar é cortesia: nunca derruba a produção
        log.warning("não consegui parar o Kokoro: %s", type(e).__name__)
