"""Utilitários de mídia: wrapper de ffmpeg/ffprobe."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ErroMidia(Exception):
    pass


@dataclass(frozen=True)
class InfoMidia:
    duracao: float
    largura: int | None
    altura: int | None
    tem_audio: bool
    tem_video: bool


class Midia:
    def __init__(self, ffmpeg: Path, ffprobe: Path):
        self.ffmpeg = Path(ffmpeg)
        self.ffprobe = Path(ffprobe)

    def rodar(
        self, args: list[str], *, cwd: Path | None = None, timeout_s: int = 1800
    ) -> str:
        cmd = [str(self.ffmpeg), "-hide_banner", "-y", *args]
        try:
            r = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True,
                timeout=timeout_s, errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            raise ErroMidia(f"ffmpeg excedeu {timeout_s}s") from e
        if r.returncode != 0:
            raise ErroMidia(
                f"ffmpeg falhou (código {r.returncode}): {r.stderr[-2000:]}"
            )
        return r.stderr

    def info(self, arquivo: Path) -> InfoMidia:
        r = subprocess.run(
            [str(self.ffprobe), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(arquivo)],
            capture_output=True, text=True, errors="replace",
        )
        if r.returncode != 0:
            raise ErroMidia(f"ffprobe falhou: {r.stderr[-2000:]}")
        dados = json.loads(r.stdout)
        streams = dados.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = any(s.get("codec_type") == "audio" for s in streams)
        duracao = dados.get("format", {}).get("duration")
        if duracao is None:
            duracao = next(
                (s["duration"] for s in streams if s.get("duration")), 0
            )
        return InfoMidia(
            duracao=float(duracao),
            largura=int(video["width"]) if video else None,
            altura=int(video["height"]) if video else None,
            tem_audio=audio,
            tem_video=video is not None,
        )

    def loudness(self, arquivo: Path) -> float:
        err = self.rodar(
            ["-i", str(arquivo), "-af", "loudnorm=print_format=json", "-f", "null", "-"]
        )
        ini, fim = err.rfind("{"), err.rfind("}")
        if ini < 0 or fim < ini:
            raise ErroMidia(f"loudnorm sem JSON: {err[-2000:]}")
        try:
            return float(json.loads(err[ini : fim + 1])["input_i"])
        except (ValueError, KeyError) as e:
            raise ErroMidia(f"loudnorm com saída inválida: {err[-2000:]}") from e

    def silencios(
        self, arquivo: Path, limiar_db: float = -40.0, minimo_s: float = 2.0
    ) -> list[tuple[float, float]]:
        err = self.rodar(
            ["-i", str(arquivo), "-af",
             f"silencedetect=noise={limiar_db}dB:d={minimo_s}", "-f", "null", "-"]
        )
        intervalos: list[tuple[float, float]] = []
        inicio: float | None = None
        for linha in err.splitlines():
            m = re.search(r"silence_start: (-?[\d.]+)", linha)
            if m:
                inicio = max(0.0, float(m.group(1)))
                continue
            m = re.search(r"silence_end: (-?[\d.]+)", linha)
            if m and inicio is not None:
                intervalos.append((inicio, float(m.group(1))))
                inicio = None
        if inicio is not None:
            intervalos.append((inicio, self.info(arquivo).duracao))
        return intervalos

    def extrair_frames(
        self, video: Path, n: int, destino: Path, lado_max: int | None = None
    ) -> list[Path]:
        """`n` quadros espaçados; com `lado_max`, reduzidos para que o lado maior caiba nele."""
        destino.mkdir(parents=True, exist_ok=True)
        escala = [] if lado_max is None else [
            "-vf", f"scale='if(gte(iw,ih),{lado_max},-2)':'if(gte(iw,ih),-2,{lado_max})'"]
        dur = self.info(video).duracao
        saidas: list[Path] = []
        for i in range(n):
            t = (i + 0.5) * dur / n
            saida = destino / f"quadro_{i + 1:02d}.jpg"
            self.rodar(
                ["-ss", f"{t:.3f}", "-i", str(video), *escala, "-frames:v", "1",
                 "-q:v", "2", str(saida)]
            )
            saidas.append(saida)
        return saidas

    def concatenar_wav(
        self, wavs: list[Path], destino: Path, pausa_s: float = 0.25
    ) -> Path:
        if not wavs:
            raise ErroMidia("nenhum WAV para concatenar")
        args: list[str] = []
        for w in wavs:
            args += ["-i", str(w)]
        fmt = "aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono"
        partes: list[str] = []
        filtros: list[str] = []
        for i in range(len(wavs)):
            filtros.append(f"[{i}:a]{fmt}[a{i}]")
            partes.append(f"[a{i}]")
            if i < len(wavs) - 1:
                filtros.append(
                    f"anullsrc=r=24000:cl=mono,atrim=duration={pausa_s}[p{i}]"
                )
                partes.append(f"[p{i}]")
        filtros.append(f"{''.join(partes)}concat=n={len(partes)}:v=0:a=1[out]")
        self.rodar(
            [*args, "-filter_complex", ";".join(filtros), "-map", "[out]",
             "-ar", "24000", "-ac", "1", str(destino)]
        )
        return destino
