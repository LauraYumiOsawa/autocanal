"""Pontua vários ganchos ou pares título/thumbnail numa chamada só (uma linha de comando = um turno).

Copiado para `<pasta>/metodo/pontuar.py`; usa as funções dos scripts do plugin copiados ao lado
(`yt-script/hookscore.py` e `yt-package/title.py`), sem modificá-los.

    python3 metodo/pontuar.py ganchos "<gancho 1>" "<gancho 2>" ...
    python3 metodo/pontuar.py titulos "<título 1>" "<thumb 1>" "<título 2>" "<thumb 2>" ...
"""
import importlib.util
import json
import sys
from pathlib import Path

USO = __doc__.split("\n\n", 2)[2]
AQUI = Path(__file__).resolve().parent


def _modulo(relativo: str):
    caminho = AQUI / relativo
    spec = importlib.util.spec_from_file_location(caminho.stem, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ganchos(textos: list[str]) -> list[dict]:
    hs = _modulo("yt-script/hookscore.py")
    saida = []
    for t in textos:
        _, verdict, formula, _ = hs.score(t)
        saida.append({"gancho": t.strip(), "verdict": verdict, "band": hs.band(verdict),
                      "formula": formula})
    return sorted(saida, key=lambda r: -r["verdict"])


def titulos(pares: list[tuple[str, str]]) -> list[dict]:
    tp = _modulo("yt-package/title.py")
    saida = []
    for titulo, thumb in pares:
        r = tp.check(titulo, thumb or None)
        saida.append({"titulo": titulo, "thumb": thumb, "score": r["score"],
                      "issues": [f"{tipo}: {msg}" for tipo, msg in r["issues"]]})
    return sorted(saida, key=lambda r: -r["score"])


def main(args: list[str]) -> int:
    if len(args) < 2 or args[0] not in ("ganchos", "titulos"):
        print(f"uso:\n{USO}", file=sys.stderr)
        return 2
    comando, valores = args[0], args[1:]
    if comando == "titulos":
        if len(valores) % 2:
            print(f"uso: títulos e thumbnails em pares\n{USO}", file=sys.stderr)
            return 2
        resultado = titulos(list(zip(valores[::2], valores[1::2])))
    else:
        resultado = ganchos(valores)
    print(json.dumps(resultado, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
