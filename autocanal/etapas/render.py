"""Etapa render: segmentos animados → base concatenada → vídeo final com legendas e áudio."""
from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Literal

from autocanal.etapas.base import Contexto, ErroEtapa, ler_json

CODEC_VIDEO = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def comando_segmento(
    entrada: Path,
    tipo: Literal["imagem", "video"],
    duracao: float,
    largura: int,
    altura: int,
    saida: Path,
    fps: int = 30,
) -> list[str]:
    ajuste = f"scale={largura}:{altura}:force_original_aspect_ratio=increase,crop={largura}:{altura}"
    if tipo == "imagem":
        frames = max(1, round(duracao * fps))
        filtro = (
            f"{ajuste},zoompan=z='min(zoom+0.0007,1.12)':d={frames}"
            f":s={largura}x{altura}:fps={fps}"
        )
        # a imagem entra como um único quadro; o zoompan gera os `frames` de saída
        origem = ["-loop", "1", "-framerate", str(fps), "-t", f"{1 / fps:.6f}", "-i", str(entrada)]
    else:
        filtro = f"{ajuste},fps={fps},setsar=1"
        origem = ["-stream_loop", "-1", "-t", f"{duracao:.3f}", "-i", str(entrada)]
    return [*origem, "-vf", filtro, "-t", f"{duracao:.3f}", "-an", *CODEC_VIDEO, str(saida)]


def comando_final(
    base: Path, narracao: Path, legendas_nome: str, trilha: Path | None, saida: Path
) -> list[str]:
    video = f"[0:v]ass={legendas_nome}[v]"
    if trilha is None:
        filtro = f"{video};[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        entradas = ["-i", str(base), "-i", str(narracao)]
    else:
        filtro = (
            f"{video};[2:a]volume=-20dB[m];"
            "[1:a][m]amix=inputs=2:duration=first:normalize=0[mix];"
            "[mix]loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
        entradas = ["-i", str(base), "-i", str(narracao), "-stream_loop", "-1", "-i", str(trilha)]
    return [
        *entradas, "-filter_complex", filtro, "-map", "[v]", "-map", "[a]",
        *CODEC_VIDEO, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(saida),
    ]


def _escolher_trilha(ctx: Contexto) -> Path | None:
    pasta = ctx.cfg.dados / "biblioteca" / "musica"
    arquivos = sorted(p for p in pasta.glob("*") if p.suffix.lower() in (".wav", ".mp3"))
    if not arquivos:
        return None
    return random.Random(ctx.job.id).choice(arquivos)


def _duracoes(segmentos: list[dict], total: float) -> list[float]:
    """Cada clip vai até o início do próximo; o último absorve o restante da narração."""
    inicios = [s["inicio"] for s in segmentos]
    fins = [*inicios[1:], total]
    return [round(f - i, 3) for i, f in zip(inicios, fins)]


def executar(ctx: Contexto) -> None:
    narr = ler_json(ctx, "narracao.json")
    fontes = ler_json(ctx, "fontes_midia.json")
    segmentos = narr["segmentos"]
    if len(fontes) != len(segmentos):
        raise ErroEtapa("fontes_midia.json e narracao.json têm quantidades diferentes")
    for nome in ("narracao.wav", "legendas.ass"):
        if not (ctx.pasta / nome).exists():
            raise ErroEtapa(f"artefato ausente: {nome}")
    fmt = ctx.formato
    pasta = ctx.pasta / "render"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()

    clipes: list[Path] = []
    for i, (fonte, dur) in enumerate(zip(fontes, _duracoes(segmentos, narr["duracao"])), 1):
        saida = pasta / f"seg_{i:02d}.mp4"
        cmd = comando_segmento(
            Path(fonte["caminho"]), fonte["tipo"], dur, fmt.largura, fmt.altura, saida
        )
        ctx.midia.rodar(cmd, cwd=ctx.pasta)
        clipes.append(saida)

    lista = pasta / "lista.txt"
    lista.write_text("".join(f"file '{c.name}'\n" for c in clipes), encoding="utf-8")
    base = pasta / "base.mp4"
    ctx.midia.rodar(
        ["-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(base)],
        cwd=ctx.pasta,
    )
    cmd = comando_final(
        base, ctx.pasta / "narracao.wav", "legendas.ass", _escolher_trilha(ctx),
        ctx.pasta / "video.mp4",
    )
    ctx.midia.rodar(cmd, cwd=ctx.pasta)
