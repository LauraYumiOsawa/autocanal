import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autocanal import etapas, fila
from autocanal.db import conectar
from autocanal.etapas import revisao
from autocanal.etapas.base import contexto_para, gravar_json, ler_json
from autocanal.fila import Estado
from autocanal.formatos import FORMATOS
from autocanal.midia import Midia
from autocanal.produtor import produzir

PUBLICAR = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
ETAPAS = FORMATOS["short"].etapas
VENDOR = Path(__file__).parents[1] / "vendor" / "bin"
TEXTOS = [
    "O mar é salgado porque a chuva desgasta as rochas dos continentes.",
    "Os rios carregam esses sais minerais até o oceano há bilhões de anos.",
    "A água evapora, mas o sal fica para trás e se acumula.",
    "Por isso cada litro de água do mar tem cerca de trinta e cinco gramas de sal.",
]
FERRAMENTAS_REVISOR = ["Read", "WebFetch", "WebSearch"]


def _video(midia: Midia, destino: Path, duracao: int, tamanho="1080x1920", audio="tom"):
    fontes = {
        "tom": ["-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={duracao}"],
        "mudo": ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={duracao}"],
        None: [],
    }[audio]
    som = ["-af", "volume=8dB", "-c:a", "aac"] if audio == "tom" else (  # ≈ −14 LUFS
        ["-c:a", "aac"] if audio else ["-an"]
    )
    midia.rodar([
        "-f", "lavfi", "-i", f"color=c=navy:s={tamanho}:r=2:d={duracao}", *fontes,
        *som, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-shortest", str(destino),
    ])
    return destino


@pytest.fixture(scope="module")
def videos(tmp_path_factory) -> dict[str, Path]:
    midia = Midia(VENDOR / "ffmpeg", VENDOR / "ffprobe")
    pasta = tmp_path_factory.mktemp("videos")
    return {
        "ok": _video(midia, pasta / "ok.mp4", 35),
        "longo": _video(midia, pasta / "longo.mp4", 75),
        "paisagem": _video(midia, pasta / "paisagem.mp4", 35, tamanho="1920x1080"),
        "mudo": _video(midia, pasta / "mudo.mp4", 35, audio="mudo"),
        "sem_audio": _video(midia, pasta / "sem_audio.mp4", 35, audio=None),
    }


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


def _roteiro(textos=TEXTOS) -> dict:
    return {
        "gancho": textos[0], "nota_gancho": 80,
        "segmentos": [{"texto": t, "visual": "ocean waves"} for t in textos],
        "fontes": [{"afirmacao": "35 g de sal por litro", "url": "https://exemplo.org/mar"}],
        "resumo": "Por que o mar é salgado.",
    }


@pytest.fixture
def ctx(cfg, con, videos):
    """Job em revisão com todos os artefatos dentro dos limites."""
    job = fila.criar_job(con, "teste", "short", "por que o mar é salgado", PUBLICAR)
    for e in (Estado.PRODUZINDO, Estado.EM_REVISAO):
        fila.mudar_estado(con, job.id, e)
    for nome in ETAPAS[:-1]:
        fila.marcar_etapa(con, job.id, nome, "ok")
    fila.marcar_etapa(con, job.id, "revisao", "rodando")
    c = contexto_para(cfg, con, fila.obter(con, job.id))
    shutil.copy(videos["ok"], c.pasta / "video.mp4")
    gravar_json(c, "roteiro.json", _roteiro())
    gravar_json(c, "narracao.json", {"segmentos": [], "duracao": 35.0})
    gravar_json(c, "transcricao.json", {
        "texto": "…", "palavras": [{"texto": "sal.", "inicio": 34.2, "fim": 34.6}],
    })
    gravar_json(c, "fontes_midia.json", [
        {"caminho": f"visuais/seg_{i:02d}.jpg", "tipo": "imagem", "origem": "pexels",
         "url": f"https://pexels.com/{i}", "licenca": "Pexels License", "autor": "Ana"}
        for i in range(1, 5)
    ])
    gravar_json(c, "metadados.json", {
        "titulo": "Por que o mar é salgado? 3 motivos", "texto_thumb": "SAL ESCONDIDO",
        "descricao_final": "…", "tags": [], "capitulos": [], "consultas_alvo": ["a", "b", "c"],
        "nota_titulo": 100, "problemas_titulo": [], "sintetico": False,
    })
    return c


