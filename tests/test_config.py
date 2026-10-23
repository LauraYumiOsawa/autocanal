import pytest
import yaml
from pydantic import ValidationError

from autocanal.config import Slot, carregar


def test_carregar_resolve_caminhos(raiz_teste):
    ay = raiz_teste / "config" / "autocanal.yaml"
    dados = yaml.safe_load(ay.read_text())
    dados["ffmpeg"] = "vendor/bin/ffmpeg"
    ay.write_text(yaml.safe_dump(dados))
    cfg = carregar(raiz_teste)
    assert cfg.ffmpeg == raiz_teste / "vendor/bin/ffmpeg"
    assert cfg.dados == raiz_teste / "dados"
    assert cfg.canais["teste"].slug == "teste"
    assert cfg.canais["teste"].perfil_voz == raiz_teste / "config/vozes/exemplo.md"


def test_segredos_opcionais(raiz_teste):
    assert carregar(raiz_teste).chaves == {}
    (raiz_teste / "config" / "segredos.env").write_text("PEXELS_API_KEY=abc\n")
    assert carregar(raiz_teste).chaves["PEXELS_API_KEY"] == "abc"


def test_segredos_sem_aspas(raiz_teste):
    (raiz_teste / "config" / "segredos.env").write_text(
        "A=\"abc\"\nB='def'\nC=\"g\nD=\"\n"
    )
    chaves = carregar(raiz_teste).chaves
    assert chaves == {"A": "abc", "B": "def", "C": "\"g", "D": "\""}


def test_slot_hora_invalida():
    with pytest.raises(ValidationError):
        Slot(formato="short", dias=["seg"], hora="25:00")


def test_canal_sem_nicho_padrao_vazio(cfg):
    assert cfg.canais["teste"].nicho == ""


def test_carregar_resolve_bin_do_claude(raiz_teste, monkeypatch, tmp_path):
    from autocanal import config

    monkeypatch.setattr(config.shutil, "which", lambda nome: f"/opt/bin/{nome}")
    assert carregar(raiz_teste).claude.bin == "/opt/bin/claude"
    monkeypatch.setattr(config.shutil, "which", lambda nome: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.resolver_bin_claude("claude") == str(tmp_path / ".local" / "bin" / "claude")
    assert config.resolver_bin_claude("/x/claude") == "/x/claude"
