"""Visuais: candidatos do Pixabay, folha de contato, cadeia de provedores em lote e fundo
procedural (só emergência: a revisão reprova vídeo que o use)."""
from __future__ import annotations

import colorsys
import hashlib
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from autocanal.config import Canal, Config, Paleta
from autocanal.provedores import ErroProvedor

log = logging.getLogger(__name__)

Orientacao = Literal["retrato", "paisagem"]
Estilo = Literal["realista", "desenho"]

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
    prompt: str = ""  # FLUX: prompt usado
    motivo: str = ""  # curadoria: por que este visual combina com a fala

    def como_dict(self) -> dict:
        d = asdict(self)
        d["caminho"] = str(self.caminho)
        return d


class ProvedorImagens(Protocol):
    """Provedor que resolve um trecho por vez (hoje só o fundo procedural)."""

    nome: str

    def buscar(
        self,
        consulta: str,
        orientacao: Orientacao,
        destino_sem_ext: Path,
        excluir: set[str],
    ) -> MidiaObtida | None: ...


@dataclass(frozen=True)
class Pedido:
    id: str
    consulta: str  # busca curta em inglês para o banco de imagens
    texto: str  # o que a voz fala no trecho
    cena: str  # o que a imagem deve mostrar (critério da curadoria e prompt do FLUX)
    destino_sem_ext: Path


class ProvedorLote(Protocol):
    """Recebe todos os trechos ainda sem mídia; devolve só os que resolveu.
    `ErroProvedor` = o lote inteiro falhou."""

    nome: str

    def buscar_lote(
        self, pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str]
    ) -> dict[str, MidiaObtida]: ...


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


@dataclass(frozen=True)
class Candidato:
    tipo: Literal["imagem", "video"]
    pagina: str
    preview: str
    download: str
    autor: str


_TIPO_VIDEO = {"realista": "film", "desenho": "animation"}
_TIPO_IMAGEM = {"realista": "photo", "desenho": "illustration"}
LICENCA_PIXABAY = "Pixabay Content License"


class PixabayCandidatos:
    """Busca candidatos (vídeos primeiro) para a curadoria e baixa só o que for escolhido."""

    def __init__(self, api_key: str, sessao=requests):
        self._api_key = api_key
        self.sessao = sessao

    def __repr__(self) -> str:
        return "PixabayCandidatos()"

    def _hits(self, url: str, consulta: str, extra: dict) -> list[dict]:
        r = _get(
            self.sessao,
            "Pixabay",
            url,
            params={"key": self._api_key, "q": consulta, "safesearch": "true", "per_page": 20,
                    **extra},
        )
        return _json(r, "Pixabay").get("hits", [])

    @staticmethod
    def _serve(arq: dict, orientacao: Orientacao) -> bool:
        # a API de vídeos ignora `orientation`: um vídeo deitado num Short vira um recorte
        # ampliado de 1/3 da largura, então a orientação é conferida aqui
        larg, alt = arq.get("width") or 0, arq.get("height") or 0
        deitado = larg >= alt
        return bool(arq.get("url")) and alt >= 1080 and deitado == (orientacao == "paisagem")

    @staticmethod
    def _aceito(h: dict, estilo: Estilo, excluir: set[str]) -> bool:
        if h.get("isLowQuality"):
            return False
        # imagem de IA realista sem a marcação de mídia sintética seria risco de política
        if estilo == "realista" and h.get("isAiGenerated"):
            return False
        return bool(h.get("pageURL")) and h["pageURL"] not in excluir

    def buscar(
        self,
        consulta: str,
        orientacao: Orientacao,
        estilo: Estilo,
        excluir: set[str],
        maximo: int = 6,
    ) -> list[Candidato]:
        videos: list[Candidato] = []
        for h in self._hits(PIXABAY_VIDEOS, consulta, {"video_type": _TIPO_VIDEO[estilo]}):
            if len(videos) == 4:
                break
            if not self._aceito(h, estilo, excluir):
                continue
            for tam in ("large", "medium"):
                arq = (h.get("videos") or {}).get(tam) or {}
                if self._serve(arq, orientacao) and arq.get("thumbnail"):
                    videos.append(Candidato("video", h["pageURL"], arq["thumbnail"], arq["url"],
                                            h.get("user", "")))
                    break
        vagas = min(4, maximo - len(videos))
        imagens: list[Candidato] = []
        extra = {"image_type": _TIPO_IMAGEM[estilo],
                 "orientation": "vertical" if orientacao == "retrato" else "horizontal"}
        for h in self._hits(PIXABAY_FOTOS, consulta, extra):
            if len(imagens) >= vagas:
                break
            if self._aceito(h, estilo, excluir) and h.get("largeImageURL") and h.get("webformatURL"):
                imagens.append(Candidato("imagem", h["pageURL"], h["webformatURL"],
                                         h["largeImageURL"], h.get("user", "")))
        return videos + imagens

    def baixar_preview(self, c: Candidato, destino: Path) -> Path:
        r = _get(self.sessao, "Pixabay", c.preview)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino

    def baixar(self, c: Candidato, destino_sem_ext: Path) -> MidiaObtida:
        ext = ".mp4" if c.tipo == "video" else ".jpg"
        caminho = _salvar(self.sessao, "Pixabay", c.download, destino_sem_ext, ext)
        return MidiaObtida(caminho, c.tipo, "pixabay", c.pagina, LICENCA_PIXABAY, c.autor)


