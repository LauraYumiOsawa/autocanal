from datetime import UTC, datetime

import pytest

from autocanal import etapas, fila
from autocanal.claude import LimiteUsoClaude
from autocanal.db import conectar
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS
from autocanal.produtor import MAX_TENTATIVAS_ETAPA, produzir

PUBLICAR = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
ETAPAS = FORMATOS["short"].etapas


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def job(con):
    return fila.criar_job(con, "teste", "short", "tema", PUBLICAR)


def _registro(chamadas: list[str], revisao=None, **substitutas):
    def falsa(nome):
        def etapa(ctx):
            chamadas.append(nome)
        return etapa

    reg = {n: falsa(n) for n in ETAPAS}
    reg.update(substitutas)

    def revisao_aprova(ctx):
        chamadas.append("revisao")
        fila.mudar_estado(ctx.con, ctx.job.id, Estado.APROVADO)

    reg["revisao"] = revisao or revisao_aprova
    return reg


def test_max_tentativas():
    assert MAX_TENTATIVAS_ETAPA == 3


def test_produzir_executa_em_ordem_e_pula_concluidas(cfg, con, job):
    chamadas: list[str] = []
    estados: list[Estado] = []

    def revisao(ctx):
        estados.append(fila.obter(ctx.con, ctx.job.id).estado)
        chamadas.append("revisao")
        fila.mudar_estado(ctx.con, ctx.job.id, Estado.APROVADO)

    fila.marcar_etapa(con, job.id, "roteiro", "ok")
    estado = produzir(cfg, con, job.id, registro=_registro(chamadas, revisao))
    assert estado == Estado.APROVADO
    assert fila.obter(con, job.id).estado == Estado.APROVADO
    assert chamadas == ["narracao", "legendas", "visuais", "render", "metadados", "revisao"]
    assert estados == [Estado.EM_REVISAO]
    assert all(fila.estado_etapa(con, job.id, n) == "ok" for n in ETAPAS)
    assert (cfg.dados / "jobs" / job.id).is_dir()


def test_produzir_falha_apos_tres_tentativas(cfg, con, job):
    chamadas: list[str] = []

    def quebra(ctx):
        chamadas.append("narracao")
        raise RuntimeError("tts caiu")

    reg = _registro(chamadas, narracao=quebra)
    assert produzir(cfg, con, job.id, registro=reg) == Estado.PRODUZINDO
    assert produzir(cfg, con, job.id, registro=reg) == Estado.PRODUZINDO
    assert fila.tentativas_etapa(con, job.id, "narracao") == 2
    assert produzir(cfg, con, job.id, registro=reg) == Estado.FALHOU
    j = fila.obter(con, job.id)
    assert j.estado == Estado.FALHOU
    assert "narracao" in j.erro and "tts caiu" in j.erro
    assert chamadas == ["roteiro", "narracao", "narracao", "narracao"]
    assert fila.estado_etapa(con, job.id, "narracao") == "erro"
    # job que já falhou não é tocado
    assert produzir(cfg, con, job.id, registro=reg) == Estado.FALHOU
    assert len(chamadas) == 4


def test_produzir_limite_uso_nao_conta_tentativa(cfg, con, job):
    chamadas: list[str] = []

    def limite(ctx):
        chamadas.append("roteiro")
        raise LimiteUsoClaude("Claude usage limit reached")

    estado = produzir(cfg, con, job.id, registro=_registro(chamadas, roteiro=limite))
    assert estado == Estado.PRODUZINDO
    assert fila.obter(con, job.id).estado == Estado.PRODUZINDO
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 0
    assert fila.estado_etapa(con, job.id, "roteiro") != "ok"
    assert chamadas == ["roteiro"]


def test_produzir_refazer_volta_a_produzir(cfg, con, job):
    chamadas: list[str] = []
    rodadas = []

    def revisao(ctx):
        chamadas.append("revisao")
        rodadas.append(1)
        if len(rodadas) == 1:
            fila.resetar_etapas(ctx.con, ctx.job.id, ["metadados"])
            fila.mudar_estado(ctx.con, ctx.job.id, Estado.PRODUZINDO)
        else:
            fila.mudar_estado(ctx.con, ctx.job.id, Estado.APROVADO)

    estado = produzir(cfg, con, job.id, registro=_registro(chamadas, revisao))
    assert estado == Estado.APROVADO
    assert chamadas == [
        "roteiro", "narracao", "legendas", "visuais", "render", "metadados", "revisao",
        "metadados", "revisao",
    ]
    assert fila.estado_etapa(con, job.id, "revisao") == "ok"


def test_produzir_retoma_em_revisao(cfg, con, job):
    chamadas: list[str] = []
    for n in ETAPAS[:-1]:
        fila.marcar_etapa(con, job.id, n, "ok")
    for e in (Estado.PRODUZINDO, Estado.EM_REVISAO):
        fila.mudar_estado(con, job.id, e)
    assert produzir(cfg, con, job.id, registro=_registro(chamadas)) == Estado.APROVADO
    assert chamadas == ["revisao"]


def test_produzir_etapa_nao_implementada(cfg, con, job, monkeypatch):
    chamadas: list[str] = []
    reg = _registro(chamadas)
    del reg["render"]
    monkeypatch.setattr(etapas, "REGISTRO", reg)
    for _ in range(MAX_TENTATIVAS_ETAPA):
        produzir(cfg, con, job.id)
    j = fila.obter(con, job.id)
    assert j.estado == Estado.FALHOU
    assert j.erro == "render: etapa não implementada: render"
    assert chamadas == ["roteiro", "narracao", "legendas", "visuais"]


def test_produzir_canal_desconhecido_falha(cfg, con):
    job = fila.criar_job(con, "sumido", "short", "tema", PUBLICAR)
    assert produzir(cfg, con, job.id, registro={}) == Estado.FALHOU
    assert "sumido" in fila.obter(con, job.id).erro
