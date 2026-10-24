#!/usr/bin/env python3
"""`gerar.py` falso do FLUX: segue o contrato (entrada.json → <id>.png + resultado.json).
Prompt com FALHAR → item ok=false; TRAVAR → dorme 5 s; QUEBRAR → sai com código 3."""
import json
import sys
import time
from pathlib import Path

from PIL import Image


def main() -> int:
    entrada = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    saida = Path(sys.argv[2])
    itens = []
    for item in entrada["itens"]:
        if "QUEBRAR" in item["prompt"]:
            print("erro ao carregar o modelo", file=sys.stderr)
            return 3
        if "TRAVAR" in item["prompt"]:
            time.sleep(5)
        if "FALHAR" in item["prompt"]:
            itens.append({"id": item["id"], "ok": False, "erro": "CUDA out of memory"})
            continue
        Image.new("RGB", (entrada["largura"], entrada["altura"]), (item["semente"] % 255, 80, 140)) \
            .save(saida / f"{item['id']}.png")
        itens.append({"id": item["id"], "ok": True, "erro": ""})
    (saida / "resultado.json").write_text(json.dumps({
        "modelo": "falso", "itens": itens, "vram_pico_mb": 100, "ram_pico_mb": 100, "segundos": 0.1,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
