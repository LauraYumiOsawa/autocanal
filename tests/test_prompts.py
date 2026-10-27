import re
from pathlib import Path

import pytest

PROMPTS = sorted((Path(__file__).parent.parent / "autocanal" / "prompts").glob("*.md"))
DIALETO = re.compile(r"\b(Read|WebSearch|WebFetch|Skill|skills?)\b|youtube-agent:|dir_plugin")


@pytest.mark.parametrize("arq", PROMPTS, ids=lambda p: p.name)
def test_prompts_sem_dialeto_do_claude(arq):
    achado = DIALETO.search(arq.read_text(encoding="utf-8"))
    assert achado is None, achado
