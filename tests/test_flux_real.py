import json
from pathlib import Path

import pytest
from PIL import Image

from autocanal.config import carregar
from autocanal.provedores import flux
from autocanal.provedores.imagens import Pedido

RAIZ = Path(__file__).parents[1]


@pytest.mark.real
def test_flux_real_gera_16x9_e_9x16(tmp_path):
    cfg = carregar(RAIZ)
    assert flux.instalado(cfg), "servicos/flux não instalado"
    flux.preparar_memoria(cfg.raiz)
    gerador = flux.GeradorFlux(cfg, job_id="teste-real", rodada=0, estilo_visual="realista")
    for orientacao, tamanho in (("paisagem", (1360, 768)), ("retrato", (768, 1360))):
        pasta = tmp_path / orientacao
        pasta.mkdir()
        pedido = Pedido("seg_01", "router", "o roteador do vizinho",
                        "a wireless router with blinking lights on a wooden shelf at night",
                        pasta / "seg_01")
        r = gerador.buscar_lote([pedido], orientacao, set())
        with Image.open(r["seg_01"].caminho) as im:
            assert im.size == tamanho
        resultado = json.loads((pasta / "flux_lote" / "saida" / "resultado.json").read_text())
        assert resultado["vram_pico_mb"] <= 7680, resultado
