"""Etapa narração: um WAV por segmento do roteiro + o WAV total com pausas."""
from __future__ import annotations

import shutil

from autocanal.etapas.base import Contexto, gravar_json, ler_json
from autocanal.provedores.tts import criar_tts

PAUSA_S = 0.25


def executar(ctx: Contexto) -> None:
    textos = [s["texto"] for s in ler_json(ctx, "roteiro.json")["segmentos"]]
    pasta = ctx.pasta / "narracao"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    wavs = [pasta / f"seg_{i:02d}.wav" for i in range(1, len(textos) + 1)]

    tts = criar_tts(ctx.cfg, ctx.canal)
    caiu_em: int | None = None
    for i, (texto, wav) in enumerate(zip(textos, wavs)):
        tts.sintetizar(texto, wav)
        if tts.usou_reserva:
            caiu_em = i
            break
    usou_reserva = caiu_em is not None
    if usou_reserva:
        # a principal caiu: o resto (e o que veio antes) vai na reserva, sem misturar vozes
        for j, (texto, wav) in enumerate(zip(textos, wavs)):
            if j != caiu_em:
                tts.reserva.sintetizar(texto, wav)
    provedor = tts.reserva.nome if usou_reserva else tts.nome

    total = ctx.midia.concatenar_wav(wavs, ctx.pasta / "narracao.wav", pausa_s=PAUSA_S)
    duracao = round(ctx.midia.info(total).duracao, 3)
    segmentos = []
    inicio = 0.0
    for n, wav in enumerate(wavs, 1):
        fim = duracao if n == len(wavs) else inicio + ctx.midia.info(wav).duracao
        segmentos.append({"arquivo": str(wav), "inicio": round(inicio, 3), "fim": round(fim, 3)})
        inicio = fim + PAUSA_S
    gravar_json(
        ctx,
        "narracao.json",
        {"segmentos": segmentos, "duracao": duracao, "provedor": provedor,
         "usou_reserva": usou_reserva},
    )
