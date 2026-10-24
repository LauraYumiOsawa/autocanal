"""Legendas: transcrição com faster-whisper (palavra a palavra) e arquivo ASS."""
from __future__ import annotations

import difflib
import gc
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

_MODELOS: dict[tuple[str, str, str], object] = {}
_FINAIS = (".", "!", "?")
_SEM_PAR_S = 0.25  # duração de palavra do roteiro sem par no Whisper, nas pontas


@dataclass(frozen=True)
class Palavra:
    texto: str
    inicio: float
    fim: float


@dataclass(frozen=True)
class Legenda:
    texto: str
    inicio: float
    fim: float


def _modelo(modelo: str, dispositivo: str, compute_type: str):
    chave = (modelo, dispositivo, compute_type)
    if chave not in _MODELOS:
        from faster_whisper import WhisperModel  # import tardio: é pesado

        _MODELOS[chave] = WhisperModel(
            modelo, device=dispositivo, compute_type=compute_type
        )
    return _MODELOS[chave]


def liberar_modelos() -> None:
    """Solta os modelos do Whisper em cache (libera a placa antes do FLUX)."""
    _MODELOS.clear()
    gc.collect()


def transcrever(
    wav: Path,
    *,
    modelo: str = "small",
    dispositivo: str = "cpu",
    compute_type: str = "int8",
    idioma: str = "pt",
) -> list[Palavra]:
    segmentos, _ = _modelo(modelo, dispositivo, compute_type).transcribe(
        str(wav), language=idioma, word_timestamps=True, vad_filter=False
    )
    palavras = []
    for seg in segmentos:
        for w in seg.words or []:
            texto = w.word.strip()
            if texto:
                palavras.append(Palavra(texto, float(w.start), float(w.end)))
    return palavras


def _normalizar(palavra: str) -> str:
    """Minúsculas, sem acentos nem pontuação (só para comparar)."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", palavra) if not unicodedata.combining(c)
    )
    return "".join(c for c in sem_acento.lower() if c.isalnum())


def _palavras_roteiro(texto: str) -> list[str]:
    """Palavras do roteiro; sinais soltos (ex.: "—") grudam na palavra vizinha."""
    palavras: list[str] = []
    pendente = ""
    for token in texto.split():
        if _normalizar(token):
            palavras.append(pendente + token)
            pendente = ""
        elif palavras:
            palavras[-1] += " " + token
        else:
            pendente += token + " "
    return palavras


def alinhar(texto: str, transcritas: list[Palavra]) -> list[Palavra]:
    """Texto do roteiro com os tempos do Whisper.

    Palavras casadas herdam os tempos do Whisper; trechos trocados dividem o intervalo
    das palavras do Whisper correspondentes; palavras sem par são interpoladas entre
    as vizinhas. O texto (caixa, acentos, pontuação) é sempre o do roteiro.
    """
    roteiro = _palavras_roteiro(texto)
    if not roteiro or not transcritas:
        return []
    a = [_normalizar(p) for p in roteiro]
    b = [_normalizar(p.texto) for p in transcritas]
    saida: list[Palavra] = []

    def distribuir(i1: int, i2: int, inicio: float, fim: float) -> None:
        fim = max(fim, inicio)
        passo = (fim - inicio) / (i2 - i1)
        for k, i in enumerate(range(i1, i2)):
            saida.append(Palavra(roteiro[i], inicio + k * passo, inicio + (k + 1) * passo))

    fim_total = transcritas[-1].fim
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                w = transcritas[j]
                saida.append(Palavra(roteiro[i], w.inicio, w.fim))
        elif op == "replace":
            distribuir(i1, i2, transcritas[j1].inicio, transcritas[j2 - 1].fim)
        elif op == "delete":
            n = i2 - i1
            if j1 >= len(transcritas):  # sobra no fim: logo após a última palavra
                inicio = saida[-1].fim if saida else fim_total
                distribuir(i1, i2, inicio, inicio + _SEM_PAR_S * n)
            elif not saida:  # sobra no começo: logo antes da primeira palavra
                fim = transcritas[0].inicio
                distribuir(i1, i2, max(0.0, fim - _SEM_PAR_S * n), fim)
            else:
                distribuir(i1, i2, saida[-1].fim, transcritas[j1].inicio)
    return saida


MARGEM_LATERAL = 60
CONTORNO = 6


def largura_util(largura: int) -> int:
    """Largura em que a legenda cabe: o quadro menos as margens e o contorno dos dois lados."""
    return largura - 2 * MARGEM_LATERAL - 2 * CONTORNO


def medidor(fonte_arquivo: Path, tamanho: int) -> Callable[[str], float]:
    fonte = ImageFont.truetype(str(fonte_arquivo), tamanho)
    return fonte.getlength


def agrupar(
    palavras: list[Palavra],
    max_palavras: int = 3,
    max_duracao_s: float = 1.6,
    *,
    largura_max: float | None = None,
    medir: Callable[[str], float] | None = None,
) -> list[Legenda]:
    """Blocos de legenda; com `medir`, fecha o bloco antes de passar de `largura_max`
    (o texto vai em caixa alta, com `WrapStyle: 2` o libass não quebra a linha)."""
    legendas: list[Legenda] = []
    grupo: list[Palavra] = []

    def fechar() -> None:
        if grupo:
            legendas.append(
                Legenda(" ".join(p.texto for p in grupo), grupo[0].inicio, grupo[-1].fim)
            )
            grupo.clear()

    def largo_demais(p: Palavra) -> bool:
        if medir is None or largura_max is None:
            return False
        texto = " ".join(x.texto for x in [*grupo, p]).upper()
        return medir(texto) > largura_max

    for p in palavras:
        if grupo and (p.fim - grupo[0].inicio > max_duracao_s or largo_demais(p)):
            fechar()
        grupo.append(p)
        if len(grupo) >= max_palavras or p.texto.endswith(_FINAIS):
            fechar()
    fechar()
    return legendas


def cor_ass(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return "&H00" + f"{b}{g}{r}".upper()


def escapar_ass(texto: str) -> str:
    texto = texto.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return texto.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _tempo(s: float) -> str:
    cs = round(s * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    seg, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{seg:02d}.{cs:02d}"


def gerar_ass(
    legendas: list[Legenda],
    destino: Path,
    *,
    largura: int,
    altura: int,
    fonte_nome: str,
    tamanho: int,
    cor_texto: str,
    cor_destaque: str,
    margem_v: int,
) -> Path:
    estilo = (
        f"Style: Legenda,{fonte_nome},{tamanho},{cor_ass(cor_texto)},"
        f"{cor_ass(cor_destaque)},&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{CONTORNO},0,2,{MARGEM_LATERAL},{MARGEM_LATERAL},{margem_v},1"
    )
    linhas = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {largura}",
        f"PlayResY: {altura}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        estilo,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for lg in legendas:
        linhas.append(
            f"Dialogue: 0,{_tempo(lg.inicio)},{_tempo(lg.fim)},Legenda,,0,0,0,,"
            f"{escapar_ass(lg.texto.upper())}"
        )
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return destino
