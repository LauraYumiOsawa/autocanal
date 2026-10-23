import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from autocanal import fila, planejador
from autocanal.db import conectar
from autocanal.fila import Estado

SP = ZoneInfo("America/Sao_Paulo")
AGORA = datetime(2026, 10, 26, 8, 0, tzinfo=SP)  # segunda-feira
# grade do canal `teste`: seg/qua/sex 12:00; janela [agora + 2 h, agora + 3 + 7 dias)
SLOTS = [datetime(2026, 10, d, 12, tzinfo=SP) for d in (26, 28, 30)] + [
    datetime(2026, 11, d, 12, tzinfo=SP) for d in (2, 4)
]


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def cfg_nicho(cfg):
    cfg.canais["teste"].nicho = "curiosidades de ciência"
    return cfg


class Sugestor:
    def __init__(self):
        self.pedidos: list[tuple[str, str, int]] = []

    def __call__(self, canal, formato, n):
        self.pedidos.append((canal.slug, formato, n))
        return [f"tema {len(self.pedidos)}.{i}" for i in range(1, n + 1)]


def test_planejar_preenche_slots(cfg_nicho, con):
    sugerir = Sugestor()
    jobs = planejador.planejar(cfg_nicho, con, AGORA, sugerir_temas=sugerir)
    assert sugerir.pedidos == [("teste", "short", 5)]
    assert [j.publish_at for j in jobs] == [s.astimezone(UTC) for s in SLOTS]
    assert [j.tema for j in jobs] == [f"tema 1.{i}" for i in range(1, 6)]
    assert all(
        (j.canal, j.formato, j.estado) == ("teste", "short", Estado.PLANEJADO) for j in jobs
    )
    assert fila.listar(con) == jobs
    temas = con.execute("SELECT canal, formato, tema, usado FROM temas ORDER BY id").fetchall()
    assert [tuple(r) for r in temas] == [("teste", "short", j.tema, 1) for j in jobs]


def test_planejar_nao_duplica(cfg_nicho, con):
    sugerir = Sugestor()
    primeiros = planejador.planejar(cfg_nicho, con, AGORA, sugerir_temas=sugerir)
    assert planejador.planejar(cfg_nicho, con, AGORA, sugerir_temas=sugerir) == []
    assert len(sugerir.pedidos) == 1  # nada livre: o Claude nem é chamado
    # job descartado libera o slot; os outros estados continuam ocupando
    fila.mudar_estado(con, primeiros[1].id, Estado.DESCARTADO)
    fila.mudar_estado(con, primeiros[2].id, Estado.PRODUZINDO)
    fila.mudar_estado(con, primeiros[2].id, Estado.FALHOU)
    novos = planejador.planejar(cfg_nicho, con, AGORA, sugerir_temas=sugerir)
    assert [j.publish_at for j in novos] == [primeiros[1].publish_at]
    assert sugerir.pedidos[-1] == ("teste", "short", 1)


def test_planejar_respeita_margem_de_duas_horas(cfg_nicho, con):
    agora = datetime(2026, 10, 26, 10, 30, tzinfo=SP)  # slot das 12:00 fica a 1h30
    jobs = planejador.planejar(cfg_nicho, con, agora, sugerir_temas=Sugestor())
    assert jobs[0].publish_at == SLOTS[1].astimezone(UTC)


def test_planejar_ignora_canal_sem_nicho(cfg, con, caplog):
    sugerir = Sugestor()
    with caplog.at_level(logging.WARNING, logger="autocanal.planejador"):
        assert planejador.planejar(cfg, con, AGORA, sugerir_temas=sugerir) == []
    assert sugerir.pedidos == []
    assert fila.listar(con) == []
    assert "teste" in caplog.text and "nicho" in caplog.text


def test_planejar_temas_a_menos(cfg_nicho, con, caplog):
    with caplog.at_level(logging.WARNING, logger="autocanal.planejador"):
        jobs = planejador.planejar(
            cfg_nicho, con, AGORA, sugerir_temas=lambda c, f, n: ["só um", "  "]
        )
    assert [j.tema for j in jobs] == ["só um"]
    assert jobs[0].publish_at == SLOTS[0].astimezone(UTC)
    assert "1 de 5" in caplog.text


def _ok(dados):
    return {
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 6,
        "total_cost_usd": 0.3, "duration_ms": 9000, "structured_output": dados,
    }


def test_sugerir_temas_claude(cfg_nicho, con, claude_falso):
    cfg = cfg_nicho
    cfg.claude.bin = claude_falso.claude_bin
    antigo = fila.criar_job(con, "teste", "short", "tema antigo", datetime(2026, 10, 1, tzinfo=UTC))
    pasta = cfg.dados / "jobs" / antigo.id
    pasta.mkdir(parents=True)
    (pasta / "metadados.json").write_text(json.dumps({"titulo": "Título que já saiu"}))
    planejador.registrar_tema(con, "teste", "short", "tema já usado")
    claude_falso.responder(_ok({"temas": [
        {"tema": "Por que o céu é azul", "angulo": "o erro que a escola ensina"},
        {"tema": " Raios ", "angulo": " "},
    ]}))

    temas = planejador.sugerir_temas_claude(cfg, con)(cfg.canais["teste"], "short", 2)

    assert temas == ["Por que o céu é azul — o erro que a escola ensina", "Raios"]
    argv = claude_falso.chamadas()[0]
    assert argv[argv.index("--allowedTools") + 1:] == ["Skill", "Read", "WebSearch", "WebFetch"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "15"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["temas"]["minItems"] == 2
    assert schema["properties"]["temas"]["maxItems"] == 2
    prompt = argv[2]
    for trecho in ("youtube-agent:yt-plan", "curiosidades de ciência", "Título que já saiu",
                   "tema já usado", "Não faça perguntas"):
        assert trecho in prompt
    r = con.execute("SELECT job_id, etapa FROM chamadas_claude").fetchall()
    assert [tuple(x) for x in r] == [(None, "temas")]
