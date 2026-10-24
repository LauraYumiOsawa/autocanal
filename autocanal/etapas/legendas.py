"""Etapa legendas: tempos da narração real (faster-whisper) + texto do roteiro → `.ass`."""
from __future__ import annotations

import json

from autocanal.etapas.base import Contexto, ErroEtapa, gravar_json
from autocanal.legendas import agrupar, alinhar, gerar_ass, largura_util, medidor, transcrever


def executar(ctx: Contexto) -> None:
    wav = ctx.pasta / "narracao.wav"
    if not wav.exists():
        raise ErroEtapa("artefato ausente: narracao.wav")
    arq_roteiro = ctx.pasta / "roteiro.json"
    if not arq_roteiro.exists():
        raise ErroEtapa("artefato ausente: roteiro.json")
    segmentos = json.loads(arq_roteiro.read_text(encoding="utf-8"))["segmentos"]
    texto = " ".join(s["texto"] for s in segmentos)
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
    alinhadas = alinhar(texto, palavras)
    if not alinhadas:
        raise ErroEtapa("roteiro sem palavras para legendar")
    fmt, canal = ctx.formato, ctx.canal
    blocos = agrupar(
        alinhadas,
        fmt.legenda_max_palavras,
        fmt.legenda_max_duracao_s,
        largura_max=largura_util(fmt.largura),
        medir=medidor(canal.fonte_arquivo, fmt.legenda_tamanho),
    )
    gerar_ass(
        blocos,
        ctx.pasta / "legendas.ass",
        largura=fmt.largura,
        altura=fmt.altura,
        fonte_nome=canal.fonte_nome,
        tamanho=fmt.legenda_tamanho,
        cor_texto=canal.paleta.texto,
        cor_destaque=canal.paleta.destaque,
        margem_v=fmt.legenda_margem_v,
    )
