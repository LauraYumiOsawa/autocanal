"""Etapa revisão: checagens determinísticas e, se passarem, o revisor (IA)."""
from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from autocanal import fila, fontes, ia
from autocanal.etapas.metadados import AVISOS_IA
from autocanal.etapas.base import (
    PROBLEMAS_REVISAO,
    Contexto,
    carregar_prompt,
    etapas_a_refazer,
    gravar_json,
    json_compacto,
    ler_json,
    ler_json_opcional,
    lista_md,
    titulos_recentes,
)
from autocanal.fila import Estado

NOME = "revisao"
N_QUADROS = 6
LADO_QUADRO = 960  # quadros menores: ~700 tokens cada em vez de ~1.800, relidos a cada turno
N_TITULOS = 15
N_ROTEIROS = 30
LOUDNESS_ALVO = -14.0
LOUDNESS_TOLERANCIA = 1.5
SILENCIO_DB = -40.0
SILENCIO_S = 2.0
FOLGA_LEGENDAS_S = 1.0

REVISAO = {
    "type": "object",
    "required": ["aprovado", "nota", "problemas", "etapa_para_refazer",
                 "refazer_metadados", "contains_synthetic_media"],
    "additionalProperties": False,
    "properties": {
        "aprovado": {"type": "boolean"},
        "nota": {"type": "number"},
        "problemas": {"type": "array", "items": {"type": "string"}},
        "etapa_para_refazer": {
            "type": ["string", "null"],
            "enum": ["roteiro", "narracao", "legendas", "visuais", "render", "metadados",
                     "thumbnail", None],
        },
        "refazer_metadados": {"type": "boolean"},
        "contains_synthetic_media": {"type": "boolean"},
        # segmentos (1 = primeiro) cujo visual deve ser refeito; os outros são mantidos
        "trechos_visuais": {"type": "array", "items": {"type": "integer", "minimum": 1}},
    },
}


