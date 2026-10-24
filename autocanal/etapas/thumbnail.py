"""Etapa thumbnail (médio): imagem-base 1280×720 + texto do `yt-package` em caixa alta."""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from autocanal.config import Canal
from autocanal.etapas.base import Contexto, gravar_json, ler_json
from autocanal.provedores import ErroProvedor, flux
from autocanal.provedores.flux import GeradorFlux
from autocanal.provedores.imagens import FundoProcedural, Pedido

log = logging.getLogger(__name__)
LARGURA, ALTURA = 1280, 720
MARGEM = 48
CAIXA = (int(LARGURA * 0.45), int(ALTURA * 0.60))  # terço esquerdo
CONTORNO = 10
LIMITE_BYTES = 2_000_000  # limite do YouTube para miniaturas
ENTRELINHA = 1.1


def _linhas_equilibradas(palavras: list[str]) -> list[str]:
    if len(palavras) < 2:
        return [" ".join(palavras)]
    corte = min(range(1, len(palavras)), key=lambda i: abs(
        len(" ".join(palavras[:i])) - len(" ".join(palavras[i:]))))
    return [" ".join(palavras[:corte]), " ".join(palavras[corte:])]


def layout_texto(
    texto: str, fonte_arquivo: Path, largura_caixa: int, altura_caixa: int
) -> tuple[int, list[str]]:
    """Maior fonte (de 160 para baixo, de 8 em 8) em que o texto cabe em 1 ou 2 linhas."""
    palavras = texto.upper().split()
    opcoes = [[" ".join(palavras)], _linhas_equilibradas(palavras)]
    tamanho = 160
    while True:
        fonte = ImageFont.truetype(str(fonte_arquivo), tamanho)
        for linhas in opcoes:
            cabe_largura = all(fonte.getlength(linha) <= largura_caixa for linha in linhas)
            if cabe_largura and len(linhas) * tamanho * ENTRELINHA <= altura_caixa:
                return tamanho, linhas
        if tamanho <= 40:
            return tamanho, opcoes[1]
        tamanho -= 8


def compor_thumbnail(base: Path, texto: str, canal: Canal, saida: Path) -> Path:
    with Image.open(base) as im:
        img = ImageOps.fit(im.convert("RGB"), (LARGURA, ALTURA))
    largura_util = CAIXA[0] - 2 * CONTORNO
    tamanho, linhas = layout_texto(texto, canal.fonte_arquivo, largura_util, CAIXA[1])
    fonte = ImageFont.truetype(str(canal.fonte_arquivo), tamanho)
    d = ImageDraw.Draw(img)
    altura_bloco = len(linhas) * tamanho * ENTRELINHA
    y = (ALTURA - altura_bloco) / 2
    for linha in linhas:
        d.text((MARGEM + CONTORNO, y), linha, font=fonte, fill=canal.paleta.destaque,
               stroke_width=CONTORNO, stroke_fill="#000000")
        y += tamanho * ENTRELINHA
    qualidade = 90
    while True:
        img.save(saida, format="JPEG", quality=qualidade)
        if saida.stat().st_size < LIMITE_BYTES or qualidade <= 60:
            return saida
        qualidade -= 10


def _base_flux(ctx: Contexto, meta: dict) -> Path | None:
    if not ("flux" in ctx.canal.visuais and meta.get("cena_thumb") and flux.instalado(ctx.cfg)):
        return None
    flux.preparar_memoria(ctx.cfg.raiz)
    gerador = GeradorFlux(ctx.cfg, job_id=ctx.job.id, rodada=ctx.job.tentativas_revisao,
                          estilo_visual=ctx.canal.estilo_visual, estilo_ia=ctx.canal.estilo_ia)
    pedido = Pedido("thumbnail_base", meta["titulo"], meta["titulo"], meta["cena_thumb"],
                    ctx.pasta / "thumbnail_base")
    try:
        m = gerador.buscar_lote([pedido], "paisagem", set()).get(pedido.id)
    except ErroProvedor as e:
        log.warning("FLUX não gerou a base da thumbnail de %s: %s", ctx.job.id, e)
        return None
    return m.caminho if m else None


def imagem_base(ctx: Contexto, meta: dict) -> tuple[Path, str]:
    """FLUX com a `cena_thumb`; senão o primeiro visual não procedural do vídeo (vídeo → quadro
    do meio); senão procedural."""
    gerada = _base_flux(ctx, meta)
    if gerada is not None:
        return gerada, "flux"
    arq = ctx.pasta / "fontes_midia.json"
    fontes = ler_json(ctx, "fontes_midia.json") if arq.exists() else []
    for f in fontes:
        if f.get("origem") == "procedural":
            continue
        caminho = Path(f["caminho"])
        if not caminho.is_absolute():
            caminho = ctx.pasta / caminho
        if not caminho.exists():
            continue
        if f.get("tipo") == "video":
            quadro = ctx.pasta / "thumbnail_base.jpg"
            meio = ctx.midia.info(caminho).duracao / 2
            ctx.midia.rodar(["-ss", f"{meio:.2f}", "-i", str(caminho), "-frames:v", "1",
                             str(quadro)])
            return quadro, f["origem"]
        return caminho, f["origem"]
    fundo = FundoProcedural(ctx.canal.paleta, ctx.canal.fonte_arquivo, LARGURA, ALTURA)
    m = fundo.buscar(meta["titulo"], "paisagem", ctx.pasta / "thumbnail_base", set())
    return m.caminho, "procedural"


def executar(ctx: Contexto) -> None:
    meta = ler_json(ctx, "metadados.json")
    base, origem = imagem_base(ctx, meta)
    compor_thumbnail(base, meta.get("texto_thumb") or meta["titulo"], ctx.canal,
                     ctx.pasta / "thumbnail.jpg")
    gravar_json(ctx, "thumbnail.json", {"base_origem": origem})