def _ok(dados):
    return {
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
        "total_cost_usd": 0.2, "duration_ms": 3000, "structured_output": dados,
    }


def _parecer(aprovado: bool, etapa: str | None = None, sintetico: bool = False, problemas=()):
    return _ok({
        "aprovado": aprovado, "nota": 90 if aprovado else 40, "problemas": list(problemas),
        "etapa_para_refazer": etapa, "contains_synthetic_media": sintetico,
    })


def _outro_job(cfg, con, canal: str, roteiro: dict, titulo: str | None = None):
    j = fila.criar_job(con, canal, "short", "outro", PUBLICAR.replace(day=1))
    pasta = cfg.dados / "jobs" / j.id
    pasta.mkdir(parents=True)
    (pasta / "roteiro.json").write_text(json.dumps(roteiro), encoding="utf-8")
    if titulo:
        (pasta / "metadados.json").write_text(json.dumps({"titulo": titulo}), encoding="utf-8")
    return j


def _revisoes(con, job_id):
    return [tuple(r) for r in con.execute(
        "SELECT rodada, aprovado, motivos_json, etapa_para_refazer FROM revisoes"
        " WHERE job_id = ? ORDER BY rodada", (job_id,),
    )]


def test_similaridade():
    a = " ".join(TEXTOS)
    assert revisao.similaridade(a, a) == 1.0
    assert revisao.similaridade(a, "Por que o céu é azul? A luz do Sol se espalha no ar.") == 0.0
    assert revisao.similaridade(a, a.upper().replace(" ", ", ")) == 1.0
    assert 0.0 < revisao.similaridade(a, " ".join(TEXTOS[:2])) < 1.0
    assert revisao.similaridade("um dois três", "um dois três") == 0.0
    cinco = "um dois três quatro cinco"
    assert revisao.similaridade(cinco, cinco, n=3) == 1.0


def test_checagens_tudo_ok(ctx, cfg, con):
    _outro_job(cfg, con, "teste", _roteiro(["Outro assunto sem relação nenhuma com isto aqui."]))
    assert revisao.checagens(ctx) == []


def test_checagem_duracao_manda_para_roteiro(ctx, videos):
    shutil.copy(videos["longo"], ctx.pasta / "video.mp4")
    problemas = revisao.checagens(ctx)
    assert [e for _, e in problemas] == ["roteiro"]
    assert "duração" in problemas[0][0]


def _trocar_video(nome):
    def mudar(ctx, cfg, con, videos):
        shutil.copy(videos[nome], ctx.pasta / "video.mp4")
    return mudar


def _transcricao_curta(ctx, cfg, con, videos):
    gravar_json(ctx, "transcricao.json", {
        "texto": "…", "palavras": [{"texto": "sal", "inicio": 33.0, "fim": 33.9}],
    })


def _titulo_fraco(ctx, cfg, con, videos):
    m = ler_json(ctx, "metadados.json")
    gravar_json(ctx, "metadados.json", {
        **m, "nota_titulo": 62, "problemas_titulo": ["no-number: sem número"],
    })


def _midia_sem_licenca(ctx, cfg, con, videos):
    fontes = ler_json(ctx, "fontes_midia.json")
    fontes[2]["licenca"] = ""
    gravar_json(ctx, "fontes_midia.json", fontes)


def _midia_faltando(ctx, cfg, con, videos):
    gravar_json(ctx, "fontes_midia.json", ler_json(ctx, "fontes_midia.json")[:3])


def _roteiro_repetido(ctx, cfg, con, videos):
    _outro_job(cfg, con, "teste", _roteiro())


