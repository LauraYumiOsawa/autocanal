from unittest.mock import MagicMock

from typer.testing import CliRunner

from autocanal import cli, fila
from autocanal.db import conectar
from autocanal.fila import Estado
from datetime import UTC, datetime

runner = CliRunner()


def _agendado(cfg):
    con = conectar(cfg.dados / "autocanal.db")
    j = fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    for e in (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO):
        fila.mudar_estado(con, j.id, e)
    fila.atualizar(con, j.id, youtube_video_id="abc")
    fila.mudar_estado(con, j.id, Estado.AGENDADO)
    return con, j


def test_vetar(cfg, raiz_teste, monkeypatch):
    con, j = _agendado(cfg)
    pasta = cfg.dados / "jobs" / j.id
    pasta.mkdir(parents=True)
    (pasta / "metadados.json").write_text('{"sintetico": true}')
    yt = MagicMock()
    monkeypatch.setattr(cli.publicador, "youtube_real", lambda c: (lambda canal: yt))
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "vetar", j.id])
    assert r.exit_code == 0, r.output
    assert fila.obter(con, j.id).estado == Estado.VETADO
    assert yt.videos.return_value.update.call_args.kwargs["body"]["id"] == "abc"
    corpo = yt.videos.return_value.update.call_args.kwargs["body"]
    assert corpo["status"]["containsSyntheticMedia"] is True


def test_vetar_so_agendado(cfg, raiz_teste):
    con = conectar(cfg.dados / "autocanal.db")
    j = fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "vetar", j.id])
    assert r.exit_code == 1


def test_auth_imprime_so_titulo(cfg, raiz_teste, monkeypatch):
    creds = MagicMock()
    creds.to_json.return_value = "SEGREDO"
    monkeypatch.setattr(cli.oauth, "autorizar", lambda *a, **k: creds)
    monkeypatch.setattr(cli.oauth, "cliente", lambda c: "yt")
    monkeypatch.setattr(cli.oauth, "canal_autenticado", lambda yt: ("UC1", "Meu Canal"))
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "auth", "teste"])
    assert r.exit_code == 0 and "Meu Canal" in r.output and "SEGREDO" not in r.output
    row = conectar(cfg.dados / "autocanal.db").execute("SELECT * FROM canais_estado").fetchone()
    assert row["auth_ok"] == 1 and row["youtube_channel_id"] == "UC1"


def test_publicar_job(cfg, raiz_teste, monkeypatch):
    chamado = {}

    def falso(cfg_, con, agora, *, youtube_por_canal, job_id=None):
        chamado["job_id"] = job_id
        return ["v1"]

    monkeypatch.setattr(cli.publicador, "publicar_aprovados", falso)
    monkeypatch.setattr(cli.publicador, "youtube_real", lambda c: None)
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "publicar", "--job", "x"])
    assert r.exit_code == 0 and chamado["job_id"] == "x" and "v1" in r.output


def test_auth_verificacao_falha_remove_token(cfg, raiz_teste, monkeypatch):
    from autocanal.youtube import oauth

    token = cfg.dados / "tokens" / "teste.json"

    def autorizar(cs, destino, porta=8080):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text("SEGREDO")
        return MagicMock()

    def sem_canal(yt):
        raise oauth.AuthExpirada("conta sem canal")

    monkeypatch.setattr(cli.oauth, "autorizar", autorizar)
    monkeypatch.setattr(cli.oauth, "cliente", lambda c: "yt")
    monkeypatch.setattr(cli.oauth, "canal_autenticado", sem_canal)
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "auth", "teste"])
    assert r.exit_code == 1 and not token.exists() and "SEGREDO" not in r.output
    assert conectar(cfg.dados / "autocanal.db").execute("SELECT * FROM canais_estado").fetchall() == []
