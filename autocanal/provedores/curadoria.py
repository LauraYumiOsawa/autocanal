"""Curadoria do Pixabay: candidatos por trecho → folha de contato → o Claude escolhe o que mostra
a fala (ou nenhum) → baixa só os escolhidos."""
from __future__ import annotations

import dataclasses
import logging
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


Escolher = Callable[[list[ItemCuradoria]], dict[str, tuple[int, str]]]


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
        escolhas: dict[str, tuple[int, str]] = {}
        for i in range(0, len(itens), LOTE):
            escolhas.update(self.escolher(itens[i:i + LOTE]))

        destinos = {p.id: p.destino_sem_ext for p in pedidos}
        usadas: set[str] = set()
        obtidas: dict[str, MidiaObtida] = {}
        for item in itens:
            indice, motivo = escolhas.get(item.id, (0, ""))
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
