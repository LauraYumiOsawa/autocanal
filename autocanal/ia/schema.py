"""Schema no modo estrito (exigido pelo Codex) e limpeza dos nulos que ele devolve."""
from __future__ import annotations

import copy

# palavras-chave que o modo estrito recusa (preenchido pela sondagem do Codex)
NAO_ACEITAS: frozenset[str] = frozenset()
_COMBINADORES = ("anyOf", "oneOf", "allOf")


def _anulavel(no: dict) -> dict:
    if "type" not in no:
        return {"anyOf": [no, {"type": "null"}]}
    tipos = list(no["type"]) if isinstance(no["type"], list) else [no["type"]]
    novo = {**no, "type": tipos if "null" in tipos else [*tipos, "null"]}
    if "enum" in novo and None not in novo["enum"]:
        novo["enum"] = [*novo["enum"], None]
    return novo


def _estrito(no: dict) -> dict:
    novo = {k: v for k, v in no.items() if k not in NAO_ACEITAS}
    if "properties" in novo:
        obrigatorias = set(novo.get("required", []))
        props = {}
        for nome, sub in novo["properties"].items():
            sub = _estrito(sub)
            props[nome] = sub if nome in obrigatorias else _anulavel(sub)
        novo["properties"] = props
        novo["required"] = list(props)
        novo["additionalProperties"] = False
    if isinstance(novo.get("items"), dict):
        novo["items"] = _estrito(novo["items"])
    for chave in _COMBINADORES:
        if chave in novo:
            novo[chave] = [_estrito(x) for x in novo[chave]]
    if "$defs" in novo:
        novo["$defs"] = {k: _estrito(v) for k, v in novo["$defs"].items()}
    return novo


def schema_estrito(schema: dict) -> dict:
    """Cópia aceita pelo modo estrito: objetos fechados, tudo obrigatório, opcionais anuláveis."""
    return _estrito(copy.deepcopy(schema))


def remover_nulos_opcionais(dados: object, schema: dict) -> object:
    """Cópia de `dados` sem as chaves `null` que eram opcionais no schema original."""
    if isinstance(dados, dict) and "properties" in schema:
        obrigatorias = set(schema.get("required", []))
        saida = {}
        for k, v in dados.items():
            if v is None and k not in obrigatorias:
                continue
            sub = schema["properties"].get(k)
            saida[k] = remover_nulos_opcionais(v, sub) if isinstance(sub, dict) else v
        return saida
    if isinstance(dados, list) and isinstance(schema.get("items"), dict):
        return [remover_nulos_opcionais(x, schema["items"]) for x in dados]
    return dados
