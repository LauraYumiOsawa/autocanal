"""Etapa legendas: transcreve a narração real (faster-whisper) e gera o `.ass`."""
from __future__ import annotations

from autocanal.etapas.base import Contexto, ErroEtapa, gravar_json
from autocanal.legendas import agrupar, gerar_ass, transcrever

TAMANHO = 88
MARGEM_V = 560


def executar(ctx: Contexto) -> None:
    wav = ctx.pasta / "narracao.wav"
    if not wav.exists():
        raise ErroEtapa("artefato ausente: narracao.wav")
    w = ctx.cfg.whisper
    palavras = transcrever(
        wav,
        modelo=w.modelo,
        dispositivo=w.dispositivo,
        compute_type=w.compute_type,
        idioma=ctx.canal.idioma.split("-")[0].lower(),
    )
    if not palavras:
        raise ErroEtapa("transcrição da narração veio vazia")
    gravar_json(
        ctx,
        "transcricao.json",
        {
            "texto": " ".join(p.texto for p in palavras),
            "palavras": [{"texto": p.texto, "inicio": p.inicio, "fim": p.fim} for p in palavras],
        },
    )
    fmt, canal = ctx.formato, ctx.canal
    gerar_ass(
        agrupar(palavras),
        ctx.pasta / "legendas.ass",
        largura=fmt.largura,
        altura=fmt.altura,
        fonte_nome=canal.fonte_nome,
        tamanho=TAMANHO,
        cor_texto=canal.paleta.texto,
        cor_destaque=canal.paleta.destaque,
        margem_v=MARGEM_V,
    )
