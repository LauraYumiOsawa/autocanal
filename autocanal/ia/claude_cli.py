"""Adaptador do Claude Code (`claude -p` headless): traduz o `Pedido` para o CLI."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autocanal.ia.base import (
    LIMITE,
    Capacidades,
    ErroIA,
    IAIndisponivel,
    LimiteUso,
    Pedido,
    Resultado,
    lista_imagens,
    validar,
)
from autocanal.ia.cofre import caminhos_proibidos

NOME = "claude"
CAPACIDADES = Capacidades(web=True, imagens=True, scripts=True)


def _permitidas(pedido: Pedido) -> list[str]:
    return [
        "Read",
        *(["WebSearch", "WebFetch"] if pedido.web else []),
        *(f"Bash(python3 {s}:*)" for s in pedido.scripts),
    ]


def _negadas(raiz: Path) -> list[str]:
    """Regras `--disallowedTools` do cofre; caminhos absolutos usam `//` na sintaxe do CLI."""
    return [f"{ferr}(/{alvo})" for alvo in caminhos_proibidos(raiz)
            for ferr in ("Read", "Edit", "Write")]


def montar_argv(
    pedido: Pedido, *, modelo: str | None, max_turns: int, bin: str, raiz: Path
) -> list[str]:
    return (
        [
            bin, "-p", pedido.prompt + lista_imagens(pedido),
            "--output-format", "json",
            "--json-schema", json.dumps(pedido.schema),
            "--no-session-persistence",
            "--max-turns", str(max_turns),
        ]
        + (["--model", modelo] if modelo else [])
        + ["--disallowedTools", *_negadas(raiz)]
        + ["--allowedTools", *_permitidas(pedido)]
    )


def _tokens(uso: object) -> tuple[int | None, int | None]:
    if not isinstance(uso, dict):
        return None, None
    entrada = sum(int(uso.get(k) or 0) for k in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return entrada, int(uso.get("output_tokens") or 0)


def _interpretar(stdout: str, pedido: Pedido, modelo: str | None) -> Resultado:
    """Converte a saída do CLI em Resultado; ErroIA se inválida."""
    try:
        saida = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ErroIA(NOME, f"saída não é JSON: {e}") from e
    if not isinstance(saida, dict):
        raise ErroIA(NOME, "saída JSON não é um objeto")
    texto = str(saida.get("result") or "")
    if saida.get("is_error"):
        if LIMITE.search(texto):
            raise LimiteUso(NOME, texto)
        raise ErroIA(NOME, f"claude retornou erro ({saida.get('subtype')}): {texto}")
    dados = saida.get("structured_output")
    if dados is None:
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ErroIA(NOME, f"sem structured_output e result não é JSON: {e}") from e
    validar(NOME, dados, pedido.schema)
    entrada, saida_tokens = _tokens(saida.get("usage"))
    return Resultado(
        dados=dados,
        provedor=NOME,
        modelo=modelo,
        tokens_entrada=entrada,
        tokens_saida=saida_tokens,
        custo_usd=float(saida.get("total_cost_usd") or 0.0),
        turnos=int(saida.get("num_turns") or 0),
        duracao_s=float(saida.get("duration_ms") or 0) / 1000,
    )


def chamar(
    pedido: Pedido,
    *,
    modelo: str | None,
    max_turns: int,
    bin: str,
    raiz: Path,
    tentativas: int = 3,
    executar=subprocess.run,
) -> Resultado:
    argv = montar_argv(pedido, modelo=modelo, max_turns=max_turns, bin=bin, raiz=raiz)
    motivo = "nenhuma tentativa feita"
    for _ in range(tentativas):
        try:
            proc = executar(argv, cwd=pedido.pasta, capture_output=True, text=True,
                            timeout=pedido.timeout_s)
        except subprocess.TimeoutExpired:
            motivo = f"timeout após {pedido.timeout_s}s"
            continue
        except (FileNotFoundError, PermissionError) as e:
            raise IAIndisponivel(NOME, f"não consegui executar {bin}: {type(e).__name__}") from None
        try:
            return _interpretar(proc.stdout, pedido, modelo)
        except LimiteUso:
            raise
        except ErroIA as e:
            if proc.returncode != 0 and LIMITE.search(proc.stderr or ""):
                raise LimiteUso(NOME, (proc.stderr or "").strip()[:500]) from None
            motivo = e.msg
            if proc.returncode != 0 and not proc.stdout.strip():
                motivo = f"claude saiu com código {proc.returncode}: {proc.stderr.strip()[:500]}"
    raise ErroIA(NOME, f"falhou após {tentativas} tentativas: {motivo}")
