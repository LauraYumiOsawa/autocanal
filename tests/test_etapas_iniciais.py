import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autocanal import etapas, fila
from autocanal.db import conectar
from autocanal.etapas import legendas, narracao, roteiro, visuais
from autocanal.etapas.base import (
    ErroEtapa,
    contexto_para,
    gravar_json,
    ler_json,
    perfil_voz,
    titulos_recentes,
)
from autocanal.formatos import FORMATOS
from autocanal.legendas import Palavra
from autocanal.provedores import ErroProvedor
from autocanal.provedores.tts import TTSComReserva, TTSFalso
from autocanal.texto import contar_palavras, sanitizar_texto

PUBLICAR = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
TEMPLATE = Path(__file__).parents[1] / "autocanal" / "prompts" / "roteiro_short.md"


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


def _roteiro(palavras: int, n_segmentos: int = 6) -> dict:
    por_seg = palavras // n_segmentos
    segs = []
    for s in range(n_segmentos):
        n = por_seg if s < n_segmentos - 1 else palavras - por_seg * (n_segmentos - 1)
        segs.append({"texto": " ".join(f"palavra{s}x{i}" for i in range(n)),
                     "visual": f"ocean view {s}", "cena": f"a calm ocean view number {s}"})
    return {
        "gancho": segs[0]["texto"].split()[0], "nota_gancho": 72, "segmentos": segs,
        "fontes": [{"afirmacao": "o mar é salgado", "url": "https://exemplo.org/mar"}],
        "resumo": "Por que o mar é salgado.",
    }


def _segmentos(ctx, textos, visuais_=None):
    visuais_ = visuais_ or [f"visual {i}" for i in range(len(textos))]
    dados = _roteiro(100)
    dados["segmentos"] = [{"texto": t, "visual": v} for t, v in zip(textos, visuais_)]
    gravar_json(ctx, "roteiro.json", dados)


def test_formato_short():
    f = FORMATOS["short"]
    assert (f.largura, f.altura, f.duracao_min_s, f.duracao_max_s) == (1080, 1920, 30, 60)
    assert (f.palavras_min, f.palavras_max, f.orientacao) == (75, 150, "retrato")
    assert f.etapas == (
        "roteiro", "narracao", "legendas", "visuais", "render", "metadados", "revisao"
    )


def test_texto_utilitarios():
    assert contar_palavras("Olá, mundo — tudo  bem?\nSim.") == 5
    assert contar_palavras("") == 0
    assert sanitizar_texto("a<b>c\x00\td\x7f\ne\r") == "a‹b›cd\ne"


def test_registro_tem_etapas_iniciais():
    for nome, mod in (("roteiro", roteiro), ("narracao", narracao),
                      ("legendas", legendas), ("visuais", visuais)):
        assert etapas.REGISTRO[nome] is mod.executar


def test_contexto_cria_pasta_e_json(ctx, cfg):
    assert ctx.pasta == cfg.dados / "jobs" / ctx.job.id and ctx.pasta.is_dir()
    assert ctx.canal.slug == "teste" and ctx.formato is FORMATOS["short"]
    caminho = gravar_json(ctx, "x.json", {"á": [1, 2]})
    assert caminho == ctx.pasta / "x.json"
    assert ler_json(ctx, "x.json") == {"á": [1, 2]}
    with pytest.raises(ErroEtapa, match="y.json"):
        ler_json(ctx, "y.json")


def test_contexto_canal_desconhecido(cfg, con):
    job = fila.criar_job(con, "sumido", "short", "tema", PUBLICAR)
    with pytest.raises(ErroEtapa, match="sumido"):
        contexto_para(cfg, con, job)


