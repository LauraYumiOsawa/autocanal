"""Configuração do autocanal: carrega YAMLs e segredos, valida com pydantic."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator

Dia = Literal["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Paleta(BaseModel):
    fundo: str
    texto: str
    destaque: str

    @field_validator("fundo", "texto", "destaque")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError(f"cor inválida (esperado #RRGGBB): {v}")
        return v


class ConfigTTS(BaseModel):
    provedor: Literal["google", "kokoro", "falso"]
    voz: str
    reserva: Literal["kokoro", "falso"] | None = "kokoro"
    voz_reserva: str = "pm_alex"


class Slot(BaseModel):
    formato: str
    dias: list[Dia]
    hora: str

    @field_validator("hora")
    @classmethod
    def _hora(cls, v: str) -> str:
        if not _HORA.match(v):
            raise ValueError(f"hora inválida (esperado HH:MM): {v}")
        return v


class Canal(BaseModel):
    slug: str
    nome: str
    nicho: str = ""
    idioma: str = "pt-BR"
    categoria_id: str
    tts: ConfigTTS
    paleta: Paleta
    fonte_arquivo: Path
    fonte_nome: str
    perfil_voz: Path
    grade: list[Slot]
    feito_para_criancas: bool = False
    # fontes dos visuais, na ordem; o fundo procedural é sempre a última (e reprovado na revisão)
    visuais: list[Literal["pixabay", "flux"]] = ["pixabay"]
    estilo_visual: Literal["realista", "desenho"] = "realista"
    estilo_ia: str = ""


ETAPAS_IA = ("roteiro", "curadoria", "metadados", "revisao", "temas")


def _etapas_conhecidas(chaves, onde: str) -> None:
    desconhecidas = sorted(set(chaves) - set(ETAPAS_IA))
    if desconhecidas:
        raise ValueError(f"etapa(s) desconhecida(s) em {onde}: {', '.join(desconhecidas)}")


class ConfigProvedor(BaseModel):
    bin: str
    modelos: dict[str, str | None] = {}
    esforco: dict[str, str | None] = {}  # nível de esforço/raciocínio por etapa


class ConfigIA(BaseModel):
    """Provedor de IA por etapa: `padrao` vale para as etapas fora de `etapas`."""

    padrao: str = "claude"
    etapas: dict[str, str] = {}
    max_turns: dict[str, int] = {}
    provedores: dict[str, ConfigProvedor]

    @model_validator(mode="after")
    def _coerente(self) -> ConfigIA:
        _etapas_conhecidas(self.etapas, "ia.etapas")
        _etapas_conhecidas(self.max_turns, "ia.max_turns")
        for nome, prov in self.provedores.items():
            _etapas_conhecidas(prov.modelos, f"ia.provedores.{nome}.modelos")
            _etapas_conhecidas(prov.esforco, f"ia.provedores.{nome}.esforco")
        for usado in {self.padrao, *self.etapas.values()}:
            if usado not in self.provedores:
                raise ValueError(f"provedor {usado} sem entrada em ia.provedores")
        return self


class ConfigWhisper(BaseModel):
    modelo: str = "small"
    dispositivo: str = "cpu"
    compute_type: str = "int8"


class ConfigRevisao(BaseModel):
    nota_titulo_minima: int = 70
    similaridade_maxima: float = 0.35
    max_rodadas: int = 2
    supervisao_dias: int = 14


class ConfigFlux(BaseModel):
    """FLUX.2 [klein] 4B em lote no projeto uv `projeto` (caminhos já resolvidos pela raiz)."""

    projeto: Path = Path("servicos/flux")
    passos: int = 4
    largura: int = 1360  # paisagem; o retrato inverte
    altura: int = 768
    ram_minima_gb: float = 6.0
    vram_minima_gb: float = 7.0
    timeout_base_s: int = 180
    timeout_por_item_s: int = 45
    variantes: int = 3  # imagens por trecho; a curadoria escolhe a melhor ou nenhuma
    sufixos: dict[str, str] = {
        "realista": "photorealistic photo, natural light, no text, no watermark",
        "desenho": "colorful 2D cartoon illustration, clean lines, flat shading, no text, "
                   "no watermark",
    }


class Config(BaseModel):
    raiz: Path
    dados: Path
    fuso: str
    folga_dias: int
    ffmpeg: Path
    ffprobe: Path
    plugin_dir: Path
    client_secret: Path
    kokoro_url: str
    kokoro_timeout_s: int = 600  # o contêiner leva ~4 min para carregar o modelo na CPU
    ia: ConfigIA
    whisper: ConfigWhisper = ConfigWhisper()
    revisao: ConfigRevisao = ConfigRevisao()
    flux: ConfigFlux = ConfigFlux()
    chaves: dict[str, str]
    canais: dict[str, Canal]


def _caminho(raiz: Path, valor: str | Path) -> Path:
    p = Path(valor).expanduser()
    return p if p.is_absolute() else raiz / p


def _ler_yaml(arquivo: Path) -> dict:
    return yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}


def _ler_segredos(arquivo: Path) -> dict[str, str]:
    chaves: dict[str, str] = {}
    if not arquivo.exists():
        return chaves
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]  # CHAVE="valor" ou CHAVE='valor'
        chaves[nome.strip()] = valor
    return chaves


def resolver_bin(valor: str) -> str:
    """Caminho absoluto do CLI de um provedor de IA (o systemd não tem o PATH do shell).

    Absoluto fica como está; senão `shutil.which`; senão `~/.local/bin/<valor>`.
    """
    p = Path(valor).expanduser()
    if p.is_absolute():
        return str(p)
    achado = shutil.which(valor)
    if achado:
        return os.path.abspath(achado)  # sem resolve(): o link em ~/.local/bin sobrevive a updates
    return str(Path.home() / ".local" / "bin" / valor)


def carregar(raiz: Path) -> Config:
    raiz = Path(raiz).expanduser()
    base = _ler_yaml(raiz / "config" / "autocanal.yaml")
    for campo in ("ffmpeg", "ffprobe", "plugin_dir", "client_secret"):
        base[campo] = _caminho(raiz, base[campo])

    brutos = _ler_yaml(raiz / "config" / "canais.yaml")
    brutos = brutos.get("canais", brutos)
    canais: dict[str, Canal] = {}
    for slug, dados in brutos.items():
        dados = dict(dados)
        dados["fonte_arquivo"] = _caminho(raiz, dados["fonte_arquivo"])
        dados["perfil_voz"] = _caminho(raiz, dados["perfil_voz"])
        canais[slug] = Canal(slug=slug, **dados)

    flux_cfg = dict(base.get("flux") or {})
    flux_cfg["projeto"] = _caminho(raiz, flux_cfg.get("projeto", "servicos/flux"))
    base["flux"] = flux_cfg

    if "claude" in base:
        raise ValueError("o bloco `claude:` virou `ia:`; veja `config/autocanal.yaml`")
    ia_cfg = dict(base.get("ia") or {})
    ia_cfg["provedores"] = {
        nome: {**dados, "bin": resolver_bin(str(dados.get("bin") or nome))}
        for nome, dados in (ia_cfg.get("provedores") or {}).items()
    }
    base["ia"] = ia_cfg

    chaves = _ler_segredos(raiz / "config" / "segredos.env")
    return Config(
        raiz=raiz, dados=raiz / "dados", chaves=chaves, canais=canais, **base
    )
