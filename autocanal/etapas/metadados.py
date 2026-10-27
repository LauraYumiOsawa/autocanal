"""Etapa metadados: a IA (yt-package, yt-seo, yt-chapters) escreve título, descrição e tags."""
from __future__ import annotations

# módulo, não a função: `ferramentas_plugin` importa `etapas.base` (import circular)
from autocanal import ferramentas_plugin, ia
from autocanal.etapas.base import (
    Contexto,
    ErroEtapa,
    carregar_prompt,
    gravar_json,
    ler_json,
    lista_md,
    perfil_voz,
    problemas_revisao,
    titulos_recentes,
)
from autocanal.texto import sanitizar_texto

LIMITE_TAGS = 500
LIMITE_DESCRICAO_BYTES = 5000

_TEXTO = {"type": "string"}
METADADOS = {
    "type": "object",
    "required": ["titulo", "texto_thumb", "cena_thumb", "descricao", "tags", "consultas_alvo"],
    "additionalProperties": False,
    "properties": {
        "titulo": {"type": "string", "minLength": 1, "maxLength": 100},
        "texto_thumb": _TEXTO,
        "cena_thumb": _TEXTO,
        "descricao": {"type": "string", "maxLength": 4500},
        "tags": {"type": "array", "maxItems": 15, "items": _TEXTO},
        "capitulos": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tempo", "titulo"],
                "additionalProperties": False,
                "properties": {"tempo": {"type": "number", "minimum": 0}, "titulo": _TEXTO},
            },
        },
        "consultas_alvo": {"type": "array", "minItems": 3, "maxItems": 3, "items": _TEXTO},
    },
}


CREDITO_PIXABAY = "Imagens: Pixabay"
AVISO_IA_TODAS = "As imagens deste vídeo foram geradas por IA."
AVISO_IA_MAIORIA = "A maioria das imagens deste vídeo foi gerada por IA."
AVISO_IA = "Algumas imagens deste vídeo foram geradas por IA."
AVISOS_IA = (AVISO_IA_TODAS, AVISO_IA_MAIORIA, AVISO_IA)


def capitulos_calculados(roteiro: dict, narracao: dict, minimo_s: float = 10.0) -> list[dict]:
    """Um capítulo por seção, começando no primeiro segmento dela na narração real.

    Regras do YouTube: o primeiro em 0, pelo menos 3, cada um com `minimo_s` ou mais. Seção curta
    funde-se à anterior (a primeira, à seguinte, que passa a começar em 0); se sobrarem menos de
    3, não há capítulos.
    """
    titulos = roteiro.get("secoes") or []
    inicios: dict[int, float] = {}
    for seg, tempo in zip(roteiro.get("segmentos") or [], narracao.get("segmentos") or []):
        inicios.setdefault(seg.get("secao"), float(tempo["inicio"]))
    blocos = [[inicios[i], titulo] for i, titulo in enumerate(titulos) if i in inicios]
    if not blocos:
        return []
    blocos[0][0] = 0.0
    fim_total = float(narracao["duracao"])
    i = 0
    while i < len(blocos) and len(blocos) > 1:
        fim = blocos[i + 1][0] if i + 1 < len(blocos) else fim_total
        if fim - blocos[i][0] >= minimo_s:
            i += 1
            continue
        if i == 0:
            blocos[1][0] = 0.0
        del blocos[i]
        i = max(i - 1, 0)
    if len(blocos) < 3:
        return []
    return [{"tempo": tempo, "titulo": titulo} for tempo, titulo in blocos]


def creditos(fontes: list[dict]) -> str:
    """Crédito do Pixabay e aviso de IA proporcional (todas / a maioria / algumas)."""
    origens = [f.get("origem") for f in fontes]
    n_flux = origens.count("flux")
    linhas = []
    if "pixabay" in origens:
        linhas.append(CREDITO_PIXABAY)
    if n_flux == len(origens) and n_flux:
        linhas.append(AVISO_IA_TODAS)
    elif n_flux * 2 > len(origens):
        linhas.append(AVISO_IA_MAIORIA)
    elif n_flux:
        linhas.append(AVISO_IA)
    return "\n".join(linhas)


def descricao_final(
    descricao: str, capitulos: list[dict], urls: list[str], formato: str, creditos: str = ""
) -> str:
    """Descrição + capítulos (`MM:SS Título`) + créditos das imagens + `Fontes:` + `#shorts`
    (só no formato short)."""
    partes = [descricao.strip()]
    if capitulos:
        partes.append("\n".join(
            f"{int(c['tempo']) // 60:02d}:{int(c['tempo']) % 60:02d} {c['titulo'].strip()}"
            for c in capitulos
        ))
    if creditos:
        partes.append(creditos)
    urls = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    if urls:
        partes.append("Fontes:\n" + "\n".join(urls))
    if formato == "short":
        partes.append("#shorts")
    return sanitizar_texto("\n\n".join(p for p in partes if p))


