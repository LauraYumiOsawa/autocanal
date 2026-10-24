"""Etapa visuais: uma mídia por segmento, pela cadeia do canal (procedural só em emergência)."""
from __future__ import annotations

import json
import shutil

from autocanal import claude
from autocanal.claude import ErroClaude, ErroClaudeIndisponivel, LimiteUsoClaude
from autocanal.etapas.base import Contexto, ErroEtapa, carregar_prompt, gravar_json, ler_json
from autocanal.provedores.curadoria import CuradoriaPixabay, Escolher, ItemCuradoria
from autocanal.provedores.imagens import Pedido, PixabayCandidatos, criar_cadeia, resolver

CURADORIA = {
    "type": "object",
    "required": ["escolhas"],
    "additionalProperties": False,
    "properties": {
        "escolhas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "candidato", "motivo"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "candidato": {"type": "integer", "minimum": 0},
                    "motivo": {"type": "string"},
                },
            },
        },
    },
}


def _itens_md(itens: list[ItemCuradoria]) -> str:
    return "\n".join(
        f"- `{i.id}` — fala: \"{i.texto}\" — cena esperada: {i.cena or '(não informada)'} — "
        f"folha: `{i.folha}` ({i.n} candidatos)"
        for i in itens
    )


def escolher_com_claude(ctx: Contexto) -> Escolher:
    """Uma chamada do Claude por lote: devolve `{id: (candidato, motivo)}`."""

    def escolher(itens: list[ItemCuradoria]) -> dict[str, tuple[int, str]]:
        prompt = carregar_prompt("curadoria", estilo=ctx.canal.estilo_visual, itens=_itens_md(itens))
        try:
            r = claude.chamar(
                prompt,
                CURADORIA,
                ["Read"],
                ctx.pasta,
                modelo=ctx.cfg.claude.modelos.get("curadoria"),
                max_turns=ctx.cfg.claude.max_turns.get("curadoria", 25),
                claude_bin=ctx.cfg.claude.bin,
                raiz=ctx.cfg.raiz,
            )
        except (LimiteUsoClaude, ErroClaudeIndisponivel):
            raise
        except ErroClaude as e:
            # sem curadoria não há garantia de que o visual combina com a fala
            raise ErroEtapa(f"curadoria dos visuais falhou: {e}") from e
        claude.registrar(ctx.con, ctx.job.id, "visuais", r)
        return {e["id"]: (int(e["candidato"]), e["motivo"]) for e in r.dados["escolhas"]}

    return escolher


def executar(ctx: Contexto) -> None:
    segmentos = ler_json(ctx, "roteiro.json")["segmentos"]
    fmt = ctx.formato
    pasta = ctx.pasta / "visuais"
    # refazer (revisão reprovou): não repetir as mídias da versão anterior
    excluir: set[str] = set()
    anterior = ctx.pasta / "fontes_midia.json"
    if anterior.exists():
        try:
            excluir = {f["url"] for f in json.loads(anterior.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError):
            excluir = set()
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    pixabay = None
    chave = ctx.cfg.chaves.get("PIXABAY_API_KEY")
    if chave and "pixabay" in ctx.canal.visuais:
        pixabay = CuradoriaPixabay(PixabayCandidatos(chave), escolher_com_claude(ctx),
                                   ctx.canal.estilo_visual, pasta / "curadoria")
    cadeia = criar_cadeia(ctx.cfg, ctx.canal, fmt.largura, fmt.altura, pixabay=pixabay)
    pedidos = [
        Pedido(f"seg_{i:02d}", seg["visual"], seg["texto"], seg.get("cena", ""),
               pasta / f"seg_{i:02d}")
        for i, seg in enumerate(segmentos, 1)
    ]
    obtidas = resolver(cadeia, pedidos, fmt.orientacao, excluir)
    gravar_json(ctx, "fontes_midia.json", [obtidas[p.id].como_dict() for p in pedidos])
