"""Formatos de vídeo: receitas (dimensões, limites e etapas em ordem)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Formato:
    nome: str
    largura: int
    altura: int
    duracao_min_s: int
    duracao_max_s: int
    palavras_min: int
    palavras_max: int
    orientacao: Literal["retrato", "paisagem"]
    etapas: tuple[str, ...]
    # legendas: tamanho da fonte, distância da base, palavras e duração máximas por bloco
    legenda_tamanho: int
    legenda_margem_v: int
    legenda_max_palavras: int
    legenda_max_duracao_s: float

    @property
    def resumo(self) -> str:
        """Ex.: `short (1080×1920, 30 a 60 s)`, para os prompts."""
        return (
            f"{self.nome} ({self.largura}×{self.altura}, "
            f"{self.duracao_min_s} a {self.duracao_max_s} s)"
        )


FORMATOS: dict[str, Formato] = {
    "short": Formato(
        "short", 1080, 1920, 30, 60, 75, 150, "retrato",
        ("roteiro", "narracao", "legendas", "visuais", "render", "metadados", "revisao"),
        legenda_tamanho=88, legenda_margem_v=560, legenda_max_palavras=3,
        legenda_max_duracao_s=1.6,
    ),
    "medio": Formato(
        "medio", 1920, 1080, 60, 300, 150, 750, "paisagem",
        ("roteiro", "narracao", "legendas", "visuais", "render", "metadados", "thumbnail",
         "revisao"),
        legenda_tamanho=60, legenda_margem_v=70, legenda_max_palavras=6,
        legenda_max_duracao_s=2.5,
    ),
}
