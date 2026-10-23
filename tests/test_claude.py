from pathlib import Path

import pytest

from autocanal.claude import (
    ErroClaude,
    LimiteUsoClaude,
    ResultadoClaude,
    chamar,
    montar_argv,
    registrar,
)
from autocanal.db import conectar

SCHEMA = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}


def _ok(dados, **extra):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 3,
        "total_cost_usd": 0.1,
        "duration_ms": 1200,
        "structured_output": dados,
        **extra,
    }


def _chamar(falso, tmp_path, **kw):
    return chamar("oi", SCHEMA, ["Read", "Write"], tmp_path, claude_bin=falso.claude_bin, **kw)


def test_montar_argv_ordem():
    argv = montar_argv("p", SCHEMA, ["Read", "Write"], modelo="sonnet", max_turns=5, claude_bin="c")
    assert argv[:3] == ["c", "-p", "p"]
    assert argv[-3:] == ["--allowedTools", "Read", "Write"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "5"
    sem = montar_argv("p", SCHEMA, ["Read"], modelo=None, max_turns=5, claude_bin="c")
    assert "--model" not in sem
    assert sem[-2:] == ["--allowedTools", "Read"]
    assert "--disallowedTools" not in sem


def test_montar_argv_nega_segredos(tmp_path):
    argv = montar_argv(
        "p", SCHEMA, ["Read"], modelo=None, max_turns=5, claude_bin="c", raiz=tmp_path
    )
    i, j = argv.index("--disallowedTools"), argv.index("--allowedTools")
    assert i < j and argv[j + 1:] == ["Read"]
    negadas = argv[i + 1:j]
    raiz = tmp_path.resolve()
    for ferr in ("Read", "Edit", "Write"):
        assert f"{ferr}(/{raiz}/dados/tokens/**)" in negadas
        assert f"{ferr}(/{raiz}/config/**)" in negadas
        assert f"{ferr}(/{raiz}/dados/*.db)" in negadas
        assert f"{ferr}(/{Path.home()}/.claude/**)" in negadas
    assert negadas[0].startswith("Read(//")


def test_chamar_repassa_raiz(claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}))
    _chamar(claude_falso, tmp_path, raiz=tmp_path)
    [argv] = claude_falso.chamadas()
    assert f"Read(/{tmp_path.resolve()}/config/**)" in argv


def test_chamar_structured_output(claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}))
    r = _chamar(claude_falso, tmp_path)
    assert r.dados == {"x": 1}
    assert r.turnos == 3
    assert r.custo_usd == 0.1
    assert r.duracao_s == 1.2
    assert len(claude_falso.chamadas()) == 1


def test_chamar_fallback_result_json(claude_falso, tmp_path):
    saida = _ok(None)
    del saida["structured_output"]
    saida["result"] = '{"x": 7}'
    claude_falso.responder(saida)
    assert _chamar(claude_falso, tmp_path).dados == {"x": 7}


def test_chamar_invalido_tenta_de_novo(claude_falso, tmp_path):
    claude_falso.responder(_ok({"y": 1}), _ok({"x": 2}))
    r = _chamar(claude_falso, tmp_path)
    assert r.dados == {"x": 2}
    assert len(claude_falso.chamadas()) == 2


def test_chamar_esgota_tentativas(claude_falso, tmp_path):
    claude_falso.responder(_ok({"y": 1}), _ok({"y": 1}), _ok({"y": 1}))
    with pytest.raises(ErroClaude):
        _chamar(claude_falso, tmp_path)
    assert len(claude_falso.chamadas()) == 3


def test_limite_de_uso(claude_falso, tmp_path):
    claude_falso.responder(
        {"type": "result", "subtype": "success", "is_error": True, "result": "Claude usage limit reached"}
    )
    with pytest.raises(LimiteUsoClaude):
        _chamar(claude_falso, tmp_path)
    assert len(claude_falso.chamadas()) == 1


def test_binario_ausente_e_indisponivel(tmp_path):
    from autocanal.claude import ErroClaudeIndisponivel

    chamadas = []

    def executar(argv, **k):
        chamadas.append(argv)
        raise FileNotFoundError(2, "No such file", argv[0])

    with pytest.raises(ErroClaudeIndisponivel):
        chamar("oi", SCHEMA, [], tmp_path, claude_bin="/nao/existe", executar=executar)
    assert len(chamadas) == 1  # sem retentativa: é falha do sistema

    def sem_permissao(argv, **k):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(ErroClaudeIndisponivel):
        chamar("oi", SCHEMA, [], tmp_path, executar=sem_permissao)


def test_limite_de_uso_no_stderr(tmp_path):
    import subprocess

    def executar(argv, **k):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Error: weekly limit reached")

    with pytest.raises(LimiteUsoClaude):
        chamar("oi", SCHEMA, [], tmp_path, executar=executar)


def test_timeout_conta_como_tentativa_falha(tmp_path):
    import subprocess

    def executar(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)

    with pytest.raises(ErroClaude, match="timeout"):
        chamar("oi", SCHEMA, [], tmp_path, tentativas=2, executar=executar)


def test_registrar(tmp_path):
    con = conectar(tmp_path / "db.sqlite")
    registrar(con, "job1", "roteiro", ResultadoClaude({"x": 1}, 0.25, 4, 12.5))
    linha = con.execute("SELECT * FROM chamadas_claude").fetchone()
    assert (linha["job_id"], linha["etapa"], linha["custo_usd"], linha["turnos"], linha["duracao_s"]) == (
        "job1", "roteiro", 0.25, 4, 12.5,
    )
    assert linha["criado_em"].endswith("+00:00")


def test_limite_de_sessao_real(claude_falso, tmp_path):
    # mensagem real do CLI em 2026-10-23
    claude_falso.responder({
        "type": "result", "subtype": "success", "is_error": True,
        "result": "You've hit your session limit · resets 9am (America/Sao_Paulo)",
    })
    with pytest.raises(LimiteUsoClaude):
        _chamar(claude_falso, tmp_path)
    assert len(claude_falso.chamadas()) == 1


def test_montar_argv_nega_pastas_pessoais(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    monkeypatch.setenv("HOME", str(casa))
    argv = montar_argv(
        "p", SCHEMA, ["Read"], modelo=None, max_turns=5, claude_bin="c", raiz=tmp_path / "raiz"
    )
    negadas = argv[argv.index("--disallowedTools") + 1:argv.index("--allowedTools")]
    pastas = (".ssh", ".config", ".aws", ".gnupg", ".docker", ".kube", ".local/share",
              ".claude", "Downloads", "Documents", "Desktop")
    for ferr in ("Read", "Edit", "Write"):
        for pasta in pastas:
            assert f"{ferr}(/{casa}/{pasta}/**)" in negadas
        for arquivo in (".netrc", ".git-credentials", ".bash_history"):
            assert f"{ferr}(/{casa}/{arquivo})" in negadas
