"""Etapa revisão: checagens determinísticas e, se passarem, o revisor Claude."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from autocanal import claude, fila
from autocanal.etapas.base import (
    PROBLEMAS_REVISAO,
    Contexto,
    carregar_prompt,
    etapas_a_refazer,
    gravar_json,
    ler_json,
    lista_md,
    titulos_recentes,
)
from autocanal.fila import Estado

NOME = "revisao"
FERRAMENTAS = ["Read", "WebFetch", "WebSearch"]
N_QUADROS = 6
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
                 "contains_synthetic_media"],
    "additionalProperties": False,
    "properties": {
        "aprovado": {"type": "boolean"},
        "nota": {"type": "number"},
        "problemas": {"type": "array", "items": {"type": "string"}},
        "etapa_para_refazer": {
            "type": ["string", "null"],
            "enum": ["roteiro", "narracao", "legendas", "visuais", "render", "metadados", None],
        },
        "contains_synthetic_media": {"type": "boolean"},
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


def _revisor(ctx: Contexto) -> dict:
    fmt = ctx.formato
    quadros = ctx.midia.extrair_frames(
        ctx.pasta / "video.mp4", N_QUADROS, ctx.pasta / "revisao" / "quadros"
    )
    prompt = carregar_prompt(
        "revisao",
        tema=ctx.job.tema,
        formato=fmt.resumo,
        quadros=lista_md([str(q.relative_to(ctx.pasta)) for q in quadros], "(nenhum)"),
        titulos_recentes=lista_md(titulos_recentes(ctx, N_TITULOS), "(nenhum ainda)"),
    )
    r = claude.chamar(
        prompt,
        REVISAO,
        FERRAMENTAS,
        ctx.pasta,
        modelo=ctx.cfg.claude.modelos.get(NOME),
        max_turns=ctx.cfg.claude.max_turns.get(NOME, 20),
        claude_bin=ctx.cfg.claude.bin,
        raiz=ctx.cfg.raiz,
    )
    claude.registrar(ctx.con, ctx.job.id, NOME, r)
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
        }
    else:
        resultado = {"origem": "revisor", **_revisor(ctx)}
        resultado["etapa_para_refazer"] = (
            None if resultado["aprovado"]
            else _etapa_mais_cedo(ctx, [resultado["etapa_para_refazer"]])
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
        meta["sintetico"] = resultado["contains_synthetic_media"]
        gravar_json(ctx, "metadados.json", meta)
        novo = Estado.APROVADO
    elif rodada >= ctx.cfg.revisao.max_rodadas:
        novo = Estado.QUARENTENA
    else:
        # refaz da etapa mais cedo até a própria revisão (zera as tentativas dela também)
        gravar_json(ctx, PROBLEMAS_REVISAO, {"problemas": problemas})
        fila.resetar_etapas(ctx.con, job.id, etapas_a_refazer(ctx.formato.etapas, etapa))
        novo = Estado.PRODUZINDO
    fila.atualizar(ctx.con, job.id, tentativas_revisao=rodada)
    fila.mudar_estado(ctx.con, job.id, novo)
