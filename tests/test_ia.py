import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from autocanal import ia, produtor
from autocanal.config import carregar
from autocanal.db import conectar

SCHEMA = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
AGORA = datetime(2026, 10, 26, 12, tzinfo=UTC)


def _ok(dados, **extra):
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
            "total_cost_usd": 0.1, "duration_ms": 1200, "structured_output": dados, **extra}


def _pedido(pasta: Path, **kw) -> ia.Pedido:
    return ia.Pedido(prompt="oi", schema=SCHEMA, pasta=pasta, **kw)


def _mudar_ia(raiz: Path, **mudancas) -> None:
    arq = raiz / "config" / "autocanal.yaml"
    d = yaml.safe_load(arq.read_text(encoding="utf-8"))
    d["ia"].update(mudancas)
    arq.write_text(yaml.safe_dump(d), encoding="utf-8")


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def cfg_falso(cfg, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    return cfg


# --- config ---

def test_config_padrao_e_excecoes(raiz_teste):
    _mudar_ia(raiz_teste, etapas={"curadoria": "codex"},
              provedores={"claude": {"bin": "claude"}, "codex": {"bin": "codex"}})
    cfg = carregar(raiz_teste)
    assert ia.provedor_de(cfg, "curadoria") == "codex"
    assert ia.provedor_de(cfg, "roteiro") == "claude"


def test_config_bloco_claude_recusado(raiz_teste):
    arq = raiz_teste / "config" / "autocanal.yaml"
    d = yaml.safe_load(arq.read_text(encoding="utf-8"))
    d["claude"] = {"bin": "claude"}
    arq.write_text(yaml.safe_dump(d), encoding="utf-8")
    with pytest.raises(ValueError, match="o bloco `claude:` virou `ia:`"):
        carregar(raiz_teste)


def test_config_padrao_sem_provedor(raiz_teste):
    _mudar_ia(raiz_teste, padrao="gemini")
    with pytest.raises(ValueError, match="gemini"):
        carregar(raiz_teste)


def test_config_etapa_desconhecida(raiz_teste):
    _mudar_ia(raiz_teste, max_turns={"render": 3})
    with pytest.raises(ValueError, match="render"):
        carregar(raiz_teste)


# --- chamar ---

def test_chamar_registra_em_chamadas_ia(cfg_falso, con, claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}, usage={"input_tokens": 3, "output_tokens": 2}))
    r = ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path))
    assert r.dados == {"x": 1}
    linha = con.execute("SELECT * FROM chamadas_ia").fetchone()
    assert (linha["job_id"], linha["etapa"], linha["provedor"], linha["modelo"],
            linha["tokens_entrada"], linha["tokens_saida"], linha["custo_usd"],
            linha["turnos"], linha["duracao_s"]) == ("j1", "roteiro", "claude", None, 3, 2, 0.1, 3, 1.2)
    assert linha["criado_em"].endswith("+00:00")


def test_chamar_usa_modelo_e_max_turns_da_etapa(cfg_falso, con, claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}))
    ia.chamar(cfg_falso, con, None, "metadados", _pedido(tmp_path))
    [argv] = claude_falso.chamadas()
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "20"


def test_chamar_provedor_pausado_nao_executa(cfg_falso, con, claude_falso, tmp_path):
    ia.pausar(cfg_falso, "claude", "limite de uso")
    with pytest.raises(ia.LimiteUso):
        ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path))
    assert claude_falso.chamadas() == []


def test_pausa_nao_estende(cfg_falso, con, tmp_path):
    ate = ia.pausar(cfg_falso, "claude", "limite de uso", datetime.now(UTC) - timedelta(minutes=10))
    with pytest.raises(ia.LimiteUso):
        ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path))
    assert ia.pausa(cfg_falso, "claude") == (ate, "limite de uso")


def test_chamar_limite_grava_pausa(cfg_falso, con, claude_falso, tmp_path):
    claude_falso.responder({"type": "result", "is_error": True, "result": "Claude usage limit reached"})
    with pytest.raises(ia.LimiteUso):
        ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path))
    ate, motivo = ia.pausa(cfg_falso, "claude")
    assert motivo == "limite de uso"
    assert abs(ate - (datetime.now(UTC) + timedelta(hours=1))) < timedelta(seconds=30)
    assert con.execute("SELECT COUNT(*) FROM chamadas_ia").fetchone()[0] == 0


def test_chamar_indisponivel_pausa_o_provedor(cfg, con, tmp_path):
    cfg.ia.provedores["claude"].bin = str(tmp_path / "nao-existe")
    with pytest.raises(ia.IAIndisponivel):
        ia.chamar(cfg, con, "j1", "roteiro", _pedido(tmp_path))
    ate, motivo = ia.pausa(cfg, "claude")
    assert "FileNotFoundError" in motivo
    assert produtor.pausa_sistema(cfg) is None


