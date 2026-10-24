from pathlib import Path

import pytest
from PIL import Image

from autocanal.provedores import ErroProvedor
from autocanal.provedores.curadoria import CuradoriaPixabay, ItemCuradoria
from autocanal.provedores.imagens import Candidato, MidiaObtida, Pedido


class FonteFalsa:
    """Candidatos fixos por consulta; previews são imagens de verdade (a folha abre cada uma)."""

    def __init__(self, por_consulta: dict[str, int], quebra: set[str] = frozenset()):
        self.por_consulta, self.quebra = por_consulta, quebra
        self.baixados: list[str] = []

    def buscar(self, consulta, orientacao, estilo, excluir, maximo=6):
        if consulta in self.quebra:
            raise ErroProvedor("Pixabay respondeu HTTP 500")
        return [Candidato("video" if k == 1 else "imagem", f"https://pixabay.com/{consulta}/{k}",
                          f"https://pb/{consulta}_{k}_prev.jpg", f"https://pb/{consulta}_{k}.jpg",
                          "Ana")
                for k in range(1, self.por_consulta.get(consulta, 0) + 1)]

    def baixar_preview(self, c, destino):
        destino.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), (90, 120, 200)).save(destino, format="JPEG")
        return destino

    def baixar(self, c, destino_sem_ext):
        self.baixados.append(c.pagina)
        caminho = Path(str(destino_sem_ext) + ".jpg")
        caminho.write_bytes(b"x")
        return MidiaObtida(caminho, c.tipo, "pixabay", c.pagina, "Pixabay Content License", c.autor)


def _pedidos(tmp_path, consultas):
    return [Pedido(f"seg_{i:02d}", q, f"fala {i}", f"cena {i}", tmp_path / f"seg_{i:02d}")
            for i, q in enumerate(consultas, 1)]


def _curadoria(tmp_path, fonte, escolhas):
    chamadas: list[list[ItemCuradoria]] = []

    def escolher(itens):
        chamadas.append(list(itens))
        return {k: v for k, v in escolhas.items() if any(i.id == k for i in itens)} \
            if isinstance(escolhas, dict) else escolhas(itens)

    return CuradoriaPixabay(fonte, escolher, "realista", tmp_path / "curadoria"), chamadas


def test_curadoria_baixa_so_escolhidos(tmp_path):
    fonte = FonteFalsa({"router": 3})
    cur, chamadas = _curadoria(tmp_path, fonte, {"seg_01": (2, "mostra roteador")})
    r = cur.buscar_lote(_pedidos(tmp_path, ["router"]), "retrato", set())
    assert r["seg_01"].url == "https://pixabay.com/router/2"
    assert r["seg_01"].motivo == "mostra roteador"
    assert fonte.baixados == ["https://pixabay.com/router/2"]
    [item] = chamadas[0]
    assert (item.texto, item.cena, item.n) == ("fala 1", "cena 1", 3)
    with Image.open(item.folha) as im:
        assert im.size == (1200, 600)


def test_curadoria_nenhum_fica_pendente(tmp_path):
    cur, _ = _curadoria(tmp_path, FonteFalsa({"router": 3}), {"seg_01": (0, "nada serve")})
    assert cur.buscar_lote(_pedidos(tmp_path, ["router"]), "retrato", set()) == {}


def test_curadoria_indice_invalido_vira_nenhum(tmp_path):
    escolhas = {"seg_01": (7, "?"), "seg_99": (1, "id que não foi pedido")}
    cur, _ = _curadoria(tmp_path, FonteFalsa({"router": 3}), escolhas)
    assert cur.buscar_lote(_pedidos(tmp_path, ["router"]), "retrato", set()) == {}


def test_curadoria_trecho_sem_candidatos_nao_vai_ao_claude(tmp_path):
    fonte = FonteFalsa({"router": 2, "vazio": 0, "quebra": 2}, quebra={"quebra"})
    cur, chamadas = _curadoria(tmp_path, fonte, {"seg_01": (1, "ok")})
    r = cur.buscar_lote(_pedidos(tmp_path, ["router", "vazio", "quebra"]), "retrato", set())
    assert [i.id for i in chamadas[0]] == ["seg_01"] and list(r) == ["seg_01"]


def test_curadoria_lotes_de_15(tmp_path):
    consultas = [f"q{i}" for i in range(20)]
    cur, chamadas = _curadoria(tmp_path, FonteFalsa({q: 1 for q in consultas}),
                               lambda itens: {i.id: (1, "ok") for i in itens})
    r = cur.buscar_lote(_pedidos(tmp_path, consultas), "retrato", set())
    assert [len(c) for c in chamadas] == [15, 5] and len(r) == 20


def test_curadoria_nao_repete_candidato(tmp_path):
    class MesmaPagina(FonteFalsa):
        def buscar(self, consulta, *a, **k):
            return [Candidato("imagem", "https://pixabay.com/mesma", "https://pb/p.jpg",
                              "https://pb/d.jpg", "Ana")]

    cur, _ = _curadoria(tmp_path, MesmaPagina({}), lambda itens: {i.id: (1, "ok") for i in itens})
    r = cur.buscar_lote(_pedidos(tmp_path, ["a", "b"]), "retrato", set())
    assert list(r) == ["seg_01"]
