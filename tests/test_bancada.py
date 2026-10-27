import json
from datetime import UTC, datetime

import pytest

from autocanal import bancada, fila
from autocanal.db import conectar
from autocanal.etapas.base import ErroEtapa

PUBLICAR = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
AGORA = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
METADADOS = {
    "titulo": "Por que o mar é salgado? 3 motivos",
    "texto_thumb": "SAL ESCONDIDO",
    "cena_thumb": "salt crystals glowing on a dark rock by the sea",
    "descricao": "O sal do mar vem das rochas.",
    "tags": ["mar", "oceano"],
    "consultas_alvo": ["por que o mar é salgado", "sal do mar", "oceano salgado"],
}


def _ok(dados, **extra):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
            "total_cost_usd": 0.1, "duration_ms": 3000, "structured_output": dados, **extra}


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def job(cfg, con):
    j = fila.criar_job(con, "teste", "short", "por que o mar é salgado", PUBLICAR)
    con.execute("UPDATE jobs SET estado = 'agendado' WHERE id = ?", (j.id,))
    con.commit()
    pasta = cfg.dados / "jobs" / j.id
    pasta.mkdir(parents=True)
    (pasta / "roteiro.json").write_text(json.dumps({
        "segmentos": [{"texto": "o mar é salgado", "visual": "sea", "cena": "sea"}],
        "fontes": [{"afirmacao": "a", "url": "https://exemplo.org/mar"}],
    }), encoding="utf-8")
    return j