def test_titulos_recentes_e_perfil_voz(ctx, cfg, con):
    assert titulos_recentes(ctx) == []
    for i in range(3):
        j = fila.criar_job(con, "teste", "short", f"t{i}", PUBLICAR.replace(day=3 + i))
        pasta = cfg.dados / "jobs" / j.id
        pasta.mkdir(parents=True)
        (pasta / "metadados.json").write_text(json.dumps({"titulo": f"Título {i}"}))
    outro = fila.criar_job(con, "outro", "short", "x", PUBLICAR)
    (cfg.dados / "jobs" / outro.id).mkdir(parents=True)
    (cfg.dados / "jobs" / outro.id / "metadados.json").write_text('{"titulo": "Alheio"}')
    assert titulos_recentes(ctx) == ["Título 2", "Título 1", "Título 0"]
    assert titulos_recentes(ctx, n=2) == ["Título 2", "Título 1"]

    assert perfil_voz(ctx).strip()  # arquivo ausente → texto padrão
    ctx.canal.perfil_voz.parent.mkdir(parents=True, exist_ok=True)
    ctx.canal.perfil_voz.write_text("Fala como pirata.", encoding="utf-8")
    assert perfil_voz(ctx) == "Fala como pirata."


def test_template_tem_todos_os_campos():
    t = TEMPLATE.read_text(encoding="utf-8")
    for campo in ("tema", "perfil_voz", "titulos_recentes", "palavras_min",
                  "palavras_max", "problemas_anteriores", "dir_plugin"):
        assert "{" + campo + "}" in t


def test_roteiro_grava_json_e_valida_palavras(ctx, cfg, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_ok(_roteiro(20)), _ok(_roteiro(100)))
    with pytest.raises(ErroEtapa, match="20 palavras"):
        roteiro.executar(ctx)
    assert not (ctx.pasta / "roteiro.json").exists()

    roteiro.executar(ctx)
    assert ler_json(ctx, "roteiro.json") == _roteiro(100)
    argv = claude_falso.chamadas()[-1]
    script = cfg.plugin_dir / "skills" / "yt-script" / "hookscore.py"
    assert argv[argv.index("--allowedTools") + 1:] == [
        "Skill", "Read", f"Bash(python3 {script}:*)", "WebSearch", "WebFetch"
    ]
    assert f"Read(/{cfg.raiz.resolve()}/dados/tokens/**)" in argv
    assert argv[argv.index("--max-turns") + 1] == "30"
    assert "--model" not in argv
    n = ctx.con.execute(
        "SELECT COUNT(*) FROM chamadas_claude WHERE job_id = ? AND etapa = 'roteiro'",
        (ctx.job.id,),
    ).fetchone()[0]
    assert n == 2


def test_roteiro_inclui_problemas_anteriores(ctx, cfg, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_ok(_roteiro(100)), _ok(_roteiro(100)))
    roteiro.executar(ctx)
    primeiro = claude_falso.chamadas()[0][2]
    assert "por que o mar é salgado" in primeiro
    assert f"{cfg.plugin_dir}/skills/yt-script/hookscore.py --json" in primeiro
    assert "entre 75 e 150" in primeiro
    assert "muito longo" not in primeiro

    gravar_json(ctx, "problemas_revisao.json", {"problemas": ["muito longo"]})
    roteiro.executar(ctx)
    assert "muito longo" in claude_falso.chamadas()[1][2]


def test_narracao_tempos_por_segmento(ctx, midia):
    _segmentos(ctx, ["um dois", "três quatro cinco", "seis sete oito nove"])
    narracao.executar(ctx)
    n = ler_json(ctx, "narracao.json")
    segs = n["segmentos"]
    assert len(segs) == 3
    assert [Path(s["arquivo"]).name for s in segs] == ["seg_01.wav", "seg_02.wav", "seg_03.wav"]
    assert all((ctx.pasta / s["arquivo"]).exists() for s in segs)
    assert segs[0]["inicio"] == 0
    assert segs[0]["inicio"] < segs[1]["inicio"] < segs[2]["inicio"]
    for a, b in zip(segs, segs[1:]):
        assert b["inicio"] == pytest.approx(a["fim"] + 0.25, abs=0.01)
    # TTSFalso: 0,4 s por palavra → 0,8 + 1,2 + 1,6 + 2 pausas de 0,25
    assert n["duracao"] == pytest.approx(3.6 + 0.5, abs=0.05)
    assert segs[-1]["fim"] == n["duracao"]
    assert n["duracao"] == pytest.approx(
        midia.info(ctx.pasta / "narracao.wav").duracao, abs=0.001
    )
    assert n["provedor"] == "falso" and n["usou_reserva"] is False


