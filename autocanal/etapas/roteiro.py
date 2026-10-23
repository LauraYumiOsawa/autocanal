"""Etapa roteiro: Claude (skill yt-script) escreve o roteiro com fontes."""
from __future__ import annotations

from autocanal import claude
from autocanal.etapas.base import (
    Contexto,
    ErroEtapa,
    carregar_prompt,
    gravar_json,
    lista_md,
    perfil_voz,
    problemas_revisao,
    titulos_recentes,
)
from autocanal.texto import contar_palavras

def ferramentas(ctx: Contexto) -> list[str]:
    """Ferramentas do Claude: Bash só para o script de nota de gancho do plugin."""
    script = ctx.cfg.plugin_dir / "skills" / "yt-script" / "hookscore.py"
    return ["Skill", "Read", f"Bash(python3 {script}:*)", "WebSearch", "WebFetch"]

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


def executar(ctx: Contexto) -> None:
    fmt = ctx.formato
    prompt = carregar_prompt(
        "roteiro_short",
        tema=ctx.job.tema,
        perfil_voz=perfil_voz(ctx),
        titulos_recentes=lista_md(titulos_recentes(ctx), "(nenhum ainda)"),
        palavras_min=fmt.palavras_min,
        palavras_max=fmt.palavras_max,
        problemas_anteriores=lista_md(problemas_revisao(ctx), "Nenhum: esta é a primeira versão."),
        dir_plugin=ctx.cfg.plugin_dir,
    )
    r = claude.chamar(
        prompt,
        ROTEIRO_SHORT,
        ferramentas(ctx),
        ctx.pasta,
        modelo=ctx.cfg.claude.modelos.get("roteiro"),
        max_turns=ctx.cfg.claude.max_turns.get("roteiro", 20),
        claude_bin=ctx.cfg.claude.bin,
        raiz=ctx.cfg.raiz,
    )
    claude.registrar(ctx.con, ctx.job.id, "roteiro", r)
    palavras = contar_palavras(" ".join(s["texto"] for s in r.dados["segmentos"]))
    if not fmt.palavras_min <= palavras <= fmt.palavras_max:
        raise ErroEtapa(
            f"roteiro com {palavras} palavras (esperado {fmt.palavras_min}–{fmt.palavras_max})"
        )
    gravar_json(ctx, "roteiro.json", r.dados)
