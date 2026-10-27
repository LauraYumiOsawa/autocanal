from datetime import UTC, datetime, timedelta

import pytest

from autocanal import etapas, fila, ia
from autocanal.ia import IAIndisponivel, LimiteUso
from autocanal.db import conectar
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS
from autocanal.produtor import (
    MAX_TENTATIVAS_ETAPA,
    pausa_sistema,
    produzir,
)
from autocanal.servicos import ServicoIndisponivel

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


@pytest.mark.parametrize("erro", [
    LimiteUso("claude", "Claude usage limit reached"),
    IAIndisponivel("claude", "não consegui executar claude: FileNotFoundError"),
])
def test_produzir_ia_parada_nao_conta_tentativa(cfg, con, job, erro):
    chamadas: list[str] = []

    def parada(ctx):
        chamadas.append("roteiro")
        raise erro

    estado = produzir(cfg, con, job.id, registro=_registro(chamadas, roteiro=parada))
    assert estado == Estado.PRODUZINDO
    assert fila.obter(con, job.id).estado == Estado.PRODUZINDO
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 0
    assert fila.estado_etapa(con, job.id, "roteiro") != "ok"
    assert chamadas == ["roteiro"]
    # a pausa do provedor é gravada por ia.chamar; o produtor não pausa o sistema
    assert pausa_sistema(cfg) is None


def test_produzir_etapas_sem_ia_andam_antes_da_pausada(cfg, con, job):
    chamadas: list[str] = []

    def parada(ctx):
        chamadas.append("metadados")
        raise LimiteUso("claude", "pausado")

    produzir(cfg, con, job.id, registro=_registro(chamadas, metadados=parada))
    i = ETAPAS.index("metadados")
    assert all(fila.estado_etapa(con, job.id, n) == "ok" for n in ETAPAS[:i])
    assert fila.tentativas_etapa(con, job.id, "metadados") == 0


@pytest.mark.parametrize("erro", [
    ServicoIndisponivel("não consegui subir o serviço kokoro: CalledProcessError"),
])
def test_produzir_queda_geral_pausa_sem_contar_tentativa(cfg, con, job, erro):
    chamadas: list[str] = []
    fila.marcar_etapa(con, job.id, "roteiro", "erro", "falha anterior")

    def cai(ctx):
        chamadas.append("roteiro")
        raise erro

    for _ in range(MAX_TENTATIVAS_ETAPA + 1):
        assert produzir(cfg, con, job.id, registro=_registro(chamadas, roteiro=cai)) == (
            Estado.PRODUZINDO
        )
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 1  # só a falha anterior
    assert fila.estado_etapa(con, job.id, "roteiro") == "erro"
    ate, motivo = pausa_sistema(cfg)
    assert abs(ate - (datetime.now(UTC) + timedelta(hours=1))) < timedelta(seconds=30)
    assert motivo == str(erro)
    assert ia.pausas(cfg, datetime.now(UTC)) == {}


def test_produzir_etapa_rodando_conta_como_interrompida(cfg, con, job):
    chamadas: list[str] = []
    reg = _registro(chamadas)
    fila.marcar_etapa(con, job.id, "roteiro", "rodando")  # processo anterior morreu
    assert produzir(cfg, con, job.id, registro=reg) == Estado.APROVADO
    assert fila.tentativas_etapa(con, job.id, "roteiro") == 1
    assert chamadas[0] == "roteiro"


def test_produzir_interrompida_tres_vezes_falha(cfg, con, job):
    chamadas: list[str] = []
    for _ in range(MAX_TENTATIVAS_ETAPA - 1):
        fila.marcar_etapa(con, job.id, "narracao", "erro", "interrompida")
    fila.marcar_etapa(con, job.id, "narracao", "rodando")
    assert produzir(cfg, con, job.id, registro=_registro(chamadas)) == Estado.FALHOU
    assert "narracao: interrompida" in fila.obter(con, job.id).erro
    assert chamadas == []


def test_produzir_para_o_kokoro_se_narrou(cfg, con, job, monkeypatch):
    from autocanal import produtor

    paradas = []

    def parar(nome, raiz):
        paradas.append((nome, raiz))
        raise OSError("docker fora")  # erro ao parar é ignorado

    monkeypatch.setattr(produtor.servicos, "parar", parar)
    compose = cfg.raiz / "servicos" / "kokoro" / "docker-compose.yml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n")
    assert produzir(cfg, con, job.id, registro=_registro([])) == Estado.APROVADO
    assert paradas == [("kokoro", cfg.raiz)]
    produzir(cfg, con, job.id, registro=_registro([]))  # nada a fazer: não mexe no Docker
    assert len(paradas) == 1


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


def test_produzir_revisao_que_nao_decide_conta_tentativa(cfg, con, job):
    chamadas: list[str] = []
    reg = _registro(chamadas, revisao=lambda ctx: chamadas.append("revisao"))
    assert produzir(cfg, con, job.id, registro=reg) == Estado.EM_REVISAO
    assert fila.estado_etapa(con, job.id, "revisao") == "erro"
    assert fila.tentativas_etapa(con, job.id, "revisao") == 1
    for _ in range(MAX_TENTATIVAS_ETAPA - 1):
        estado = produzir(cfg, con, job.id, registro=reg)
    assert estado == Estado.FALHOU
    assert fila.obter(con, job.id).erro == "revisao: revisão não decidiu"
    assert chamadas.count("revisao") == MAX_TENTATIVAS_ETAPA


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
