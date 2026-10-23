"""Interface de linha de comando do autocanal."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from autocanal import agendador, claude, fila, grade, planejador, produtor, publicador, relatorio
from autocanal.config import Config, carregar
from autocanal.db import conectar
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS
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


@contextmanager
def _exclusivo(cfg: Config) -> Iterator[None]:
    """A mesma trava do tick: comandos que mexem na fila nunca rodam junto com ele."""
    try:
        with agendador.trava(cfg.dados / "tick.lock"):
            yield
    except agendador.TravaOcupada:
        typer.echo("outro processo do autocanal está rodando", err=True)
        raise typer.Exit(2)


def _obter(con, job_id: str) -> fila.Job:
    try:
        return fila.obter(con, job_id)
    except KeyError:
        typer.echo(f"job desconhecido: {job_id}", err=True)
        raise typer.Exit(1)


def _local(cfg: Config, d: datetime) -> str:
    return d.astimezone(ZoneInfo(cfg.fuso)).strftime("%d/%m/%Y %H:%M")


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
    token = cfg.dados / "tokens" / f"{canal}.json"
    creds = oauth.autorizar(cfg.client_secret, token, porta)
    try:
        canal_id, titulo = oauth.canal_autenticado(oauth.cliente(creds))
    except Exception as e:
        token.unlink(missing_ok=True)  # token não verificado nunca fica utilizável
        typer.echo(f"falha: {type(e).__name__}: {e}", err=True)
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
    with _exclusivo(cfg):
        agora = datetime.now(UTC)
        youtube = publicador.youtube_real(cfg)
        ids = publicador.publicar_aprovados(cfg, con, agora, youtube_por_canal=youtube, job_id=job)
        typer.echo(f"enviados: {', '.join(ids) or 'nenhum'}")
        if job is None:
            feitos = publicador.sincronizar_publicados(
                cfg, con, datetime.now(UTC), youtube_por_canal=youtube
            )
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
        meta = json.loads((cfg.dados / "jobs" / j.id / "metadados.json").read_text(encoding="utf-8"))
        sintetico = bool(meta.get("sintetico"))
    except (OSError, ValueError):
        sintetico = False
    try:
        upload.vetar(publicador.youtube_real(cfg)(j.canal), j.youtube_video_id, sintetico)
    except (oauth.AuthExpirada, upload.CotaEsgotada, upload.ErroUpload) as e:
        typer.echo(f"falha ao vetar no YouTube: {e}", err=True)
        raise typer.Exit(1)
    fila.mudar_estado(con, job, fila.Estado.VETADO)
    typer.echo(f"vetado: {job}")


_FORA_DA_FILA = {Estado.PUBLICADO, Estado.DESCARTADO, Estado.VETADO}


@app.command()
def status(ctx: typer.Context) -> None:
    """Quantos jobs há em cada estado, alertas e os próximos 10 jobs."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    contagem = dict(con.execute("SELECT estado, COUNT(*) FROM jobs GROUP BY estado").fetchall())
    typer.echo("Jobs por estado:")
    for e in Estado:
        if contagem.get(e.value):
            typer.echo(f"  {e.value}: {contagem[e.value]}")
    if not contagem:
        typer.echo("  (fila vazia)")
    avisos = relatorio.alertas(cfg, con, datetime.now(UTC))
    if avisos:
        typer.echo("Alertas:")
        for a in avisos:
            typer.echo(f"  {a}")
    proximos = [j for j in fila.listar(con) if j.estado not in _FORA_DA_FILA][:10]
    typer.echo("Próximos (até 10):")
    for j in proximos:
        typer.echo(f"  {_local(cfg, j.publish_at)}  {j.estado.value:<11} {j.id}  {j.tema}")


@app.command()
def novo(
    ctx: typer.Context,
    canal: str,
    formato: str,
    tema: str,
    quando: str = typer.Option(
        None, "--quando", help='"AAAA-MM-DD HH:MM" no fuso do projeto (padrão: próximo slot).'
    ),
) -> None:
    """Cria um job com o tema dado, no horário indicado ou no próximo slot livre da grade."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    tema = tema.strip()
    erro = (
        f"canal desconhecido: {canal}" if canal not in cfg.canais
        else f"formato desconhecido: {formato}" if formato not in FORMATOS
        else "tema vazio" if not tema
        else None
    )
    if erro:
        typer.echo(erro, err=True)
        raise typer.Exit(1)
    with _exclusivo(cfg):
        agora = datetime.now(UTC)
        if quando:
            try:
                local = datetime.strptime(quando, "%Y-%m-%d %H:%M")
            except ValueError:
                typer.echo('--quando deve ser "AAAA-MM-DD HH:MM"', err=True)
                raise typer.Exit(1)
            publish_at = local.replace(tzinfo=ZoneInfo(cfg.fuso)).astimezone(UTC)
            if publish_at <= agora:
                typer.echo(f"horário já passou: {quando}", err=True)
                raise typer.Exit(1)
        else:
            try:
                publish_at = grade.proximo_slot(
                    cfg.canais[canal], formato, agora + planejador.MARGEM,
                    planejador.ocupados(con, canal, formato), cfg.fuso,
                )
            except ValueError as e:
                typer.echo(str(e), err=True)
                raise typer.Exit(1)
        job = fila.criar_job(con, canal, formato, tema, publish_at)
        planejador.registrar_tema(con, canal, formato, tema)
    typer.echo(f"criado: {job.id} (vai ao ar em {_local(cfg, job.publish_at)})")


@app.command()
def planejar(ctx: typer.Context) -> None:
    """Preenche a fila com temas sugeridos pelo Claude para os slots livres da grade."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    with _exclusivo(cfg):
        try:
            jobs = planejador.planejar(
                cfg, con, datetime.now(UTC),
                sugerir_temas=planejador.sugerir_temas_claude(cfg, con),
            )
        except claude.ErroClaude as e:
            typer.echo(f"falha ao sugerir temas: {e}", err=True)
            raise typer.Exit(1)
    typer.echo(f"{len(jobs)} job(s) planejado(s)")
    for j in jobs:
        typer.echo(f"  {_local(cfg, j.publish_at)}  {j.id}  {j.tema}")


