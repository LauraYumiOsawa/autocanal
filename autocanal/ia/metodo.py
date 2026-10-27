"""Skills do plugin copiadas para a pasta do job: qualquer agente lê o método como arquivo."""
from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

# script nosso que pontua vários candidatos numa chamada só (usa os scripts do plugin)
PONTUAR = Path(__file__).parent / "extras" / "pontuar.py"


def preparar(pasta: Path, plugin_dir: Path, nomes: Sequence[str]) -> None:
    """Copia `<plugin_dir>/skills/<nome>/` para `<pasta>/metodo/<nome>/` (cópia nova a cada vez).

    Com algum método, copia também `metodo/pontuar.py`.
    """
    for nome in nomes:
        origem = Path(plugin_dir) / "skills" / nome
        if not origem.is_dir():
            raise FileNotFoundError(f"método {nome} não encontrado em {origem}")
        destino = Path(pasta) / "metodo" / nome
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(origem, destino, ignore=shutil.ignore_patterns("__pycache__"))
    if nomes:
        shutil.copy(PONTUAR, Path(pasta) / "metodo" / "pontuar.py")
