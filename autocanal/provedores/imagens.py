"""Provedores de imagens e b-roll: Pixabay e fundo procedural."""
from __future__ import annotations

import colorsys
import hashlib
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

import requests
from PIL import Image, ImageDraw

from autocanal.config import Canal, Config, Paleta
from autocanal.provedores import ErroProvedor

log = logging.getLogger(__name__)

Orientacao = Literal["retrato", "paisagem"]

PIXABAY_VIDEOS = "https://pixabay.com/api/videos/"
PIXABAY_FOTOS = "https://pixabay.com/api/"


@dataclass(frozen=True)
class MidiaObtida:
    caminho: Path
    tipo: Literal["imagem", "video"]
    origem: str
    url: str
    licenca: str
    autor: str

    def como_dict(self) -> dict:
        d = asdict(self)
        d["caminho"] = str(self.caminho)
        return d


class ProvedorImagens(Protocol):
    nome: str

    def buscar(
        self,
        consulta: str,
        orientacao: Orientacao,
        destino_sem_ext: Path,
        excluir: set[str],
    ) -> MidiaObtida | None: ...


def _get(sessao, nome: str, url: str, **kw):
    """GET que nunca vaza a URL (pode conter a chave) nas mensagens de erro."""
    try:
        r = sessao.get(url, timeout=60, **kw)
    except requests.RequestException as e:
        raise ErroProvedor(f"{nome} indisponível: {type(e).__name__}") from None
    if r.status_code != 200:
        raise ErroProvedor(f"{nome} respondeu HTTP {r.status_code}")
    return r


def _json(r, nome: str) -> dict:
    try:
        d = r.json()
    except ValueError:
        raise ErroProvedor(f"{nome}: resposta não é JSON") from None
    if not isinstance(d, dict):
        raise ErroProvedor(f"{nome}: resposta inesperada")
    return d


def _salvar(sessao, nome: str, link: str, destino_sem_ext: Path, ext: str) -> Path:
    r = _get(sessao, nome, link)
    destino = Path(str(destino_sem_ext) + ext)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.content)
    return destino


class PixabayImagens:
    nome = "pixabay"
    licenca = "Pixabay Content License"

    def __init__(self, api_key: str, sessao=requests):
        self._api_key = api_key
        self.sessao = sessao

    def __repr__(self) -> str:
        return "PixabayImagens()"

    def _buscar(self, url: str, consulta: str, orientacao: Orientacao) -> list[dict]:
        r = _get(
            self.sessao,
            "Pixabay",
            url,
            params={
                "key": self._api_key,
                "q": consulta,
                "orientation": "vertical" if orientacao == "retrato" else "horizontal",
                "safesearch": "true",
                "per_page": 20,
            },
        )
        return _json(r, "Pixabay").get("hits", [])

    @staticmethod
    def _serve(arq: dict, orientacao: Orientacao) -> bool:
        # a API de vídeos ignora `orientation`: um vídeo deitado num Short vira um recorte
        # ampliado de 1/3 da largura, então a orientação é conferida aqui
        larg, alt = arq.get("width") or 0, arq.get("height") or 0
        deitado = larg >= alt
        return bool(arq.get("url")) and alt >= 1080 and deitado == (orientacao == "paisagem")

    def buscar(self, consulta, orientacao, destino_sem_ext, excluir):
        for h in self._buscar(PIXABAY_VIDEOS, consulta, orientacao):
            pagina = h.get("pageURL", "")
            if pagina in excluir:
                continue
            for tam in ("large", "medium"):
                arq = (h.get("videos") or {}).get(tam) or {}
                if self._serve(arq, orientacao):
                    caminho = _salvar(
                        self.sessao, "Pixabay", arq["url"], destino_sem_ext, ".mp4"
                    )
                    return MidiaObtida(
                        caminho, "video", self.nome, pagina, self.licenca,
                        h.get("user", ""),
                    )
        for h in self._buscar(PIXABAY_FOTOS, consulta, orientacao):
            pagina = h.get("pageURL", "")
            link = h.get("largeImageURL")
            if pagina in excluir or not link:
                continue
            caminho = _salvar(self.sessao, "Pixabay", link, destino_sem_ext, ".jpg")
            return MidiaObtida(
                caminho, "imagem", self.nome, pagina, self.licenca, h.get("user", "")
            )
        return None