class _CaiNoSegundo(TTSFalso):
    nome = "principal"

    def __init__(self):
        super().__init__()
        self.chamadas = 0

    def sintetizar(self, texto, destino):
        self.chamadas += 1
        if self.chamadas >= 2:
            raise ErroProvedor("caiu")
        return super().sintetizar(texto, destino)


class _Reserva(TTSFalso):
    nome = "reserva"

    def __init__(self):
        super().__init__()
        self.destinos = []

    def sintetizar(self, texto, destino):
        self.destinos.append(Path(destino).name)
        return super().sintetizar(texto, destino)


def test_narracao_reserva_nao_mistura_vozes(ctx, monkeypatch):
    reserva = _Reserva()
    monkeypatch.setattr(
        narracao, "criar_tts", lambda cfg, canal: TTSComReserva(_CaiNoSegundo(), reserva)
    )
    _segmentos(ctx, ["um dois", "três quatro", "cinco seis"])
    narracao.executar(ctx)
    n = ler_json(ctx, "narracao.json")
    assert n["provedor"] == "reserva" and n["usou_reserva"] is True
    assert sorted(set(reserva.destinos)) == ["seg_01.wav", "seg_02.wav", "seg_03.wav"]


def test_legendas_usa_transcricao(ctx, cfg, monkeypatch):
    TTSFalso().sintetizar("um dois três", ctx.pasta / "narracao.wav")
    _segmentos(ctx, ["Olá, mundo.", "Tudo bem?"])
    palavras = [Palavra("Olá", 0.0, 0.3), Palavra("mundo.", 0.35, 0.7),
                Palavra("tudo", 0.9, 1.1), Palavra("bem", 1.15, 1.4)]
    recebido = {}

    def transcrever_falso(wav, **kw):
        recebido.update(kw, wav=wav)
        return palavras

    monkeypatch.setattr(legendas, "transcrever", transcrever_falso)
    legendas.executar(ctx)
    assert recebido == {
        "wav": ctx.pasta / "narracao.wav", "modelo": cfg.whisper.modelo,
        "dispositivo": cfg.whisper.dispositivo, "compute_type": cfg.whisper.compute_type,
        "idioma": "pt",
    }
    t = ler_json(ctx, "transcricao.json")
    assert t["texto"] == "Olá mundo. tudo bem"
    assert t["palavras"][1] == {"texto": "mundo.", "inicio": 0.35, "fim": 0.7}
    ass = (ctx.pasta / "legendas.ass").read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: Legenda,DejaVu Sans,88,&H00FCFAF8,&H0015CCFA," in ass
    assert ass.split("Style: Legenda", 1)[1].splitlines()[0].endswith(",560,1")
    assert "OLÁ, MUNDO." in ass and "TUDO BEM?" in ass  # texto do roteiro, não do Whisper


def test_legendas_ass_usa_formato(cfg, con, monkeypatch):
    job = fila.criar_job(con, "teste", "medio", "por que o mar é salgado", PUBLICAR)
    c = contexto_para(cfg, con, job)
    TTSFalso().sintetizar("um dois", c.pasta / "narracao.wav")
    _segmentos(c, ["Olá, mundo."])
    monkeypatch.setattr(legendas, "transcrever", lambda wav, **kw: [
        Palavra("Olá", 0.0, 0.3), Palavra("mundo.", 0.35, 0.7)])
    legendas.executar(c)
    ass = (c.pasta / "legendas.ass").read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    assert "Style: Legenda,DejaVu Sans,60," in ass
    assert ass.split("Style: Legenda", 1)[1].splitlines()[0].endswith(",60,60,70,1")


