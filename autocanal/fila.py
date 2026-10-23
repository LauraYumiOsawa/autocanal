"""Fila de jobs e máquina de estados."""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class Estado(StrEnum):
    PLANEJADO = "planejado"
    PRODUZINDO = "produzindo"
    EM_REVISAO = "em_revisao"
    APROVADO = "aprovado"
    AGENDADO = "agendado"
    PUBLICADO = "publicado"
    QUARENTENA = "quarentena"
    FALHOU = "falhou"
    VETADO = "vetado"
    DESCARTADO = "descartado"


E = Estado
TRANSICOES: dict[Estado, frozenset[Estado]] = {
    E.PLANEJADO: frozenset({E.PRODUZINDO, E.DESCARTADO}),
    E.PRODUZINDO: frozenset({E.EM_REVISAO, E.FALHOU, E.DESCARTADO}),
    E.EM_REVISAO: frozenset({E.APROVADO, E.PRODUZINDO, E.QUARENTENA, E.FALHOU}),
    E.APROVADO: frozenset({E.AGENDADO, E.FALHOU, E.DESCARTADO}),
    E.AGENDADO: frozenset({E.PUBLICADO, E.VETADO}),
    E.QUARENTENA: frozenset({E.APROVADO, E.PRODUZINDO, E.DESCARTADO}),
    E.FALHOU: frozenset({E.PRODUZINDO, E.DESCARTADO}),
    E.VETADO: frozenset({E.DESCARTADO}),
    E.PUBLICADO: frozenset(),
    E.DESCARTADO: frozenset(),
}


class TransicaoInvalida(Exception):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    canal: str
    formato: str
    tema: str
    briefing: dict
    estado: Estado
    publish_at: datetime
    tentativas_revisao: int
    youtube_video_id: str | None
    erro: str | None
    criado_em: datetime


def _agora() -> datetime:
    return datetime.now(UTC)


def _iso(d: datetime) -> str:
    if d.tzinfo is None:
        raise ValueError("datetime precisa ter fuso (tz-aware)")
    return d.astimezone(UTC).isoformat()


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _job(r: sqlite3.Row) -> Job:
    return Job(
        id=r["id"], canal=r["canal"], formato=r["formato"], tema=r["tema"],
        briefing=json.loads(r["briefing_json"]), estado=Estado(r["estado"]),
        publish_at=_dt(r["publish_at"]), tentativas_revisao=r["tentativas_revisao"],
        youtube_video_id=r["youtube_video_id"], erro=r["erro"],
        criado_em=_dt(r["criado_em"]),
    )


def criar_job(
    con: sqlite3.Connection, canal: str, formato: str, tema: str,
    publish_at: datetime, briefing: dict | None = None,
) -> Job:
    pa = _iso(publish_at)
    agora = _iso(_agora())
    dia = publish_at.astimezone(UTC).strftime("%Y%m%d")
    for _ in range(20):
        job_id = f"{dia}-{canal}-{formato}-{secrets.token_hex(2)}"
        try:
            con.execute(
                "INSERT INTO jobs (id, canal, formato, tema, briefing_json, estado,"
                " publish_at, criado_em, atualizado_em) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, canal, formato, tema, json.dumps(briefing or {}, ensure_ascii=False),
                 Estado.PLANEJADO.value, pa, agora, agora),
            )
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise RuntimeError("não foi possível gerar id único para o job")
    con.commit()
    return obter(con, job_id)


def obter(con: sqlite3.Connection, job_id: str) -> Job:
    r = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if r is None:
        raise KeyError(job_id)
    return _job(r)


def listar(
    con: sqlite3.Connection, estados: set[Estado] | None = None, canal: str | None = None
) -> list[Job]:
    sql, args, filtros = "SELECT * FROM jobs", [], []
    if estados is not None:
        filtros.append(f"estado IN ({','.join('?' * len(estados))})")
        args += [Estado(e).value for e in estados]
    if canal is not None:
        filtros.append("canal = ?")
        args.append(canal)
    if filtros:
        sql += " WHERE " + " AND ".join(filtros)
    sql += " ORDER BY publish_at, id"
    return [_job(r) for r in con.execute(sql, args)]


