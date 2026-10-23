import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from autocanal import agendador, cli, fila, planejador
from autocanal.db import conectar
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS

runner = CliRunner()
SP = ZoneInfo("America/Sao_Paulo")
ETAPAS = FORMATOS["short"].etapas
FUTURO = datetime.now(UTC) + timedelta(days=5)


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def rodar(raiz_teste):
    def _rodar(*args):
        return runner.invoke(cli.app, ["--raiz", str(raiz_teste), *args])
    return _rodar


@pytest.fixture
def producao(monkeypatch):
    """Substitui produtor.produzir: registra os jobs e o estado de cada um na chamada."""
    chamadas = []

    def produzir(cfg, con, job_id):
        chamadas.append((job_id, fila.obter(con, job_id).estado))
        return fila.obter(con, job_id).estado

    monkeypatch.setattr(cli.produtor, "produzir", produzir)
    return chamadas


def _job(con, *estados, publish_at=FUTURO, erro=None):
    j = fila.criar_job(con, "teste", "short", "tema", publish_at)
    for e in estados:
        fila.mudar_estado(con, j.id, e, erro=erro if e == Estado.FALHOU else None)
    return fila.obter(con, j.id)


QUARENTENA = (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.QUARENTENA)


def _pasta(cfg, job_id) -> Path:
    p = cfg.dados / "jobs" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- trava ---------------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["produzir"], ["revisar", "x"], ["aprovar", "x"], ["descartar", "x"], ["planejar"],
    ["publicar"], ["novo", "teste", "short", "tema"],
])
def test_comandos_manuais_respeitam_a_trava(cfg, rodar, args, producao):
    with agendador.trava(cfg.dados / "tick.lock"):
        r = rodar(*args)
    assert r.exit_code == 2
    assert "outro processo do autocanal está rodando" in r.output
    assert producao == []


def test_status_nao_usa_trava(cfg, con, rodar):
    _job(con)
    with agendador.trava(cfg.dados / "tick.lock"):
        r = rodar("status")
    assert r.exit_code == 0, r.output
    assert "planejado: 1" in r.output


# --- status / novo -------------------------------------------------------------------

def test_status_mostra_planejamento_adiado(cfg, rodar):
    planejador.adiar_planejamento(cfg, datetime.now(UTC))
    r = rodar("status")
    assert r.exit_code == 0 and "Planejamento automático adiado" in r.output


def test_status_contagens_e_proximos(con, rodar):
    a = _job(con, publish_at=datetime(2026, 11, 2, 15, tzinfo=UTC))
    _job(con, Estado.DESCARTADO)
    for i in range(11):
        _job(con, publish_at=datetime(2026, 12, 1, 15, tzinfo=UTC) + timedelta(days=i))
    r = rodar("status")
    assert r.exit_code == 0, r.output
    assert "planejado: 12" in r.output and "descartado: 1" in r.output
    proximos = r.output.split("Próximos", 1)[1].strip().splitlines()[1:]
    assert len(proximos) == 10
    assert a.id in proximos[0] and "02/11/2026 12:00" in proximos[0]


def test_novo_no_proximo_slot(cfg, con, rodar):
    r = rodar("novo", "teste", "short", "por que o céu é azul")
    assert r.exit_code == 0, r.output
    (j,) = fila.listar(con)
    assert j.tema == "por que o céu é azul" and j.estado == Estado.PLANEJADO
    local = j.publish_at.astimezone(SP)
    assert local.weekday() in (0, 2, 4) and (local.hour, local.minute) == (12, 0)
    assert j.publish_at > datetime.now(UTC) + timedelta(hours=2)
    assert j.id in r.output
    temas = [x["tema"] for x in con.execute("SELECT tema FROM temas")]
    assert temas == ["por que o céu é azul"]
    # o segundo vai para o slot seguinte
    assert rodar("novo", "teste", "short", "outro").exit_code == 0
    assert fila.listar(con)[1].publish_at > j.publish_at


def test_novo_com_quando(con, rodar):
    quando = (datetime.now(SP) + timedelta(days=3)).replace(hour=18, minute=30)
    r = rodar("novo", "teste", "short", "tema", "--quando", quando.strftime("%Y-%m-%d %H:%M"))
    assert r.exit_code == 0, r.output
    assert fila.listar(con)[0].publish_at == quando.replace(second=0, microsecond=0).astimezone(UTC)


@pytest.mark.parametrize("args", [
    ["nada", "short", "t"], ["teste", "longo", "t"], ["teste", "short", "  "],
    ["teste", "short", "t", "--quando", "amanhã"],
    ["teste", "short", "t", "--quando", "2020-01-01 10:00"],
])
def test_novo_rejeita_entrada_invalida(con, rodar, args):
    r = rodar("novo", *args)
    assert r.exit_code == 1
    assert fila.listar(con) == []