def _rgb(hexa: str) -> tuple[int, int, int]:
    h = hexa.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class FundoProcedural:
    nome = "procedural"

    def __init__(self, paleta: Paleta, fonte_arquivo: Path, largura: int, altura: int):
        self.paleta = paleta
        self.fonte_arquivo = Path(fonte_arquivo)
        self.largura = largura
        self.altura = altura

    def buscar(self, consulta, orientacao, destino_sem_ext, excluir):
        h = hashlib.sha256(consulta.encode("utf-8")).digest()
        w, alt = self.largura, self.altura
        fundo = _rgb(self.paleta.fundo)
        # tom derivado do destaque: matiz levemente deslocada, escurecido
        r, g, b = (c / 255 for c in _rgb(self.paleta.destaque))
        hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
        hh = (hh + (h[0] - 128) / 128 * 0.04) % 1.0
        tom = tuple(
            round(c * 255) for c in colorsys.hsv_to_rgb(hh, ss * 0.8, vv * 0.45)
        )

        # gradiente linear com ângulo derivado do hash
        diag = int(math.hypot(w, alt)) + 2
        mascara = Image.linear_gradient("L").resize((diag, diag))
        mascara = mascara.rotate(h[1] / 255 * 360, resample=Image.BICUBIC)
        esq, topo = (diag - w) // 2, (diag - alt) // 2
        mascara = mascara.crop((esq, topo, esq + w, topo + alt))
        img = Image.composite(
            Image.new("RGB", (w, alt), tom), Image.new("RGB", (w, alt), fundo), mascara
        ).convert("RGBA")

        # formas translúcidas derivadas do hash
        camada = Image.new("RGBA", (w, alt), (0, 0, 0, 0))
        d = ImageDraw.Draw(camada)
        cor = _rgb(self.paleta.destaque)
        for i in range(5):
            x = h[2 + i * 5] / 255 * w
            y = h[3 + i * 5] / 255 * alt
            raio = (0.08 + h[4 + i * 5] / 255 * 0.22) * min(w, alt)
            alfa = 18 + h[5 + i * 5] % 40
            caixa = (x - raio, y - raio, x + raio, y + raio)
            if h[6 + i * 5] % 2:
                d.ellipse(caixa, fill=(*cor, alfa))
            else:
                d.rectangle(caixa, fill=(*cor, alfa))
        img = Image.alpha_composite(img, camada).convert("RGB")

        destino = Path(str(destino_sem_ext) + ".png")
        destino.parent.mkdir(parents=True, exist_ok=True)
        img.save(destino, format="PNG")  # sem metadados/timestamps: determinístico
        return MidiaObtida(
            destino, "imagem", "procedural", f"procedural:{h.hex()[:8]}",
            "gerado localmente", "autocanal",
        )


def criar_cadeia(
    cfg: Config, canal: Canal, largura: int, altura: int
) -> list[ProvedorImagens]:
    cadeia: list[ProvedorImagens] = []
    if cfg.chaves.get("PIXABAY_API_KEY"):
        cadeia.append(PixabayImagens(cfg.chaves["PIXABAY_API_KEY"]))
    cadeia.append(FundoProcedural(canal.paleta, canal.fonte_arquivo, largura, altura))
    return cadeia


def buscar_em_cadeia(
    cadeia: list[ProvedorImagens],
    consulta: str,
    orientacao: Orientacao,
    destino_sem_ext: Path,
    excluir: set[str],
) -> MidiaObtida:
    for p in cadeia:
        try:
            m = p.buscar(consulta, orientacao, destino_sem_ext, excluir)
        except ErroProvedor as e:
            log.warning("provedor de imagens %s falhou para %r: %s", p.nome, consulta, e)
            continue
        if m is not None:
            return m
    raise ErroProvedor("nenhum provedor de imagens devolveu mídia")