def cortar_tags(tags: list[str], limite: int = LIMITE_TAGS) -> list[str]:
    """Tags na ordem, sem vazias nem repetidas, enquanto caracteres + vírgulas ≤ `limite`."""
    saida: list[str] = []
    vistas: set[str] = set()
    total = -1  # n tags têm n − 1 vírgulas
    for tag in tags:
        tag = sanitizar_texto(tag).strip()
        if not tag or tag.casefold() in vistas:
            continue
        if total + len(tag) + 1 > limite:
            break
        vistas.add(tag.casefold())
        saida.append(tag)
        total += len(tag) + 1
    return saida


def executar(ctx: Contexto) -> None:
    fmt = ctx.formato
    roteiro = ler_json(ctx, "roteiro.json")
    prompt = carregar_prompt(
        "metadados",
        tema=ctx.job.tema,
        formato=fmt.resumo,
        perfil_voz=perfil_voz(ctx),
        titulos_recentes=lista_md(titulos_recentes(ctx), "(nenhum ainda)"),
        problemas_anteriores=lista_md(problemas_revisao(ctx), "Nenhum: esta é a primeira versão."),
        dir_plugin=ctx.cfg.plugin_dir,
    )
    r = ia.chamar(ctx.cfg, ctx.con, ctx.job.id, "metadados",
                  ia.Pedido(prompt=prompt, schema=METADADOS, pasta=ctx.pasta))
    d = r.dados
    titulo = sanitizar_texto(d["titulo"]).strip()
    if not titulo:
        raise ErroEtapa("título vazio")
    texto_thumb = sanitizar_texto(d["texto_thumb"]).strip()
    if "secoes" in roteiro:  # médio: capítulos pelas seções e tempos reais, não pelo Claude
        capitulos = capitulos_calculados(roteiro, ler_json(ctx, "narracao.json"))
    else:
        capitulos = d.get("capitulos", [])
    arq_fontes = ctx.pasta / "fontes_midia.json"
    fontes_midia = ler_json(ctx, "fontes_midia.json") if arq_fontes.exists() else []
    descricao = descricao_final(
        d["descricao"], capitulos, [f["url"] for f in roteiro.get("fontes", [])], fmt.nome,
        creditos(fontes_midia),
    )
    tamanho = len(descricao.encode("utf-8"))
    if tamanho > LIMITE_DESCRICAO_BYTES:
        raise ErroEtapa(f"descrição final com {tamanho} bytes (máximo {LIMITE_DESCRICAO_BYTES})")
    nota, problemas = ferramentas_plugin.nota_titulo(ctx.cfg, titulo, texto_thumb or None)
    gravar_json(ctx, "metadados.json", {
        "titulo": titulo,
        "texto_thumb": texto_thumb,
        "cena_thumb": sanitizar_texto(d["cena_thumb"]).strip(),
        "descricao": sanitizar_texto(d["descricao"]).strip(),  # base para refazer os créditos
        "descricao_final": descricao,
        "tags": cortar_tags(d["tags"]),
        "capitulos": capitulos,
        "consultas_alvo": d["consultas_alvo"],
        "nota_titulo": nota,
        "problemas_titulo": problemas,
        # imagem do FLUX é mídia sintética realista: marcação obrigatória no upload
        "sintetico": any(f.get("origem") == "flux" for f in fontes_midia),
    })


def atualizar_creditos(ctx: Contexto) -> None:
    """Visuais refeitos sem refazer os metadados: os créditos e a marcação de IA acompanham as
    mídias novas (sem isso, imagem do FLUX iria ao ar sem o aviso)."""
    if not (ctx.pasta / "metadados.json").exists():
        return
    meta = ler_json(ctx, "metadados.json")
    fontes = ler_json(ctx, "fontes_midia.json")
    meta["sintetico"] = bool(meta.get("sintetico")) or any(f.get("origem") == "flux" for f in fontes)
    if "descricao" in meta:
        roteiro = ler_json(ctx, "roteiro.json")
        meta["descricao_final"] = descricao_final(
            meta["descricao"], meta.get("capitulos", []),
            [f["url"] for f in roteiro.get("fontes", [])], ctx.formato.nome, creditos(fontes),
        )
    gravar_json(ctx, "metadados.json", meta)
