"""Etapa visuais: uma mídia por segmento, pela cadeia do canal (procedural só em emergência)."""
from __future__ import annotations

import json
import shutil

from autocanal.etapas.base import Contexto, gravar_json, ler_json
from autocanal.provedores.imagens import Pedido, criar_cadeia, resolver


def executar(ctx: Contexto) -> None:
    segmentos = ler_json(ctx, "roteiro.json")["segmentos"]
    fmt = ctx.formato
    cadeia = criar_cadeia(ctx.cfg, ctx.canal, fmt.largura, fmt.altura)
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
    pedidos = [
        Pedido(f"seg_{i:02d}", seg["visual"], seg["texto"], seg.get("cena", ""),
               pasta / f"seg_{i:02d}")
        for i, seg in enumerate(segmentos, 1)
    ]
    obtidas = resolver(cadeia, pedidos, fmt.orientacao, excluir)
    gravar_json(ctx, "fontes_midia.json", [obtidas[p.id].como_dict() for p in pedidos])