def test_legendas_short_quebra_linha_larga(ctx, monkeypatch):
    # prova real de 2026-10-23: "VELOCIDADE NUM COMPUTADOR" em 88 px passava das bordas
    TTSFalso().sintetizar("um dois três", ctx.pasta / "narracao.wav")
    _segmentos(ctx, ["velocidade num computador"])
    monkeypatch.setattr(legendas, "transcrever", lambda wav, **kw: [
        Palavra("velocidade", 0.0, 0.4), Palavra("num", 0.45, 0.6),
        Palavra("computador", 0.65, 1.1)])
    legendas.executar(ctx)
    falas = [linha.rsplit(",", 1)[1] for linha in
             (ctx.pasta / "legendas.ass").read_text(encoding="utf-8").splitlines()
             if linha.startswith("Dialogue:")]
    assert "COMPUTADOR" in falas and "VELOCIDADE NUM COMPUTADOR" not in falas


def test_legendas_sem_roteiro_falha(ctx, monkeypatch):
    TTSFalso().sintetizar("um", ctx.pasta / "narracao.wav")
    monkeypatch.setattr(legendas, "transcrever", lambda wav, **kw: [Palavra("um", 0.0, 0.3)])
    with pytest.raises(ErroEtapa, match="roteiro.json"):
        legendas.executar(ctx)


def test_legendas_sem_palavras_falha(ctx, monkeypatch):
    TTSFalso().sintetizar("um", ctx.pasta / "narracao.wav")
    _segmentos(ctx, ["um"])
    monkeypatch.setattr(legendas, "transcrever", lambda wav, **kw: [])
    with pytest.raises(ErroEtapa):
        legendas.executar(ctx)


def test_visuais_refazer_nao_repete_midias_anteriores(ctx, monkeypatch):
    from autocanal.provedores.imagens import MidiaObtida

    vistos: list[set[str]] = []

    class Banco:
        nome = "banco"

        def buscar(self, consulta, orientacao, destino_sem_ext, excluir):
            vistos.append(set(excluir))
            url = next(f"https://b/{i}" for i in range(100) if f"https://b/{i}" not in excluir)
            caminho = Path(str(destino_sem_ext) + ".png")
            caminho.write_bytes(b"x")
            return MidiaObtida(caminho, "imagem", "banco", url, "livre", "autor")

    monkeypatch.setattr(visuais, "criar_cadeia", lambda *a, **k: [Banco()])
    _segmentos(ctx, ["a b", "c d"])
    visuais.executar(ctx)
    primeira = [f["url"] for f in ler_json(ctx, "fontes_midia.json")]
    assert primeira == ["https://b/0", "https://b/1"]
    visuais.executar(ctx)  # a revisão pediu para refazer os visuais
    segunda = [f["url"] for f in ler_json(ctx, "fontes_midia.json")]
    assert segunda == ["https://b/2", "https://b/3"]
    assert vistos[2] == set(primeira)


def test_visuais_um_por_segmento_sem_repetir(ctx):
    _segmentos(ctx, ["a b", "c d", "e f"], ["ocean waves", "city night", "pine forest"])
    visuais.executar(ctx)
    fontes = ler_json(ctx, "fontes_midia.json")
    assert len(fontes) == 3
    assert len({f["url"] for f in fontes}) == 3
    arquivos = sorted(p.name for p in (ctx.pasta / "visuais").iterdir())
    assert arquivos == ["seg_01.png", "seg_02.png", "seg_03.png"]
    assert [Path(f["caminho"]).name for f in fontes] == arquivos
    assert all(f["licenca"] and f["origem"] for f in fontes)


def _itens_curadoria(ctx, n=1):
    from PIL import Image

    from autocanal.provedores.curadoria import ItemCuradoria
    folha = ctx.pasta / "visuais" / "curadoria" / "seg_01.jpg"
    folha.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1200, 600)).save(folha)
    return [ItemCuradoria("seg_01", "o roteador do vizinho", "a router on a shelf", folha, n)]