@pytest.mark.parametrize(("mudar", "trecho", "etapas_esperadas"), [
    (_trocar_video("paisagem"), "resolução", ["render"]),
    (_trocar_video("mudo"), "silêncio", ["render", "render"]),  # loudness e silêncio
    (_trocar_video("sem_audio"), "sem áudio", ["render"]),
    (_transcricao_curta, "legendas", ["legendas"]),
    (_titulo_fraco, "nota 62", ["metadados"]),
    (_midia_sem_licenca, "licença", ["visuais"]),
    (_midia_faltando, "3 mídias para 4 segmentos", ["visuais"]),
    (_roteiro_repetido, "parecido", ["roteiro"]),
])
def test_checagens_mapeiam_etapa(ctx, cfg, con, videos, mudar, trecho, etapas_esperadas):
    mudar(ctx, cfg, con, videos)
    problemas = revisao.checagens(ctx)
    assert [e for _, e in problemas] == etapas_esperadas
    assert any(trecho in p for p, _ in problemas)


def test_similaridade_ignora_outros_canais(ctx, cfg, con):
    _outro_job(cfg, con, "outro", _roteiro())
    assert revisao.checagens(ctx) == []


def test_similaridade_ignora_descartados(ctx, cfg, con):
    j = _outro_job(cfg, con, "teste", _roteiro())
    fila.mudar_estado(con, j.id, Estado.DESCARTADO)
    assert revisao.checagens(ctx) == []


