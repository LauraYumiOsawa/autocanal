"""Etapa metadados: Claude (yt-package, yt-seo, yt-chapters) escreve título, descrição e tags."""
from __future__ import annotations

# módulo, não a função: `ferramentas_plugin` importa `etapas.base` (import circular)
from autocanal import claude, ferramentas_plugin
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

def ferramentas(ctx: Contexto) -> list[str]:
    """Ferramentas do Claude: Bash só para o linter de título do plugin."""
    script = ctx.cfg.plugin_dir / "skills" / "yt-package" / "title.py"
    return ["Skill", "Read", f"Bash(python3 {script}:*)"]
LIMITE_TAGS = 500
LIMITE_DESCRICAO_BYTES = 5000

_TEXTO = {"type": "string"}
METADADOS = {
    "type": "object",
    "required": ["titulo", "texto_thumb", "descricao", "tags", "consultas_alvo"],
    "additionalProperties": False,
    "properties": {
        "titulo": {"type": "string", "minLength": 1, "maxLength": 100},
        "texto_thumb": _TEXTO,
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


def descricao_final(descricao: str, capitulos: list[dict], urls: list[str], formato: str) -> str:
    """Descrição + capítulos (`MM:SS Título`) + `Fontes:` + `#shorts` (só no formato short)."""
    partes = [descricao.strip()]
    if capitulos:
        partes.append("\n".join(
            f"{int(c['tempo']) // 60:02d}:{int(c['tempo']) % 60:02d} {c['titulo'].strip()}"
            for c in capitulos
        ))
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
    r = claude.chamar(
        prompt,
        METADADOS,
        ferramentas(ctx),
        ctx.pasta,
        modelo=ctx.cfg.claude.modelos.get("metadados"),
        max_turns=ctx.cfg.claude.max_turns.get("metadados", 20),
        claude_bin=ctx.cfg.claude.bin,
        raiz=ctx.cfg.raiz,
    )
    claude.registrar(ctx.con, ctx.job.id, "metadados", r)
    d = r.dados
    titulo = sanitizar_texto(d["titulo"]).strip()
    if not titulo:
        raise ErroEtapa("título vazio")
    texto_thumb = sanitizar_texto(d["texto_thumb"]).strip()
    capitulos = d.get("capitulos", [])
    descricao = descricao_final(
        d["descricao"], capitulos, [f["url"] for f in roteiro.get("fontes", [])], fmt.nome
    )
    tamanho = len(descricao.encode("utf-8"))
    if tamanho > LIMITE_DESCRICAO_BYTES:
        raise ErroEtapa(f"descrição final com {tamanho} bytes (máximo {LIMITE_DESCRICAO_BYTES})")
    nota, problemas = ferramentas_plugin.nota_titulo(ctx.cfg, titulo, texto_thumb or None)
    gravar_json(ctx, "metadados.json", {
        "titulo": titulo,
        "texto_thumb": texto_thumb,
        "descricao_final": descricao,
        "tags": cortar_tags(d["tags"]),
        "capitulos": capitulos,
        "consultas_alvo": d["consultas_alvo"],
        "nota_titulo": nota,
        "problemas_titulo": problemas,
        "sintetico": False,
    })
