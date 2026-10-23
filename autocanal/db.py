"""Banco SQLite do autocanal: conexão e schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    canal TEXT NOT NULL,
    formato TEXT NOT NULL,
    tema TEXT NOT NULL,
    briefing_json TEXT NOT NULL DEFAULT '{}',
    estado TEXT NOT NULL,
    publish_at TEXT NOT NULL,
    tentativas_revisao INTEGER NOT NULL DEFAULT 0,
    youtube_video_id TEXT,
    erro TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_estado_publish ON jobs(estado, publish_at);
CREATE TABLE IF NOT EXISTS etapas (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    estado TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0,
    iniciado_em TEXT,
    concluido_em TEXT,
    erro TEXT,
    PRIMARY KEY (job_id, nome)
);
CREATE TABLE IF NOT EXISTS revisoes (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    rodada INTEGER NOT NULL,
    aprovado INTEGER NOT NULL,
    motivos_json TEXT NOT NULL DEFAULT '[]',
    etapa_para_refazer TEXT,
    criado_em TEXT NOT NULL,
    PRIMARY KEY (job_id, rodada)
);
CREATE TABLE IF NOT EXISTS canais_estado (
    canal TEXT PRIMARY KEY,
    youtube_channel_id TEXT,
    auth_ok INTEGER NOT NULL DEFAULT 0,
    ultimo_erro TEXT
);
CREATE TABLE IF NOT EXISTS cota (
    dia TEXT NOT NULL,
    bucket TEXT NOT NULL,
    unidades INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (dia, bucket)
);
CREATE TABLE IF NOT EXISTS metricas (
    youtube_video_id TEXT NOT NULL,
    dia TEXT NOT NULL,
    views INTEGER,
    minutos_assistidos REAL,
    inscritos_ganhos INTEGER,
    ctr REAL,
    PRIMARY KEY (youtube_video_id, dia)
);
CREATE TABLE IF NOT EXISTS temas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canal TEXT NOT NULL,
    formato TEXT NOT NULL,
    tema TEXT NOT NULL,
    referencia_url TEXT,
    multiplo REAL,
    usado INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chamadas_claude (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    etapa TEXT NOT NULL,
    custo_usd REAL,
    turnos INTEGER,
    duracao_s REAL,
    criado_em TEXT NOT NULL
);
"""


def conectar(caminho: Path) -> sqlite3.Connection:
    """Abre (criando diretório e schema, se preciso) o banco em `caminho`."""
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    con.commit()
    return con
