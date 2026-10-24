"""FLUX.2 [klein] 4B local, em lote: o autocanal chama o projeto uv de `servicos/flux` como
subprocesso (contrato JSON) e nunca importa torch."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

from autocanal import legendas, servicos
from autocanal.config import Config
from autocanal.provedores import ErroProvedor
from autocanal.provedores.imagens import MidiaObtida, Orientacao, Pedido

log = logging.getLogger(__name__)

LICENCA = "gerado localmente (FLUX.2 klein 4B, Apache 2.0)"


def _python(cfg: Config) -> Path:
    return cfg.flux.projeto / ".venv" / "bin" / "python"


def instalado(cfg: Config) -> bool:
    """Código no git não basta: precisa do ambiente criado com `uv sync --project servicos/flux`."""
    return _python(cfg).is_file() and (cfg.flux.projeto / "gerar.py").is_file()


def rodar_em_grupo(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Roda em sessão própria; no timeout mata o grupo inteiro (o python do FLUX pode ser neto
    do comando e, órfão, seguraria ~7 GB da placa)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        saida, erro = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, saida, erro)


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


def semente(job_id: str, item_id: str, rodada: int, tentativa: int = 0) -> int:
    """Determinística por trecho e rodada de revisão: refazer os visuais gera imagens novas.
    `tentativa` pula sementes cujas imagens já foram usadas (ex.: refazer da quarentena)."""
    chave = f"{job_id}|{item_id}|{rodada}" + (f"|{tentativa}" if tentativa else "")
    digest = hashlib.sha256(chave.encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _url(prompt: str, semente_: int) -> str:
    return "flux:" + hashlib.sha256(f"{prompt}|{semente_}".encode()).hexdigest()[:8]


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
        executar=rodar_em_grupo,
        memoria=memoria_livre,
    ):
        self.cfg = cfg
        self.job_id = job_id
        self.rodada = rodada
        self.estilo_visual = estilo_visual
        self.estilo_ia = estilo_ia
        # o python do ambiente direto, sem `uv run`: nada de sincronizar, baixar pacotes ou
        # reescrever o uv.lock no meio da produção
        self.comando_base = comando_base or [str(_python(cfg)), str(cfg.flux.projeto / "gerar.py")]
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
        itens = []
        for p in pedidos:
            prompt = prompt_final(p.cena or p.consulta, self.estilo_ia, sufixo)
            tentativa = 0
            while _url(prompt, semente(self.job_id, p.id, self.rodada, tentativa)) in excluir:
                tentativa += 1
            itens.append({"id": p.id, "prompt": prompt,
                          "semente": semente(self.job_id, p.id, self.rodada, tentativa)})
        entrada = lote / "entrada.json"
        entrada.write_text(json.dumps(
            {"largura": largura, "altura": altura, "passos": f.passos, "itens": itens},
            ensure_ascii=False,
        ), encoding="utf-8")
        timeout = f.timeout_base_s + f.timeout_por_item_s * len(itens)
        try:
            r = self.executar([*self.comando_base, str(entrada), str(lote / "saida")],
                              timeout=timeout)
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
            obtidas[pid] = MidiaObtida(destino, "imagem", "flux",
                                       _url(pedido["prompt"], pedido["semente"]), LICENCA,
                                       "autocanal", prompt=pedido["prompt"])
        return obtidas