# --- planejar / tick -----------------------------------------------------------------

def test_planejar_usa_claude(cfg, rodar, monkeypatch):
    recebido = {}

    def planejar(cfg_, con, agora, *, sugerir_temas):
        recebido["sugerir"] = sugerir_temas
        return [fila.criar_job(con, "teste", "short", "t", FUTURO)]

    monkeypatch.setattr(cli.planejador, "planejar", planejar)
    monkeypatch.setattr(cli.planejador, "sugerir_temas_claude", lambda cfg_, con: "claude")
    r = rodar("planejar")
    assert r.exit_code == 0, r.output
    assert recebido["sugerir"] == "claude" and "1 job" in r.output


def test_tick_liga_as_fases_reais(cfg, rodar, monkeypatch):
    recebido = {}

    def tick(cfg_, con, agora, **fases):
        recebido.update(fases, agora=agora)
        return agendador.ResumoTick(False, 2, "j1", ["v1"], [], None)

    monkeypatch.setattr(cli.agendador, "tick", tick)
    monkeypatch.setattr(cli.publicador, "youtube_real", lambda c: "yt")
    r = rodar("tick")
    assert r.exit_code == 0, r.output
    assert "planejados: 2" in r.output and "j1" in r.output and "v1" in r.output
    assert recebido["produzir_fn"] is cli.produtor.produzir
    assert abs(recebido["agora"] - datetime.now(UTC)) < timedelta(minutes=1)

    chamadas = {}

    def publicar(cfg_, con, agora, *, youtube_por_canal):
        chamadas["publicar"] = (agora, youtube_por_canal)
        return ["v9"]

    def sincronizar(cfg_, con, agora, *, youtube_por_canal):
        chamadas["sincronizar"] = (agora, youtube_por_canal)
        return []

    monkeypatch.setattr(cli.publicador, "publicar_aprovados", publicar)
    monkeypatch.setattr(cli.publicador, "sincronizar_publicados", sincronizar)
    quando = datetime(2026, 10, 26, 12, tzinfo=UTC)
    assert recebido["publicar_fn"](cfg, None, quando) == ["v9"]
    recebido["sincronizar_fn"](cfg, None, quando)
    assert chamadas == {"publicar": (quando, "yt"), "sincronizar": (quando, "yt")}


def test_tick_configura_log_info(cfg, rodar, monkeypatch):
    niveis = []
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: niveis.append(kw["level"]))
    monkeypatch.setattr(cli.agendador, "tick",
                        lambda *a, **k: agendador.ResumoTick(False, 0, None, [], [], None))
    monkeypatch.setattr(cli.publicador, "youtube_real", lambda c: "yt")
    assert rodar("tick").exit_code == 0
    assert niveis == [logging.INFO]


def test_tick_pulado_pela_trava_sai_com_2(cfg, rodar):
    with agendador.trava(cfg.dados / "tick.lock"):
        r = rodar("tick")
    assert r.exit_code == 2
    assert "outro processo do autocanal está rodando" in r.output


# --- produzir / revisar --------------------------------------------------------------

def test_produzir_sem_job_pega_o_proximo(con, rodar, producao):
    tarde = _job(con, publish_at=FUTURO + timedelta(days=2))
    cedo = _job(con, publish_at=FUTURO)
    r = rodar("produzir")
    assert r.exit_code == 0, r.output
    assert producao == [(cedo.id, Estado.PLANEJADO)]
    assert tarde.id != cedo.id


def test_produzir_fila_vazia(rodar, producao):
    r = rodar("produzir")
    assert r.exit_code == 0 and "nada para produzir" in r.output and producao == []


def test_produzir_falhou_reseta_etapas_com_erro(cfg, con, rodar, producao):
    j = _job(con, Estado.PRODUZINDO)
    fila.marcar_etapa(con, j.id, "roteiro", "ok")
    for _ in range(3):
        fila.marcar_etapa(con, j.id, "narracao", "erro", "tts caiu")
    fila.mudar_estado(con, j.id, Estado.FALHOU, erro="narracao: tts caiu")
    r = rodar("produzir", j.id)
    assert r.exit_code == 0, r.output
    assert producao == [(j.id, Estado.PRODUZINDO)]
    assert fila.estado_etapa(con, j.id, "roteiro") == "ok"
    assert fila.estado_etapa(con, j.id, "narracao") is None
    assert fila.tentativas_etapa(con, j.id, "narracao") == 0


