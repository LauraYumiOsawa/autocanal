"""Wrapper do `claude -p` (headless): saída estruturada, validação e retentativas."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

_LIMITE = re.compile(
    r"(?i)usage limit|rate limit|session limit|limit reached|hour limit|weekly limit"
    r"|hit your [^.]{0,40}limit"
)


@dataclass(frozen=True)
class ResultadoClaude:
    dados: dict
    custo_usd: float
    turnos: int
    duracao_s: float


class ErroClaude(Exception):
    pass


class LimiteUsoClaude(ErroClaude):
    pass


class ErroClaudeIndisponivel(ErroClaude):
    """O CLI do Claude não pode rodar (binário ausente/sem permissão): falha do sistema."""


# Credenciais e dados pessoais fora do projeto que o Claude headless nunca precisa ler.
PASTAS_PESSOAIS = (".claude", ".ssh", ".config", ".aws", ".gnupg", ".docker", ".kube",
                   ".local/share", "Downloads", "Documents", "Desktop")
ARQUIVOS_PESSOAIS = (".netrc", ".git-credentials", ".bash_history")


def regras_negadas(raiz: Path) -> list[str]:
    """Regras `--disallowedTools` que impedem o Claude headless de ler/alterar segredos.

    Caminhos absolutos usam `//` na sintaxe de permissões do CLI.
    """
    raiz = Path(raiz).resolve()
    casa = Path.home()
    alvos = [
        f"/{raiz}/dados/tokens/**",
        f"/{raiz}/config/**",
        f"/{raiz}/dados/*.db",
        *(f"/{casa}/{pasta}/**" for pasta in PASTAS_PESSOAIS),
        *(f"/{casa}/{arquivo}" for arquivo in ARQUIVOS_PESSOAIS),
    ]
    return [f"{ferr}({alvo})" for alvo in alvos for ferr in ("Read", "Edit", "Write")]


def montar_argv(
    prompt: str,
    schema: dict,
    ferramentas: list[str],
    *,
    modelo: str | None,
    max_turns: int,
    claude_bin: str,
    raiz: Path | None = None,
) -> list[str]:
    return (
        [
            claude_bin, "-p", prompt,
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--no-session-persistence",
            "--max-turns", str(max_turns),
        ]
        + (["--model", modelo] if modelo else [])
        + (["--disallowedTools", *regras_negadas(raiz)] if raiz is not None else [])
        + ["--allowedTools", *ferramentas]
    )


def _interpretar(stdout: str, schema: dict) -> ResultadoClaude:
    """Converte a saída do CLI em ResultadoClaude; ErroClaude se inválida."""
    try:
        saida = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ErroClaude(f"saída não é JSON: {e}") from e
    if not isinstance(saida, dict):
        raise ErroClaude("saída JSON não é um objeto")
    texto = str(saida.get("result") or "")
    if saida.get("is_error"):
        if _LIMITE.search(texto):
            raise LimiteUsoClaude(texto)
        raise ErroClaude(f"claude retornou erro ({saida.get('subtype')}): {texto}")
    dados = saida.get("structured_output")
    if dados is None:
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as e:
            raise ErroClaude(f"sem structured_output e result não é JSON: {e}") from e
    try:
        jsonschema.validate(dados, schema)
    except jsonschema.ValidationError as e:
        raise ErroClaude(f"saída viola o schema: {e.message}") from e
    return ResultadoClaude(
        dados=dados,
        custo_usd=float(saida.get("total_cost_usd") or 0.0),
        turnos=int(saida.get("num_turns") or 0),
        duracao_s=float(saida.get("duration_ms") or 0) / 1000,
    )


def chamar(
    prompt: str,
    schema: dict,
    ferramentas: list[str],
    cwd: Path,
    *,
    modelo: str | None = None,
    max_turns: int = 20,
    claude_bin: str = "claude",
    tentativas: int = 3,
    timeout_s: int = 900,
    raiz: Path | None = None,
    executar=subprocess.run,
) -> ResultadoClaude:
    argv = montar_argv(
        prompt, schema, ferramentas,
        modelo=modelo, max_turns=max_turns, claude_bin=claude_bin, raiz=raiz,
    )
    motivo = "nenhuma tentativa feita"
    for _ in range(tentativas):
        try:
            proc = executar(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            motivo = f"timeout após {timeout_s}s"
            continue
        except (FileNotFoundError, PermissionError) as e:
            raise ErroClaudeIndisponivel(
                f"não consegui executar {claude_bin}: {type(e).__name__}"
            ) from None
        try:
            return _interpretar(proc.stdout, schema)
        except LimiteUsoClaude:
            raise
        except ErroClaude as e:
            if proc.returncode != 0 and _LIMITE.search(proc.stderr or ""):
                raise LimiteUsoClaude((proc.stderr or "").strip()[:500]) from None
            motivo = str(e)
            if proc.returncode != 0 and not proc.stdout.strip():
                motivo = f"claude saiu com código {proc.returncode}: {proc.stderr.strip()[:500]}"
    raise ErroClaude(f"falhou após {tentativas} tentativas: {motivo}")


def registrar(con: sqlite3.Connection, job_id: str | None, etapa: str, r: ResultadoClaude) -> None:
    con.execute(
        "INSERT INTO chamadas_claude (job_id, etapa, custo_usd, turnos, duracao_s, criado_em)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, etapa, r.custo_usd, r.turnos, r.duracao_s, datetime.now(UTC).isoformat()),
    )
    con.commit()
