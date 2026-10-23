from pathlib import Path

import pytest
import requests

from autocanal import servicos
from autocanal.legendas import (
    Legenda,
    Palavra,
    agrupar,
    alinhar,
    cor_ass,
    escapar_ass,
    gerar_ass,
    transcrever,
)
from autocanal.provedores.tts import criar_tts


def _no_ar(url: str) -> bool:
    try:
        return requests.get(url, timeout=3).status_code == 200
    except requests.RequestException:
        return False


def _palavras(textos, passo=0.3):
    return [Palavra(t, i * passo, i * passo + 0.25) for i, t in enumerate(textos)]


def _monotonas(palavras):
    for p in palavras:
        assert p.inicio <= p.fim
    for a, b in zip(palavras, palavras[1:]):
        assert a.fim <= b.inicio + 1e-9


def test_alinhar_usa_texto_do_roteiro_e_tempos_do_whisper():
    whisper = _palavras(["porque", "aqui", "na", "terra", "ele", "é", "laranja"])
    out = alinhar("Por que aqui na Terra ele é laranja?", whisper)
    assert [p.texto for p in out] == ["Por", "que", "aqui", "na", "Terra", "ele", "é", "laranja?"]
    _monotonas(out)
    # "Por que" divide o intervalo de "porque"; as casadas herdam os tempos do Whisper
    assert (out[0].inicio, out[1].fim) == (whisper[0].inicio, whisper[0].fim)
    assert (out[4].inicio, out[4].fim) == (whisper[3].inicio, whisper[3].fim)
    assert (out[-1].inicio, out[-1].fim) == (whisper[-1].inicio, whisper[-1].fim)
    legendas = agrupar(out)
    assert " ".join(lg.texto for lg in legendas) == "Por que aqui na Terra ele é laranja?"


def test_alinhar_interpola_palavras_sem_par():
    whisper = [Palavra("o", 0.0, 0.2), Palavra("mar", 0.2, 0.5), Palavra("salgado", 1.5, 2.0)]
    out = alinhar("Então o mar — muito — salgado. Fim", whisper)
    assert [p.texto for p in out] == ["Então", "o", "mar —", "muito —", "salgado.", "Fim"]
    _monotonas(out)
    assert out[0].fim == 0.0  # antes da primeira palavra, sem tempo negativo
    assert 0.5 <= out[3].inicio and out[3].fim <= 1.5
    assert out[-1].inicio == 2.0 and out[-1].fim > 2.0


def test_alinhar_vazio():
    assert alinhar("", _palavras(["a"])) == []
    assert alinhar("a b", []) == []


def test_agrupar_por_tamanho_e_pontuacao():
    ps = _palavras(["olá", "fim.", "um", "dois", "três", "quatro", "cinco"])
    grupos = agrupar(ps)
    assert grupos[0].texto == "olá fim."
    assert [g.texto for g in grupos] == ["olá fim.", "um dois três", "quatro cinco"]
    assert all(len(g.texto.split()) <= 3 for g in grupos)
    assert grupos[1].inicio == ps[2].inicio and grupos[1].fim == ps[4].fim


def test_agrupar_respeita_duracao():
    ps = [Palavra("a", 0.0, 0.5), Palavra("b", 0.6, 1.2), Palavra("c", 1.3, 2.0)]
    grupos = agrupar(ps)
    assert [g.texto for g in grupos] == ["a b", "c"]


def test_agrupar_vazio():
    assert agrupar([]) == []


def test_cor_ass():
    assert cor_ass("#FACC15") == "&H0015CCFA"


def test_escapar_ass():
    assert escapar_ass('a{b}c\\d\n"e" 🚀') == 'a\\{b\\}c\\\\d "e" 🚀'


def test_gerar_ass_cabecalho_e_tempo(tmp_path):
    destino = gerar_ass(
        [Legenda("olá mundo", 1.5, 2.0)],
        tmp_path / "l.ass",
        largura=1080, altura=1920, fonte_nome="DejaVu Sans", tamanho=96,
        cor_texto="#F8FAFC", cor_destaque="#FACC15", margem_v=300,
    )
    txt = destino.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in txt
    assert "PlayResY: 1920" in txt
    assert "ScaledBorderAndShadow: yes" in txt
    assert "WrapStyle: 2" in txt
    assert "Dialogue: 0,0:00:01.50,0:00:02.00" in txt
    assert "OLÁ MUNDO" in txt
    assert "&H0015CCFA" in txt and "&H00FCFAF8" in txt


def test_import_nao_carrega_whisper():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, autocanal.legendas; print('faster_whisper' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    assert r.stdout.strip() == "False"


@pytest.mark.real
def test_transcrever_kokoro(cfg, tmp_path):
    canal = cfg.canais["teste"].model_copy(
        update={"tts": cfg.canais["teste"].tts.model_copy(
            update={"provedor": "kokoro", "voz": "pf_dora"})}
    )
    cfg_real = cfg.model_copy(update={"raiz": Path(__file__).parent.parent})
    frase = "O céu é azul e o mar é muito grande hoje"
    url = f"{cfg_real.kokoro_url.rstrip('/')}/health"
    ja_no_ar = _no_ar(url)
    try:
        tts = criar_tts(cfg_real, canal)
        wav = tts.sintetizar(frase, tmp_path / "k.wav")
        palavras = transcrever(wav)
    finally:
        if not ja_no_ar:
            servicos.parar("kokoro", cfg_real.raiz)
    assert palavras and all(p.fim >= p.inicio for p in palavras)
    esperadas = {"céu", "azul", "mar", "muito", "grande", "hoje", "o", "é", "e"}
    obtidas = {p.texto.lower().strip(".,!?") for p in palavras}
    assert len(esperadas & obtidas) / len(esperadas) >= 0.5
