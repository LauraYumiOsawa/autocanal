import itertools
import json

import requests

from autocanal import fontes

PAGINA = """<html><head><title>Mar</title><style>.x{color:red}</style>
<script>var segredo = "o mar é doce";</script></head>
<body><p>O sal do mar vem   das rochas,
dissolvidas pela chuva ao longo de milhões de anos.</p>
<p>“Água” do oceano tem cerca de 3,5% de sal — em média.</p></body></html>"""


class Resposta:
    def __init__(self, corpo: bytes, status=200, tipo="text/html; charset=utf-8", infinito=False):
        self.status_code = status
        self.headers = {"Content-Type": tipo}
        self.encoding = "utf-8"
        self._corpo = corpo
        self._infinito = infinito
        self.lidos = 0

    def iter_content(self, chunk_size=65536):
        if self._infinito:
            for _ in itertools.count():
                self.lidos += chunk_size
                yield b"a" * chunk_size
        for i in range(0, len(self._corpo), chunk_size):
            yield self._corpo[i:i + chunk_size]

    def close(self):
        pass


def _obter(resposta=None, erro=None):
    chamadas = []

    def obter(url, **kw):
        chamadas.append((url, kw))
        if erro is not None:
            raise erro
        return resposta

    obter.chamadas = chamadas
    return obter


def _fonte(trecho):
    return {"afirmacao": "o sal vem das rochas", "url": "https://exemplo.org/mar", "trecho": trecho}


def test_conferir_confirmada_inteiro():
    obter = _obter(Resposta(PAGINA.encode()))
    [r] = fontes.conferir([_fonte("O sal do mar vem das rochas")], obter=obter)
    assert (r["status"], r["detalhe"]) == ("confirmada", "inteiro")
    assert r["url"] == "https://exemplo.org/mar" and r["trecho"] == "O sal do mar vem das rochas"
    _, kw = obter.chamadas[0]
    assert kw["timeout"] == 15 and kw["stream"] is True and "User-Agent" in kw["headers"]


def test_conferir_ignora_acento_aspas_e_espacos():
    obter = _obter(Resposta(PAGINA.encode()))
    [r] = fontes.conferir([_fonte('"agua" do OCEANO tem cerca de 3,5% de sal - em media')],
                          obter=obter)
    assert r["status"] == "confirmada"


def test_conferir_parcial_80():
    trecho = "O sal do mar vem das rochas, dissolvidas pela chuva ao longo de bilhões"
    [r] = fontes.conferir([_fonte(trecho)], obter=_obter(Resposta(PAGINA.encode())))
    assert r["status"] == "confirmada" and r["detalhe"].startswith("parcial ")


def test_conferir_trecho_ausente():
    [r] = fontes.conferir([_fonte("A Lua é feita de queijo suíço maturado")],
                          obter=_obter(Resposta(PAGINA.encode())))
    assert r["status"] == "trecho_ausente" and r["detalhe"].startswith("maior trecho em comum ")


def test_conferir_inacessivel_http_e_excecao():
    [r] = fontes.conferir([_fonte("x y z")], obter=_obter(Resposta(b"", status=403)))
    assert r["status"] == "inacessivel" and "403" in r["detalhe"]
    [r] = fontes.conferir([_fonte("x y z")], obter=_obter(erro=requests.ConnectionError("caiu")))
    assert r["status"] == "inacessivel" and "ConnectionError" in r["detalhe"]


def test_conferir_pdf_e_inacessivel():
    [r] = fontes.conferir([_fonte("x y z")],
                          obter=_obter(Resposta(b"%PDF-1.7", tipo="application/pdf")))
    assert r["status"] == "inacessivel" and "application/pdf" in r["detalhe"]


def test_conferir_limita_2mb():
    resposta = Resposta(b"", infinito=True)
    [r] = fontes.conferir([_fonte("x y z")], obter=_obter(resposta))
    assert r["status"] == "trecho_ausente"
    assert resposta.lidos <= fontes.MAX_BYTES + 65536


def test_conferir_sem_trecho():
    obter = _obter(Resposta(PAGINA.encode()))
    resultado = fontes.conferir([{"afirmacao": "a", "url": "https://x.org"}, _fonte("  ")],
                                obter=obter)
    assert [r["status"] for r in resultado] == ["sem_trecho", "sem_trecho"]
    assert obter.chamadas == []


def test_conferidas_usa_cache_e_invalida(tmp_path):
    obter = _obter(Resposta(PAGINA.encode()))
    lista = [_fonte("O sal do mar vem das rochas")]
    primeira = fontes.conferidas(tmp_path, lista, obter=obter)
    assert fontes.conferidas(tmp_path, lista, obter=obter) == primeira
    assert len(obter.chamadas) == 1
    cache = json.loads((tmp_path / "fontes_conferidas.json").read_text(encoding="utf-8"))
    assert cache["resultado"] == primeira
    fontes.conferidas(tmp_path, [_fonte("dissolvidas pela chuva")], obter=obter)
    assert len(obter.chamadas) == 2


def test_texto_da_pagina_sem_script_style():
    texto = fontes.texto_da_pagina(PAGINA)
    assert "doce" not in texto and "color" not in texto
    assert "O sal do mar vem das rochas" in " ".join(texto.split())
