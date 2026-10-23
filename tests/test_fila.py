import re
from datetime import UTC, datetime, timedelta

import pytest

from autocanal import fila
from autocanal.db import conectar
from autocanal.fila import Estado, TransicaoInvalida


@pytest.fixture
def con(tmp_path):
    return conectar(tmp_path / "dados" / "autocanal.db")


def _novo(con, quando=None, tema="céu azul"):
    quando = quando or datetime(2026, 10, 25, 15, tzinfo=UTC)
    return fila.criar_job(con, "teste", "short", tema, quando)


def test_criar_e_obter(con):
    quando = datetime(2026, 10, 25, 15, tzinfo=UTC)
    job = _novo(con, quando)
    lido = fila.obter(con, job.id)
    assert lido.estado == Estado.PLANEJADO
    assert lido.publish_at == quando
    assert lido.publish_at.tzinfo is not None
    assert lido.criado_em.tzinfo is not None
    assert re.match(r"^20261025-teste-short-[0-9a-f]{4}$", lido.id)
    assert lido.briefing == {}


def test_criar_rejeita_data_ingenua(con):
    with pytest.raises(ValueError):
        fila.criar_job(con, "teste", "short", "x", datetime(2026, 10, 25, 15))


def test_obter_inexistente(con):
    with pytest.raises(KeyError):
        fila.obter(con, "nada")


def test_transicao_valida_e_invalida(con):
    job = _novo(con)
    assert fila.mudar_estado(con, job.id, Estado.PRODUZINDO).estado == Estado.PRODUZINDO
    with pytest.raises(TransicaoInvalida):
        fila.mudar_estado(con, job.id, Estado.PUBLICADO)
    assert fila.obter(con, job.id).estado == Estado.PRODUZINDO


def test_mudar_estado_grava_erro(con):
    job = _novo(con)
    fila.mudar_estado(con, job.id, Estado.PRODUZINDO)
    assert fila.mudar_estado(con, job.id, Estado.FALHOU, erro="boom").erro == "boom"


def test_todas_as_transicoes_declaradas():
    assert set(fila.TRANSICOES) == set(Estado)


def test_proximo_para_produzir_ordem(con):
    base = datetime(2026, 10, 25, 15, tzinfo=UTC)
    tarde = _novo(con, base + timedelta(days=2))
    cedo = _novo(con, base + timedelta(days=1))
    aprovado = _novo(con, base)
    fila.mudar_estado(con, aprovado.id, Estado.PRODUZINDO)
    fila.mudar_estado(con, aprovado.id, Estado.EM_REVISAO)
    fila.mudar_estado(con, aprovado.id, Estado.APROVADO)
    assert fila.proximo_para_produzir(con).id == cedo.id
    assert tarde.id != cedo.id


def test_proximo_vazio(con):
    assert fila.proximo_para_produzir(con) is None


def test_listar_filtros_e_ordem(con):
    base = datetime(2026, 10, 25, 15, tzinfo=UTC)
    b = _novo(con, base + timedelta(days=1))
    a = _novo(con, base)
    fila.mudar_estado(con, b.id, Estado.PRODUZINDO)
    assert [j.id for j in fila.listar(con)] == [a.id, b.id]
    assert [j.id for j in fila.listar(con, {Estado.PRODUZINDO})] == [b.id]
    assert fila.listar(con, canal="outro") == []


def test_atualizar(con):
    job = _novo(con)
    novo_pa = datetime(2026, 11, 1, 9, tzinfo=UTC)
    j = fila.atualizar(
        con, job.id, publish_at=novo_pa, youtube_video_id="abc",
        tentativas_revisao=1, briefing={"a": 1}, erro="e",
    )
    assert (j.publish_at, j.youtube_video_id, j.tentativas_revisao) == (novo_pa, "abc", 1)
    assert j.briefing == {"a": 1} and j.erro == "e"
    with pytest.raises(ValueError):
        fila.atualizar(con, job.id, estado="publicado")


def test_etapas_checkpoint(con):
    job = _novo(con)
    assert fila.estado_etapa(con, job.id, "roteiro") is None
    fila.marcar_etapa(con, job.id, "roteiro", "rodando")
    fila.marcar_etapa(con, job.id, "roteiro", "erro", "x")
    fila.marcar_etapa(con, job.id, "roteiro", "erro", "x")
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 2
    fila.marcar_etapa(con, job.id, "roteiro", "ok")
    assert fila.estado_etapa(con, job.id, "roteiro") == "ok"
    fila.resetar_etapas(con, job.id, ["roteiro"])
    assert fila.estado_etapa(con, job.id, "roteiro") is None
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 0


def test_conectar_idempotente(tmp_path):
    arq = tmp_path / "sub" / "x.db"
    conectar(arq).close()
    con = conectar(arq)
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    nomes = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"jobs", "etapas", "revisoes", "canais_estado", "cota", "metricas",
            "temas", "chamadas_claude"} <= nomes


def test_transicoes_falha_youtube_e_reaprovacao():
    assert Estado.FALHOU in fila.TRANSICOES[Estado.AGENDADO]
    assert Estado.APROVADO in fila.TRANSICOES[Estado.FALHOU]
