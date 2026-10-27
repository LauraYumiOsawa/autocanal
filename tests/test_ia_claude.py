import subprocess
from pathlib import Path

import pytest

from autocanal.ia.base import ErroIA, IAIndisponivel, LimiteUso, Pedido, lista_imagens
from autocanal.ia.claude_cli import chamar, montar_argv

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


def pedido(pasta: Path, **kw) -> Pedido:
    return Pedido(prompt="oi", schema=SCHEMA, pasta=pasta, **kw)


def _argv(p: Pedido, raiz: Path, modelo=None, esforco=None) -> list[str]:
    return montar_argv(p, modelo=modelo, esforco=esforco, max_turns=5, bin="c", raiz=raiz)


def _chamar(falso, tmp_path, p=None, **kw):
    return chamar(p or pedido(tmp_path), modelo=None, max_turns=5, bin=falso.claude_bin,
                  raiz=tmp_path, **kw)


def test_montar_argv_ordem(tmp_path):
    argv = _argv(pedido(tmp_path), tmp_path, modelo="sonnet")
    assert argv[:3] == ["c", "-p", "oi"]
    assert argv[-2:] == ["--allowedTools", "Read"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "5"
    assert "--model" not in _argv(pedido(tmp_path), tmp_path)


def test_montar_argv_nega_segredos(tmp_path):
    argv = _argv(pedido(tmp_path), tmp_path)
    i, j = argv.index("--disallowedTools"), argv.index("--allowedTools")
    assert i < j and argv[j + 1:] == ["Read"]
    negadas = argv[i + 1:j]
    raiz = tmp_path.resolve()
    for ferr in ("Read", "Edit", "Write"):
        assert f"{ferr}(/{raiz}/dados/tokens/**)" in negadas
        assert f"{ferr}(/{raiz}/config/**)" in negadas
        assert f"{ferr}(/{raiz}/dados/*.db)" in negadas
        assert f"{ferr}(/{Path.home()}/.claude/**)" in negadas
        assert f"{ferr}(/{Path.home()}/.codex/**)" in negadas
    assert negadas[0].startswith("Read(//")


def test_montar_argv_nega_pastas_pessoais(tmp_path, monkeypatch):
    casa = tmp_path / "casa"
    monkeypatch.setenv("HOME", str(casa))
    argv = _argv(pedido(tmp_path), tmp_path / "raiz")
    negadas = argv[argv.index("--disallowedTools") + 1:argv.index("--allowedTools")]
    pastas = (".ssh", ".config", ".aws", ".gnupg", ".docker", ".kube", ".local/share",
              ".claude", ".codex", "Downloads", "Documents", "Desktop")
    for ferr in ("Read", "Edit", "Write"):
        for pasta in pastas:
            assert f"{ferr}(/{casa}/{pasta}/**)" in negadas
        for arquivo in (".netrc", ".git-credentials", ".bash_history"):
            assert f"{ferr}(/{casa}/{arquivo})" in negadas


def test_argv_web_so_quando_pedido(tmp_path):
    sem = _argv(pedido(tmp_path), tmp_path)
    com = _argv(pedido(tmp_path, web=True), tmp_path)
    assert "WebSearch" not in sem and "WebFetch" not in sem
    assert {"WebSearch", "WebFetch"} <= set(com)


def test_argv_scripts_viram_bash_e_sem_skill(tmp_path):
    a = _argv(pedido(tmp_path, scripts=("metodo/yt-script/hookscore.py",)), tmp_path)
    assert "Bash(python3 metodo/yt-script/hookscore.py:*)" in a
    assert "Skill" not in a


def test_prompt_ganha_lista_de_imagens(tmp_path):
    p = pedido(tmp_path, imagens=(tmp_path / "f.jpg",))
    a = _argv(p, tmp_path)
    assert a[a.index("-p") + 1] == p.prompt + lista_imagens(p)


def test_chamar_roda_na_pasta_do_pedido(tmp_path):
    vistos = {}

    def executar(argv, **k):
        vistos.update(k)
        return subprocess.CompletedProcess(argv, 0, stdout='{"structured_output": {"x": 1}}',
                                           stderr="")

    chamar(pedido(tmp_path, timeout_s=77), modelo=None, max_turns=5, bin="c", raiz=tmp_path,
           executar=executar)
    assert vistos["cwd"] == tmp_path and vistos["timeout"] == 77


def test_chamar_structured_output(claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}))
    r = _chamar(claude_falso, tmp_path)
    assert r.dados == {"x": 1}
    assert r.turnos == 3
    assert r.custo_usd == 0.1
    assert r.duracao_s == 1.2
    assert r.tokens_entrada is None and r.tokens_saida is None
    assert len(claude_falso.chamadas()) == 1