def test_produzir_falhou_com_tudo_pronto_sugere_aprovar(cfg, con, rodar, producao):
    j = _job(con, Estado.PRODUZINDO)
    for n in ETAPAS:
        fila.marcar_etapa(con, j.id, n, "ok")
    fila.mudar_estado(con, j.id, Estado.FALHOU, erro="youtube: rejected")
    r = rodar("produzir", j.id)
    assert r.exit_code == 1 and "autocanal aprovar" in r.output
    assert producao == [] and fila.obter(con, j.id).estado == Estado.FALHOU


def test_produzir_quarentena_refaz_da_etapa_da_ultima_rodada(cfg, con, rodar, producao):
    j = _job(con, *QUARENTENA)
    fila.atualizar(con, j.id, tentativas_revisao=2)
    for n in ETAPAS:
        fila.marcar_etapa(con, j.id, n, "ok")
    pasta = _pasta(cfg, j.id)
    (pasta / "revisao_1.json").write_text(json.dumps(
        {"problemas": ["velho"], "etapa_para_refazer": "roteiro"}))
    (pasta / "revisao_2.json").write_text(json.dumps(
        {"problemas": ["título fraco"], "etapa_para_refazer": "metadados"}))
    r = rodar("produzir", j.id)
    assert r.exit_code == 0, r.output
    assert producao == [(j.id, Estado.PRODUZINDO)]
    assert fila.obter(con, j.id).tentativas_revisao == 0
    assert json.loads((pasta / "problemas_revisao.json").read_text()) == {
        "problemas": ["título fraco"]}
    assert [fila.estado_etapa(con, j.id, n) for n in ETAPAS] == [
        "ok", "ok", "ok", "ok", "ok", None, None]


def test_produzir_quarentena_sem_etapa_refaz_do_roteiro(cfg, con, rodar, producao):
    j = _job(con, *QUARENTENA)
    fila.atualizar(con, j.id, tentativas_revisao=2)
    for n in ETAPAS:
        fila.marcar_etapa(con, j.id, n, "ok")
    (_pasta(cfg, j.id) / "revisao_2.json").write_text(json.dumps(
        {"problemas": ["x"], "etapa_para_refazer": None}))
    assert rodar("produzir", j.id).exit_code == 0
    assert all(fila.estado_etapa(con, j.id, n) is None for n in ETAPAS)


def test_produzir_estado_sem_producao(con, rodar, producao):
    j = _job(con, Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO)
    r = rodar("produzir", j.id)
    assert r.exit_code == 1 and "aprovado" in r.output and producao == []
    assert rodar("produzir", "nao-existe").exit_code == 1


def test_revisar_quarentena(cfg, con, rodar, producao):
    j = _job(con, *QUARENTENA)
    fila.atualizar(con, j.id, tentativas_revisao=2)
    for n in ETAPAS:
        fila.marcar_etapa(con, j.id, n, "ok")
    r = rodar("revisar", j.id)
    assert r.exit_code == 0, r.output
    assert producao == [(j.id, Estado.PRODUZINDO)]
    assert fila.obter(con, j.id).tentativas_revisao == 0
    assert [fila.estado_etapa(con, j.id, n) for n in ETAPAS] == ["ok"] * 6 + [None]


def test_revisar_em_revisao_so_reseta_a_revisao(con, rodar, producao):
    j = _job(con, Estado.PRODUZINDO, Estado.EM_REVISAO)
    for n in ETAPAS:
        fila.marcar_etapa(con, j.id, n, "ok" if n != "revisao" else "erro")
    fila.atualizar(con, j.id, tentativas_revisao=1)
    assert rodar("revisar", j.id).exit_code == 0
    assert producao == [(j.id, Estado.EM_REVISAO)]
    assert fila.estado_etapa(con, j.id, "revisao") is None
    assert fila.obter(con, j.id).tentativas_revisao == 1


def test_revisar_estado_invalido(con, rodar, producao):
    j = _job(con)
    r = rodar("revisar", j.id)
    assert r.exit_code == 1 and producao == []


# --- aprovar / descartar -------------------------------------------------------------

def test_aprovar_quarentena_leva_declaracao_de_midia_sintetica(cfg, con, rodar):
    j = _job(con, *QUARENTENA)
    fila.atualizar(con, j.id, tentativas_revisao=2)
    pasta = _pasta(cfg, j.id)
    (pasta / "metadados.json").write_text(json.dumps({"titulo": "T", "sintetico": False}))
    (pasta / "revisao_1.json").write_text(json.dumps(
        {"origem": "revisor", "aprovado": False, "contains_synthetic_media": True}))
    (pasta / "revisao_2.json").write_text(json.dumps({"origem": "checagens", "aprovado": False}))
    r = rodar("aprovar", j.id)
    assert r.exit_code == 0, r.output
    assert fila.obter(con, j.id).estado == Estado.APROVADO
    assert json.loads((pasta / "metadados.json").read_text())["sintetico"] is True