FOLHA_CELULA = (400, 300)


def folha_contato(previews: list[Path], tipos: list[str], saida: Path) -> Path:
    """Grade 3×2 numerada (1 a 6) para o Claude escolher; marca os vídeos."""
    cw, ch = FOLHA_CELULA
    folha = Image.new("RGB", (cw * 3, ch * 2), (20, 20, 20))
    fonte_num = ImageFont.load_default(size=72)
    fonte_tag = ImageFont.load_default(size=30)
    for i, (arq, tipo) in enumerate(zip(previews, tipos)):
        x, y = (i % 3) * cw, (i // 3) * ch
        with Image.open(arq) as im:
            folha.paste(ImageOps.fit(im.convert("RGB"), (cw, ch)), (x, y))
        d = ImageDraw.Draw(folha)
        d.rectangle((x, y, x + 90, y + 90), fill=(0, 0, 0))
        d.text((x + 45, y + 45), str(i + 1), font=fonte_num, fill=(255, 255, 0), anchor="mm")
        if tipo == "video":
            d.rectangle((x, y + ch - 44, x + cw, y + ch), fill=(180, 0, 0))
            d.text((x + cw // 2, y + ch - 22), "VÍDEO", font=fonte_tag, fill=(255, 255, 255),
                   anchor="mm")
        d.rectangle((x, y, x + cw - 1, y + ch - 1), outline=(255, 255, 255), width=2)
    saida.parent.mkdir(parents=True, exist_ok=True)
    folha.save(saida, format="JPEG", quality=85)
    return saida


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
    cfg: Config,
    canal: Canal,
    largura: int,
    altura: int,
    *,
    pixabay: ProvedorLote | None = None,
    flux: ProvedorLote | None = None,
) -> list[ProvedorLote | ProvedorImagens]:
    """Provedores na ordem de `canal.visuais` (os ausentes ficam de fora) e o procedural no fim."""
    disponiveis = {"pixabay": pixabay, "flux": flux}
    cadeia: list[ProvedorLote | ProvedorImagens] = []
    for nome in dict.fromkeys(canal.visuais):
        if disponiveis.get(nome) is not None:
            cadeia.append(disponiveis[nome])
    cadeia.append(FundoProcedural(canal.paleta, canal.fonte_arquivo, largura, altura))
    return cadeia


def resolver(
    cadeia: list[ProvedorLote | ProvedorImagens],
    pedidos: list[Pedido],
    orientacao: Orientacao,
    excluir: set[str],
) -> dict[str, MidiaObtida]:
    """Cada provedor recebe todos os trechos ainda sem mídia; `excluir` acumula as URLs usadas."""
    obtidas: dict[str, MidiaObtida] = {}
    for prov in cadeia:
        pendentes = [p for p in pedidos if p.id not in obtidas]
        if not pendentes:
            break
        if hasattr(prov, "buscar_lote"):
            try:
                novas = prov.buscar_lote(pendentes, orientacao, excluir)
            except ErroProvedor as e:
                log.warning("provedor de imagens %s falhou: %s", prov.nome, e)
                continue
            ids = {p.id for p in pendentes}
            for pid, m in novas.items():
                if pid in ids:
                    obtidas[pid] = m
                    excluir.add(m.url)
            continue
        for p in pendentes:
            try:
                m = prov.buscar(p.consulta, orientacao, p.destino_sem_ext, excluir)
            except ErroProvedor as e:
                log.warning("provedor de imagens %s falhou para %r: %s", prov.nome, p.consulta, e)
                continue
            if m is not None:
                obtidas[p.id] = m
                excluir.add(m.url)
    faltam = [p.id for p in pedidos if p.id not in obtidas]
    if faltam:
        raise ErroProvedor(f"nenhum provedor de imagens devolveu mídia para {', '.join(faltam)}")
    return obtidas
