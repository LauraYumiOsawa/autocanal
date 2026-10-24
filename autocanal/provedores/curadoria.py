"""Curadoria do Pixabay: candidatos por trecho → folha de contato → o Claude escolhe o que mostra
a fala (ou nenhum) → baixa só os escolhidos."""
from __future__ import annotations

import dataclasses
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autocanal.provedores import ErroProvedor
from autocanal.provedores.imagens import (
    Candidato,
    Estilo,
    MidiaObtida,
    Orientacao,
    Pedido,
    PixabayCandidatos,
    folha_contato,
)

log = logging.getLogger(__name__)

LOTE = 15  # trechos por chamada do Claude


@dataclass(frozen=True)
class ItemCuradoria:
    id: str
    texto: str
    cena: str
    folha: Path
    n: int


# {id: (candidato, motivo[, nova_cena])}: candidato 0 = nenhum; `nova_cena` (opcional) é uma
# cena mais simples que o Claude sugere para o gerador quando recusa todos
Escolher = Callable[[list[ItemCuradoria]], dict[str, tuple]]


class CuradoriaPixabay:
    nome = "pixabay"

    def __init__(self, fonte: PixabayCandidatos, escolher: Escolher, estilo: Estilo, pasta: Path):
        self.fonte = fonte
        self.escolher = escolher
        self.estilo = estilo
        self.pasta = pasta

    def _candidatos(self, p: Pedido, orientacao: Orientacao, excluir: set[str]) -> list[Candidato]:
        try:
            achados = self.fonte.buscar(p.consulta, orientacao, self.estilo, excluir)
        except ErroProvedor as e:
            log.warning("Pixabay falhou para %s (%r): %s", p.id, p.consulta, e)
            return []
        prontos: list[tuple[Candidato, Path]] = []
        for k, c in enumerate(achados, 1):
            try:
                prontos.append((c, self.fonte.baixar_preview(c, self.pasta / f"{p.id}_{k}.jpg")))
            except ErroProvedor as e:
                log.warning("pré-visualização %s de %s não baixou: %s", k, p.id, e)
        if prontos:
            folha_contato([arq for _, arq in prontos], [c.tipo for c, _ in prontos],
                          self.pasta / f"{p.id}.jpg")
        return [c for c, _ in prontos]

    def buscar_lote(
        self, pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str]
    ) -> dict[str, MidiaObtida]:
        candidatos: dict[str, list[Candidato]] = {}
        itens: list[ItemCuradoria] = []
        for p in pedidos:
            cs = self._candidatos(p, orientacao, excluir)
            if cs:
                candidatos[p.id] = cs
                itens.append(ItemCuradoria(p.id, p.texto, p.cena, self.pasta / f"{p.id}.jpg",
                                           len(cs)))
        escolhas: dict[str, tuple] = {}
        for i in range(0, len(itens), LOTE):
            escolhas.update(self.escolher(itens[i:i + LOTE]))

        destinos = {p.id: p.destino_sem_ext for p in pedidos}
        usadas: set[str] = set()
        obtidas: dict[str, MidiaObtida] = {}
        for item in itens:
            indice, motivo = escolhas.get(item.id, (0, ""))[:2]
            if not 1 <= indice <= item.n:
                continue  # 0 = nenhum serve; fora da faixa conta como nenhum
            c = candidatos[item.id][indice - 1]
            if c.pagina in usadas or c.pagina in excluir:
                continue
            try:
                m = self.fonte.baixar(c, destinos[item.id])
            except ErroProvedor as e:
                log.warning("download do escolhido para %s falhou: %s", item.id, e)
                continue
            usadas.add(c.pagina)
            obtidas[item.id] = dataclasses.replace(m, motivo=motivo)
        return obtidas


class FluxCurado:
    """FLUX com controle de qualidade: `variantes` imagens por trecho num lote só, e a curadoria
    escolhe a que mostra a fala sem defeitos (dedos a mais, texto embaralhado...) ou nenhuma."""

    nome = "flux"

    def __init__(self, gerador, escolher: Escolher, pasta: Path, variantes: int = 3):
        self.gerador = gerador
        self.escolher = escolher
        self.pasta = pasta
        self.variantes = variantes

    def buscar_lote(
        self, pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str]
    ) -> dict[str, MidiaObtida]:
        obtidas, novas_cenas = self._rodada(pedidos, orientacao, excluir, "v")
        # segunda chance: cena composta demais o FLUX não acerta; vale a cena mais simples que o
        # Claude escreveu ao recusar as variantes, ou o termo curto de busca
        simples = []
        for p in pedidos:
            cena = novas_cenas.get(p.id) or p.consulta
            if p.id not in obtidas and cena and cena != p.cena:
                simples.append(dataclasses.replace(p, cena=cena))
        if simples:
            obtidas.update(self._rodada(simples, orientacao, excluir, "s")[0])
        return obtidas

    def _rodada(
        self, pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str], marca: str
    ) -> tuple[dict[str, MidiaObtida], dict[str, str]]:
        self.pasta.mkdir(parents=True, exist_ok=True)
        lote = [
            dataclasses.replace(p, id=f"{p.id}_{marca}{k}",
                                destino_sem_ext=self.pasta / f"{p.id}_{marca}{k}")
            for p in pedidos
            for k in range(1, self.variantes + 1)
        ]
        geradas = self.gerador.buscar_lote(lote, orientacao, excluir)
        variantes: dict[str, list[MidiaObtida]] = {}
        itens: list[ItemCuradoria] = []
        for p in pedidos:
            vs = [geradas[f"{p.id}_{marca}{k}"] for k in range(1, self.variantes + 1)
                  if f"{p.id}_{marca}{k}" in geradas]
            if not vs:
                continue
            # células grandes, numa linha só: defeitos (dedos, letras) aparecem como na revisão
            celula = (640, 360) if orientacao == "paisagem" else (360, 640)
            folha = folha_contato([m.caminho for m in vs], ["imagem"] * len(vs),
                                  self.pasta / f"{p.id}{'' if marca == 'v' else '_simples'}.jpg",
                                  celula=celula, colunas=self.variantes, linhas=1)
            variantes[p.id] = vs
            itens.append(ItemCuradoria(p.id, p.texto, p.cena, folha, len(vs)))
        escolhas: dict[str, tuple] = {}
        for i in range(0, len(itens), LOTE):
            escolhas.update(self.escolher(itens[i:i + LOTE]))

        destinos = {p.id: p.destino_sem_ext for p in pedidos}
        obtidas: dict[str, MidiaObtida] = {}
        novas_cenas: dict[str, str] = {}
        for item in itens:
            escolha = escolhas.get(item.id, (0, ""))
            indice, motivo = escolha[:2]
            if not 1 <= indice <= item.n:
                if len(escolha) > 2 and escolha[2]:
                    novas_cenas[item.id] = escolha[2]
                continue
            m = variantes[item.id][indice - 1]
            destino = Path(str(destinos[item.id]) + m.caminho.suffix)
            shutil.copyfile(m.caminho, destino)
            obtidas[item.id] = dataclasses.replace(m, caminho=destino, motivo=motivo)
        return obtidas, novas_cenas
