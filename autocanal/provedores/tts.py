"""Provedores de voz (TTS): Google, Kokoro local e um falso para testes."""
from __future__ import annotations

import base64
import math
import struct
import wave
from pathlib import Path
from typing import Protocol

import requests

from autocanal import servicos
from autocanal.config import Canal, Config
from autocanal.provedores import ErroProvedor

GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"


class ProvedorTTS(Protocol):
    nome: str

    def sintetizar(self, texto: str, destino: Path) -> Path: ...


class TTSFalso:
    nome = "falso"

    def __init__(self, segundos_por_palavra: float = 0.4, taxa: int = 24000):
        self.segundos_por_palavra = segundos_por_palavra
        self.taxa = taxa

    def sintetizar(self, texto: str, destino: Path) -> Path:
        duracao = max(0.5, len(texto.split()) * self.segundos_por_palavra)
        n = int(duracao * self.taxa)
        quadros = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / self.taxa)))
            for i in range(n)
        )
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destino), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.taxa)
            w.writeframes(quadros)
        return destino


class KokoroTTS:
    nome = "kokoro"

    def __init__(self, url: str, voz: str, velocidade: float = 1.0, sessao=requests):
        self.url = url.rstrip("/")
        self.voz = voz
        self.velocidade = velocidade
        self.sessao = sessao

    def sintetizar(self, texto: str, destino: Path) -> Path:
        corpo = {
            "model": "kokoro",
            "input": texto,
            "voice": self.voz,
            "response_format": "wav",
            "speed": self.velocidade,
        }
        try:
            r = self.sessao.post(f"{self.url}/v1/audio/speech", json=corpo, timeout=300)
        except requests.RequestException as e:
            raise ErroProvedor(f"Kokoro indisponível: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ErroProvedor(f"Kokoro respondeu HTTP {r.status_code}")
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino


class GoogleTTS:
    nome = "google"

    def __init__(self, api_key: str, voz: str, idioma: str = "pt-BR", sessao=requests):
        self._api_key = api_key
        self.voz = voz
        self.idioma = idioma
        self.sessao = sessao

    def __repr__(self) -> str:
        return f"GoogleTTS(voz={self.voz!r}, idioma={self.idioma!r})"

    def sintetizar(self, texto: str, destino: Path) -> Path:
        corpo = {
            "input": {"text": texto},
            "voice": {"languageCode": self.idioma, "name": self.voz},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
        }
        try:
            r = self.sessao.post(
                f"{GOOGLE_URL}?key={self._api_key}", json=corpo, timeout=60
            )
        except requests.RequestException as e:
            # não propaga a exceção original: a URL (com a chave) iria na mensagem
            raise ErroProvedor(f"Google TTS indisponível: {type(e).__name__}") from None
        if r.status_code != 200:
            raise ErroProvedor(f"Google TTS respondeu HTTP {r.status_code}")
        try:
            audio = base64.b64decode(r.json()["audioContent"])
        except (KeyError, ValueError) as e:
            raise ErroProvedor("Google TTS: resposta sem audioContent") from None
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(audio)
        return destino


class TTSComReserva:
    def __init__(self, principal: ProvedorTTS, reserva: ProvedorTTS | None):
        self.principal = principal
        self.reserva = reserva
        self.usou_reserva = False
        self.nome = principal.nome

    def sintetizar(self, texto: str, destino: Path) -> Path:
        try:
            r = self.principal.sintetizar(texto, destino)
        except ErroProvedor:
            if self.reserva is None:
                raise
            r = self.reserva.sintetizar(texto, destino)
            self.usou_reserva = True
            self.nome = self.reserva.nome
            return r
        self.usou_reserva = False
        self.nome = self.principal.nome
        return r


class _KokoroGerenciado(KokoroTTS):
    """KokoroTTS que sobe o contêiner antes da primeira síntese."""

    def __init__(self, cfg: Config, voz: str):
        super().__init__(cfg.kokoro_url, voz)
        self._cfg = cfg
        self._pronto = False

    def sintetizar(self, texto: str, destino: Path) -> Path:
        if not self._pronto:
            servicos.garantir(
                "kokoro", self._cfg.raiz, f"{self._cfg.kokoro_url.rstrip('/')}/health",
                timeout_s=self._cfg.kokoro_timeout_s,
            )
            self._pronto = True
        return super().sintetizar(texto, destino)


def _montar(cfg: Config, nome: str, voz: str, canal: Canal) -> ProvedorTTS:
    if nome == "falso":
        return TTSFalso()
    if nome == "kokoro":
        return _KokoroGerenciado(cfg, voz)
    return GoogleTTS(cfg.chaves["GOOGLE_TTS_API_KEY"], voz, canal.idioma)


def criar_tts(cfg: Config, canal: Canal) -> TTSComReserva:
    t = canal.tts
    reserva = (
        _montar(cfg, t.reserva, t.voz_reserva, canal) if t.reserva else None
    )
    if t.provedor == "google" and not cfg.chaves.get("GOOGLE_TTS_API_KEY"):
        if reserva is None:
            raise ErroProvedor("GOOGLE_TTS_API_KEY ausente e sem reserva configurada")
        return TTSComReserva(reserva, None)
    principal = _montar(cfg, t.provedor, t.voz, canal)
    if reserva is not None and reserva.nome == principal.nome:
        reserva = None
    return TTSComReserva(principal, reserva)
