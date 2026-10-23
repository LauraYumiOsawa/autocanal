#!/usr/bin/env python3
"""Executável falso do `claude`: registra o argv e devolve saídas pré-gravadas."""
import json
import os
import sys
from pathlib import Path


def main() -> int:
    log = Path(os.environ["CLAUDE_FALSO_LOG"])
    anteriores = json.loads(log.read_text()) if log.exists() else []
    log.write_text(json.dumps(anteriores + [sys.argv]))
    pasta = os.environ.get("CLAUDE_FALSO_SAIDAS")
    if pasta and Path(pasta).is_dir():
        arquivo = Path(pasta) / f"{len(anteriores) + 1:02d}.json"
    else:
        arquivo = Path(os.environ["CLAUDE_FALSO_SAIDA"])
    sys.stdout.write(arquivo.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
