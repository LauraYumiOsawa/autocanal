import json
import subprocess
from pathlib import Path

import pytest

from autocanal import fila
from autocanal.db import conectar
from autocanal.etapas import render
from autocanal.etapas.base import contexto_para, gravar_json
from autocanal.etapas.render import (
    comando_audio,
    comando_mux,
    comando_segmento,
    comando_video_legendado,
)


def test_comando_segmento_imagem():
    cmd = comando_segmento(Path("a.jpg"), "imagem", 3.0, 1080, 1920, Path("out.mp4"))
    texto = " ".join(cmd)
    assert "-loop" in cmd and "zoompan" in texto and "s=1080x1920" in texto
    assert "d=90" in texto and "-an" in cmd and cmd[-1] == "out.mp4"


def test_comando_segmento_video():
    cmd = comando_segmento(Path("a.mp4"), "video", 3.0, 1080, 1920, Path("out.mp4"))
    assert "-stream_loop" in cmd and "setsar=1" in " ".join(cmd) and "zoompan" not in " ".join(cmd)


def test_comando_video_legendado():
    cmd = comando_video_legendado(Path("b.mp4"), "legendas.ass", Path("v.mp4"))
    assert "ass=legendas.ass" in cmd and "-an" in cmd and cmd[-1] == "v.mp4"


def test_comando_audio_sem_trilha():
    texto = " ".join(comando_audio(Path("n.wav"), None, Path("a.m4a")))
    assert "aresample=48000,acompressor" in texto and "loudnorm=I=-14" in texto and "amix" not in texto


def test_comando_audio_com_trilha():
    cmd = comando_audio(Path("n.wav"), Path("m.mp3"), Path("a.m4a"))
    texto = " ".join(cmd)
    assert "volume=-20dB" in texto and "amix=inputs=2:duration=first:normalize=0" in texto
    assert "-stream_loop" in cmd


def test_comando_mux():
    cmd = comando_mux(Path("v.mp4"), Path("a.m4a"), Path("o.mp4"))
    assert "-shortest" in cmd and "+faststart" in cmd and cmd[cmd.index("-c") + 1] == "copy"


def test_duracoes_ladrilham_a_narracao():
    segs = [{"inicio": 0.0}, {"inicio": 3.25}, {"inicio": 7.0}, {"inicio": 10.5}]
    d = render._duracoes(segs, 14.2)
    assert abs(sum(d) - 14.2) < 0.05 and d[-1] == pytest.approx(3.7)


def test_executar_gera_video(cfg, midia, tmp_path):
    from datetime import UTC, datetime

    con = conectar(cfg.dados / "autocanal.db")
    job = fila.criar_job(con, "teste", "short", "tema", datetime(2026, 11, 2, 15, tzinfo=UTC))
    ctx = contexto_para(cfg, con, job)
    pasta = ctx.pasta
    ffmpeg = str(cfg.ffmpeg)
    img = pasta / "a.jpg"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=640x480",
                    "-frames:v", "1", str(img)], check=True)
    vid = pasta / "b.mp4"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1:r=30",
                    str(vid)], check=True)
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=4.2",
                    "-ar", "24000", "-ac", "1", str(pasta / "narracao.wav")], check=True)
    (pasta / "legendas.ass").write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,DejaVu Sans,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,100,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.50,0:00:03.00,Default,,0,0,0,,oi\n",
        encoding="utf-8",
    )
    gravar_json(ctx, "narracao.json", {
        "segmentos": [{"arquivo": "x", "inicio": 0.0, "fim": 2.0},
                      {"arquivo": "y", "inicio": 2.25, "fim": 4.2}],
        "duracao": 4.2, "provedor": "falso", "usou_reserva": False})
    gravar_json(ctx, "fontes_midia.json", [
        {"caminho": str(img), "tipo": "imagem"}, {"caminho": str(vid), "tipo": "video"}])
    render.executar(ctx)
    info = midia.info(pasta / "video.mp4")
    assert (info.largura, info.altura) == (1080, 1920) and info.tem_audio
    assert abs(info.duracao - 4.2) < 0.2
    con.close()