def mudar_estado(
    con: sqlite3.Connection, job_id: str, novo: Estado, erro: str | None = None
) -> Job:
    atual = obter(con, job_id).estado
    novo = Estado(novo)
    if novo not in TRANSICOES[atual]:
        raise TransicaoInvalida(f"{job_id}: {atual.value} -> {novo.value}")
    con.execute(
        "UPDATE jobs SET estado = ?, erro = ?, atualizado_em = ? WHERE id = ?",
        (novo.value, erro, _iso(_agora()), job_id),
    )
    con.commit()
    return obter(con, job_id)


_CAMPOS = {"publish_at", "youtube_video_id", "tentativas_revisao", "briefing", "erro"}


def atualizar(con: sqlite3.Connection, job_id: str, **campos) -> Job:
    invalidos = set(campos) - _CAMPOS
    if invalidos:
        raise ValueError(f"campos não atualizáveis: {sorted(invalidos)}")
    obter(con, job_id)
    if not campos:
        return obter(con, job_id)
    sets, args = [], []
    for nome, valor in campos.items():
        if nome == "publish_at":
            sets.append("publish_at = ?")
            args.append(_iso(valor))
        elif nome == "briefing":
            sets.append("briefing_json = ?")
            args.append(json.dumps(valor, ensure_ascii=False))
        else:
            sets.append(f"{nome} = ?")
            args.append(valor)
    sets.append("atualizado_em = ?")
    args += [_iso(_agora()), job_id]
    con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    con.commit()
    return obter(con, job_id)


def proximo_para_produzir(con: sqlite3.Connection) -> Job | None:
    r = con.execute(
        "SELECT * FROM jobs WHERE estado IN (?, ?) ORDER BY publish_at, id LIMIT 1",
        (Estado.PLANEJADO.value, Estado.PRODUZINDO.value),
    ).fetchone()
    return _job(r) if r else None


def estado_etapa(con: sqlite3.Connection, job_id: str, nome: str) -> str | None:
    r = con.execute(
        "SELECT estado FROM etapas WHERE job_id = ? AND nome = ?", (job_id, nome)
    ).fetchone()
    return r["estado"] if r else None


def tentativas_etapa(con: sqlite3.Connection, job_id: str, nome: str) -> int:
    r = con.execute(
        "SELECT tentativas FROM etapas WHERE job_id = ? AND nome = ?", (job_id, nome)
    ).fetchone()
    return r["tentativas"] if r else 0


def marcar_etapa(
    con: sqlite3.Connection, job_id: str, nome: str, estado: str, erro: str | None = None
) -> None:
    if estado not in ("rodando", "ok", "erro"):
        raise ValueError(f"estado de etapa inválido: {estado}")
    agora = _iso(_agora())
    inc = 1 if estado == "erro" else 0
    con.execute(
        "INSERT INTO etapas (job_id, nome, estado, tentativas, iniciado_em, concluido_em, erro)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(job_id, nome) DO UPDATE SET estado = excluded.estado,"
        " tentativas = etapas.tentativas + ?,"
        " iniciado_em = CASE WHEN excluded.estado = 'rodando' THEN excluded.iniciado_em"
        "   ELSE COALESCE(etapas.iniciado_em, excluded.iniciado_em) END,"
        " concluido_em = excluded.concluido_em, erro = excluded.erro",
        (job_id, nome, estado, inc, agora, None if estado == "rodando" else agora,
         erro, inc),
    )
    con.commit()


def resetar_etapas(con: sqlite3.Connection, job_id: str, nomes: list[str]) -> None:
    con.executemany(
        "DELETE FROM etapas WHERE job_id = ? AND nome = ?", [(job_id, n) for n in nomes]
    )
    con.commit()