def test_aprovar_falhou_exige_video_e_metadados(cfg, con, rodar):
    j = _job(con, Estado.PRODUZINDO, Estado.FALHOU, erro="youtube: rejected")
    r = rodar("aprovar", j.id)
    assert r.exit_code == 1 and f"autocanal produzir {j.id}" in r.output
    assert fila.obter(con, j.id).estado == Estado.FALHOU
    pasta = _pasta(cfg, j.id)
    (pasta / "video.mp4").write_bytes(b"v")
    (pasta / "metadados.json").write_text(json.dumps({"titulo": "T", "sintetico": True}))
    _revisao(con, j.id, 1, True)
    assert rodar("aprovar", j.id).exit_code == 0
    assert fila.obter(con, j.id).estado == Estado.APROVADO
    assert json.loads((pasta / "metadados.json").read_text())["sintetico"] is True


def _revisao(con, job_id, rodada, aprovado):
    con.execute(
        "INSERT INTO revisoes (job_id, rodada, aprovado, motivos_json, etapa_para_refazer,"
        " criado_em) VALUES (?, ?, ?, '[]', NULL, '2026-10-23T00:00:00+00:00')",
        (job_id, rodada, int(aprovado)),
    )
    con.commit()


def test_aprovar_falhou_exige_revisao_aprovada(cfg, con, rodar):
    j = _job(con, Estado.PRODUZINDO, Estado.FALHOU, erro="youtube: rejected")
    pasta = _pasta(cfg, j.id)
    (pasta / "video.mp4").write_bytes(b"v")
    (pasta / "metadados.json").write_text(json.dumps({"titulo": "T"}))
    r = rodar("aprovar", j.id)  # nunca revisado
    assert r.exit_code == 1 and f"autocanal revisar {j.id}" in r.output
    _revisao(con, j.id, 1, True)
    _revisao(con, j.id, 2, False)  # a última rodada reprovou
    r = rodar("aprovar", j.id)
    assert r.exit_code == 1 and f"autocanal revisar {j.id}" in r.output
    assert fila.obter(con, j.id).estado == Estado.FALHOU


def test_aprovar_outro_estado(con, rodar):
    j = _job(con)
    r = rodar("aprovar", j.id)
    assert r.exit_code == 1 and fila.obter(con, j.id).estado == Estado.PLANEJADO


def test_descartar(con, rodar):
    j = _job(con, *QUARENTENA)
    assert rodar("descartar", j.id).exit_code == 0
    assert fila.obter(con, j.id).estado == Estado.DESCARTADO


def test_descartar_agendado_explica(con, rodar):
    j = _job(con, Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO)
    fila.atualizar(con, j.id, youtube_video_id="abc")
    fila.mudar_estado(con, j.id, Estado.AGENDADO)
    r = rodar("descartar", j.id)
    assert r.exit_code == 1
    assert "agendado" in r.output and f"autocanal vetar {j.id}" in r.output
    assert fila.obter(con, j.id).estado == Estado.AGENDADO


# --- instalar-timer ------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    casa.mkdir()
    monkeypatch.setenv("HOME", str(casa))
    systemctl = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kw: systemctl.append(argv))
    return casa, systemctl


def test_instalar_timer_so_escreve(raiz_teste, rodar, home):
    casa, systemctl = home
    r = rodar("instalar-timer")
    assert r.exit_code == 0, r.output
    unidades = casa / ".config" / "systemd" / "user"
    servico = (unidades / "autocanal.service").read_text()
    assert f'ExecStart="{raiz_teste}/.venv/bin/autocanal" --raiz "{raiz_teste}" tick' in servico
    assert f"Environment=PATH={Path.home()}/.local/bin:/usr/local/bin:/usr/bin:/bin" in servico
    assert "TimeoutStartSec=2h" in servico
    assert "Type=oneshot" in servico
    assert "SuccessExitStatus=2" in servico  # tick pulado pela trava não é falha
    timer = (unidades / "autocanal.timer").read_text()
    assert "OnCalendar=*:0/15" in timer and "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    assert systemctl == []
    assert "--ativar" in r.output


def test_instalar_timer_ativar(rodar, home):
    _, systemctl = home
    assert rodar("instalar-timer", "--ativar").exit_code == 0
    assert systemctl[-1] == ["systemctl", "--user", "enable", "--now", "autocanal.timer"]
