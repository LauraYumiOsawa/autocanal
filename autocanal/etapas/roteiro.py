"""Etapa roteiro: a IA (método yt-script) escreve o roteiro com fontes."""
from __future__ import annotations

from autocanal import ia
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

_TEXTO = {"type": "string"}
MAX_SEGMENTOS = 60
# um segmento = uma frase = um visual
_SEGMENTO = {
    "type": "object",
    "required": ["texto", "visual", "cena"],
    "additionalProperties": False,
    "properties": {"texto": _TEXTO, "visual": _TEXTO, "cena": _TEXTO},
}
_FONTES = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["afirmacao", "url"],
        "additionalProperties": False,
        "properties": {"afirmacao": _TEXTO, "url": _TEXTO},
    },
}
ROTEIRO_SHORT = {
    "type": "object",
    "required": ["gancho", "nota_gancho", "segmentos", "fontes", "resumo"],
    "additionalProperties": False,
    "properties": {
        "gancho": _TEXTO,
        "nota_gancho": {"type": "number"},
        "segmentos": {"type": "array", "minItems": 6, "maxItems": 16, "items": _SEGMENTO},
        "fontes": _FONTES,
        "resumo": _TEXTO,
    },
}
ROTEIRO_MEDIO = {
    "type": "object",
    "required": ["gancho", "nota_gancho", "secoes", "fontes", "resumo"],
    "additionalProperties": False,
    "properties": {
        "gancho": _TEXTO,
        "nota_gancho": {"type": "number"},
        "secoes": {
            "type": "array",
            "minItems": 3,
            "maxItems": 7,
            "items": {
                "type": "object",
                "required": ["titulo", "segmentos"],
                "additionalProperties": False,
                "properties": {
                    "titulo": _TEXTO,
                    "segmentos": {
                        "type": "array", "minItems": 2, "maxItems": 10, "items": _SEGMENTO,
                    },
                },
            },
        },
        "fontes": _FONTES,
        "resumo": _TEXTO,
    },
}
SCHEMAS = {"short": ROTEIRO_SHORT, "medio": ROTEIRO_MEDIO}


def achatar(dados: dict) -> dict:
    """Seções do médio → `secoes` (títulos) + `segmentos` em sequência, cada um com `secao`."""
    segmentos = [
        {**seg, "secao": i}
        for i, secao in enumerate(dados["secoes"])
        for seg in secao["segmentos"]
    ]
    if len(segmentos) > MAX_SEGMENTOS:
        raise ErroEtapa(f"roteiro com {len(segmentos)} segmentos (máximo {MAX_SEGMENTOS})")
    return {
        "gancho": dados["gancho"],
        "nota_gancho": dados["nota_gancho"],
        "secoes": [s["titulo"] for s in dados["secoes"]],
        "segmentos": segmentos,
        "fontes": dados["fontes"],
        "resumo": dados["resumo"],
    }


def executar(ctx: Contexto) -> None:
    fmt = ctx.formato
    schema = SCHEMAS.get(fmt.nome)
    if schema is None:
        raise ErroEtapa(f"formato sem roteiro: {fmt.nome}")
    prompt = carregar_prompt(
        f"roteiro_{fmt.nome}",
        tema=ctx.job.tema,
        perfil_voz=perfil_voz(ctx),
        titulos_recentes=lista_md(titulos_recentes(ctx), "(nenhum ainda)"),
        palavras_min=fmt.palavras_min,
        palavras_max=fmt.palavras_max,
        problemas_anteriores=lista_md(problemas_revisao(ctx), "Nenhum: esta é a primeira versão."),
    )
    r = ia.chamar(ctx.cfg, ctx.con, ctx.job.id, "roteiro",
                  ia.Pedido(prompt=prompt, schema=schema, pasta=ctx.pasta, web=True,
                            metodos=ia.METODOS["roteiro"], scripts=ia.SCRIPTS["roteiro"]))
    dados = achatar(r.dados) if "secoes" in r.dados else r.dados
    palavras = contar_palavras(" ".join(s["texto"] for s in dados["segmentos"]))
    if not fmt.palavras_min <= palavras <= fmt.palavras_max:
        raise ErroEtapa(
            f"roteiro com {palavras} palavras (esperado {fmt.palavras_min}–{fmt.palavras_max})"
        )
    gravar_json(ctx, "roteiro.json", dados)