def test_checagens_ok_chama_claude_e_aprova(ctx, cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    _outro_job(cfg, con, "teste", _roteiro(["Assunto diferente, nada a ver."]), "Céu azul em 40 s")
    claude_falso.responder(_parecer(True, sintetico=True))
    revisao.executar(ctx)

    j = fila.obter(con, ctx.job.id)
    assert j.estado == Estado.APROVADO and j.tentativas_revisao == 1
    assert ler_json(ctx, "metadados.json")["sintetico"] is True
    assert ler_json(ctx, "revisao_1.json")["aprovado"] is True
    assert _revisoes(con, ctx.job.id) == [(1, 1, "[]", None)]
    quadros = sorted(p.name for p in (ctx.pasta / "revisao" / "quadros").iterdir())
    assert quadros == [f"quadro_{i:02d}.jpg" for i in range(1, 7)]

    [argv] = claude_falso.chamadas()
    assert argv[argv.index("--allowedTools") + 1:] == FERRAMENTAS_REVISOR
    assert f"Read(/{cfg.raiz.resolve()}/dados/*.db)" in argv
    assert argv[argv.index("--max-turns") + 1] == "30" and "--model" not in argv
    prompt = argv[2]
    assert "revisao/quadros/quadro_01.jpg" in prompt and "revisao/quadros/quadro_06.jpg" in prompt
    assert "Céu azul em 40 s" in prompt and "por que o mar é salgado" in prompt
    n = con.execute(
        "SELECT COUNT(*) FROM chamadas_claude WHERE job_id = ? AND etapa = 'revisao'",
        (ctx.job.id,),
    ).fetchone()[0]
    assert n == 1


def test_reprovado_primeira_rodada_refaz(ctx, cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_parecer(False, "visuais", problemas=["imagem não combina"]))
    revisao.executar(ctx)

    j = fila.obter(con, ctx.job.id)
    assert j.estado == Estado.PRODUZINDO and j.tentativas_revisao == 1
    # metadados não muda com visuais novos: fica `ok`
    assert [fila.estado_etapa(con, j.id, n) for n in ETAPAS] == [
        "ok", "ok", "ok", None, None, "ok", None,
    ]
    assert fila.tentativas_etapa(con, j.id, "revisao") == 0
    assert ler_json(ctx, "problemas_revisao.json") == {"problemas": ["imagem não combina"]}
    assert _revisoes(con, j.id) == [(1, 0, '["imagem não combina"]', "visuais")]
    assert ler_json(ctx, "metadados.json")["sintetico"] is False


def test_etapas_a_refazer():
    from autocanal.etapas.base import etapas_a_refazer

    ordem = ("roteiro", "narracao", "legendas", "visuais", "render", "metadados", "revisao")
    assert etapas_a_refazer(ordem, "roteiro") == list(ordem)
    assert etapas_a_refazer(ordem, "legendas") == ["legendas", "visuais", "render", "revisao"]
    assert etapas_a_refazer(ordem, "render") == ["render", "revisao"]
    assert etapas_a_refazer(ordem, "metadados") == ["metadados", "revisao"]


def test_reprovado_sem_etapa_refaz_tudo(ctx, cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_parecer(False, None, problemas=["sem valor próprio"]))
    revisao.executar(ctx)
    assert fila.obter(con, ctx.job.id).estado == Estado.PRODUZINDO
    assert all(fila.estado_etapa(con, ctx.job.id, n) is None for n in ETAPAS)
    assert _revisoes(con, ctx.job.id)[0][3] == "roteiro"


def test_segunda_reprovacao_quarentena(ctx, cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    fila.atualizar(con, ctx.job.id, tentativas_revisao=1)
    claude_falso.responder(_parecer(False, "metadados", problemas=["título enganoso"]))
    revisao.executar(ctx)

    j = fila.obter(con, ctx.job.id)
    assert j.estado == Estado.QUARENTENA and j.tentativas_revisao == 2
    assert ler_json(ctx, "revisao_2.json")["problemas"] == ["título enganoso"]
    assert _revisoes(con, j.id) == [(2, 0, '["título enganoso"]', "metadados")]
    assert fila.estado_etapa(con, j.id, "metadados") == "ok"
    assert not (ctx.pasta / "problemas_revisao.json").exists()


def test_checagem_falha_nao_chama_claude(ctx, cfg, con, claude_falso, videos):
    cfg.claude.bin = claude_falso.claude_bin
    shutil.copy(videos["longo"], ctx.pasta / "video.mp4")
    _titulo_fraco(ctx, cfg, con, videos)
    revisao.executar(ctx)

    assert claude_falso.chamadas() == []
    assert fila.obter(con, ctx.job.id).estado == Estado.PRODUZINDO
    # duração → roteiro e título → metadados: refaz a partir da mais cedo
    assert all(fila.estado_etapa(con, ctx.job.id, n) is None for n in ETAPAS)
    problemas = ler_json(ctx, "problemas_revisao.json")["problemas"]
    assert len(problemas) == 2 and "duração" in problemas[0] and "nota 62" in problemas[1]
    r = ler_json(ctx, "revisao_1.json")
    assert r["origem"] == "checagens" and r["etapa_para_refazer"] == "roteiro"
    assert not (ctx.pasta / "revisao" / "quadros").exists()


def test_reprovacao_refaz_so_as_etapas_resetadas(ctx, cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_parecer(False, "visuais", problemas=["imagem fora"]), _parecer(True))
    chamadas: list[str] = []

    def falsa(nome):
        return lambda c: chamadas.append(nome)

    registro = {n: falsa(n) for n in ETAPAS} | {"revisao": revisao.executar}
    fila.resetar_etapas(con, ctx.job.id, ["revisao"])  # o fixture a deixa `rodando`
    assert produzir(cfg, con, ctx.job.id, registro=registro) == Estado.APROVADO
    assert chamadas == ["visuais", "render"]  # metadados não depende dos visuais
    assert len(claude_falso.chamadas()) == 2
    j = fila.obter(con, ctx.job.id)
    assert j.tentativas_revisao == 2
    assert all(fila.estado_etapa(con, j.id, n) == "ok" for n in ETAPAS)
    assert [r[:2] for r in _revisoes(con, j.id)] == [(1, 0), (2, 1)]


def test_registro_tem_revisao():
    assert etapas.REGISTRO["revisao"] is revisao.executar


def test_revisor_pode_pedir_refazer_legendas():
    enum = revisao.REVISAO["properties"]["etapa_para_refazer"]["enum"]
    assert "legendas" in enum
    prompt = Path(revisao.__file__).parent.parent / "prompts" / "revisao.md"
    assert "`legendas`" in prompt.read_text(encoding="utf-8")
