import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from autocanal.provedores import ErroProvedor
from autocanal.provedores import flux
from autocanal.provedores.imagens import Pedido

FALSO = [sys.executable, str(Path(__file__).parent / "fakes" / "flux_falso.py")]
MUITA = lambda: (16.0, 8.0)  # noqa: E731


def _pedidos(tmp_path, cenas):
    pasta = tmp_path / "visuais"
    pasta.mkdir(exist_ok=True)
    return [Pedido(f"seg_{i:02d}", "q", "fala", c, pasta / f"seg_{i:02d}")
            for i, c in enumerate(cenas, 1)]


def _gerador(cfg, **kw):
    kw.setdefault("comando_base", FALSO)
    kw.setdefault("memoria", MUITA)
    return flux.GeradorFlux(cfg, job_id="j1", rodada=0, estilo_visual=kw.pop("estilo_visual", "realista"),
                            **kw)


def test_lote_gera_midias(cfg, tmp_path):
    r = _gerador(cfg).buscar_lote(_pedidos(tmp_path, ["a red router", "a phone"]), "paisagem", set())
    assert sorted(r) == ["seg_01", "seg_02"]
    m = r["seg_01"]
    assert m.caminho == tmp_path / "visuais" / "seg_01.png" and m.caminho.is_file()
    assert (m.tipo, m.origem, m.autor) == ("imagem", "flux", "autocanal")
    assert m.licenca == "gerado localmente (FLUX.2 klein 4B, Apache 2.0)"
    assert m.url.startswith("flux:") and len(m.url) == len("flux:") + 8
    assert m.prompt == "a red router, photorealistic photo, natural light, no text, no watermark"
    with Image.open(m.caminho) as im:
        assert im.size == (1360, 768)


def test_sufixo_desenho(cfg, tmp_path):
    r = _gerador(cfg, estilo_visual="desenho", estilo_ia="pastel colors").buscar_lote(
        _pedidos(tmp_path, ["a router"]), "paisagem", set())
    assert r["seg_01"].prompt.startswith("a router, pastel colors, colorful 2D cartoon illustration")


def test_item_falhou_fica_de_fora(cfg, tmp_path):
    r = _gerador(cfg).buscar_lote(_pedidos(tmp_path, ["FALHAR aqui", "ok"]), "paisagem", set())
    assert list(r) == ["seg_02"]


def test_timeout_vira_erro(cfg, tmp_path):
    cfg.flux.timeout_base_s, cfg.flux.timeout_por_item_s = 1, 0
    with pytest.raises(ErroProvedor, match="excedeu"):
        _gerador(cfg).buscar_lote(_pedidos(tmp_path, ["TRAVAR"]), "paisagem", set())


def test_saida_nao_zero_vira_erro(cfg, tmp_path):
    with pytest.raises(ErroProvedor, match="código 3"):
        _gerador(cfg).buscar_lote(_pedidos(tmp_path, ["QUEBRAR"]), "paisagem", set())


def test_sem_memoria_pula_sem_rodar(cfg, tmp_path):
    executar = MagicMock()
    with pytest.raises(ErroProvedor, match="memória insuficiente"):
        _gerador(cfg, memoria=lambda: (2.0, 8.0), executar=executar).buscar_lote(
            _pedidos(tmp_path, ["x"]), "paisagem", set())
    executar.assert_not_called()


def test_retrato_inverte_dimensoes(cfg, tmp_path):
    _gerador(cfg).buscar_lote(_pedidos(tmp_path, ["x"]), "retrato", set())
    entrada = json.loads((tmp_path / "visuais" / "flux_lote" / "entrada.json").read_text())
    assert (entrada["largura"], entrada["altura"], entrada["passos"]) == (768, 1360, 4)


def test_semente_muda_com_rodada():
    assert flux.semente("j", "seg_01", 0) == flux.semente("j", "seg_01", 0)
    assert flux.semente("j", "seg_01", 0) != flux.semente("j", "seg_01", 1)
    assert 0 <= flux.semente("j", "seg_01", 0) < 2**31


def test_instalado(cfg):
    assert flux.instalado(cfg) is False
    cfg.flux.projeto.mkdir(parents=True)
    (cfg.flux.projeto / "pyproject.toml").write_text("[project]\n")
    (cfg.flux.projeto / "gerar.py").write_text("")
    assert flux.instalado(cfg) is True
