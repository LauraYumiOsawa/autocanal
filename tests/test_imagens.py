from pathlib import Path

import pytest
import requests
from PIL import Image

from autocanal.config import Paleta
from autocanal.provedores import ErroProvedor
from autocanal.provedores.imagens import (
    FundoProcedural,
    MidiaObtida,
    PexelsImagens,
    PixabayImagens,
    buscar_em_cadeia,
    criar_cadeia,
)

FONTE = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
PALETA = Paleta(fundo="#0F172A", texto="#F8FAFC", destaque="#FACC15")


class Resp:
    def __init__(self, status=200, json_=None, content=b""):
        self.status_code, self._json, self.content = status, json_, content

    def json(self):
        return self._json


class SessaoFalsa:
    """Responde por prefixo de URL; registra (url, params, headers)."""

    def __init__(self, respostas=None, erro=None):
        self.respostas = respostas or {}
        self.erro = erro
        self.chamadas = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.chamadas.append((url, params, headers))
        if self.erro:
            raise self.erro
        for prefixo, r in self.respostas.items():
            if url.startswith(prefixo):
                return r
        return Resp(404)


def _fundo():
    return FundoProcedural(PALETA, FONTE, 1080, 1920)


def test_procedural_deterministico(tmp_path):
    f = _fundo()
    a = f.buscar("gato", "retrato", tmp_path / "a", set())
    b = f.buscar("gato", "retrato", tmp_path / "b", set())
    c = f.buscar("cachorro", "retrato", tmp_path / "c", set())
    assert a.caminho.read_bytes() == b.caminho.read_bytes()
    assert a.caminho.read_bytes() != c.caminho.read_bytes()
    assert Image.open(a.caminho).size == (1080, 1920)
    assert (a.origem, a.licenca, a.autor, a.tipo) == (
        "procedural", "gerado localmente", "autocanal", "imagem")
    assert a.url.startswith("procedural:") and len(a.url) == len("procedural:") + 8


def test_como_dict(tmp_path):
    m = _fundo().buscar("x", "retrato", tmp_path / "x", set())
    d = m.como_dict()
    assert d["caminho"] == str(m.caminho) and isinstance(d["caminho"], str)


def _video(id_, pagina, arquivos):
    return {"id": id_, "url": pagina, "user": {"name": "Ana"}, "video_files": arquivos}


def test_pexels_prefere_video_retrato(tmp_path):
    arquivos = [
        {"link": "https://v/720.mp4", "width": 720, "height": 1280, "file_type": "video/mp4"},
        {"link": "https://v/2160.mp4", "width": 2160, "height": 3840, "file_type": "video/mp4"},
        {"link": "https://v/1080.mp4", "width": 1080, "height": 1920, "file_type": "video/mp4"},
    ]
    s = SessaoFalsa({
        "https://api.pexels.com/videos/search": Resp(
            json_={"videos": [_video(1, "https://pexels.com/v/1", arquivos)]}),
        "https://v/1080.mp4": Resp(content=b"VIDEO1080"),
    })
    m = PexelsImagens("SEGREDO", sessao=s).buscar("mar", "retrato", tmp_path / "seg", set())
    assert m.tipo == "video" and m.licenca == "Pexels License"
    assert m.autor == "Ana" and m.url == "https://pexels.com/v/1"
    assert m.caminho.read_bytes() == b"VIDEO1080"
    url, params, headers = s.chamadas[0]
    assert params["orientation"] == "portrait" and params["per_page"] == 8
    assert headers["Authorization"] == "SEGREDO"


def test_pexels_respeita_excluir(tmp_path):
    arquivos = [{"link": "https://v/1.mp4", "width": 1080, "height": 1920, "file_type": "video/mp4"}]
    foto = {"id": 9, "url": "https://pexels.com/p/9", "photographer": "Bia",
            "src": {"large2x": "https://p/9.jpg"}}
    s = SessaoFalsa({
        "https://api.pexels.com/videos/search": Resp(
            json_={"videos": [_video(1, "https://pexels.com/v/1", arquivos)]}),
        "https://api.pexels.com/v1/search": Resp(json_={"photos": [foto]}),
        "https://p/9.jpg": Resp(content=b"JPG"),
    })
    m = PexelsImagens("k", sessao=s).buscar(
        "mar", "retrato", tmp_path / "seg", {"https://pexels.com/v/1"})
    assert m.tipo == "imagem" and m.autor == "Bia" and m.url == "https://pexels.com/p/9"
    assert all(not c[0].startswith("https://v/") for c in s.chamadas)


def test_pixabay_video_e_chave_fora_do_erro(tmp_path):
    hit = {"pageURL": "https://pixabay.com/v/1", "user": "Caio", "videos": {
        "large": {"url": "https://pb/l.mp4", "width": 1080, "height": 1920},
        "medium": {"url": "https://pb/m.mp4", "width": 720, "height": 1280}}}
    s = SessaoFalsa({
        "https://pixabay.com/api/videos/": Resp(json_={"hits": [hit]}),
        "https://pb/l.mp4": Resp(content=b"PB"),
    })
    m = PixabayImagens("SEGREDO", sessao=s).buscar("mar", "retrato", tmp_path / "s", set())
    assert m.tipo == "video" and m.licenca == "Pixabay Content License" and m.autor == "Caio"
    assert s.chamadas[0][1]["orientation"] == "vertical"
    assert s.chamadas[0][1]["key"] == "SEGREDO"

    s2 = SessaoFalsa(erro=requests.ConnectionError("https://pixabay.com/api/?key=SEGREDO"))
    with pytest.raises(ErroProvedor) as e:
        PixabayImagens("SEGREDO", sessao=s2).buscar("mar", "retrato", tmp_path / "t", set())
    assert "SEGREDO" not in str(e.value) and e.value.__cause__ is None


def test_pixabay_imagem(tmp_path):
    hit = {"pageURL": "https://pixabay.com/p/1", "user": "Dani", "largeImageURL": "https://pb/i.jpg"}
    s = SessaoFalsa({
        "https://pixabay.com/api/videos/": Resp(json_={"hits": []}),
        "https://pixabay.com/api/": Resp(json_={"hits": [hit]}),
        "https://pb/i.jpg": Resp(content=b"IMG"),
    })
    m = PixabayImagens("k", sessao=s).buscar("mar", "paisagem", tmp_path / "s", set())
    assert m.tipo == "imagem" and m.caminho.suffix == ".jpg"
    assert s.chamadas[0][1]["safesearch"] == "true"


class Quebrado:
    nome = "q"

    def buscar(self, *a, **k):
        raise ErroProvedor("falhou")


class Vazio:
    nome = "v"

    def buscar(self, *a, **k):
        return None


def test_cadeia_cai_para_procedural(tmp_path):
    m = buscar_em_cadeia([Quebrado(), Vazio(), _fundo()], "gato", "retrato", tmp_path / "s", set())
    assert isinstance(m, MidiaObtida) and m.origem == "procedural"


def test_criar_cadeia_sem_chaves(cfg):
    cadeia = criar_cadeia(cfg, cfg.canais["teste"], 1080, 1920)
    assert [type(p) for p in cadeia] == [FundoProcedural]


def test_criar_cadeia_com_chaves(cfg):
    cfg.chaves["PEXELS_API_KEY"] = "a"
    cfg.chaves["PIXABAY_API_KEY"] = "b"
    cadeia = criar_cadeia(cfg, cfg.canais["teste"], 1080, 1920)
    assert [type(p) for p in cadeia] == [PexelsImagens, PixabayImagens, FundoProcedural]