@pytest.fixture
def falso(cfg, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    return claude_falso


def test_bancada_nao_toca_no_job_real(cfg, con, job, falso):
    falso.responder(_ok(METADADOS))
    linha = bancada.rodar(cfg, con, job.id, "metadados", "antes", agora=AGORA)
    assert linha["erro"] is None
    assert fila.obter(con, job.id).estado.value == "agendado"
    assert not (cfg.dados / "jobs" / job.id / "metadados.json").exists()
    copias = list((cfg.dados / "bancada").glob(f"*-{job.id}-metadados-antes/jobs/{job.id}"))
    assert len(copias) == 1 and (copias[0] / "metadados.json").exists()
    assert con.execute("SELECT COUNT(*) FROM chamadas_ia").fetchone()[0] == 0


def test_bancada_grava_somas_e_metricas(cfg, con, job, falso):
    falso.responder(_ok(METADADOS, usage={"input_tokens": 10, "cache_read_input_tokens": 5,
                                         "output_tokens": 3}))
    linha = bancada.rodar(cfg, con, job.id, "metadados", "antes", agora=AGORA)
    assert (linha["chamadas"], linha["tokens_entrada"], linha["tokens_saida"]) == (1, 15, 3)
    assert linha["custo_usd"] == 0.1 and linha["turnos"] == 4
    assert linha["metricas"]["nota_titulo"] == 100
    assert linha["metricas"]["problemas_titulo"] == 0
    assert (linha["job"], linha["etapa"], linha["rotulo"]) == (job.id, "metadados", "antes")
    gravadas = (cfg.dados / "bancada" / "resultados.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(gravadas[-1]) == linha


def test_bancada_modelo_e_esforco_so_na_etapa(cfg, con, job, falso):
    falso.responder(_ok(METADADOS))
    linha = bancada.rodar(cfg, con, job.id, "metadados", "x", modelo="haiku", esforco="low",
                          agora=AGORA)
    [argv] = falso.chamadas()
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--effort") + 1] == "low"
    assert (linha["modelo"], linha["esforco"]) == ("haiku", "low")
    assert cfg.ia.provedores["claude"].modelos.get("metadados") == "sonnet"
    assert cfg.ia.provedores["claude"].esforco.get("metadados") is None


def test_bancada_erro_vira_resultado(cfg, con, job):
    def quebra(ctx):
        raise ErroEtapa("quebrou")

    linha = bancada.rodar(cfg, con, job.id, "roteiro", "antes", registro={"roteiro": quebra},
                          agora=AGORA)
    assert linha["erro"] == "quebrou"
    assert linha["chamadas"] == 0


def test_bancada_etapa_invalida(cfg, con, job):
    with pytest.raises(ValueError, match="visuais"):
        bancada.rodar(cfg, con, job.id, "visuais", "antes", agora=AGORA)


def test_relatorio_medias(cfg):
    pasta = cfg.dados / "bancada"
    pasta.mkdir(parents=True)
    base = {"etapa": "metadados", "rotulo": "antes", "chamadas": 1, "tokens_saida": 10,
            "custo_usd": 0.2, "turnos": 4, "erro": None, "metricas": {"nota_titulo": 90}}
    linhas = [{**base, "tokens_entrada": 100}, {**base, "tokens_entrada": 300}]
    (pasta / "resultados.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in linhas), encoding="utf-8")
    texto = "\n".join(bancada.relatorio(cfg))
    assert "metadados" in texto and "antes" in texto and "200" in texto and "nota_titulo=90" in texto
    assert "chamadas=1" in texto


def test_cli_bancada(raiz_teste, cfg, con, job, falso):
    from typer.testing import CliRunner

    from autocanal import cli

    runner = CliRunner()
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "bancada", "relatorio"])
    assert r.exit_code == 0 and "nenhuma execução" in r.output
    r = runner.invoke(cli.app, ["--raiz", str(raiz_teste), "bancada", "rodar", job.id, "visuais",
                                "--rotulo", "x"])
    assert r.exit_code == 1 and "visuais" in r.output


def test_primeira_versao_tira_problemas_so_da_copia(cfg, con, job, falso):
    falso.responder(_ok(METADADOS))
    pasta = cfg.dados / "jobs" / job.id
    (pasta / "problemas_revisao.json").write_text('{"problemas": ["x"]}', encoding="utf-8")
    linha = bancada.rodar(cfg, con, job.id, "metadados", "antes", agora=AGORA,
                          primeira_versao=True)
    assert linha["primeira_versao"] is True
    assert (pasta / "problemas_revisao.json").exists()
    [copia] = (cfg.dados / "bancada").glob(f"*-{job.id}-metadados-antes/jobs/{job.id}")
    assert not (copia / "problemas_revisao.json").exists()


def test_sem_primeira_versao_a_copia_leva_os_problemas(cfg, con, job, falso):
    falso.responder(_ok(METADADOS))
    pasta = cfg.dados / "jobs" / job.id
    (pasta / "problemas_revisao.json").write_text('{"problemas": ["x"]}', encoding="utf-8")
    linha = bancada.rodar(cfg, con, job.id, "metadados", "antes", agora=AGORA)
    assert linha["primeira_versao"] is False
    [copia] = (cfg.dados / "bancada").glob(f"*-{job.id}-metadados-antes/jobs/{job.id}")
    assert (copia / "problemas_revisao.json").exists()


def test_copia_nao_leva_render_nem_curadoria(cfg, con, job, falso):
    falso.responder(_ok(METADADOS))
    pasta = cfg.dados / "jobs" / job.id
    for sub in ("render", "visuais/curadoria", "visuais/flux_lote/saida"):
        (pasta / sub).mkdir(parents=True)
        (pasta / sub / "lixo.bin").write_bytes(b"x")
    (pasta / "visuais" / "seg_01.jpg").write_bytes(b"j")
    (pasta / "video.mp4").write_bytes(b"v")
    bancada.rodar(cfg, con, job.id, "metadados", "antes", agora=AGORA)
    [copia] = (cfg.dados / "bancada").glob(f"*-{job.id}-metadados-antes/jobs/{job.id}")
    assert not (copia / "render").exists()
    assert not (copia / "visuais" / "curadoria").exists()
    assert not (copia / "visuais" / "flux_lote").exists()
    assert (copia / "visuais" / "seg_01.jpg").exists() and (copia / "video.mp4").exists()
    assert (pasta / "render" / "lixo.bin").exists()  # o job real continua intacto
    assert (pasta / "visuais" / "curadoria" / "lixo.bin").exists()
    assert (pasta / "visuais" / "flux_lote" / "saida" / "lixo.bin").exists()


def test_cli_bancada_job_sem_pasta(raiz_teste, cfg, con, job):
    import shutil

    from typer.testing import CliRunner

    from autocanal import cli

    shutil.rmtree(cfg.dados / "jobs" / job.id)
    r = CliRunner().invoke(cli.app, ["--raiz", str(raiz_teste), "bancada", "rodar", job.id,
                                     "metadados", "--rotulo", "x"])
    assert r.exit_code == 1 and "erro:" in r.output and job.id in r.output
    assert not isinstance(r.exception, FileNotFoundError)


def test_bancada_revisao_clona_em_revisao_e_le_o_parecer(cfg, con, job):
    vistos = {}

    def revisa(ctx):
        vistos["estado"] = ctx.job.estado
        (ctx.pasta / "revisao_1.json").write_text(json.dumps({
            "aprovado": False, "etapa_para_refazer": "visuais", "problemas": ["a", "b"],
        }), encoding="utf-8")

    linha = bancada.rodar(cfg, con, job.id, "revisao", "antes", registro={"revisao": revisa},
                          agora=AGORA)
    assert vistos["estado"].value == "em_revisao"
    assert fila.obter(con, job.id).estado.value == "agendado"
    assert linha["erro"] is None
    assert linha["metricas"] == {"aprovado": False, "etapa_para_refazer": "visuais",
                                 "problemas": 2}


@pytest.mark.parametrize("conferidas, esperado", [
    (None, None),
    ({"resultado": [{"status": "confirmada"}, {"status": "inacessivel"}]}, 1),
])
def test_bancada_roteiro_mede_palavras_gancho_e_fontes(cfg, con, job, conferidas, esperado):
    def escreve(ctx):
        palavras = " ".join(["mar"] * 100)
        (ctx.pasta / "roteiro.json").write_text(json.dumps({
            "segmentos": [{"texto": palavras}], "nota_gancho": 85,
            "fontes": [{"url": "https://a"}, {"url": "https://b"}],
        }), encoding="utf-8")
        if conferidas is not None:
            (ctx.pasta / "fontes_conferidas.json").write_text(json.dumps(conferidas),
                                                              encoding="utf-8")

    linha = bancada.rodar(cfg, con, job.id, "roteiro", "antes", registro={"roteiro": escreve},
                          agora=AGORA)
    assert linha["erro"] is None
    m = linha["metricas"]
    assert (m["nota_gancho"], m["fontes"], m["fontes_confirmadas"]) == (85, 2, esperado)
    assert m["palavras_ok"] is True  # 100 palavras, faixa do short: 75 a 150
