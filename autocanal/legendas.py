"""Legendas: transcrição com faster-whisper (palavra a palavra) e arquivo ASS."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_MODELOS: dict[tuple[str, str, str], object] = {}
_FINAIS = (".", "!", "?")


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


def agrupar(
    palavras: list[Palavra], max_palavras: int = 3, max_duracao_s: float = 1.6
) -> list[Legenda]:
    legendas: list[Legenda] = []
    grupo: list[Palavra] = []

    def fechar() -> None:
        if grupo:
            legendas.append(
                Legenda(" ".join(p.texto for p in grupo), grupo[0].inicio, grupo[-1].fim)
            )
            grupo.clear()

    for p in palavras:
        if grupo and p.fim - grupo[0].inicio > max_duracao_s:
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
        f"-1,0,0,0,100,100,0,0,1,6,0,2,60,60,{margem_v},1"
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
