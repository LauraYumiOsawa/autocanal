import subprocess
import sys
from pathlib import Path
from datetime import UTC, datetime

import pytest

from autocanal import etapas, fila
from autocanal.ia import ErroIA
from autocanal.db import conectar
from autocanal.etapas import metadados
from autocanal.etapas.base import ErroEtapa, contexto_para, gravar_json, ler_json
from autocanal.ferramentas_plugin import nota_titulo
from autocanal.texto import sanitizar_texto

PUBLICAR = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
URLS = ["https://exemplo.org/mar", "https://exemplo.org/rios"]


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


@pytest.fixture
def ctx(cfg, con):
    job = fila.criar_job(con, "teste", "short", "por que o mar é salgado", PUBLICAR)
    return contexto_para(cfg, con, job)


def _ok(dados):
    return {
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
        "total_cost_usd": 0.2, "duration_ms": 3000, "structured_output": dados,
    }


def _metadados(**extra) -> dict:
    return {
        "titulo": "Por que o mar é salgado? 3 motivos",
        "texto_thumb": "SAL ESCONDIDO",
        "cena_thumb": "salt crystals glowing on a dark rock by the sea",
        "descricao": "O sal do mar vem das rochas <de verdade>.",
        "tags": ["mar", "Mar", "", "oceano"],
        "consultas_alvo": ["por que o mar é salgado", "sal do mar", "oceano salgado"],
        **extra,
    }


def test_sanitizar_texto():
    assert sanitizar_texto('A <b> & "c" 🚀\x07') == 'A ‹b› & "c" 🚀'


def test_descricao_final_com_fontes_e_shorts():
    d = metadados.descricao_final("Resumo do vídeo.", [], [*URLS, URLS[0]], "short")
    assert "Fontes:" in d
    assert all(u in d for u in URLS) and d.count(URLS[0]) == 1
    assert d.endswith("#shorts")
    assert d == (
        "Resumo do vídeo.\n\nFontes:\nhttps://exemplo.org/mar\nhttps://exemplo.org/rios"
        "\n\n#shorts"
    )


def test_descricao_final_com_capitulos_sem_fontes_nem_shorts():
    capitulos = [{"tempo": 0, "titulo": "Início"}, {"tempo": 75.4, "titulo": "O <fim>"}]
    d = metadados.descricao_final("Oi\x07.", capitulos, [], "medio")
    assert d == "Oi.\n\n00:00 Início\n01:15 O ‹fim›"


def test_tags_cortadas_em_500():
    tags = [f"tag{i:02d}".ljust(40, "x") for i in range(15)]
    cortadas = metadados.cortar_tags(tags)
    assert sum(len(t) for t in cortadas) + len(cortadas) - 1 <= 500
    assert cortadas == tags[:12]


def test_tags_sem_vazias_nem_repetidas():
    assert metadados.cortar_tags(["IA", "", "  ", "ia", " robôs "]) == ["IA", "robôs"]


def test_nota_titulo_real_do_plugin(cfg):
    nota, problemas = nota_titulo(cfg, "Como a IA vai mudar seu trabalho em 2027", "ROBÔ CHEFE")
    assert nota == 100 and problemas == []
    nota, problemas = nota_titulo(cfg, "O mar é salgado", None)
    assert nota < 100 and any(p.startswith("no-number: ") for p in problemas)


