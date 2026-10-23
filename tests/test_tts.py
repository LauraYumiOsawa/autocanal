import base64
import wave
from pathlib import Path

import pytest
import requests

from autocanal import servicos
from autocanal.provedores import ErroProvedor
from autocanal.provedores.tts import (
    GoogleTTS,
    KokoroTTS,
    TTSComReserva,
    TTSFalso,
    criar_tts,
)


class Resp:
    def __init__(self, status=200, content=b"", json_=None):
        self.status_code = status
        self.content = content
        self._json = json_

    def json(self):
        return self._json


class SessaoFalsa:
    def __init__(self, resposta=None, erro=None):
        self.resposta, self.erro, self.chamadas = resposta, erro, []

    def post(self, url, json=None, timeout=None):
        self.chamadas.append((url, json))
        if self.erro:
            raise self.erro
        return self.resposta


def test_tts_falso_duracao(midia, tmp_path):
    arq = TTSFalso().sintetizar("um dois três quatro cinco seis sete oito nove dez", tmp_path / "a.wav")
    assert abs(midia.info(arq).duracao - 4.0) < 0.05


def test_tts_falso_minimo_meio_segundo(tmp_path):
    arq = TTSFalso().sintetizar("oi", tmp_path / "a.wav")
    with wave.open(str(arq)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(0.5, abs=0.01)


def test_kokoro_corpo_da_requisicao(tmp_path):
    sessao = SessaoFalsa(Resp(200, b"RIFFxxxx"))
    k = KokoroTTS("http://127.0.0.1:8880", "pf_dora", sessao=sessao)
    arq = k.sintetizar("Olá", tmp_path / "k.wav")
    url, corpo = sessao.chamadas[0]
    assert url == "http://127.0.0.1:8880/v1/audio/speech"
    assert corpo == {
        "model": "kokoro", "input": "Olá", "voice": "pf_dora",
        "response_format": "wav", "speed": 1.0,
    }
    assert arq.read_bytes() == b"RIFFxxxx"


def test_kokoro_erros_viram_erro_provedor(tmp_path):
    with pytest.raises(ErroProvedor):
        KokoroTTS("http://x", "v", sessao=SessaoFalsa(Resp(500))).sintetizar("a", tmp_path / "a.wav")
    with pytest.raises(ErroProvedor):
        KokoroTTS(
            "http://x", "v", sessao=SessaoFalsa(erro=requests.ConnectionError("recusada"))
        ).sintetizar("a", tmp_path / "a.wav")


def test_google_decodifica_base64_e_esconde_chave(tmp_path):
    chave = "SEGREDO-123"
    audio = base64.b64encode(b"RIFF...").decode()
    sessao = SessaoFalsa(Resp(200, json_={"audioContent": audio}))
    g = GoogleTTS(chave, "pt-BR-Chirp3-HD-Aoede", sessao=sessao)
    arq = g.sintetizar("Olá", tmp_path / "g.wav")
    assert arq.read_bytes() == b"RIFF..."
    url, corpo = sessao.chamadas[0]
    assert url == f"https://texttospeech.googleapis.com/v1/text:synthesize?key={chave}"
    assert corpo["voice"] == {"languageCode": "pt-BR", "name": "pt-BR-Chirp3-HD-Aoede"}
    assert corpo["audioConfig"] == {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}

    ruim = GoogleTTS(chave, "v", sessao=SessaoFalsa(Resp(403)))
    with pytest.raises(ErroProvedor) as e:
        ruim.sintetizar("a", tmp_path / "x.wav")
    assert chave not in str(e.value)
    assert chave not in repr(ruim)

    # exceção de rede cuja mensagem contém a URL (com a chave)
    rede = GoogleTTS(chave, "v", sessao=SessaoFalsa(erro=requests.ConnectionError(f"falha em ...key={chave}")))
    with pytest.raises(ErroProvedor) as e:
        rede.sintetizar("a", tmp_path / "x.wav")
    assert chave not in str(e.value)
    assert chave not in repr(e.value.__cause__)


def test_reserva_usada_quando_principal_falha(tmp_path):
    class Quebrado:
        nome = "quebrado"

        def sintetizar(self, texto, destino):
            raise ErroProvedor("falhou")

    tts = TTSComReserva(Quebrado(), TTSFalso())
    tts.sintetizar("oi", tmp_path / "a.wav")
    assert tts.usou_reserva is True
    assert tts.nome == "falso"


def test_criar_tts_google_sem_chave_vira_reserva(cfg):
    canal = cfg.canais["teste"].model_copy(
        update={"tts": cfg.canais["teste"].tts.model_copy(update={"provedor": "google"})}
    )
    assert "GOOGLE_TTS_API_KEY" not in cfg.chaves
    tts = criar_tts(cfg, canal)
    assert isinstance(tts.principal, KokoroTTS)
    assert tts.principal.voz == "pm_alex"
    assert tts.nome == "kokoro"


def test_criar_tts_falso(cfg):
    assert isinstance(criar_tts(cfg, cfg.canais["teste"]).principal, TTSFalso)


def test_garantir_sobe_compose(tmp_path):
    chamadas = []
    respostas = [requests.ConnectionError("recusada"), Resp(200)]

    def obter(url, timeout=None):
        r = respostas.pop(0) if respostas else Resp(200)
        if isinstance(r, Exception):
            raise r
        return r

    servicos.garantir(
        "kokoro", tmp_path, "http://127.0.0.1:8880/health",
        executar=lambda cmd, **kw: chamadas.append(cmd), obter=obter,
    )
    assert chamadas == [[
        "docker", "compose", "-f",
        str(tmp_path / "servicos" / "kokoro" / "docker-compose.yml"), "up", "-d",
    ]]


def test_garantir_timeout(tmp_path):
    def obter(url, timeout=None):
        raise requests.ConnectionError("x")

    with pytest.raises(ErroProvedor):
        servicos.garantir(
            "kokoro", tmp_path, "http://x/health", timeout_s=0,
            executar=lambda cmd, **kw: None, obter=obter,
        )


def test_garantir_nao_sobe_se_saudavel(tmp_path):
    chamadas = []
    servicos.garantir(
        "kokoro", tmp_path, "http://x/health",
        executar=lambda cmd, **kw: chamadas.append(cmd), obter=lambda u, timeout=None: Resp(200),
    )
    assert chamadas == []


@pytest.mark.real
def test_kokoro_real_pt_br(cfg, midia, tmp_path):
    canal = cfg.canais["teste"].model_copy(
        update={"tts": cfg.canais["teste"].tts.model_copy(
            update={"provedor": "kokoro", "voz": "pf_dora"})}
    )
    # compose real do repositório
    cfg_real = cfg.model_copy(update={"raiz": Path(__file__).parent.parent})
    try:
        tts = criar_tts(cfg_real, canal)
        arq = tts.sintetizar("Olá, isto é um teste.", tmp_path / "k.wav")
        assert midia.info(arq).duracao > 1.0
    finally:
        servicos.parar("kokoro", cfg_real.raiz)
