"""Serviços locais em Docker (ex.: Kokoro): sobe sob demanda e para depois."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import requests

from autocanal.provedores import ErroProvedor


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
    executar([*_compose(nome, raiz), "up", "-d"], check=True, capture_output=True)
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        if _saudavel(url_saude, obter):
            return
        time.sleep(min(2, max(0.0, limite - time.monotonic())))
    raise ErroProvedor(f"serviço {nome} não ficou saudável em {timeout_s}s ({url_saude})")


def parar(nome: str, raiz: Path, executar=subprocess.run) -> None:
    executar([*_compose(nome, raiz), "down"], check=True, capture_output=True)
