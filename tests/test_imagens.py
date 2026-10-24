from pathlib import Path

import pytest
import requests
from PIL import Image

from autocanal.config import Paleta
from autocanal.provedores import ErroProvedor
from autocanal.provedores.imagens import (
    Candidato,
    FundoProcedural,
    MidiaObtida,
    Pedido,
    PixabayCandidatos,
    criar_cadeia,
    folha_contato,
    resolver,
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


VIDEOS = "https://pixabay.com/api/videos/"
FOTOS = "https://pixabay.com/api/"


def _hit_video(n, w=1080, h=1920, **extra):
    return {"pageURL": f"https://pixabay.com/videos/v{n}", "user": f"u{n}", **extra,
            "videos": {"large": {"url": f"https://pb/v{n}.mp4", "width": w, "height": h,
                                 "thumbnail": f"https://pb/v{n}.jpg"}}}


def _hit_img(n, **extra):
    return {"pageURL": f"https://pixabay.com/photos/i{n}", "user": f"u{n}", **extra,
            "webformatURL": f"https://pb/i{n}_640.jpg", "largeImageURL": f"https://pb/i{n}.jpg"}


def _sessao(videos=(), fotos=()):
    # a URL de vídeos tem a de fotos como prefixo: vídeos primeiro no dicionário
    return SessaoFalsa({VIDEOS: Resp(json_={"hits": list(videos)}),
                        FOTOS: Resp(json_={"hits": list(fotos)}),
                        "https://pb/": Resp(content=b"BYTES")})


@pytest.mark.parametrize("estilo, orientacao, video_type, image_type, orientation", [
    ("realista", "retrato", "film", "photo", "vertical"),
    ("desenho", "paisagem", "animation", "illustration", "horizontal"),
])
def test_candidatos_filtros_por_estilo(estilo, orientacao, video_type, image_type, orientation):
    s = _sessao()
    assert PixabayCandidatos("k", sessao=s).buscar("mar", orientacao, estilo, set()) == []
    (u1, p1, _), (u2, p2, _) = s.chamadas
    assert u1 == VIDEOS and p1["video_type"] == video_type and p1["per_page"] == 20
    assert p1["safesearch"] == "true" and p1["key"] == "k" and p1["q"] == "mar"
    assert u2 == FOTOS and p2["image_type"] == image_type and p2["orientation"] == orientation


def test_candidatos_descarta_baixa_qualidade_e_ia():
    videos = [_hit_video(1, isLowQuality=True), _hit_video(2, isAiGenerated=True), _hit_video(3)]
    real = PixabayCandidatos("k", sessao=_sessao(videos)).buscar("x", "retrato", "realista", set())
    assert [c.pagina for c in real] == ["https://pixabay.com/videos/v3"]
    desenho = PixabayCandidatos("k", sessao=_sessao(videos)).buscar("x", "retrato", "desenho", set())
    assert [c.pagina for c in desenho] == [
        "https://pixabay.com/videos/v2", "https://pixabay.com/videos/v3"]


def test_candidatos_ordem_e_limite():
    s = _sessao([_hit_video(i) for i in range(5)], [_hit_img(i) for i in range(5)])
    cs = PixabayCandidatos("k", sessao=s).buscar("x", "retrato", "realista", set())
    assert [c.tipo for c in cs] == ["video"] * 4 + ["imagem"] * 2
    assert cs[0] == Candidato("video", "https://pixabay.com/videos/v0", "https://pb/v0.jpg",
                              "https://pb/v0.mp4", "u0")
    assert cs[4] == Candidato("imagem", "https://pixabay.com/photos/i0", "https://pb/i0_640.jpg",
                              "https://pb/i0.jpg", "u0")


def test_candidatos_respeita_orientacao_e_excluir():
    s = _sessao([_hit_video(1, w=1920, h=1080), _hit_video(2)], [_hit_img(1), _hit_img(2)])
    cs = PixabayCandidatos("k", sessao=s).buscar(
        "x", "retrato", "realista", {"https://pixabay.com/photos/i1"})
    assert [c.pagina for c in cs] == ["https://pixabay.com/videos/v2", "https://pixabay.com/photos/i2"]


def test_candidatos_chave_fora_do_erro():
    s = SessaoFalsa(erro=requests.ConnectionError("https://pixabay.com/api/?key=SEGREDO"))
    with pytest.raises(ErroProvedor) as e:
        PixabayCandidatos("SEGREDO", sessao=s).buscar("mar", "retrato", "realista", set())
    assert "SEGREDO" not in str(e.value) and e.value.__cause__ is None


def test_baixar_video_e_imagem(tmp_path):
    fonte = PixabayCandidatos("k", sessao=_sessao())
    v = fonte.baixar(Candidato("video", "https://pixabay.com/videos/v1", "https://pb/v1.jpg",
                               "https://pb/v1.mp4", "Ana"), tmp_path / "seg_01")
    assert v.caminho == tmp_path / "seg_01.mp4" and v.caminho.read_bytes() == b"BYTES"
    assert (v.tipo, v.origem, v.url, v.licenca, v.autor) == (
        "video", "pixabay", "https://pixabay.com/videos/v1", "Pixabay Content License", "Ana")
    i = fonte.baixar(Candidato("imagem", "https://pixabay.com/photos/i1", "https://pb/i1_640.jpg",
                               "https://pb/i1.jpg", "Bia"), tmp_path / "seg_02")
    assert i.caminho.suffix == ".jpg" and i.tipo == "imagem"


def test_baixar_preview(tmp_path):
    c = Candidato("imagem", "https://pixabay.com/photos/i1", "https://pb/i1_640.jpg",
                  "https://pb/i1.jpg", "Bia")
    destino = PixabayCandidatos("k", sessao=_sessao()).baixar_preview(c, tmp_path / "p.jpg")
    assert destino.read_bytes() == b"BYTES"


@pytest.mark.parametrize("n", [6, 2])
def test_folha_contato(tmp_path, n):
    previews = []
    for i in range(n):
        arq = tmp_path / f"p{i}.png"
        Image.new("RGB", (640, 360 + 40 * i), (40 * i, 100, 150)).save(arq)
        previews.append(arq)
    tipos = ["video", "imagem"] * (n // 2)
    folha = folha_contato(previews, tipos, tmp_path / "folha.jpg")
    with Image.open(folha) as im:
        assert im.format == "JPEG" and im.size == (1200, 600)


def _pedidos(tmp_path, n=3):
    return [Pedido(f"seg_{i:02d}", f"q{i}", f"texto {i}", f"cena {i}", tmp_path / f"seg_{i:02d}")
            for i in range(1, n + 1)]


def _midia(tmp_path, pid, url):
    caminho = tmp_path / f"{pid}.png"
    caminho.write_bytes(b"x")
    return MidiaObtida(caminho, "imagem", "dublê", url, "livre", "autor")


class Lote:
    def __init__(self, nome, tmp_path, resolve=(), erro=False):
        self.nome, self.tmp, self.resolve, self.erro = nome, tmp_path, set(resolve), erro
        self.chamadas = []

    def buscar_lote(self, pedidos, orientacao, excluir):
        self.chamadas.append(([p.id for p in pedidos], set(excluir)))
        if self.erro:
            raise ErroProvedor("falhou")
        return {p.id: _midia(self.tmp, p.id, f"https://{self.nome}/{p.id}")
                for p in pedidos if p.id in self.resolve}


class SoPrimeiro:
    nome = "so_primeiro"

    def __init__(self, tmp_path):
        self.tmp = tmp_path

    def buscar(self, consulta, orientacao, destino_sem_ext, excluir):
        return _midia(self.tmp, "x", "https://p/1") if consulta == "q1" else None


def test_resolver_lote_recebe_so_pendentes(tmp_path):
    lote = Lote("lote", tmp_path, resolve={"seg_02", "seg_03"})
    r = resolver([SoPrimeiro(tmp_path), lote], _pedidos(tmp_path), "retrato", set())
    assert sorted(r) == ["seg_01", "seg_02", "seg_03"]
    assert [ids for ids, _ in lote.chamadas] == [["seg_02", "seg_03"]]


def test_resolver_erro_do_lote_segue(tmp_path, caplog):
    caplog.set_level("WARNING", logger="autocanal.provedores.imagens")
    r = resolver([Lote("quebrado", tmp_path, erro=True), _fundo()], _pedidos(tmp_path),
                 "retrato", set())
    assert {m.origem for m in r.values()} == {"procedural"}
    [aviso] = caplog.records
    assert "quebrado" in aviso.getMessage() and "falhou" in aviso.getMessage()


def test_resolver_mantem_excluir(tmp_path):
    primeiro = Lote("a", tmp_path, resolve={"seg_01"})
    segundo = Lote("b", tmp_path, resolve={"seg_02", "seg_03"})
    excluir = {"https://antiga/1"}
    resolver([primeiro, segundo], _pedidos(tmp_path), "retrato", excluir)
    assert segundo.chamadas[0][1] == {"https://antiga/1", "https://a/seg_01"}


def test_resolver_sem_midia_falha(tmp_path):
    with pytest.raises(ErroProvedor):
        resolver([Lote("vazio", tmp_path)], _pedidos(tmp_path), "retrato", set())


def test_criar_cadeia_segue_ordem_do_canal(cfg, tmp_path):
    cfg.canais["teste"].visuais = ["flux", "pixabay"]
    px, fx = Lote("pixabay", tmp_path), Lote("flux", tmp_path)
    cadeia = criar_cadeia(cfg, cfg.canais["teste"], 1080, 1920, pixabay=px, flux=fx)
    assert cadeia[:2] == [fx, px] and isinstance(cadeia[2], FundoProcedural) and len(cadeia) == 3


def test_criar_cadeia_sem_provedores_so_procedural(cfg):
    cadeia = criar_cadeia(cfg, cfg.canais["teste"], 1080, 1920)
    assert [type(p) for p in cadeia] == [FundoProcedural]