def test_escolher_com_claude_usa_modelo_e_read(ctx, cfg, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(_ok({"escolhas": [
        {"id": "seg_01", "candidato": 2, "motivo": "roteador"},
        {"id": "seg_02", "candidato": 0, "motivo": "texto na tela", "nova_cena": "a router alone"}]}))
    itens = _itens_curadoria(ctx, n=3)
    assert visuais.escolher_com_claude(ctx)(itens) == {
        "seg_01": (2, "roteador", ""), "seg_02": (0, "texto na tela", "a router alone")}
    argv = claude_falso.chamadas()[-1]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--allowedTools") + 1:] == ["Read"]
    assert argv[argv.index("--max-turns") + 1] == "25"
    prompt = argv[2]
    assert "o roteador do vizinho" in prompt and "a router on a shelf" in prompt
    assert str(itens[0].folha) in prompt
    n = ctx.con.execute("SELECT COUNT(*) FROM chamadas_claude WHERE job_id = ? AND etapa = 'visuais'",
                        (ctx.job.id,)).fetchone()[0]
    assert n == 1


def test_escolher_erro_claude_vira_erro_etapa(ctx, cfg, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder({"type": "result", "subtype": "error_max_turns", "is_error": True,
                            "result": "acabaram os turnos"})
    with pytest.raises(ErroEtapa, match="curadoria"):
        visuais.escolher_com_claude(ctx)(_itens_curadoria(ctx))


def test_escolher_limite_uso_propaga(ctx, cfg, claude_falso):
    from autocanal.claude import LimiteUsoClaude
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder({"type": "result", "subtype": "success", "is_error": True,
                            "result": "You've hit your session limit · resets 5am"})
    with pytest.raises(LimiteUsoClaude):
        visuais.escolher_com_claude(ctx)(_itens_curadoria(ctx))


def test_visuais_usa_curadoria_com_chave(ctx, cfg, monkeypatch):
    from PIL import Image

    from autocanal.provedores.imagens import Candidato, MidiaObtida

    class Fonte:
        def __init__(self, chave):
            assert chave == "k"

        def buscar(self, consulta, orientacao, estilo, excluir, maximo=6):
            assert estilo == "realista"
            return [Candidato("imagem", f"https://pixabay.com/{consulta}", "p", "d", "Ana")]

        def baixar_preview(self, c, destino):
            destino.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 36)).save(destino, format="JPEG")
            return destino

        def baixar(self, c, destino_sem_ext):
            caminho = Path(str(destino_sem_ext) + ".jpg")
            caminho.write_bytes(b"x")
            return MidiaObtida(caminho, "imagem", "pixabay", c.pagina, "Pixabay Content License", "Ana")

    cfg.chaves["PIXABAY_API_KEY"] = "k"
    monkeypatch.setattr(visuais, "PixabayCandidatos", Fonte)
    monkeypatch.setattr(visuais, "escolher_com_claude",
                        lambda ctx: (lambda itens: {i.id: (1, "combina") for i in itens}))
    _segmentos(ctx, ["a b", "c d"], ["ocean", "city"])
    visuais.executar(ctx)
    fontes = ler_json(ctx, "fontes_midia.json")
    assert [f["origem"] for f in fontes] == ["pixabay", "pixabay"]
    assert [f["motivo"] for f in fontes] == ["combina", "combina"]
    assert (ctx.pasta / "visuais" / "curadoria" / "seg_01.jpg").exists()


def _roteiro_medio(segs_por_secao=(2, 3, 2), palavras_por_seg=25) -> dict:
    secoes = []
    for s, n in enumerate(segs_por_secao):
        secoes.append({"titulo": f"Parte {s}", "segmentos": [
            {"texto": " ".join(f"p{s}x{k}x{i}" for i in range(palavras_por_seg)),
             "visual": f"ocean {s} {k}", "cena": f"a wide ocean scene {s} {k}"}
            for k in range(n)]})
    return {"gancho": "p0x0x0", "nota_gancho": 70, "secoes": secoes,
            "fontes": [{"afirmacao": "o mar é salgado", "url": "https://exemplo.org/mar"}],
            "resumo": "Por que o mar é salgado."}


