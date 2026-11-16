"""Etapa visuais: uma mídia por segmento, pela cadeia do canal (procedural só em emergência)."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from autocanal import ia
from autocanal.etapas.base import Contexto, ErroEtapa, carregar_prompt, gravar_json, ler_json
from autocanal.etapas.metadados import atualizar_creditos
from autocanal.provedores import flux
from autocanal.provedores.curadoria import CuradoriaPixabay, Escolher, FluxCurado, ItemCuradoria
from autocanal.provedores.flux import GeradorFlux
from autocanal.provedores.imagens import Pedido, PixabayCandidatos, criar_cadeia, resolver

log = logging.getLogger(__name__)

CURADORIA = {
    "type": "object",
    "required": ["escolhas"],
    "additionalProperties": False,
    "properties": {
        "escolhas": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "candidato", "motivo"],
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "candidato": {"type": "integer", "minimum": 0},
                    "motivo": {"type": "string"},
                    "nova_cena": {"type": "string"},
                },
            },
        },
    },
}


def _itens_md(itens: list[ItemCuradoria]) -> str:
    return "\n".join(
        f"- `{i.id}` — fala: \"{i.texto}\" — cena esperada: {i.cena or '(não informada)'} — "
        f"folha: `{i.folha}` ({i.n} candidatos)"
        for i in itens
    )


def escolher_com_ia(ctx: Contexto) -> Escolher:
    """Uma chamada da IA por lote: devolve `{id: (candidato, motivo, nova_cena)}`."""

    def escolher(itens: list[ItemCuradoria]) -> dict[str, tuple[int, str, str]]:
        prompt = carregar_prompt("curadoria", estilo=ctx.canal.estilo_visual, itens=_itens_md(itens))
        try:
            r = ia.chamar(ctx.cfg, ctx.con, ctx.job.id, "curadoria", ia.Pedido(
                prompt=prompt, schema=CURADORIA, pasta=ctx.pasta,
                imagens=tuple(i.folha for i in itens),
            ))
        except (ia.LimiteUso, ia.IAIndisponivel):
            raise
        except ia.ErroIA as e:
            # sem curadoria não há garantia de que o visual combina com a fala
            raise ErroEtapa(f"curadoria dos visuais falhou: {e}") from e
        return {e["id"]: (int(e["candidato"]), e["motivo"], e.get("nova_cena", ""))
                for e in r.dados["escolhas"]}

    return escolher


def executar(ctx: Contexto) -> None:
    segmentos = ler_json(ctx, "roteiro.json")["segmentos"]
    fmt = ctx.formato
    pasta = ctx.pasta / "visuais"
    # refazer (revisão reprovou): não repetir as mídias da versão anterior
    excluir: set[str] = set()
    anteriores: list[dict] = []
    arq_anterior = ctx.pasta / "fontes_midia.json"
    if arq_anterior.exists():
        try:
            anteriores = json.loads(arq_anterior.read_text(encoding="utf-8"))
            excluir = {f["url"] for f in anteriores}
        except (ValueError, KeyError, TypeError):
            anteriores, excluir = [], set()
    mantidas = _guardar(ctx, _a_manter(ctx, anteriores, len(segmentos)))
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir()
    mantidas = _devolver(ctx, mantidas, pasta)
    pixabay = None
    chave = ctx.cfg.chaves.get("PIXABAY_API_KEY")
    if chave and "pixabay" in ctx.canal.visuais:
        pixabay = CuradoriaPixabay(PixabayCandidatos(chave), escolher_com_ia(ctx),
                                   ctx.canal.estilo_visual, pasta / "curadoria")
    gerador = None
    if "flux" in ctx.canal.visuais:
        if flux.instalado(ctx.cfg):
            flux.preparar_memoria(ctx.cfg.raiz)
            gerador = FluxCurado(
                GeradorFlux(ctx.cfg, job_id=ctx.job.id, rodada=ctx.job.tentativas_revisao,
                            estilo_visual=ctx.canal.estilo_visual, estilo_ia=ctx.canal.estilo_ia),
                escolher_com_ia(ctx), pasta / "curadoria_flux", ctx.cfg.flux.variantes,
            )
        else:
            log.warning("o canal %s pede FLUX, mas servicos/flux não está instalado",
                        ctx.canal.slug)
    cadeia = criar_cadeia(ctx.cfg, ctx.canal, fmt.largura, fmt.altura, pixabay=pixabay,
                          flux=gerador)
    pedidos = [
        Pedido(f"seg_{i:02d}", seg["visual"], seg["texto"], seg.get("cena", ""),
               pasta / f"seg_{i:02d}")
        for i, seg in enumerate(segmentos, 1)
        if i not in mantidas
    ]
    obtidas = resolver(cadeia, pedidos, fmt.orientacao, excluir) if pedidos else {}
    gravar_json(ctx, "fontes_midia.json", [
        mantidas[i] if i in mantidas else obtidas[f"seg_{i:02d}"].como_dict()
        for i in range(1, len(segmentos) + 1)
    ])
    atualizar_creditos(ctx)


def _a_manter(ctx: Contexto, anteriores: list[dict], n_segmentos: int) -> dict[int, dict]:
    """Visuais aprovados da rodada anterior que continuam valendo.

    Reprovação das checagens automáticas (fundo procedural): ficam todos os não procedurais.
    Reprovação do revisor: ficam os que ele não apontou em `trechos_visuais` (sem a lista, tudo é
    refeito). Roteiro reescrito depois dos visuais: tudo é refeito.

    A última revisão é a da rodada atual do job: a quarentena zera as rodadas, mas deixa os
    `revisao_N.json` do ciclo anterior na pasta. Com as rodadas zeradas (logo após a
    quarentena), vale a de número mais alto, que foi a que mandou o job para a quarentena.
    """
    if ctx.job.tentativas_revisao:
        rodadas = [ctx.pasta / f"revisao_{ctx.job.tentativas_revisao}.json"]
    else:
        rodadas = sorted(
            ctx.pasta.glob("revisao_*.json"),
            key=lambda a: int(a.stem.split("_")[1]) if a.stem.split("_")[1].isdigit() else 0)
    if not rodadas or len(anteriores) != n_segmentos:
        return {}
    try:
        ultima = json.loads(rodadas[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    roteiro, fontes = ctx.pasta / "roteiro.json", ctx.pasta / "fontes_midia.json"
    if roteiro.stat().st_mtime > fontes.stat().st_mtime:
        return {}
    if ultima.get("origem") == "checagens":
        refazer: set[int] = set()
    elif ultima.get("origem") == "revisor" and ultima.get("trechos_visuais"):
        refazer = {int(n) for n in ultima["trechos_visuais"]}
    else:
        return {}
    return {i: f for i, f in enumerate(anteriores, 1)
            if i not in refazer and f.get("origem") != "procedural"
            and Path(f.get("caminho", "")).is_file()}


def _guardar(ctx: Contexto, mantidas: dict[int, dict]) -> dict[int, dict]:
    """Tira os arquivos mantidos da pasta de visuais antes de ela ser recriada."""
    tmp = ctx.pasta / "visuais_mantidos"
    shutil.rmtree(tmp, ignore_errors=True)
    if not mantidas:
        return {}
    tmp.mkdir()
    guardadas = {}
    for i, f in mantidas.items():
        destino = tmp / Path(f["caminho"]).name
        shutil.move(f["caminho"], destino)
        guardadas[i] = {**f, "caminho": str(destino)}
    return guardadas


def _devolver(ctx: Contexto, guardadas: dict[int, dict], pasta) -> dict[int, dict]:
    devolvidas = {}
    for i, f in guardadas.items():
        destino = pasta / Path(f["caminho"]).name
        shutil.move(f["caminho"], destino)
        devolvidas[i] = {**f, "caminho": str(destino)}
    shutil.rmtree(ctx.pasta / "visuais_mantidos", ignore_errors=True)
    return devolvidas
