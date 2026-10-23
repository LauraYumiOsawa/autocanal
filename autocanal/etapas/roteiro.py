"""Etapa roteiro: Claude (skill yt-script) escreve o roteiro com fontes."""
from __future__ import annotations

from autocanal import claude
from autocanal.etapas.base import (
    Contexto,
    ErroEtapa,
    carregar_prompt,
    gravar_json,
    ler_json,
    perfil_voz,
    titulos_recentes,
)
from autocanal.texto import contar_palavras

PROBLEMAS = "problemas_revisao.json"
FERRAMENTAS = ["Skill", "Read", "Bash(python3:*)", "WebSearch", "WebFetch"]

_TEXTO = {"type": "string"}
ROTEIRO_SHORT = {
    "type": "object",
    "required": ["gancho", "nota_gancho", "segmentos", "fontes", "resumo"],
    "additionalProperties": False,
    "properties": {
        "gancho": _TEXTO,
        "nota_gancho": {"type": "number"},
        "segmentos": {
            "type": "array",
            "minItems": 3,
            "maxItems": 12,
            "items": {
                "type": "object",
                "required": ["texto", "visual"],
                "additionalProperties": False,
                "properties": {"texto": _TEXTO, "visual": _TEXTO},
            },
        },
        "fontes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["afirmacao", "url"],
                "additionalProperties": False,
                "properties": {"afirmacao": _TEXTO, "url": _TEXTO},
            },
        },
        "resumo": _TEXTO,
    },
}


def _lista(itens: list[str], vazio: str) -> str:
    return "\n".join(f"- {i}" for i in itens) if itens else vazio


def _problemas_anteriores(ctx: Contexto) -> list[str]:
    if not (ctx.pasta / PROBLEMAS).exists():
        return []
    return [str(p) for p in ler_json(ctx, PROBLEMAS).get("problemas", [])]


def executar(ctx: Contexto) -> None:
    fmt = ctx.formato
    prompt = carregar_prompt(
        "roteiro_short",
        tema=ctx.job.tema,
        perfil_voz=perfil_voz(ctx),
        titulos_recentes=_lista(titulos_recentes(ctx), "(nenhum ainda)"),
        palavras_min=fmt.palavras_min,
        palavras_max=fmt.palavras_max,
        problemas_anteriores=_lista(_problemas_anteriores(ctx), "Nenhum: esta é a primeira versão."),
        dir_plugin=ctx.cfg.plugin_dir,
    )
    r = claude.chamar(
        prompt,
        ROTEIRO_SHORT,
        FERRAMENTAS,
        ctx.pasta,
        modelo=ctx.cfg.claude.modelos.get("roteiro"),
        max_turns=ctx.cfg.claude.max_turns.get("roteiro", 20),
        claude_bin=ctx.cfg.claude.bin,
    )
    claude.registrar(ctx.con, ctx.job.id, "roteiro", r)
    palavras = contar_palavras(" ".join(s["texto"] for s in r.dados["segmentos"]))
    if not fmt.palavras_min <= palavras <= fmt.palavras_max:
        raise ErroEtapa(
            f"roteiro com {palavras} palavras (esperado {fmt.palavras_min}–{fmt.palavras_max})"
        )
    gravar_json(ctx, "roteiro.json", r.dados)
