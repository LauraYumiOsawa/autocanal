import fcntl
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from autocanal import agendador, fila, ia, planejador, produtor
from autocanal.ia import ErroIA, LimiteUso
from autocanal.db import conectar
from autocanal.fila import Estado

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 10, 26, 8, 0, tzinfo=SP)  # segunda; slots seg/qua/sex 12:00


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def cfg_nicho(cfg):
    cfg.canais["teste"].nicho = "curiosidades de ciência"
    return cfg


class Fases:
    """Fases falsas do tick: registram a ordem e o que receberam."""

    def __init__(self):
        self.ordem: list[str] = []
        self.horarios: dict[str, datetime] = {}
        self.falhar: set[str] = set()

    def _marca(self, nome, quando=None):
        self.ordem.append(nome)
        if quando is not None:
            self.horarios[nome] = quando
        if nome in self.falhar:
            raise RuntimeError(f"{nome} quebrou")

    def planejar(self, cfg, con, agora):
        self._marca("planejar", agora)
        return [fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))]

    def produzir(self, cfg, con, job_id):
        self._marca("produzir")
        self.horarios["produzido"] = job_id
        return Estado.PRODUZINDO

    def publicar(self, cfg, con, agora):
        self._marca("publicar", agora)
        return ["vid1"]

    def sincronizar(self, cfg, con, agora):
        self._marca("sincronizar", agora)
        return ["job-publicado"]

    def resumo(self, cfg, con, dia):
        self._marca("resumo")
        caminho = cfg.dados / "relatorios" / f"{dia.isoformat()}.md"
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text("resumo", encoding="utf-8")
        return caminho

    def kwargs(self):
        return dict(planejar_fn=self.planejar, produzir_fn=self.produzir,
                    publicar_fn=self.publicar, sincronizar_fn=self.sincronizar,
                    resumo_fn=self.resumo)


def _tick(cfg, con, fases, agora=AGORA):
    agora = agora.astimezone(UTC)
    return agendador.tick(cfg, con, agora, relogio=lambda: agora, **fases.kwargs())


def test_tick_ordem_das_fases(cfg_nicho, con):
    fases = Fases()
    r = _tick(cfg_nicho, con, fases)
    assert fases.ordem == ["publicar", "sincronizar", "resumo", "planejar", "produzir"]
    job = fila.listar(con)[0]
    assert fases.horarios["produzido"] == job.id
    assert r == agendador.ResumoTick(
        pulou_por_trava=False, planejados=1, produzido=job.id, enviados=["vid1"],
        publicados=["job-publicado"], resumo=cfg_nicho.dados / "relatorios" / "2026-10-26.md",
    )


def test_tick_concorrente_sai_pela_trava(cfg_nicho, con):
    fases = Fases()
    caminho = cfg_nicho.dados / "tick.lock"
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(caminho, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _tick(cfg_nicho, con, fases)
    finally:
        os.close(fd)
    assert r.pulou_por_trava is True
    assert fases.ordem == []
    # trava solta: o próximo tick roda
    assert _tick(cfg_nicho, con, fases).pulou_por_trava is False


def test_trava_exclusiva_e_liberada(tmp_path):
    caminho = tmp_path / "sub" / "x.lock"
    with agendador.trava(caminho):
        with pytest.raises(agendador.TravaOcupada):
            with agendador.trava(caminho):
                pass
    with agendador.trava(caminho):
        pass


def test_tick_resumo_uma_vez_por_dia(cfg_nicho, con):
    fases = Fases()

    def resumos(agora):
        antes = fases.ordem.count("resumo")
        _tick(cfg_nicho, con, fases, agora)
        return fases.ordem.count("resumo") - antes

    assert resumos(datetime(2026, 10, 26, 6, 59, tzinfo=SP)) == 0  # antes das 07:00
    assert resumos(datetime(2026, 10, 26, 7, 0, tzinfo=SP)) == 1
    assert resumos(datetime(2026, 10, 26, 7, 15, tzinfo=SP)) == 0
    assert resumos(datetime(2026, 10, 26, 23, 30, tzinfo=SP)) == 0  # já é dia 27 em UTC
    assert resumos(datetime(2026, 10, 27, 9, 0, tzinfo=SP)) == 1
    assert sorted(p.name for p in (cfg_nicho.dados / "relatorios").iterdir()) == [
        "2026-10-26.md", "2026-10-27.md",
    ]


def test_tick_so_planeja_com_fila_curta(cfg_nicho, con):
    # 2 slots nos próximos 3 dias (seg e qua 12:00): 2 jobs pendentes bastam
    for dia in (26, 28):
        fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, dia, 15, tzinfo=UTC))
    fases = Fases()
    _tick(cfg_nicho, con, fases)
    assert "planejar" not in fases.ordem
    # quarentena e falhou não contam como fila: precisam de Laura
    j = fila.listar(con)[1]
    fila.mudar_estado(con, j.id, Estado.PRODUZINDO)
    fila.mudar_estado(con, j.id, Estado.FALHOU)
    fases = Fases()
    _tick(cfg_nicho, con, fases)
    assert "planejar" in fases.ordem


def test_tick_canal_sem_nicho_nao_planeja(cfg, con):
    fases = Fases()
    _tick(cfg, con, fases)
    assert "planejar" not in fases.ordem


