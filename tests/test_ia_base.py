from pathlib import Path

import pytest

from autocanal.ia.base import ErroIA, LimiteUso, Pedido, lista_imagens, validar
from autocanal.ia.cofre import caminhos_proibidos
from autocanal.ia.metodo import preparar


def test_erro_ia_guarda_provedor():
    e = LimiteUso("codex", "acabou")
    assert isinstance(e, ErroIA)
    assert e.provedor == "codex"
    assert str(e) == "codex: acabou"


def test_lista_imagens_relativa_e_numerada(tmp_path):
    p = Pedido(prompt="x", schema={}, pasta=tmp_path,
               imagens=(tmp_path / "a.jpg", tmp_path / "q" / "b c.jpg"))
    assert lista_imagens(p) == "\n\nImagens deste pedido, na ordem:\n1. `a.jpg`\n2. `q/b c.jpg`"
    assert lista_imagens(Pedido(prompt="x", schema={}, pasta=tmp_path)) == ""


def test_cofre_lista_segredos_do_projeto_e_pessoais(tmp_path):
    lista, casa = caminhos_proibidos(tmp_path), Path.home()
    for alvo in (f"{tmp_path}/config/**", f"{tmp_path}/dados/tokens/**", f"{tmp_path}/dados/*.db",
                 f"{casa}/.ssh/**", f"{casa}/.claude/**", f"{casa}/.codex/**", f"{casa}/.netrc"):
        assert alvo in lista


def _plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    for nome in ("a", "b"):
        (plugin / "skills" / nome).mkdir(parents=True)
        (plugin / "skills" / nome / "SKILL.md").write_text(f"# {nome}", encoding="utf-8")
    (plugin / "skills" / "a" / "__pycache__").mkdir()
    (plugin / "skills" / "a" / "__pycache__" / "x.pyc").write_bytes(b"")
    return plugin


def test_metodo_copia_so_os_pedidos(tmp_path):
    job = tmp_path / "job"
    preparar(job, _plugin(tmp_path), ("a",))
    assert (job / "metodo" / "a" / "SKILL.md").read_text(encoding="utf-8") == "# a"
    assert not (job / "metodo" / "b").exists()
    assert not (job / "metodo" / "a" / "__pycache__").exists()


def test_metodo_sobrescreve_copia_antiga(tmp_path):
    job = tmp_path / "job"
    (job / "metodo" / "a").mkdir(parents=True)
    (job / "metodo" / "a" / "velho.txt").write_text("x", encoding="utf-8")
    preparar(job, _plugin(tmp_path), ("a",))
    assert not (job / "metodo" / "a" / "velho.txt").exists()
    assert (job / "metodo" / "a" / "SKILL.md").exists()


def test_metodo_ausente(tmp_path):
    with pytest.raises(FileNotFoundError, match="nada"):
        preparar(tmp_path / "job", tmp_path / "plugin", ("nada",))


def test_validar():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}
    validar("claude", {"a": 1}, schema)
    with pytest.raises(ErroIA, match="viola o schema"):
        validar("claude", {"a": "x"}, schema)
