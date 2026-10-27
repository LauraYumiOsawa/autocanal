"""Tipos comuns do núcleo de IA: o pedido da etapa, o resultado e os erros, sem provedor."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import jsonschema

LIMITE = re.compile(
    r"(?i)usage limit|rate limit|session limit|limit reached|hour limit|weekly limit"
    r"|hit your [^.]{0,40}limit"
)


@dataclass(frozen=True)
class Pedido:
    prompt: str
    schema: dict
    pasta: Path                       # onde roda; a única pasta do projeto que a IA lê
    web: bool = False                 # pesquisa na web e leitura de páginas
    imagens: tuple[Path, ...] = ()    # o adaptador decide como entregar
    metodos: tuple[str, ...] = ()     # skills do plugin copiadas para <pasta>/metodo/
    scripts: tuple[str, ...] = ()     # relativos à pasta, ex.: "metodo/pontuar.py"
    timeout_s: int = 900


@dataclass(frozen=True)
class Resultado:
    dados: dict
    provedor: str
    modelo: str | None
    tokens_entrada: int | None        # total de entrada, cache incluído
    tokens_saida: int | None
    custo_usd: float | None           # None quando o provedor não informa (Codex)
    turnos: int | None                # None quando o provedor não informa (Codex)
    duracao_s: float


@dataclass(frozen=True)
class Capacidades:
    web: bool
    imagens: bool
    scripts: bool


class ErroIA(Exception):
    def __init__(self, provedor: str, msg: str):
        super().__init__(f"{provedor}: {msg}")
        self.provedor = provedor
        self.msg = msg


class LimiteUso(ErroIA):
    """Limite de uso do plano, ou provedor ainda pausado: não é culpa do job."""


class IAIndisponivel(ErroIA):
    """O provedor não pode rodar (binário ausente, sem permissão, método ausente)."""


def lista_imagens(pedido: Pedido) -> str:
    """Lista numerada das imagens (caminhos relativos à pasta) para o fim do prompt."""
    if not pedido.imagens:
        return ""
    linhas = [f"{i}. `{img.relative_to(pedido.pasta)}`" for i, img in enumerate(pedido.imagens, 1)]
    return "\n\nImagens deste pedido, na ordem:\n" + "\n".join(linhas)


def validar(provedor: str, dados: object, schema: dict) -> None:
    try:
        jsonschema.validate(dados, schema)
    except jsonschema.ValidationError as e:
        raise ErroIA(provedor, f"saída viola o schema: {e.message}") from e
