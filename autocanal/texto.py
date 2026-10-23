"""Utilitários de texto: contagem de palavras e limpeza."""
from __future__ import annotations

import unicodedata


def contar_palavras(s: str) -> int:
    """Conta palavras faladas (ignora sinais soltos como "—")."""
    return sum(1 for t in s.split() if any(c.isalnum() for c in t))


def sanitizar_texto(s: str) -> str:
    """Troca `<`/`>` por `‹`/`›` e remove caracteres de controle (exceto `\\n`)."""
    s = s.replace("<", "‹").replace(">", "›")
    return "".join(
        c for c in s if c == "\n" or unicodedata.category(c) != "Cc"
    )
