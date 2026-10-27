import json
import subprocess
import sys
from pathlib import Path

from autocanal.ia.metodo import preparar

PLUGIN_DIR = Path(
    "/home/losawa/.claude/plugins/cache/youtube-agent-skill/youtube-agent/1.0.0"
)


def _rodar(pasta: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "metodo/pontuar.py", *args], cwd=pasta,
                          capture_output=True, text=True, timeout=30)


def test_pontuar_ganchos_ordenados(tmp_path):
    preparar(tmp_path, PLUGIN_DIR, ("yt-script",))
    r = _rodar(tmp_path, "ganchos", "Oi.", "Você está jogando dinheiro fora toda vez que abre a geladeira")
    assert r.returncode == 0, r.stderr
    itens = json.loads(r.stdout)
    assert len(itens) == 2
    assert set(itens[0]) == {"gancho", "verdict", "band", "formula"}
    assert itens[0]["verdict"] >= itens[1]["verdict"]
    assert {i["gancho"] for i in itens} == {
        "Oi.", "Você está jogando dinheiro fora toda vez que abre a geladeira"}


def test_pontuar_titulos_pares(tmp_path):
    preparar(tmp_path, PLUGIN_DIR, ("yt-package",))
    r = _rodar(tmp_path, "titulos", "Por que o mar é salgado? 3 motivos", "SAL ESCONDIDO",
               "mar", "mar")
    assert r.returncode == 0, r.stderr
    itens = json.loads(r.stdout)
    assert [i["thumb"] for i in itens] == ["SAL ESCONDIDO", "mar"]
    assert itens[0]["score"] > itens[1]["score"]
    assert set(itens[0]) == {"titulo", "thumb", "score", "issues"}


def test_pontuar_titulos_impar(tmp_path):
    preparar(tmp_path, PLUGIN_DIR, ("yt-package",))
    r = _rodar(tmp_path, "titulos", "a", "b", "c")
    assert r.returncode == 2
    assert "uso" in r.stderr and "Traceback" not in r.stderr


def test_pontuar_sem_comando(tmp_path):
    preparar(tmp_path, PLUGIN_DIR, ("yt-script",))
    r = _rodar(tmp_path)
    assert r.returncode == 2 and "uso" in r.stderr


def test_metodo_copia_pontuar(tmp_path):
    preparar(tmp_path / "com", PLUGIN_DIR, ("yt-script",))
    assert (tmp_path / "com" / "metodo" / "pontuar.py").exists()
    preparar(tmp_path / "sem", PLUGIN_DIR, ())
    assert not (tmp_path / "sem" / "metodo" / "pontuar.py").exists()
