from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from autocanal import etapas, fila
from autocanal.db import conectar
from autocanal.etapas import thumbnail
from autocanal.etapas.base import contexto_para, gravar_json, ler_json

FONTE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
PUBLICAR = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def ctx(cfg, con):
    job = fila.criar_job(con, "teste", "medio", "por que o wi-fi fica lento", PUBLICAR)
    c = contexto_para(cfg, con, job)
    gravar_json(c, "metadados.json", {"titulo": "Wi-Fi lento à noite", "texto_thumb": "WI-FI LENTO",
                                      "cena_thumb": "a router at night"})
    return c


def _imagem(destino: Path, tamanho=(1920, 1080), cor=(30, 90, 160)) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", tamanho, cor).save(destino)
    return destino


def test_compor_thumbnail_dimensoes_e_peso(cfg, tmp_path):
    base = _imagem(tmp_path / "base.png")
    saida = thumbnail.compor_thumbnail(base, "WI-FI LENTO", cfg.canais["teste"], tmp_path / "t.jpg")
    with Image.open(saida) as im:
        assert im.format == "JPEG" and im.size == (1280, 720)
    assert saida.stat().st_size < 2_000_000


def test_thumbnail_palavra_longa_cabe():
    tamanho, linhas = thumbnail.layout_texto("interferência no wi-fi", FONTE, 576, 432)
    assert tamanho < 160 and 1 <= len(linhas) <= 2
    assert " ".join(linhas) == "INTERFERÊNCIA NO WI-FI"
    fonte = ImageFont.truetype(str(FONTE), tamanho)
    assert all(fonte.getlength(linha) <= 576 for linha in linhas)


def test_thumbnail_base_usa_visual(ctx):
    proc = _imagem(ctx.pasta / "visuais" / "seg_01.png")
    foto = _imagem(ctx.pasta / "visuais" / "seg_02.jpg")
    gravar_json(ctx, "fontes_midia.json", [
        {"caminho": str(proc), "tipo": "imagem", "origem": "procedural"},
        {"caminho": "visuais/seg_02.jpg", "tipo": "imagem", "origem": "pixabay"},
    ])
    caminho, origem = thumbnail.imagem_base(ctx, ler_json(ctx, "metadados.json"))
    assert (caminho, origem) == (foto, "pixabay")


def test_thumbnail_base_video_extrai_quadro(ctx):
    video = ctx.pasta / "visuais" / "seg_01.mp4"
    video.parent.mkdir(parents=True)
    ctx.midia.rodar(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=10:duration=2",
                     "-pix_fmt", "yuv420p", str(video)])
    gravar_json(ctx, "fontes_midia.json", [{"caminho": str(video), "tipo": "video", "origem": "pixabay"}])
    caminho, origem = thumbnail.imagem_base(ctx, ler_json(ctx, "metadados.json"))
    assert origem == "pixabay" and caminho.suffix == ".jpg"
    with Image.open(caminho) as im:
        assert im.size == (640, 360)


def test_thumbnail_base_procedural_sem_visuais(ctx):
    gravar_json(ctx, "fontes_midia.json", [
        {"caminho": str(_imagem(ctx.pasta / "visuais" / "seg_01.png")), "tipo": "imagem",
         "origem": "procedural"}])
    caminho, origem = thumbnail.imagem_base(ctx, ler_json(ctx, "metadados.json"))
    assert origem == "procedural" and caminho.exists()


def test_thumbnail_executar_grava_jpg_e_origem(ctx):
    _imagem(ctx.pasta / "visuais" / "seg_01.jpg")
    gravar_json(ctx, "fontes_midia.json", [
        {"caminho": "visuais/seg_01.jpg", "tipo": "imagem", "origem": "pixabay"}])
    thumbnail.executar(ctx)
    with Image.open(ctx.pasta / "thumbnail.jpg") as im:
        assert im.size == (1280, 720)
    assert ler_json(ctx, "thumbnail.json") == {"base_origem": "pixabay"}
    assert etapas.REGISTRO["thumbnail"] is thumbnail.executar
