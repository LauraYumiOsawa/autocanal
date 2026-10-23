"""Etapa visuais: uma mídia por segmento (Pexels → Pixabay → procedural)."""
from __future__ import annotations

import shutil

from autocanal.etapas.base import Contexto, gravar_json, ler_json
from autocanal.provedores.imagens import buscar_em_cadeia, criar_cadeia


def executar(ctx: Contexto) -> None:
    segmentos = ler_json(ctx, "roteiro.json")["segmentos"]
    fmt = ctx.formato
    cadeia = criar_cadeia(ctx.cfg, ctx.canal, fmt.largura, fmt.altura)
    pasta = ctx.pasta / "visuais"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    excluir: set[str] = set()
    fontes = []
    for i, seg in enumerate(segmentos, 1):
        m = buscar_em_cadeia(cadeia, seg["visual"], fmt.orientacao, pasta / f"seg_{i:02d}", excluir)
        excluir.add(m.url)
        fontes.append(m.como_dict())
    gravar_json(ctx, "fontes_midia.json", fontes)