def similaridade(a: str, b: str, n: int = 5) -> float:
    """Jaccard dos n-gramas de palavras (minúsculas, sem pontuação); 0.0 se < n palavras."""
    def ngramas(texto: str) -> set[tuple[str, ...]]:
        palavras = re.findall(r"\w+", texto.lower())
        return {tuple(palavras[i:i + n]) for i in range(len(palavras) - n + 1)}

    x, y = ngramas(a), ngramas(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


def _texto_roteiro(roteiro: dict) -> str:
    return " ".join(s["texto"] for s in roteiro["segmentos"])


def _roteiros_anteriores(ctx: Contexto) -> list[tuple[str, str]]:
    """(job, texto) dos últimos `N_ROTEIROS` outros jobs do canal com `roteiro.json`.

    Jobs descartados não contam (como em `titulos_recentes`): nunca foram publicados.
    """
    roteiros: list[tuple[str, str]] = []
    for j in reversed(fila.listar(ctx.con, canal=ctx.job.canal)):
        if len(roteiros) >= N_ROTEIROS:
            break
        if j.id == ctx.job.id or j.estado == Estado.DESCARTADO:
            continue
        arquivo = ctx.cfg.dados / "jobs" / j.id / "roteiro.json"
        try:
            roteiros.append((j.id, _texto_roteiro(json.loads(arquivo.read_text("utf-8")))))
        except (OSError, ValueError, LookupError, TypeError):
            continue
    return roteiros


def _checar_video(ctx: Contexto) -> list[tuple[str, str]]:
    fmt = ctx.formato
    video = ctx.pasta / "video.mp4"
    if not video.exists():
        return [("vídeo final ausente (video.mp4)", "render")]
    info = ctx.midia.info(video)
    problemas = []
    if not fmt.duracao_min_s <= info.duracao <= fmt.duracao_max_s:
        problemas.append((
            f"duração de {info.duracao:.1f} s fora do alvo de "
            f"{fmt.duracao_min_s} a {fmt.duracao_max_s} s", "roteiro",
        ))
    if (info.largura, info.altura) != (fmt.largura, fmt.altura):
        problemas.append((
            f"resolução {info.largura}×{info.altura} (esperado {fmt.largura}×{fmt.altura})",
            "render",
        ))
    if not info.tem_audio:
        problemas.append(("vídeo sem áudio", "render"))
        return problemas
    lufs = ctx.midia.loudness(video)
    if abs(lufs - LOUDNESS_ALVO) > LOUDNESS_TOLERANCIA:
        problemas.append((
            f"loudness de {lufs:.1f} LUFS fora de {LOUDNESS_ALVO:.0f} ± {LOUDNESS_TOLERANCIA} LUFS",
            "render",
        ))
    silencios = ctx.midia.silencios(video, SILENCIO_DB, SILENCIO_S)
    if silencios:
        trechos = ", ".join(f"{a:.1f}–{b:.1f} s" for a, b in silencios)
        problemas.append((f"silêncio de mais de {SILENCIO_S:.0f} s em {trechos}", "render"))
    return problemas


def _checar_thumbnail(arq: Path) -> list[tuple[str, str]]:
    if not arq.exists():
        return [("thumbnail ausente (thumbnail.jpg)", "thumbnail")]
    with Image.open(arq) as im:
        tamanho = im.size
    if tamanho != (1280, 720):
        return [(f"thumbnail com {tamanho[0]}×{tamanho[1]} (esperado 1280×720)", "thumbnail")]
    if arq.stat().st_size >= 2_000_000:
        return [(f"thumbnail com {arq.stat().st_size} bytes (máximo 2 MB)", "thumbnail")]
    return []


def checagens(ctx: Contexto) -> list[tuple[str, str]]:
    """Problemas determinísticos, cada um com a etapa que o resolve."""
    rev = ctx.cfg.revisao
    problemas = _checar_video(ctx)

    duracao = ler_json(ctx, "narracao.json")["duracao"]
    palavras = ler_json(ctx, "transcricao.json").get("palavras") or []
    fim = palavras[-1]["fim"] if palavras else 0.0
    if fim < duracao - FOLGA_LEGENDAS_S:
        problemas.append((
            f"legendas terminam em {fim:.1f} s, mas a narração vai até {duracao:.1f} s",
            "legendas",
        ))

    meta = ler_json(ctx, "metadados.json")
    if meta["nota_titulo"] < rev.nota_titulo_minima:
        detalhes = "; ".join(meta.get("problemas_titulo") or [])
        problemas.append((
            f"título com nota {meta['nota_titulo']} (mínimo {rev.nota_titulo_minima})"
            + (f": {detalhes}" if detalhes else ""), "metadados",
        ))

    roteiro = ler_json(ctx, "roteiro.json")
    fontes = ler_json(ctx, "fontes_midia.json")
    n_segmentos = len(roteiro["segmentos"])
    if len(fontes) != n_segmentos:
        problemas.append((f"{len(fontes)} mídias para {n_segmentos} segmentos", "visuais"))
    sem_registro = [
        str(i) for i, f in enumerate(fontes, 1)
        if not (str(f.get("origem") or "").strip() and str(f.get("licenca") or "").strip())
    ]
    if sem_registro:
        problemas.append((
            f"mídia sem origem ou licença registrada nos segmentos {', '.join(sem_registro)}",
            "visuais",
        ))
    for i, f in enumerate(fontes, 1):
        if f.get("origem") == "procedural":
            problemas.append((f"trecho {i} sem imagem relacionada à fala (fundo procedural)",
                              "visuais"))
    if any(f.get("origem") == "flux" for f in fontes) and (
        not any(a in meta.get("descricao_final", "") for a in AVISOS_IA)
        or not meta.get("sintetico")
    ):
        problemas.append(("descrição ou marcação sem o aviso de imagens geradas por IA",
                          "metadados"))

    if "thumbnail" in ctx.formato.etapas:
        problemas.extend(_checar_thumbnail(ctx.pasta / "thumbnail.jpg"))

    texto = _texto_roteiro(roteiro)
    maior, parecido = max(
        ((similaridade(texto, t), j) for j, t in _roteiros_anteriores(ctx)), default=(0.0, "")
    )
    if maior >= rev.similaridade_maxima:
        problemas.append((
            f"roteiro parecido com o do job {parecido} (similaridade {maior:.2f}, "
            f"máximo {rev.similaridade_maxima})", "roteiro",
        ))
    return problemas


CAMPOS_METADADOS = ("titulo", "texto_thumb", "cena_thumb", "descricao_final", "tags", "capitulos")


def _rotulo_status(c: dict) -> str:
    """`confirmada, parcial NN%` quando só parte do trecho foi achada; senão o status puro."""
    if c["status"] == "confirmada" and c.get("detalhe", "").startswith("parcial"):
        return f'confirmada, {c["detalhe"]}'
    return c["status"]


def _dados_do_video(ctx: Contexto) -> dict[str, str]:
    """Roteiro, transcrição, metadados e mídias compactos para o prompt (sem turnos de leitura)."""
    roteiro = ler_json(ctx, "roteiro.json")
    meta = ler_json(ctx, "metadados.json")
    midias = ler_json_opcional(ctx, "fontes_midia.json", [])
    return {
        "roteiro": json_compacto({
            "segmentos": [{"texto": s["texto"], "cena": s.get("cena", "")}
                          for s in roteiro["segmentos"]],
            "fontes": roteiro.get("fontes", []),
        }),
        "transcricao": json_compacto(
            ler_json_opcional(ctx, "transcricao.json", {}).get("texto", "")),
        "metadados": json_compacto({k: meta.get(k) for k in CAMPOS_METADADOS}),
        "fontes_conferidas": lista_md([
            f'[{_rotulo_status(c)}] {c["afirmacao"]} — {c["url"]} — "{c["trecho"]}"'
            for c in fontes.conferidas(ctx.pasta, roteiro.get("fontes", []))
        ], "(nenhuma fonte)"),
        "midias": json_compacto([
            {"trecho": i, "origem": m.get("origem"), "licenca": m.get("licenca"),
             "motivo": m.get("motivo", "")}
            for i, m in enumerate(midias, 1)
        ]),
    }


def _sobre_branco(im: Image.Image) -> Image.Image:
    """RGB da imagem; a transparência vira branco (`convert("RGB")` a deixaria preta)."""
    if im.mode == "P" and "transparency" in im.info:
        im = im.convert("RGBA")
    if im.mode in ("RGBA", "LA"):
        fundo = Image.new("RGB", im.size, "white")
        fundo.paste(im, mask=im.getchannel("A"))
        return fundo
    return im.convert("RGB")


def _visuais_por_segmento(ctx: Contexto) -> list[tuple[int, Path | None]]:
    """`(nº do segmento, JPEG reduzido)` na ordem do roteiro (`revisao/visuais/seg_NN.jpg`).

    O arquivo vem sempre de `ctx.pasta / "visuais"` (na bancada o `caminho` registrado
    aponta para o job real). Sem o arquivo, o par traz `None` e o prompt avisa o revisor.
    """
    destino = ctx.pasta / "revisao" / "visuais"
    saidas: list[tuple[int, Path | None]] = []
    for i, m in enumerate(ler_json_opcional(ctx, "fontes_midia.json", []), 1):
        arquivo = ctx.pasta / "visuais" / Path(m["caminho"]).name
        if not arquivo.exists():
            saidas.append((i, None))
            continue
        destino.mkdir(parents=True, exist_ok=True)
        saida = destino / f"seg_{i:02d}.jpg"
        if m["tipo"] == "video":
            tmp = ctx.pasta / "revisao" / f"quadro_seg_{i:02d}"
            try:
                [quadro] = ctx.midia.extrair_frames(arquivo, 1, tmp, lado_max=LADO_QUADRO)
                shutil.move(str(quadro), saida)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        else:
            with Image.open(arquivo) as im:
                im = _sobre_branco(im)
                im.thumbnail((LADO_QUADRO, LADO_QUADRO))
                im.save(saida, "JPEG")
        saidas.append((i, saida))
    return saidas


def _revisor(ctx: Contexto) -> dict:
    fmt = ctx.formato
    quadros = ctx.midia.extrair_frames(
        ctx.pasta / "video.mp4", N_QUADROS, ctx.pasta / "revisao" / "quadros",
        lado_max=LADO_QUADRO,
    )
    visuais = _visuais_por_segmento(ctx)
    segmentos = ler_json(ctx, "roteiro.json")["segmentos"]
    prompt = carregar_prompt(
        "revisao",
        **_dados_do_video(ctx),
        tema=ctx.job.tema,
        formato=fmt.resumo,
        quadros=lista_md([str(q.relative_to(ctx.pasta)) for q in quadros], "(nenhum)"),
        visuais=lista_md([
            f'{v.relative_to(ctx.pasta)} — "{segmentos[i - 1]["texto"]}"' if v else
            f'seg_{i:02d}: visual ausente (arquivo não encontrado) — "{segmentos[i - 1]["texto"]}"'
            for i, v in visuais
        ], "(nenhum)"),
        titulos_recentes=lista_md(titulos_recentes(ctx, N_TITULOS), "(nenhum ainda)"),
    )
    imagens = [*quadros, *(v for _, v in visuais if v)]
    if (ctx.pasta / "thumbnail.jpg").exists():
        imagens.append(ctx.pasta / "thumbnail.jpg")
    r = ia.chamar(ctx.cfg, ctx.con, ctx.job.id, NOME, ia.Pedido(
        prompt=prompt, schema=REVISAO, pasta=ctx.pasta, web=True, imagens=tuple(imagens),
    ))
    return r.dados


def _etapa_mais_cedo(ctx: Contexto, nomes: list[str | None]) -> str:
    """A etapa de `nomes` que vem primeiro no formato (a primeira do formato, se nenhuma)."""
    ordem = ctx.formato.etapas
    validas = [n for n in nomes if n in ordem]
    return min(validas, key=ordem.index) if validas else ordem[0]


def executar(ctx: Contexto) -> None:
    job = fila.obter(ctx.con, ctx.job.id)
    rodada = job.tentativas_revisao + 1
    falhas = checagens(ctx)
    if falhas:
        resultado = {
            "origem": "checagens", "aprovado": False, "problemas": [p for p, _ in falhas],
            "etapa_para_refazer": _etapa_mais_cedo(ctx, [e for _, e in falhas]),
            "refazer_metadados": any(e == "metadados" for _, e in falhas),
        }
    else:
        resultado = {"origem": "revisor", **_revisor(ctx)}
        resultado["etapa_para_refazer"] = (
            None if resultado["aprovado"]
            else _etapa_mais_cedo(ctx, [resultado["etapa_para_refazer"]])
        )
        resultado["refazer_metadados"] = (
            not resultado["aprovado"] and bool(resultado["refazer_metadados"])
        )
    aprovado, problemas = resultado["aprovado"], resultado["problemas"]
    etapa = resultado["etapa_para_refazer"]

    gravar_json(ctx, f"revisao_{rodada}.json", resultado)
    ctx.con.execute(
        "INSERT OR REPLACE INTO revisoes"
        " (job_id, rodada, aprovado, motivos_json, etapa_para_refazer, criado_em)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (job.id, rodada, int(aprovado), json.dumps(problemas, ensure_ascii=False), etapa,
         datetime.now(UTC).isoformat()),
    )
    ctx.con.commit()
    if aprovado:
        meta = ler_json(ctx, "metadados.json")
        # a revisão pode ligar a marcação de mídia sintética, nunca desligar (ex.: imagens do FLUX)
        meta["sintetico"] = bool(meta.get("sintetico")) or resultado["contains_synthetic_media"]
        gravar_json(ctx, "metadados.json", meta)
        novo = Estado.APROVADO
    elif rodada >= ctx.cfg.revisao.max_rodadas:
        novo = Estado.QUARENTENA
    else:
        # refaz da etapa mais cedo até a própria revisão (zera as tentativas dela também)
        gravar_json(ctx, PROBLEMAS_REVISAO, {"problemas": problemas})
        fila.resetar_etapas(ctx.con, job.id, etapas_a_refazer(
            ctx.formato.etapas, etapa, resultado["refazer_metadados"]))
        novo = Estado.PRODUZINDO
    fila.atualizar(ctx.con, job.id, tentativas_revisao=rodada)
    fila.mudar_estado(ctx.con, job.id, novo)