def test_ferramentas_plugin_importa_primeiro():
    # `ferramentas_plugin` → `etapas.base` → `etapas` → `metadados` → `ferramentas_plugin`
    r = subprocess.run(
        [sys.executable, "-c", "import autocanal.ferramentas_plugin"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_nota_titulo_sem_script_falha(cfg, tmp_path):
    cfg.plugin_dir = tmp_path
    with pytest.raises(ErroEtapa, match="title.py"):
        nota_titulo(cfg, "Título", None)


def test_metadados_grava_json(ctx, cfg, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    gravar_json(ctx, "roteiro.json", {
        "segmentos": [{"texto": "o mar é salgado", "visual": "sea"}],
        "fontes": [{"afirmacao": "a", "url": URLS[0]}, {"afirmacao": "b", "url": URLS[1]},
                   {"afirmacao": "c", "url": URLS[0]}],
    })
    gravar_json(ctx, "problemas_revisao.json", {"problemas": ["título fraco"]})
    claude_falso.responder(_ok(_metadados()))
    metadados.executar(ctx)

    m = ler_json(ctx, "metadados.json")
    assert m == {
        "titulo": "Por que o mar é salgado? 3 motivos",
        "texto_thumb": "SAL ESCONDIDO",
        "cena_thumb": "salt crystals glowing on a dark rock by the sea",
        "descricao": "O sal do mar vem das rochas ‹de verdade›.",
        "descricao_final": (
            "O sal do mar vem das rochas ‹de verdade›.\n\nFontes:\n"
            f"{URLS[0]}\n{URLS[1]}\n\n#shorts"
        ),
        "tags": ["mar", "oceano"],
        "capitulos": [],
        "consultas_alvo": ["por que o mar é salgado", "sal do mar", "oceano salgado"],
        "nota_titulo": 100,
        "problemas_titulo": [],
        "sintetico": False,
    }
    argv = claude_falso.chamadas()[0]
    assert argv[argv.index("--allowedTools") + 1:] == [
        "Read", "Bash(python3 metodo/yt-package/title.py:*)"
    ]
    for nome in ("yt-package", "yt-seo", "yt-chapters"):
        assert (ctx.pasta / "metodo" / nome / "SKILL.md").exists()
    assert f"Read(/{cfg.raiz.resolve()}/config/**)" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--max-turns") + 1] == "20"
    prompt = argv[2]
    assert "por que o mar é salgado" in prompt and "título fraco" in prompt
    assert "python3 metodo/yt-package/title.py --json" in prompt
    n = ctx.con.execute(
        "SELECT COUNT(*) FROM chamadas_ia WHERE job_id = ? AND etapa = 'metadados'",
        (ctx.job.id,),
    ).fetchone()[0]
    assert n == 1


def test_metadados_rejeita_schema_invalido(ctx, cfg, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    gravar_json(ctx, "roteiro.json", {"segmentos": [], "fontes": []})
    ruim = _ok(_metadados(consultas_alvo=["só uma"]))
    claude_falso.responder(ruim, ruim, ruim)
    with pytest.raises(ErroIA, match="schema"):
        metadados.executar(ctx)
    assert not (ctx.pasta / "metadados.json").exists()


@pytest.mark.parametrize(("extra", "erro"), [
    ({"titulo": " \x07 "}, "título vazio"),
    ({"descricao": "é" * 4500}, "bytes"),  # 4500 caracteres, 9000 bytes
])
def test_metadados_limites_do_youtube(ctx, cfg, claude_falso, extra, erro):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    gravar_json(ctx, "roteiro.json", {"segmentos": [], "fontes": []})
    claude_falso.responder(_ok(_metadados(**extra)))
    with pytest.raises(ErroEtapa, match=erro):
        metadados.executar(ctx)
    assert not (ctx.pasta / "metadados.json").exists()


def test_registro_tem_metadados():
    assert etapas.REGISTRO["metadados"] is metadados.executar


def _rot_nar(inicios, duracao):
    """Roteiro com uma seção por início (2 segmentos cada) e a narração correspondente."""
    segs, tempos = [], []
    for i, t in enumerate(inicios):
        for k in range(2):
            segs.append({"texto": "x", "visual": "v", "cena": "c", "secao": i})
            tempos.append({"arquivo": "a.wav", "inicio": t + k, "fim": t + k + 0.9})
    return ({"secoes": [f"S{i}" for i in range(len(inicios))], "segmentos": segs},
            {"segmentos": tempos, "duracao": duracao})


def test_capitulos_basico():
    caps = metadados.capitulos_calculados(*_rot_nar([0, 40, 95], 150))
    assert caps == [{"tempo": 0, "titulo": "S0"}, {"tempo": 40, "titulo": "S1"},
                    {"tempo": 95, "titulo": "S2"}]


def test_capitulos_funde_curta_do_meio():
    caps = metadados.capitulos_calculados(*_rot_nar([0, 40, 45, 100], 160))
    assert [(c["tempo"], c["titulo"]) for c in caps] == [(0, "S0"), (45, "S2"), (100, "S3")]


def test_capitulos_funde_primeira_curta():
    caps = metadados.capitulos_calculados(*_rot_nar([0, 6, 60, 120], 180))
    assert [(c["tempo"], c["titulo"]) for c in caps] == [(0, "S1"), (60, "S2"), (120, "S3")]


def test_capitulos_menos_de_3_vazio():
    assert metadados.capitulos_calculados(*_rot_nar([0, 50], 100)) == []


@pytest.mark.parametrize("origens, esperado", [
    (["pixabay", "procedural"], "Imagens: Pixabay"),
    (["flux"], "As imagens deste vídeo foram geradas por IA."),
    (["flux", "pixabay"], "Imagens: Pixabay\nAlgumas imagens deste vídeo foram geradas por IA."),
    # prova real: 9 de 11 do FLUX e o aviso "Algumas" contradizia o revisor
    (["flux", "flux", "pixabay"], "Imagens: Pixabay\nA maioria das imagens deste vídeo foi gerada por IA."),
    (["procedural"], ""),
])
def test_creditos(origens, esperado):
    assert metadados.creditos([{"origem": o} for o in origens]) == esperado


def test_descricao_creditos_antes_de_fontes():
    d = metadados.descricao_final("Resumo.", [], [URLS[0]], "medio", "Imagens: Pixabay")
    assert d == f"Resumo.\n\nImagens: Pixabay\n\nFontes:\n{URLS[0]}"


def test_metadados_medio_calcula_capitulos_e_sintetico(cfg, con, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    job = fila.criar_job(con, "teste", "medio", "por que o mar é salgado", PUBLICAR)
    c = contexto_para(cfg, con, job)
    roteiro, narracao = _rot_nar([0, 40, 95], 150)
    gravar_json(c, "roteiro.json", {**roteiro, "fontes": [{"afirmacao": "a", "url": URLS[0]}]})
    gravar_json(c, "narracao.json", narracao)
    gravar_json(c, "fontes_midia.json", [{"origem": "flux"}, {"origem": "pixabay"}])
    claude_falso.responder(_ok(_metadados(capitulos=[{"tempo": 3, "titulo": "do Claude"}])))
    metadados.executar(c)
    m = ler_json(c, "metadados.json")
    assert [(x["tempo"], x["titulo"]) for x in m["capitulos"]] == [(0, "S0"), (40, "S1"), (95, "S2")]
    assert m["sintetico"] is True
    assert m["cena_thumb"] == "salt crystals glowing on a dark rock by the sea"
    assert "00:40 S1" in m["descricao_final"]
    assert "Imagens: Pixabay\nAlgumas imagens deste vídeo foram geradas por IA." in m["descricao_final"]
    assert not m["descricao_final"].endswith("#shorts")


def test_metadados_guarda_descricao_base(ctx, cfg, claude_falso):
    cfg.ia.provedores["claude"].bin = claude_falso.claude_bin
    gravar_json(ctx, "roteiro.json", {"segmentos": [{"texto": "x", "visual": "v"}], "fontes": []})
    claude_falso.responder(_ok(_metadados()))
    metadados.executar(ctx)
    assert ler_json(ctx, "metadados.json")["descricao"] == "O sal do mar vem das rochas ‹de verdade›."


def test_prompt_metadados_deixa_creditos_para_o_sistema():
    texto = (Path(metadados.__file__).parents[1] / "prompts" / "metadados.md").read_text(encoding="utf-8")
    assert "avisos de IA" in texto