def test_metodo_ausente_e_indisponivel(cfg_falso, con, claude_falso, tmp_path):
    with pytest.raises(ia.IAIndisponivel, match="nao-existe"):
        ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path, metodos=("nao-existe",)))
    assert claude_falso.chamadas() == []


def test_metodo_copiado_antes_da_chamada(cfg_falso, con, claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}))
    ia.chamar(cfg_falso, con, "j1", "roteiro", _pedido(tmp_path, metodos=("yt-script",)))
    assert (tmp_path / "metodo" / "yt-script" / "SKILL.md").exists()


def test_provedor_sem_adaptador(raiz_teste, tmp_path):
    _mudar_ia(raiz_teste, etapas={"roteiro": "gemini"},
              provedores={"claude": {"bin": "claude"}, "gemini": {"bin": "gemini"}})
    cfg = carregar(raiz_teste)
    con = conectar(cfg.dados / "autocanal.db")
    with pytest.raises(ia.IAIndisponivel, match="sem adaptador"):
        ia.chamar(cfg, con, "j1", "roteiro", _pedido(tmp_path))


# --- pausa ---

def test_livre_e_pausa_ilegivel(cfg):
    assert ia.livre(cfg, "roteiro", AGORA)
    (cfg.dados / "pausa_ia").mkdir(parents=True)
    (cfg.dados / "pausa_ia" / "claude").write_text("lixo")
    assert ia.pausa(cfg, "claude") is None
    assert ia.livre(cfg, "roteiro", AGORA)
    assert ia.pausar(cfg, "claude", "x", AGORA) == AGORA + timedelta(hours=1)
    assert not ia.livre(cfg, "roteiro", AGORA)
    assert ia.livre(cfg, "roteiro", AGORA + timedelta(hours=2))


def test_pausas_so_vigentes(cfg):
    ia.pausar(cfg, "claude", "velha", AGORA - timedelta(hours=2))
    assert ia.pausas(cfg, AGORA) == {}
    ia.pausar(cfg, "claude", "limite de uso", AGORA)
    assert ia.pausas(cfg, AGORA) == {"claude": (AGORA + timedelta(hours=1), "limite de uso")}


def test_marcador_antigo_ignorado(cfg):
    cfg.dados.mkdir(parents=True, exist_ok=True)
    (cfg.dados / "pausa_claude_ate").write_text((AGORA + timedelta(hours=1)).isoformat())
    assert ia.livre(cfg, "roteiro", AGORA)
    assert ia.pausas(cfg, AGORA) == {}


# --- banco ---

def _banco_antigo(caminho: Path) -> None:
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE chamadas_claude (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT,"
                " etapa TEXT NOT NULL, custo_usd REAL, turnos INTEGER, duracao_s REAL,"
                " criado_em TEXT NOT NULL)")
    con.executemany(
        "INSERT INTO chamadas_claude (job_id, etapa, custo_usd, turnos, duracao_s, criado_em)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [("j", "visuais", 0.2, 5, 3.0, "2026-10-20T00:00:00+00:00"),
         ("j", "roteiro", 1.0, 30, None, "2026-10-20T00:01:00+00:00")],
    )
    con.commit()
    con.close()


def test_migracao_chamadas_claude(tmp_path):
    _banco_antigo(tmp_path / "db.sqlite")
    con = conectar(tmp_path / "db.sqlite")
    linhas = [tuple(r) for r in con.execute(
        "SELECT etapa, provedor, custo_usd, turnos, duracao_s FROM chamadas_ia ORDER BY id")]
    assert linhas == [("curadoria", "claude", 0.2, 5, 3.0), ("roteiro", "claude", 1.0, 30, 0.0)]
    nomes = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "chamadas_claude" not in nomes


def test_migracao_idempotente(tmp_path):
    _banco_antigo(tmp_path / "db.sqlite")
    conectar(tmp_path / "db.sqlite").close()
    con = conectar(tmp_path / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM chamadas_ia").fetchone()[0] == 2


def test_chamar_usa_esforco_da_etapa(cfg_falso, con, claude_falso, tmp_path):
    cfg_falso.ia.provedores["claude"].esforco["metadados"] = "high"
    claude_falso.responder(_ok({"x": 1}), _ok({"x": 1}))
    ia.chamar(cfg_falso, con, None, "metadados", _pedido(tmp_path))
    ia.chamar(cfg_falso, con, None, "roteiro", _pedido(tmp_path))
    meta, rot = claude_falso.chamadas()
    assert meta[meta.index("--effort") + 1] == "high"
    assert "--effort" not in rot


def test_config_esforco_etapa_desconhecida(raiz_teste):
    _mudar_ia(raiz_teste, provedores={"claude": {"bin": "claude", "esforco": {"render": "low"}}})
    with pytest.raises(ValueError, match="render"):
        carregar(raiz_teste)
