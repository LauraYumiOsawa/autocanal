import json
from datetime import UTC, datetime

from autocanal import fila
from autocanal.db import conectar
from autocanal.etapas import legendas
from autocanal.fila import Estado
from autocanal.legendas import Palavra
from autocanal.midia import Midia
from autocanal.produtor import produzir


def _ok(dados):
    return {
        "type": "result", "subtype": "success", "is_error": False, "num_turns": 4,
        "total_cost_usd": 0.2, "duration_ms": 3000, "structured_output": dados,
    }


def _roteiro(palavras: int = 100, n: int = 4) -> dict:
    por = palavras // n
    segs = [
        {"texto": " ".join(f"palavra{s}x{i}" for i in range(por)), "visual": f"ocean view {s}"}
        for s in range(n)
    ]
    return {
        "gancho": "palavra0x0", "nota_gancho": 72, "segmentos": segs,
        "fontes": [{"afirmacao": "o mar é salgado", "url": "https://exemplo.org/mar"}],
        "resumo": "Por que o mar é salgado.",
    }


def test_short_offline_gera_mp4_valido(cfg, claude_falso, monkeypatch):
    cfg.claude.bin = claude_falso.claude_bin
    claude_falso.responder(
        _ok(_roteiro()),
        _ok({"titulo": "Por que o mar é salgado? 3 motivos", "texto_thumb": "SAL ESCONDIDO",
             "descricao": "Resumo do vídeo.", "tags": ["mar", "ciência"],
             "consultas_alvo": ["por que o mar é salgado", "sal do mar", "água do mar"]}),
        _ok({"aprovado": True, "nota": 90, "problemas": [], "etapa_para_refazer": None,
             "contains_synthetic_media": False}),
    )

    def transcrever_falso(wav, **kw):
        return [Palavra(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(100)]

    monkeypatch.setattr(legendas, "transcrever", transcrever_falso)
    con = conectar(cfg.dados / "autocanal.db")
    job = fila.criar_job(
        con, "teste", "short", "por que o mar é salgado", datetime(2026, 11, 2, 15, tzinfo=UTC)
    )
    assert produzir(cfg, con, job.id) == Estado.APROVADO
    assert len(claude_falso.chamadas()) == 3  # roteiro, metadados e revisor
    pasta = cfg.dados / "jobs" / job.id
    meta = json.loads((pasta / "metadados.json").read_text(encoding="utf-8"))
    assert meta["nota_titulo"] >= cfg.revisao.nota_titulo_minima
    assert "https://exemplo.org/mar" in meta["descricao_final"]
    assert meta["descricao_final"].endswith("#shorts") and meta["sintetico"] is False
    midia = Midia(cfg.ffmpeg, cfg.ffprobe)
    video = pasta / "video.mp4"
    info = midia.info(video)
    assert (info.largura, info.altura) == (1080, 1920)
    assert info.tem_audio and 30 <= info.duracao <= 60
    assert -15.5 <= midia.loudness(video) <= -12.5
    con.close()
