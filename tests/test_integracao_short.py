from datetime import UTC, datetime

import pytest

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


@pytest.mark.xfail(strict=False, reason="depende da Task 10")
def test_short_offline_gera_mp4_valido(cfg, claude_falso, monkeypatch):
    cfg.claude.bin = claude_falso.claude_bin
    # metadados e revisão: placeholders que a Task 10 finaliza
    claude_falso.responder(
        _ok(_roteiro()),
        _ok({"titulo": "Por que o mar é salgado", "descricao": "Resumo do vídeo.",
             "tags": ["mar", "ciência"]}),
        _ok({"aprovado": True, "nota": 90, "problemas": []}),
    )

    def transcrever_falso(wav, **kw):
        return [Palavra(f"p{i}", i * 0.4, i * 0.4 + 0.35) for i in range(100)]

    monkeypatch.setattr(legendas, "transcrever", transcrever_falso)
    con = conectar(cfg.dados / "autocanal.db")
    job = fila.criar_job(
        con, "teste", "short", "por que o mar é salgado", datetime(2026, 11, 2, 15, tzinfo=UTC)
    )
    assert produzir(cfg, con, job.id) == Estado.APROVADO
    midia = Midia(cfg.ffmpeg, cfg.ffprobe)
    video = cfg.dados / "jobs" / job.id / "video.mp4"
    info = midia.info(video)
    assert (info.largura, info.altura) == (1080, 1920)
    assert info.tem_audio and 30 <= info.duracao <= 60
    assert -15.5 <= midia.loudness(video) <= -12.5
    con.close()
