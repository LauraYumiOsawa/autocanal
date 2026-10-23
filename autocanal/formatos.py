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
    ),
}