def _rodar_producao(cfg: Config, con, job_id: str) -> None:
    produtor.produzir(cfg, con, job_id)
    job = fila.obter(con, job_id)
    typer.echo(f"{job.id}: {job.estado.value}" + (f" ({job.erro})" if job.erro else ""))


@app.command()
def produzir(
    ctx: typer.Context,
    job: str = typer.Argument(None, help="Job a produzir (padrão: o próximo da fila)."),
) -> None:
    """Produz um job; `falhou` e `quarentena` são refeitos a partir do que deu errado."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    with _exclusivo(cfg):
        if job is None:
            j = fila.proximo_para_produzir(con)
            if j is None:
                typer.echo("nada para produzir")
                return
        else:
            j = _obter(con, job)
            if j.estado in (Estado.FALHOU, Estado.QUARENTENA):
                try:
                    j = produtor.preparar_refazer(cfg, con, j.id)
                except ValueError as e:
                    typer.echo(str(e), err=True)
                    raise typer.Exit(1)
            elif j.estado not in (Estado.PLANEJADO, Estado.PRODUZINDO, Estado.EM_REVISAO):
                typer.echo(f"nada a produzir: {j.id} está {j.estado.value}", err=True)
                raise typer.Exit(1)
        _rodar_producao(cfg, con, j.id)


@app.command()
def revisar(ctx: typer.Context, job: str) -> None:
    """Roda a revisão de novo (zera as rodadas se o job estiver em quarentena)."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    with _exclusivo(cfg):
        j = _obter(con, job)
        try:
            produtor.preparar_revisao(cfg, con, j.id)
        except ValueError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
        _rodar_producao(cfg, con, j.id)


