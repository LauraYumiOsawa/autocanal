"""Interface de linha de comando do autocanal."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer

from autocanal.config import carregar

app = typer.Typer(help="Criação e publicação automática de vídeos no YouTube.")


@app.callback()
def principal(
    ctx: typer.Context,
    raiz: Path = typer.Option(
        None,
        "--raiz",
        help="Raiz do projeto (padrão: env AUTOCANAL_RAIZ ou ~/autocanal).",
    ),
) -> None:
    if raiz is None:
        raiz = Path(os.environ.get("AUTOCANAL_RAIZ", "~/autocanal"))
    ctx.obj = raiz.expanduser()


def _linha(ok: bool, nome: str, detalhe: str = "") -> bool:
    marca = "OK   " if ok else "FALHA"
    typer.echo(f"[{marca}] {nome}" + (f" ({detalhe})" if detalhe else ""))
    return ok


def _executa(cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        return False, type(e).__name__
    saida = (r.stdout or r.stderr).strip().splitlines()
    return r.returncode == 0, saida[0] if saida else ""


@app.command()
def diagnostico(ctx: typer.Context) -> None:
    """Verifica ferramentas, credenciais e chaves (nunca imprime valores)."""
    cfg = carregar(ctx.obj)
    resultados = []
    for nome, caminho in (("ffmpeg", cfg.ffmpeg), ("ffprobe", cfg.ffprobe)):
        ok, det = _executa([str(caminho), "-version"])
        resultados.append(_linha(ok, nome, det[:60]))
    for nome, cmd in (
        ("claude", [cfg.claude.bin, "--version"]),
        ("nvidia-smi", ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]),
        ("docker", ["docker", "info", "--format", "{{.ServerVersion}}"]),
    ):
        ok, det = _executa(cmd)
        resultados.append(_linha(ok, nome, det))
    resultados.append(
        _linha(cfg.client_secret.exists(), "client_secret.json", str(cfg.client_secret))
    )
    for nome in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "GOOGLE_TTS_API_KEY", "YOUTUBE_API_KEY"):
        definida = bool(cfg.chaves.get(nome))
        typer.echo(f"[{'OK   ' if definida else 'FALHA'}] {nome}: {'definida' if definida else 'ausente'}")
    tokens = sorted((cfg.dados / "tokens").glob("*.json")) if (cfg.dados / "tokens").is_dir() else []
    typer.echo(f"tokens em dados/tokens/: {', '.join(t.stem for t in tokens) or 'nenhum'}")
    if not all(resultados[:2]):
        raise typer.Exit(1)
