"""Interface de linha de comando do autocanal."""
from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import typer

from autocanal import fila, publicador
from autocanal.config import carregar
from autocanal.db import conectar
from autocanal.youtube import oauth, upload

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


@app.command()
def auth(ctx: typer.Context, canal: str, porta: int = typer.Option(8080, "--porta")) -> None:
    """Autoriza a conta do YouTube de um canal (abre o navegador)."""
    cfg = carregar(ctx.obj)
    if canal not in cfg.canais:
        typer.echo(f"canal desconhecido: {canal}", err=True)
        raise typer.Exit(1)
    creds = oauth.autorizar(cfg.client_secret, cfg.dados / "tokens" / f"{canal}.json", porta)
    try:
        canal_id, titulo = oauth.canal_autenticado(oauth.cliente(creds))
    except oauth.AuthExpirada as e:
        typer.echo(f"falha: {e}", err=True)
        raise typer.Exit(1)
    con = conectar(cfg.dados / "autocanal.db")
    con.execute(
        "INSERT INTO canais_estado (canal, youtube_channel_id, auth_ok, ultimo_erro)"
        " VALUES (?, ?, 1, NULL) ON CONFLICT(canal) DO UPDATE SET"
        " youtube_channel_id = excluded.youtube_channel_id, auth_ok = 1, ultimo_erro = NULL",
        (canal, canal_id),
    )
    con.commit()
    typer.echo(f"autorizado: {titulo} ({canal_id})")


@app.command()
def publicar(ctx: typer.Context, job: str = typer.Option(None, "--job", help="Só este job.")) -> None:
    """Envia os jobs aprovados ao YouTube (privado, com publishAt) e sincroniza os publicados."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    agora = datetime.now(UTC)
    youtube = publicador.youtube_real(cfg)
    ids = publicador.publicar_aprovados(cfg, con, agora, youtube_por_canal=youtube, job_id=job)
    typer.echo(f"enviados: {', '.join(ids) or 'nenhum'}")
    if job is None:
        feitos = publicador.sincronizar_publicados(cfg, con, agora, youtube_por_canal=youtube)
        typer.echo(f"publicados: {', '.join(feitos) or 'nenhum'}")


@app.command()
def vetar(ctx: typer.Context, job: str) -> None:
    """Torna privado um vídeo agendado e marca o job como vetado."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    try:
        j = fila.obter(con, job)
    except KeyError:
        typer.echo(f"job desconhecido: {job}", err=True)
        raise typer.Exit(1)
    if j.estado != fila.Estado.AGENDADO or not j.youtube_video_id:
        typer.echo(f"só jobs agendados podem ser vetados (estado: {j.estado.value})", err=True)
        raise typer.Exit(1)
    try:
        upload.vetar(publicador.youtube_real(cfg)(j.canal), j.youtube_video_id)
    except (oauth.AuthExpirada, upload.CotaEsgotada, upload.ErroUpload) as e:
        typer.echo(f"falha ao vetar no YouTube: {e}", err=True)
        raise typer.Exit(1)
    fila.mudar_estado(con, job, fila.Estado.VETADO)
    typer.echo(f"vetado: {job}")