def test_tick_produz_o_proximo_inclusive_em_revisao(cfg_nicho, con):
    preso = fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 28, 15, tzinfo=UTC))
    fila.mudar_estado(con, preso.id, Estado.PRODUZINDO)
    fila.mudar_estado(con, preso.id, Estado.EM_REVISAO)
    fases = Fases()
    r = _tick(cfg_nicho, con, fases)
    assert r.produzido == preso.id and fases.ordem.count("produzir") == 1


def test_tick_fase_com_erro_nao_impede_as_outras(cfg_nicho, con, caplog):
    fases = Fases()
    fases.falhar = {"planejar", "produzir", "publicar"}
    fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    r = _tick(cfg_nicho, con, fases)
    assert fases.ordem == ["publicar", "sincronizar", "resumo", "planejar", "produzir"]
    assert r.enviados == [] and r.publicados == ["job-publicado"]
    assert "publicar quebrou" in caplog.text
    with agendador.trava(cfg_nicho.dados / "tick.lock"):  # trava foi solta
        pass


def test_tick_produz_com_provedor_pausado(cfg_nicho, con):
    # o job anda até a primeira etapa que depende do provedor pausado (ia.chamar recusa)
    job = fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    ia.pausar(cfg_nicho, "claude", "limite de uso", AGORA.astimezone(UTC) - timedelta(minutes=30))
    fases = Fases()
    r = _tick(cfg_nicho, con, fases)
    assert "produzir" in fases.ordem
    assert r.produzido == job.id


def test_tick_nao_planeja_com_provedor_de_temas_pausado(cfg_nicho, con):
    ia.pausar(cfg_nicho, "claude", "limite de uso", AGORA.astimezone(UTC) - timedelta(minutes=30))
    fases = Fases()
    _tick(cfg_nicho, con, fases)  # pausa vai até agora + 30 min
    assert "planejar" not in fases.ordem
    fases = Fases()
    _tick(cfg_nicho, con, fases, AGORA + timedelta(minutes=31))
    assert "planejar" in fases.ordem


def test_tick_pula_producao_durante_pausa_do_sistema(cfg_nicho, con):
    fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    meia_hora_atras = AGORA.astimezone(UTC) - timedelta(minutes=30)
    produtor.pausar_sistema(cfg_nicho, "docker parado", meia_hora_atras)
    fases = Fases()
    _tick(cfg_nicho, con, fases)
    assert "produzir" not in fases.ordem and "planejar" not in fases.ordem
    fases = Fases()
    _tick(cfg_nicho, con, fases, AGORA + timedelta(minutes=31))
    assert "produzir" in fases.ordem


def test_tick_limite_no_planejamento_nao_para_a_producao(cfg_nicho, con):
    job = fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    fases = Fases()

    def planejar(cfg, con, agora):
        fases.ordem.append("planejar")
        raise LimiteUso("claude", "usage limit reached")

    agora = AGORA.astimezone(UTC)
    r = agendador.tick(cfg_nicho, con, agora, relogio=lambda: agora,
                       **{**fases.kwargs(), "planejar_fn": planejar})
    assert fases.ordem == ["publicar", "sincronizar", "resumo", "planejar", "produzir"]
    assert r.produzido == job.id
    assert produtor.pausa_sistema(cfg_nicho) is None


def test_tick_publica_com_relogio_atualizado(cfg_nicho, con):
    agora = AGORA.astimezone(UTC)
    t = [agora]
    fila.criar_job(con, "teste", "short", "t", datetime(2026, 10, 26, 15, tzinfo=UTC))
    fases = Fases()
    publicar_original = fases.publicar

    def publicar_demorado(cfg, con, quando):
        r = publicar_original(cfg, con, quando)
        t[0] += timedelta(minutes=20)  # o envio levou 20 minutos
        return r

    agendador.tick(cfg_nicho, con, agora, relogio=lambda: t[0],
                   **{**fases.kwargs(), "publicar_fn": publicar_demorado})
    depois = agora + timedelta(minutes=20)
    assert fases.horarios["publicar"] == agora
    assert fases.horarios["sincronizar"] == depois
    assert fases.horarios["planejar"] == agora  # o planejamento usa o início do tick


def test_tick_relogio_padrao_e_o_real(cfg_nicho, con):
    fases = Fases()
    agendador.tick(cfg_nicho, con, datetime.now(UTC), **fases.kwargs())
    assert abs(fases.horarios["publicar"] - datetime.now(UTC)) < timedelta(minutes=1)


def test_tick_adia_planejamento_apos_falha(cfg_nicho, con):
    pedidos = []

    def sugerir(canal, formato, n):
        pedidos.append(n)
        raise ErroIA("claude", "falhou após 3 tentativas")

    def planejar(cfg, con, agora):
        return planejador.planejar(cfg, con, agora, sugerir_temas=sugerir)

    kwargs = {**Fases().kwargs(), "planejar_fn": planejar}
    inicio = AGORA.astimezone(UTC)
    for minutos, esperados in ((0, 1), (4 * 60 - 1, 1), (4 * 60, 2)):
        agora = inicio + timedelta(minutes=minutos)
        agendador.tick(cfg_nicho, con, agora, relogio=lambda: agora, **kwargs)
        assert len(pedidos) == esperados, minutos
    assert planejador.planejar_apos(cfg_nicho) == inicio + timedelta(hours=8)