def test_achatar_secoes():
    r = roteiro.achatar(_roteiro_medio())
    assert r["secoes"] == ["Parte 0", "Parte 1", "Parte 2"]
    assert [s["secao"] for s in r["segmentos"]] == [0, 0, 1, 1, 1, 2, 2]
    assert set(r["segmentos"][0]) == {"texto", "visual", "cena", "secao"}
    assert r["fontes"] and r["gancho"] == "p0x0x0" and r["resumo"]


def test_achatar_mais_de_60_falha():
    with pytest.raises(ErroEtapa, match="63 segmentos"):
        roteiro.achatar(_roteiro_medio((9,) * 7))


def test_roteiro_medio_usa_prompt_e_schema(cfg, con, claude_falso):
    cfg.claude.bin = claude_falso.claude_bin
    job = fila.criar_job(con, "teste", "medio", "por que o mar é salgado", PUBLICAR)
    c = contexto_para(cfg, con, job)
    claude_falso.responder(_ok(_roteiro_medio((4, 4, 4), 40)))  # 480 palavras
    roteiro.executar(c)
    argv = claude_falso.chamadas()[-1]
    assert "3 a 4 minutos" in argv[2]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == roteiro.ROTEIRO_MEDIO
    r = ler_json(c, "roteiro.json")
    assert len(r["secoes"]) == 3 and len(r["segmentos"]) == 12
    assert r["segmentos"][4]["secao"] == 1


def test_schema_short_exige_cena_e_ate_16():
    segs = roteiro.ROTEIRO_SHORT["properties"]["segmentos"]
    assert "cena" in segs["items"]["required"]
    assert (segs["minItems"], segs["maxItems"]) == (6, 16)


def test_prompts_pedem_uma_frase():
    for nome in ("roteiro_short", "roteiro_medio"):
        texto = (Path(__file__).parents[1] / "autocanal" / "prompts" / f"{nome}.md").read_text(
            encoding="utf-8")
        assert "uma única frase" in texto and "`cena`" in texto


