from datetime import UTC, datetime

import pytest

from autocanal import grade


def _canal(cfg):
    return cfg.canais["teste"]


def test_slots_semana(cfg):
    s = grade.slots(_canal(cfg), "short", datetime(2026, 10, 26, tzinfo=UTC),
                    datetime(2026, 11, 2, tzinfo=UTC), cfg.fuso)
    assert len(s) == 3
    assert s[0] == datetime(2026, 10, 26, 15, tzinfo=UTC)
    assert s == sorted(s) and all(x.tzinfo is not None for x in s)


def test_slots_exclui_fim_e_formato_outro(cfg):
    c = _canal(cfg)
    assert grade.slots(c, "short", datetime(2026, 10, 26, tzinfo=UTC),
                       datetime(2026, 10, 26, 15, tzinfo=UTC), cfg.fuso) == []
    assert grade.slots(c, "longo", datetime(2026, 10, 26, tzinfo=UTC),
                       datetime(2026, 11, 2, tzinfo=UTC), cfg.fuso) == []


def test_slots_dst(cfg):
    # horário de verão do Brasil já não existe, mas o fuso é lido por zoneinfo por data
    s = grade.slots(_canal(cfg), "short", datetime(2026, 1, 5, tzinfo=UTC),
                    datetime(2026, 1, 6, tzinfo=UTC), cfg.fuso)
    assert s == [datetime(2026, 1, 5, 15, tzinfo=UTC)]


def test_proximo_slot_pula_ocupado(cfg):
    c = _canal(cfg)
    ocupado = {datetime(2026, 10, 26, 15, tzinfo=UTC)}
    r = grade.proximo_slot(c, "short", datetime(2026, 10, 26, tzinfo=UTC), ocupado, cfg.fuso)
    assert r == datetime(2026, 10, 28, 15, tzinfo=UTC)


def test_proximo_slot_inclusivo(cfg):
    r = grade.proximo_slot(_canal(cfg), "short", datetime(2026, 10, 26, 15, tzinfo=UTC), set(), cfg.fuso)
    assert r == datetime(2026, 10, 26, 15, tzinfo=UTC)


def test_proximo_slot_sem_grade(cfg):
    with pytest.raises(ValueError):
        grade.proximo_slot(_canal(cfg), "longo", datetime(2026, 10, 26, tzinfo=UTC), set(), cfg.fuso)
