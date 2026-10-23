"""Ferramentas locais do plugin youtube-agent (scripts só com a stdlib)."""
from __future__ import annotations

import json
import subprocess
import sys

from autocanal.config import Config
from autocanal.etapas.base import ErroEtapa

TIMEOUT_S = 30


def nota_titulo(cfg: Config, titulo: str, thumb: str | None) -> tuple[int, list[str]]:
    """Nota (0–100) e problemas (`tipo: mensagem`) do par título/thumbnail pelo `title.py`."""
    script = cfg.plugin_dir / "skills" / "yt-package" / "title.py"
    argv = [sys.executable, str(script), "--title", titulo]
    if thumb:
        argv += ["--thumb", thumb]
    argv.append("--json")
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S
        )
    except subprocess.TimeoutExpired as e:
        raise ErroEtapa(f"title.py excedeu {TIMEOUT_S}s") from e
    if proc.returncode != 0:
        raise ErroEtapa(
            f"title.py saiu com código {proc.returncode}: {proc.stderr.strip()[-500:]}"
        )
    try:
        item = json.loads(proc.stdout)[0]
        return int(item["score"]), [f"{tipo}: {msg}" for tipo, msg in item["issues"]]
    except (ValueError, LookupError, TypeError) as e:
        raise ErroEtapa(f"title.py devolveu JSON inesperado: {e}") from e
