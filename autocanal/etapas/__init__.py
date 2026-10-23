"""Etapas de produção: uma função `executar(ctx)` por etapa, registradas por nome."""
from __future__ import annotations

from collections.abc import Callable

from autocanal.etapas import legendas, narracao, render, roteiro, visuais
from autocanal.etapas.base import Contexto

REGISTRO: dict[str, Callable[[Contexto], None]] = {
    "roteiro": roteiro.executar,
    "narracao": narracao.executar,
    "legendas": legendas.executar,
    "visuais": visuais.executar,
    "render": render.executar,
}
