"""Conferência das fontes do roteiro sem IA: o trecho citado está mesmo na página?

O roteirista copia, para cada afirmação, um trecho literal da página. Aqui a página é baixada e o
trecho é procurado (sem acentos, maiúsculas, pontuação nem espaços extras). O revisor só precisa
abrir as fontes que não forem confirmadas, o que economiza turnos de leitura de páginas.
"""
from __future__ import annotations

import codecs
import difflib
import json
import re
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

import requests

ARQUIVO = "fontes_conferidas.json"
TIMEOUT_S = 15
MAX_BYTES = 2 * 1024 * 1024
COBERTURA_MINIMA = 0.8
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
_IGNORAR = {"script", "style", "noscript", "template"}


class _Texto(HTMLParser):
    def __init__(self):
        super().__init__()
        self.partes: list[str] = []
        self._fundo = 0

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORAR:
            self._fundo += 1

    def handle_endtag(self, tag):
        if tag in _IGNORAR and self._fundo:
            self._fundo -= 1

    def handle_data(self, data):
        if not self._fundo:
            self.partes.append(data)


def texto_da_pagina(html: str) -> str:
    """Texto visível da página (sem `script`, `style` e afins)."""
    p = _Texto()
    p.feed(html)
    p.close()
    return " ".join(p.partes)


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, só palavras separadas por um espaço (pontuação some)."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                         if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", sem_acento))


def _charset(tipo: str) -> str:
    """Charset explícito do `Content-Type` se existir; senão UTF-8 (o `requests` assumiria Latin-1)."""
    m = re.search(r"charset=[\"']?([\w.:-]+)", tipo, re.IGNORECASE)
    if m:
        try:
            return codecs.lookup(m.group(1)).name
        except LookupError:
            pass
    return "utf-8"


def _baixar(url: str, obter) -> str:
    """Texto da página; `ValueError` com o motivo se não der para ler."""
    try:
        r = obter(url, timeout=TIMEOUT_S, headers={"User-Agent": UA}, stream=True)
    except requests.RequestException as e:
        raise ValueError(type(e).__name__) from None
    try:
        if r.status_code >= 400:
            raise ValueError(f"HTTP {r.status_code}")
        tipo = r.headers.get("Content-Type", "")
        if "html" not in tipo and "text" not in tipo:
            raise ValueError(f"tipo {tipo or 'desconhecido'}")
        corpo = bytearray()
        for pedaco in r.iter_content(chunk_size=65536):
            corpo.extend(pedaco)
            if len(corpo) >= MAX_BYTES:
                break
        return texto_da_pagina(bytes(corpo[:MAX_BYTES]).decode(_charset(tipo), errors="replace"))
    except requests.RequestException as e:
        raise ValueError(type(e).__name__) from None
    finally:
        r.close()


def _procurar(trecho: str, pagina: str) -> tuple[str, str]:
    t, p = normalizar(trecho).split(), normalizar(pagina).split()
    if f" {' '.join(t)} " in f" {' '.join(p)} ":
        return "confirmada", "inteiro"
    m = difflib.SequenceMatcher(None, p, t, autojunk=False)
    bloco = m.find_longest_match(0, len(p), 0, len(t))
    cobertura = bloco.size / len(t)
    if cobertura >= COBERTURA_MINIMA:
        return "confirmada", f"parcial {cobertura:.0%}"
    return "trecho_ausente", f"maior trecho em comum {cobertura:.0%}"


def conferir(fontes: list[dict], obter=None) -> list[dict]:
    """Status de cada fonte: `confirmada`, `trecho_ausente`, `inacessivel` ou `sem_trecho`."""
    obter = obter or requests.get
    resultado = []
    for f in fontes:
        trecho = str(f.get("trecho") or "").strip()
        item = {"afirmacao": f.get("afirmacao", ""), "url": f.get("url", ""), "trecho": trecho}
        if not normalizar(trecho):
            resultado.append({**item, "status": "sem_trecho", "detalhe": ""})
            continue
        try:
            status, detalhe = _procurar(trecho, _baixar(item["url"], obter))
        except ValueError as e:
            status, detalhe = "inacessivel", str(e)
        except Exception as e:  # a conferência nunca derruba a etapa
            status, detalhe = "inacessivel", f"{type(e).__name__}: {e}"
        resultado.append({**item, "status": status, "detalhe": detalhe})
    return resultado


def conferidas(pasta: Path, fontes: list[dict], obter=None) -> list[dict]:
    """`conferir` com cache em `<pasta>/fontes_conferidas.json` (vale para as mesmas URLs e trechos)."""
    chave = [{"url": f.get("url", ""), "trecho": f.get("trecho", "")} for f in fontes]
    arq = Path(pasta) / ARQUIVO
    if arq.exists():
        try:
            salvo = json.loads(arq.read_text(encoding="utf-8"))
            if salvo.get("fontes") == chave:
                return salvo["resultado"]
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    resultado = conferir(fontes, obter)
    temp = arq.with_name(arq.name + ".tmp")
    temp.write_text(json.dumps({"fontes": chave, "resultado": resultado}, ensure_ascii=False,
                               indent=2), encoding="utf-8")
    temp.replace(arq)
    return resultado