def _instalar_flux_falso(cfg):
    cfg.flux.projeto.mkdir(parents=True, exist_ok=True)
    (cfg.flux.projeto / "pyproject.toml").write_text("[project]\n")
    (cfg.flux.projeto / "gerar.py").write_text("")
    (cfg.flux.projeto / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    (cfg.flux.projeto / ".venv" / "bin" / "python").write_text("")


class _FluxRegistra:
    def __init__(self, eventos):
        self.nome, self.eventos = "flux", eventos

    def __call__(self, cfg, **kw):
        self.kw = kw
        return self

    def buscar_lote(self, pedidos, orientacao, excluir):
        from PIL import Image

        from autocanal.provedores.imagens import MidiaObtida
        self.eventos.append("lote")
        saida = {}
        for p in pedidos:
            caminho = Path(str(p.destino_sem_ext) + ".png")
            Image.new("RGB", (64, 36)).save(caminho)
            saida[p.id] = MidiaObtida(caminho, "imagem", "flux", f"flux:{p.id}", "livre",
                                      "autocanal", prompt=p.cena)
        return saida


def test_visuais_libera_memoria_antes_do_flux(ctx, cfg, monkeypatch):
    from autocanal import legendas as mod_legendas
    from autocanal import servicos
    eventos: list[str] = []
    _instalar_flux_falso(cfg)
    cfg.canais["teste"].visuais = ["flux"]
    gerador = _FluxRegistra(eventos)
    monkeypatch.setattr(visuais, "GeradorFlux", gerador)
    monkeypatch.setattr(visuais, "escolher_com_claude",
                        lambda ctx: (lambda itens: {i.id: (1, "sem defeitos") for i in itens}))
    monkeypatch.setattr(mod_legendas, "liberar_modelos", lambda: eventos.append("whisper"))
    monkeypatch.setattr(servicos, "parar_kokoro", lambda raiz: eventos.append("kokoro"))
    _segmentos(ctx, ["a b", "c d"])
    visuais.executar(ctx)
    assert eventos == ["whisper", "kokoro", "lote"]
    assert gerador.kw == {"job_id": ctx.job.id, "rodada": 0, "estilo_visual": "realista",
                          "estilo_ia": ""}
    fontes = ler_json(ctx, "fontes_midia.json")
    assert [f["origem"] for f in fontes] == ["flux", "flux"]
    assert [f["motivo"] for f in fontes] == ["sem defeitos", "sem defeitos"]  # passou pela curadoria
    assert [Path(f["caminho"]).name for f in fontes] == ["seg_01.png", "seg_02.png"]


def test_visuais_flux_nao_instalado_segue(ctx, cfg, caplog):
    caplog.set_level("WARNING", logger="autocanal.etapas.visuais")
    cfg.canais["teste"].visuais = ["flux"]
    _segmentos(ctx, ["a b"])
    visuais.executar(ctx)
    assert ler_json(ctx, "fontes_midia.json")[0]["origem"] == "procedural"
    assert any("FLUX" in r.getMessage() for r in caplog.records)


def test_visuais_refeitos_atualizam_creditos_e_sintetico(ctx, cfg, monkeypatch):
    # revisão final: rodada 1 sem FLUX; visuais refeitos (metadados não) passam a ter FLUX
    from autocanal import legendas as mod_legendas
    from autocanal import servicos
    _instalar_flux_falso(cfg)
    cfg.canais["teste"].visuais = ["flux"]
    monkeypatch.setattr(visuais, "GeradorFlux", _FluxRegistra([]))
    monkeypatch.setattr(visuais, "escolher_com_claude",
                        lambda ctx: (lambda itens: {i.id: (1, "ok") for i in itens}))
    monkeypatch.setattr(mod_legendas, "liberar_modelos", lambda: None)
    monkeypatch.setattr(servicos, "parar_kokoro", lambda raiz: None)
    _segmentos(ctx, ["a b", "c d"])
    gravar_json(ctx, "metadados.json", {
        "titulo": "T", "texto_thumb": "X", "cena_thumb": "c", "descricao": "Resumo.",
        "descricao_final": "Resumo.\n\nImagens: Pixabay\n\nFontes:\nhttps://exemplo.org/mar\n\n#shorts",
        "tags": [], "capitulos": [], "consultas_alvo": ["a", "b", "c"], "nota_titulo": 90,
        "problemas_titulo": [], "sintetico": False,
    })
    visuais.executar(ctx)
    m = ler_json(ctx, "metadados.json")
    assert m["sintetico"] is True
    assert m["descricao_final"] == (
        "Resumo.\n\nAs imagens deste vídeo foram geradas por IA.\n\n"
        "Fontes:\nhttps://exemplo.org/mar\n\n#shorts")


def _rodada_anterior(ctx, origem, fontes):
    gravar_json(ctx, "fontes_midia.json", fontes)
    gravar_json(ctx, "revisao_1.json", {"origem": origem, "aprovado": False, "problemas": ["x"]})


class _BancoNovo:
    nome = "novo"

    def __init__(self):
        self.pedidos = []

    def buscar_lote(self, pedidos, orientacao, excluir):
        from autocanal.provedores.imagens import MidiaObtida
        self.pedidos.append([p.id for p in pedidos])
        saida = {}
        for p in pedidos:
            caminho = Path(str(p.destino_sem_ext) + ".png")
            caminho.write_bytes(b"novo")
            saida[p.id] = MidiaObtida(caminho, "imagem", "flux", f"flux:novo-{p.id}", "x", "y")
        return saida


def test_visuais_refeitos_so_por_procedural_mantem_os_bons(ctx, monkeypatch):
    # prova real: 11 de 12 bons na rodada 1; refazer tudo jogou fora os 11 e piorou para 9
    bom = ctx.pasta / "visuais" / "seg_01.png"
    bom.parent.mkdir(parents=True)
    bom.write_bytes(b"bom")
    _segmentos(ctx, ["a b", "c d"])
    _rodada_anterior(ctx, "checagens", [
        {"caminho": str(bom), "tipo": "imagem", "origem": "flux", "url": "flux:bom",
         "licenca": "x", "autor": "y", "prompt": "p", "motivo": "ok"},
        {"caminho": str(ctx.pasta / "visuais" / "seg_02.png"), "tipo": "imagem",
         "origem": "procedural", "url": "procedural:1", "licenca": "x", "autor": "y"},
    ])
    banco = _BancoNovo()
    monkeypatch.setattr(visuais, "criar_cadeia", lambda *a, **k: [banco])
    visuais.executar(ctx)
    fontes = ler_json(ctx, "fontes_midia.json")
    assert banco.pedidos == [["seg_02"]]
    assert fontes[0]["url"] == "flux:bom" and Path(fontes[0]["caminho"]).read_bytes() == b"bom"
    assert fontes[1]["url"] == "flux:novo-seg_02"
    assert not (ctx.pasta / "visuais_mantidos").exists()


def test_visuais_reprovados_pelo_revisor_refazem_tudo(ctx, monkeypatch):
    bom = ctx.pasta / "visuais" / "seg_01.png"
    bom.parent.mkdir(parents=True)
    bom.write_bytes(b"bom")
    _rodada_anterior(ctx, "revisor", [
        {"caminho": str(bom), "tipo": "imagem", "origem": "flux", "url": "flux:bom",
         "licenca": "x", "autor": "y"},
        {"caminho": str(ctx.pasta / "visuais" / "seg_02.png"), "tipo": "imagem",
         "origem": "procedural", "url": "procedural:1", "licenca": "x", "autor": "y"},
    ])
    banco = _BancoNovo()
    monkeypatch.setattr(visuais, "criar_cadeia", lambda *a, **k: [banco])
    _segmentos(ctx, ["a b", "c d"])
    visuais.executar(ctx)
    assert banco.pedidos == [["seg_01", "seg_02"]]


def test_visuais_roteiro_reescrito_refaz_tudo(ctx, monkeypatch):
    import os
    bom = ctx.pasta / "visuais" / "seg_01.png"
    bom.parent.mkdir(parents=True)
    bom.write_bytes(b"bom")
    _rodada_anterior(ctx, "checagens", [
        {"caminho": str(bom), "tipo": "imagem", "origem": "flux", "url": "flux:bom",
         "licenca": "x", "autor": "y"},
        {"caminho": str(ctx.pasta / "visuais" / "seg_02.png"), "tipo": "imagem",
         "origem": "procedural", "url": "procedural:1", "licenca": "x", "autor": "y"},
    ])
    _segmentos(ctx, ["outra fala", "c d"])  # roteiro refeito depois dos visuais
    futuro = (ctx.pasta / "fontes_midia.json").stat().st_mtime + 10
    os.utime(ctx.pasta / "roteiro.json", (futuro, futuro))
    banco = _BancoNovo()
    monkeypatch.setattr(visuais, "criar_cadeia", lambda *a, **k: [banco])
    visuais.executar(ctx)
    assert banco.pedidos == [["seg_01", "seg_02"]]


def test_visuais_refazem_so_os_trechos_apontados_pelo_revisor(ctx, monkeypatch):
    # prova real: o revisor reprovou 2 de 12 visuais, refazer os 12 fez 3 outros falharem
    _segmentos(ctx, ["a b", "c d", "e f"])
    fontes = []
    for i in (1, 2, 3):
        arq = ctx.pasta / "visuais" / f"seg_{i:02d}.png"
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_bytes(b"bom")
        fontes.append({"caminho": str(arq), "tipo": "imagem", "origem": "flux",
                       "url": f"flux:{i}", "licenca": "x", "autor": "y"})
    gravar_json(ctx, "fontes_midia.json", fontes)
    gravar_json(ctx, "revisao_1.json", {"origem": "revisor", "aprovado": False,
                                        "problemas": ["seg 2 com quatro mãos"],
                                        "trechos_visuais": [2]})
    banco = _BancoNovo()
    monkeypatch.setattr(visuais, "criar_cadeia", lambda *a, **k: [banco])
    visuais.executar(ctx)
    assert banco.pedidos == [["seg_02"]]
    assert [f["url"] for f in ler_json(ctx, "fontes_midia.json")] == [
        "flux:1", "flux:novo-seg_02", "flux:3"]
