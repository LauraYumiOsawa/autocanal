"""FLUX.2 [klein] 4B local, em lote: o autocanal chama o projeto uv de `servicos/flux` como
subprocesso (contrato JSON) e nunca importa torch."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path

from autocanal import legendas, servicos
from autocanal.config import Config
from autocanal.provedores import ErroProvedor
from autocanal.provedores.imagens import MidiaObtida, Orientacao, Pedido

log = logging.getLogger(__name__)

LICENCA = "gerado localmente (FLUX.2 klein 4B, Apache 2.0)"


def instalado(cfg: Config) -> bool:
    return (cfg.flux.projeto / "pyproject.toml").is_file() and (cfg.flux.projeto / "gerar.py").is_file()


def memoria_livre() -> tuple[float, float]:
    """(RAM livre, VRAM livre) em GB; 0 quando não dá para medir."""
    ram = 0.0
    try:
        for linha in Path("/proc/meminfo").read_text().splitlines():
            if linha.startswith("MemAvailable:"):
                ram = int(linha.split()[1]) / 2**20
    except (OSError, ValueError, IndexError):
        pass
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        vram = float(r.stdout.split()[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        vram = 0.0
    return ram, vram


def preparar_memoria(raiz: Path) -> None:
    """Libera a placa para o FLUX: Whisper em cache neste processo e o contêiner do Kokoro."""
    legendas.liberar_modelos()
    servicos.parar_kokoro(raiz)


def prompt_final(cena: str, estilo_ia: str, sufixo: str) -> str:
    return ", ".join(p.strip() for p in (cena, estilo_ia, sufixo) if p and p.strip())


def semente(job_id: str, item_id: str, rodada: int) -> int:
    """Determinística por trecho e rodada de revisão: refazer os visuais gera imagens novas."""
    digest = hashlib.sha256(f"{job_id}|{item_id}|{rodada}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


class GeradorFlux:
    nome = "flux"

    def __init__(
        self,
        cfg: Config,
        *,
        job_id: str,
        rodada: int,
        estilo_visual: str,
        estilo_ia: str = "",
        comando_base: list[str] | None = None,
        executar=subprocess.run,
        memoria=memoria_livre,
    ):
        self.cfg = cfg
        self.job_id = job_id
        self.rodada = rodada
        self.estilo_visual = estilo_visual
        self.estilo_ia = estilo_ia
        f = cfg.flux
        self.comando_base = comando_base or [
            str(f.uv), "run", "--project", str(f.projeto), "python", str(f.projeto / "gerar.py"),
        ]
        self.executar = executar
        self.memoria = memoria

    def buscar_lote(
        self, pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str]
    ) -> dict[str, MidiaObtida]:
        if not pedidos:
            return {}
        f = self.cfg.flux
        ram, vram = self.memoria()
        if ram < f.ram_minima_gb or vram < f.vram_minima_gb:
            raise ErroProvedor(
                f"memória insuficiente para o FLUX (RAM livre {ram:.1f} GB, mínimo "
                f"{f.ram_minima_gb}; VRAM livre {vram:.1f} GB, mínimo {f.vram_minima_gb})"
            )
        lote = pedidos[0].destino_sem_ext.parent / "flux_lote"
        shutil.rmtree(lote, ignore_errors=True)
        (lote / "saida").mkdir(parents=True)
        largura, altura = (f.largura, f.altura) if orientacao == "paisagem" else (f.altura, f.largura)
        sufixo = f.sufixos.get(self.estilo_visual, "")
        itens = [
            {"id": p.id, "prompt": prompt_final(p.cena or p.consulta, self.estilo_ia, sufixo),
             "semente": semente(self.job_id, p.id, self.rodada)}
            for p in pedidos
        ]
        entrada = lote / "entrada.json"
        entrada.write_text(json.dumps(
            {"largura": largura, "altura": altura, "passos": f.passos, "itens": itens},
            ensure_ascii=False,
        ), encoding="utf-8")
        timeout = f.timeout_base_s + f.timeout_por_item_s * len(itens)
        try:
            r = self.executar([*self.comando_base, str(entrada), str(lote / "saida")],
                              capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ErroProvedor(f"FLUX excedeu {timeout}s") from None
        except OSError as e:
            raise ErroProvedor(f"FLUX não executou: {type(e).__name__}") from None
        if r.returncode != 0:
            raise ErroProvedor(
                f"FLUX falhou (código {r.returncode}): {(r.stderr or '').strip()[-300:]}"
            )
        try:
            resultado = json.loads((lote / "saida" / "resultado.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ErroProvedor("FLUX não gravou resultado.json") from None
        por_id = {i["id"]: i for i in itens}
        destinos = {p.id: p.destino_sem_ext for p in pedidos}
        obtidas: dict[str, MidiaObtida] = {}
        for item in resultado.get("itens", []):
            pid = item.get("id")
            png = lote / "saida" / f"{pid}.png"
            if pid not in destinos or not item.get("ok") or not png.is_file():
                if pid in destinos:
                    log.warning("FLUX não gerou %s: %s", pid, item.get("erro", ""))
                continue
            destino = Path(str(destinos[pid]) + ".png")
            shutil.move(png, destino)
            pedido = por_id[pid]
            marca = hashlib.sha256(f"{pedido['prompt']}|{pedido['semente']}".encode()).hexdigest()
            obtidas[pid] = MidiaObtida(destino, "imagem", "flux", f"flux:{marca[:8]}", LICENCA,
                                       "autocanal", prompt=pedido["prompt"])
        return obtidas