def test_tokens_e_provedor(claude_falso, tmp_path):
    claude_falso.responder(_ok({"x": 1}, usage={
        "input_tokens": 10, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2,
        "output_tokens": 7,
    }))
    r = chamar(pedido(tmp_path), modelo="sonnet", max_turns=5, bin=claude_falso.claude_bin,
               raiz=tmp_path)
    assert (r.provedor, r.modelo, r.tokens_entrada, r.tokens_saida) == ("claude", "sonnet", 17, 7)


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
    with pytest.raises(ErroIA) as e:
        _chamar(claude_falso, tmp_path)
    assert e.value.provedor == "claude"
    assert len(claude_falso.chamadas()) == 3


def test_limite_de_uso(claude_falso, tmp_path):
    claude_falso.responder(
        {"type": "result", "subtype": "success", "is_error": True, "result": "Claude usage limit reached"}
    )
    with pytest.raises(LimiteUso):
        _chamar(claude_falso, tmp_path)
    assert len(claude_falso.chamadas()) == 1


def test_limite_de_sessao_real(claude_falso, tmp_path):
    # mensagem real do CLI em 2026-10-23
    claude_falso.responder({
        "type": "result", "subtype": "success", "is_error": True,
        "result": "You've hit your session limit · resets 9am (America/Sao_Paulo)",
    })
    with pytest.raises(LimiteUso):
        _chamar(claude_falso, tmp_path)
    assert len(claude_falso.chamadas()) == 1


def test_binario_ausente_e_indisponivel(tmp_path):
    chamadas = []

    def executar(argv, **k):
        chamadas.append(argv)
        raise FileNotFoundError(2, "No such file", argv[0])

    with pytest.raises(IAIndisponivel):
        chamar(pedido(tmp_path), modelo=None, max_turns=5, bin="/nao/existe", raiz=tmp_path,
               executar=executar)
    assert len(chamadas) == 1  # sem retentativa: é falha do sistema

    def sem_permissao(argv, **k):
        raise PermissionError(13, "Permission denied")

    with pytest.raises(IAIndisponivel):
        chamar(pedido(tmp_path), modelo=None, max_turns=5, bin="c", raiz=tmp_path,
               executar=sem_permissao)


def test_limite_de_uso_no_stderr(tmp_path):
    def executar(argv, **k):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Error: weekly limit reached")

    with pytest.raises(LimiteUso):
        chamar(pedido(tmp_path), modelo=None, max_turns=5, bin="c", raiz=tmp_path,
               executar=executar)


def test_timeout_conta_como_tentativa_falha(tmp_path):
    def executar(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)

    with pytest.raises(ErroIA, match="timeout"):
        chamar(pedido(tmp_path), modelo=None, max_turns=5, bin="c", raiz=tmp_path,
               tentativas=2, executar=executar)


def test_argv_enxuto(tmp_path):
    a = _argv(pedido(tmp_path, web=True), tmp_path)
    for f in ("--disable-slash-commands", "--strict-mcp-config",
              "--exclude-dynamic-system-prompt-sections"):
        assert f in a
    assert a[a.index("--setting-sources") + 1] == "project"
    assert a[a.index("--tools") + 1:a.index("--disallowedTools")] == ["Read", "WebSearch", "WebFetch"]


def test_argv_tools_com_scripts(tmp_path):
    a = _argv(pedido(tmp_path, scripts=("metodo/pontuar.py",)), tmp_path)
    assert a[a.index("--tools") + 1:a.index("--disallowedTools")] == ["Read", "Bash"]


def test_argv_esforco_so_quando_definido(tmp_path):
    a = _argv(pedido(tmp_path), tmp_path, esforco="xhigh")
    assert a[a.index("--effort") + 1] == "xhigh"
    assert "--effort" not in _argv(pedido(tmp_path), tmp_path)
