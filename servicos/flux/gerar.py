"""Gera imagens com o FLUX.2 [klein] 4B em lote, numa placa de 8 GB.

Uso: gerar.py ENTRADA.json SAIDA/
  entrada: {"largura", "altura", "passos", "itens": [{"id", "prompt", "semente"}]}
  saída:   SAIDA/<id>.png e SAIDA/resultado.json
           {"modelo", "itens": [{"id", "ok", "erro"}], "vram_pico_mb", "ram_pico_mb", "segundos"}
Código 0 = o lote rodou (itens podem falhar um a um); outro código = o lote inteiro falhou.

Duas fases para caber na placa (sonda de 2026-10-23: VRAM 7,4 GB, RAM 5,9 GB, ~6,5 s/imagem):
o encoder Qwen3 em 4 bits codifica todos os prompts e é liberado; depois o transformer em 4 bits
(nf4) e o VAE geram as imagens. Os pesos vêm do cache do Hugging Face (modo offline por padrão).
"""
from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # antes de importar o huggingface_hub

import gc  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = "black-forest-labs/FLUX.2-klein-4B"


class Picos:
    """Pico de RAM do processo e de VRAM usada na placa (nvidia-smi), medidos em segundo plano."""

    def __init__(self):
        import psutil

        self.ram_mb = 0.0
        self.vram_mb = 0.0
        self._proc = psutil.Process()
        self._parar = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._parar.is_set():
            self.ram_mb = max(self.ram_mb, self._proc.memory_info().rss / 2**20)
            try:
                usado = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5).stdout.split()[0]
                self.vram_mb = max(self.vram_mb, float(usado))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
            time.sleep(0.25)

    def parar(self):
        self._parar.set()
        self._t.join()


def _codificar(prompts: list[str]):
    import torch
    from diffusers import Flux2KleinPipeline
    from transformers import BitsAndBytesConfig, Qwen3ForCausalLM

    te = Qwen3ForCausalLM.from_pretrained(
        REPO, subfolder="text_encoder", torch_dtype=torch.bfloat16, device_map="cuda",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.bfloat16))
    pipe = Flux2KleinPipeline.from_pretrained(REPO, text_encoder=te, transformer=None, vae=None,
                                              torch_dtype=torch.bfloat16)
    with torch.no_grad():
        embeds = [pipe.encode_prompt(prompt=p, device="cuda")[0].to("cpu") for p in prompts]
    del pipe, te
    gc.collect()
    torch.cuda.empty_cache()
    return embeds


def _pipeline_imagem():
    import torch
    from diffusers import BitsAndBytesConfig, Flux2KleinPipeline, Flux2Transformer2DModel

    tr = Flux2Transformer2DModel.from_pretrained(
        REPO, subfolder="transformer", torch_dtype=torch.bfloat16,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.bfloat16))
    pipe = Flux2KleinPipeline.from_pretrained(REPO, text_encoder=None, tokenizer=None,
                                              transformer=tr, torch_dtype=torch.bfloat16)
    pipe.vae.to("cuda")
    return pipe


def main(entrada_arq: Path, saida: Path) -> int:
    entrada = json.loads(entrada_arq.read_text(encoding="utf-8"))
    saida.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    picos = Picos()
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("sem GPU CUDA")
        embeds = _codificar([i["prompt"] for i in entrada["itens"]])
        pipe = _pipeline_imagem()
    except Exception as e:  # noqa: BLE001 - qualquer falha de carga derruba o lote inteiro
        picos.parar()
        print(f"falha ao carregar o FLUX: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 2

    itens = []
    for item, pe in zip(entrada["itens"], embeds):
        try:
            with torch.no_grad():
                img = pipe(prompt_embeds=pe.to("cuda"), width=entrada["largura"],
                           height=entrada["altura"], num_inference_steps=entrada["passos"],
                           guidance_scale=1.0,  # modelo destilado: sem guidance
                           generator=torch.Generator("cuda").manual_seed(item["semente"])).images[0]
            img.save(saida / f"{item['id']}.png")
            itens.append({"id": item["id"], "ok": True, "erro": ""})
        except Exception as e:  # noqa: BLE001 - um item com erro não derruba os outros
            torch.cuda.empty_cache()
            itens.append({"id": item["id"], "ok": False, "erro": f"{type(e).__name__}: {str(e)[:150]}"})
    picos.parar()
    (saida / "resultado.json").write_text(json.dumps({
        "modelo": f"{REPO} (nf4)", "itens": itens, "vram_pico_mb": round(picos.vram_mb),
        "ram_pico_mb": round(picos.ram_mb), "segundos": round(time.monotonic() - t0, 1),
    }, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("uso: gerar.py ENTRADA.json SAIDA/", file=sys.stderr)
        sys.exit(64)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