def _declarar_sintetico(pasta: Path, rodadas: int) -> None:
    """Aprovação manual: se alguma revisão viu mídia sintética, o metadados.json declara.

    A revisão só grava `sintetico` quando ela mesma aprova; nunca remove a declaração.
    """
    vistos = []
    for n in range(1, rodadas + 1):
        try:
            dados = json.loads((pasta / f"revisao_{n}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        vistos.append(bool(dados.get("contains_synthetic_media")))
    arquivo = pasta / "metadados.json"
    if not any(vistos) or not arquivo.is_file():
        return
    meta = json.loads(arquivo.read_text(encoding="utf-8"))
    if not meta.get("sintetico"):
        meta["sintetico"] = True
        arquivo.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _ultima_revisao_aprovou(con, job_id: str) -> bool:
    r = con.execute(
        "SELECT aprovado FROM revisoes WHERE job_id = ? ORDER BY rodada DESC LIMIT 1", (job_id,)
    ).fetchone()
    return r is not None and bool(r["aprovado"])


@app.command()
def aprovar(ctx: typer.Context, job: str) -> None:
    """Aprova à mão um job em quarentena (ou falhou, se o vídeo pronto passou na revisão)."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    with _exclusivo(cfg):
        j = _obter(con, job)
        pasta = cfg.dados / "jobs" / j.id
        if j.estado not in (Estado.QUARENTENA, Estado.FALHOU):
            typer.echo(f"só jobs em quarentena ou falhou podem ser aprovados ({j.id} está"
                       f" {j.estado.value})", err=True)
            raise typer.Exit(1)
        prontos = (pasta / "video.mp4").is_file() and (pasta / "metadados.json").is_file()
        if j.estado == Estado.FALHOU and not prontos:
            typer.echo(f"{j.id} não tem video.mp4 e metadados.json; rode"
                       f" `autocanal produzir {j.id}`", err=True)
            raise typer.Exit(1)
        if j.estado == Estado.FALHOU and not _ultima_revisao_aprovou(con, j.id):
            typer.echo(f"{j.id} não foi aprovado pela revisão; rode `autocanal revisar {j.id}`"
                       " (ou veja o vídeo e use a quarentena)", err=True)
            raise typer.Exit(1)
        _declarar_sintetico(pasta, j.tentativas_revisao)
        fila.mudar_estado(con, j.id, Estado.APROVADO)
    typer.echo(f"aprovado: {j.id} (vai ao ar em {_local(cfg, j.publish_at)} ou no próximo slot)")


@app.command()
def descartar(ctx: typer.Context, job: str) -> None:
    """Descarta um job (o slot volta a ficar livre para o planejador)."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    with _exclusivo(cfg):
        j = _obter(con, job)
        try:
            fila.mudar_estado(con, j.id, Estado.DESCARTADO)
        except fila.TransicaoInvalida:
            dica = f"; vete antes com `autocanal vetar {j.id}`" if j.estado == Estado.AGENDADO else ""
            typer.echo(f"não é possível descartar {j.id} no estado {j.estado.value}{dica}", err=True)
            raise typer.Exit(1)
    typer.echo(f"descartado: {j.id}")


def _resumo_e_aviso(cfg: Config, con, dia) -> Path:
    caminho = relatorio.gerar_resumo(cfg, con, dia)
    quarentena = len(fila.listar(con, {Estado.QUARENTENA}))
    falhas = len(fila.listar(con, {Estado.FALHOU}))
    relatorio.notificar(
        f"autocanal: resumo de {dia:%d/%m}",
        f"Quarentena: {quarentena} · Falhas: {falhas}\n{caminho}",
    )
    return caminho


@app.command()
def tick(ctx: typer.Context) -> None:
    """Um passo do agendador (o timer roda a cada 15 min)."""
    cfg = carregar(ctx.obj)
    con = conectar(cfg.dados / "autocanal.db")
    youtube = publicador.youtube_real(cfg)
    r = agendador.tick(
        cfg, con, datetime.now(UTC),
        planejar_fn=lambda c, k, agora: planejador.planejar(
            c, k, agora, sugerir_temas=planejador.sugerir_temas_claude(c, k)
        ),
        produzir_fn=produtor.produzir,
        # o tick passa a hora atual de cada fase (a produção pode ter levado muito tempo)
        publicar_fn=lambda c, k, agora: publicador.publicar_aprovados(
            c, k, agora, youtube_por_canal=youtube
        ),
        sincronizar_fn=lambda c, k, agora: publicador.sincronizar_publicados(
            c, k, agora, youtube_por_canal=youtube
        ),
        resumo_fn=_resumo_e_aviso,
    )
    if r.pulou_por_trava:
        # como os outros comandos com trava; o serviço trata 2 como sucesso (SuccessExitStatus)
        typer.echo("outro processo do autocanal está rodando", err=True)
        raise typer.Exit(2)
    typer.echo(f"planejados: {r.planejados}")
    typer.echo(f"produzido: {r.produzido or 'nenhum'}")
    typer.echo(f"enviados: {', '.join(r.enviados) or 'nenhum'}")
    typer.echo(f"publicados: {', '.join(r.publicados) or 'nenhum'}")
    if r.resumo:
        typer.echo(f"resumo: {r.resumo}")


_SERVICO = """\
[Unit]
Description=autocanal: um passo do agendador (planejar, produzir, publicar)

[Service]
Type=oneshot
Environment=PATH={local_bin}:/usr/local/bin:/usr/bin:/bin
ExecStart="{exe}" --raiz "{raiz}" tick
# 2 = tick pulado porque outro processo do autocanal segura a trava: não é falha
SuccessExitStatus=2
# produção travada: o systemd mata o tick; a etapa `rodando` conta como tentativa
TimeoutStartSec=2h
"""

_TIMER = """\
[Unit]
Description=autocanal: tick a cada 15 minutos

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
"""


@app.command()
def instalar_timer(
    ctx: typer.Context,
    ativar: bool = typer.Option(False, "--ativar", help="Também habilita e inicia o timer."),
) -> None:
    """Escreve o serviço e o timer do systemd do usuário; só habilita com --ativar."""
    raiz = Path(ctx.obj).resolve()
    destino = Path.home() / ".config" / "systemd" / "user"
    destino.mkdir(parents=True, exist_ok=True)
    exe = raiz / ".venv" / "bin" / "autocanal"
    (destino / "autocanal.service").write_text(
        _SERVICO.format(exe=exe, raiz=raiz, local_bin=Path.home() / ".local" / "bin"),
        encoding="utf-8",
    )
    (destino / "autocanal.timer").write_text(_TIMER, encoding="utf-8")
    typer.echo(f"escritos: {destino / 'autocanal.service'}, {destino / 'autocanal.timer'}")
    if not ativar:
        typer.echo("timer não habilitado; para habilitar: autocanal instalar-timer --ativar")
        return
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "autocanal.timer"], check=True)
    typer.echo("timer habilitado: autocanal.timer")
