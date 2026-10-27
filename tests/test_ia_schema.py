import copy
import json

from autocanal import planejador
from autocanal.etapas import visuais
from autocanal.ia import schema as schema_mod
from autocanal.ia.schema import remover_nulos_opcionais, schema_estrito

CURADORIA = visuais.CURADORIA  # schema real, com nova_cena opcional


def test_estrito_fecha_objetos_e_exige_tudo():
    item = schema_estrito(CURADORIA)["properties"]["escolhas"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"id", "candidato", "motivo", "nova_cena"}
    assert item["properties"]["nova_cena"]["type"] == ["string", "null"]
    assert item["properties"]["id"]["type"] == "string"


def test_estrito_nao_altera_original():
    antes = copy.deepcopy(CURADORIA)
    schema_estrito(CURADORIA)
    assert CURADORIA == antes


def test_estrito_sem_tipo_vira_anyof():
    s = schema_estrito({"type": "object", "properties": {"x": {"enum": ["a"]}}})
    assert s["properties"]["x"] == {"anyOf": [{"enum": ["a"]}, {"type": "null"}]}
    assert s["required"] == ["x"]
    assert s["additionalProperties"] is False


def test_estrito_remove_nao_aceitas(monkeypatch):
    monkeypatch.setattr(schema_mod, "NAO_ACEITAS", frozenset({"minLength"}))
    s = schema_estrito(planejador._schema(2))
    assert "minLength" not in json.dumps(s)


def test_remover_nulos_opcionais():
    dados = {"escolhas": [{"id": "a", "candidato": 0, "motivo": "m", "nova_cena": None}]}
    assert remover_nulos_opcionais(dados, CURADORIA) == {
        "escolhas": [{"id": "a", "candidato": 0, "motivo": "m"}]}


def test_remover_mantem_nulo_obrigatorio():
    dados = {"escolhas": [{"id": None, "candidato": 0, "motivo": "m"}]}
    assert remover_nulos_opcionais(dados, CURADORIA) == dados  # a validação é que reprova
