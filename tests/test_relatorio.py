import json
import subprocess
from datetime import UTC, date, datetime, timedelta

import pytest

from autocanal import fila, ia, planejador, produtor, relatorio
from autocanal.db import conectar
from autocanal.fila import Estado

DIA = date(2026, 10, 26)  # atividade de 25/10 (00:00–24:00 em São Paulo = 03:00–03:00 UTC)
CAMINHOS = {
    Estado.AGENDADO: (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO, Estado.AGENDADO),
    Estado.PUBLICADO: (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO, Estado.AGENDADO,
                       Estado.PUBLICADO),
    Estado.APROVADO: (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO),
    Estado.QUARENTENA: (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.QUARENTENA),
    Estado.FALHOU: (Estado.PRODUZINDO, Estado.FALHOU),
}


@pytest.fixture
def con(cfg):
    c = conectar(cfg.dados / "autocanal.db")
    yield c
    c.close()


def _job(cfg, con, estado, publish_at, tema="tema", video=None, titulo=None, erro=None):
    j = fila.criar_job(con, "teste", "short", tema, publish_at)
    for e in CAMINHOS[estado]:
        if e == Estado.AGENDADO:
            fila.atualizar(con, j.id, youtube_video_id=video)
        fila.mudar_estado(con, j.id, e, erro=erro if e == Estado.FALHOU else None)
    if titulo:
        pasta = cfg.dados / "jobs" / j.id
        pasta.mkdir(parents=True)
        (pasta / "metadados.json").write_text(json.dumps({"titulo": titulo}), encoding="utf-8")
    return fila.obter(con, j.id)


def _revisao(con, job_id, rodada, aprovado, motivos, criado_em="2026-10-25T18:00:00+00:00"):
    con.execute(
        "INSERT INTO revisoes (job_id, rodada, aprovado, motivos_json, etapa_para_refazer,"
        " criado_em) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, rodada, int(aprovado), json.dumps(motivos), None, criado_em),
    )
    con.commit()


def _chamada(con, criado_em, custo):
    con.execute(
        "INSERT INTO chamadas_ia (job_id, etapa, provedor, custo_usd, turnos, duracao_s, criado_em)"
        " VALUES (NULL, 'roteiro', 'claude', ?, 3, 10, ?)", (custo, criado_em),
    )
    con.commit()


def _secao(texto: str, titulo: str) -> str:
    return texto.split(f"## {titulo}\n", 1)[1].split("\n## ", 1)[0]


def test_resumo_tem_secoes_e_links(cfg, con):
    agendado = _job(cfg, con, Estado.AGENDADO, datetime(2026, 10, 26, 15, tzinfo=UTC),
                    video="abc", titulo="Céu azul explicado")
    _revisao(con, agendado.id, 1, True, [])
    _job(cfg, con, Estado.PUBLICADO, datetime(2026, 10, 25, 15, tzinfo=UTC), video="xyz")
    _job(cfg, con, Estado.PUBLICADO, datetime(2026, 10, 23, 15, tzinfo=UTC), video="velho")
    quarentena = _job(cfg, con, Estado.QUARENTENA, datetime(2026, 10, 28, 15, tzinfo=UTC))
    _revisao(con, quarentena.id, 1, False, ["motivo antigo"], "2026-10-24T10:00:00+00:00")
    _revisao(con, quarentena.id, 2, False, ["sem fonte para X", "título enganoso"])
    _job(cfg, con, Estado.FALHOU, datetime(2026, 10, 30, 15, tzinfo=UTC), erro="narracao: tts caiu")
    con.execute("INSERT INTO canais_estado (canal, auth_ok, ultimo_erro) VALUES ('teste', 0, 'invalid_grant')")
    con.commit()
    agora = datetime.now(UTC)
    (cfg.dados / "cota_esgotada_ate").write_text((agora + timedelta(hours=2)).isoformat())
    ia.pausar(cfg, "claude", "limite de uso", agora)
    planejador.adiar_planejamento(cfg, agora)
    _chamada(con, "2026-10-25T03:00:00+00:00", 0.5)
    _chamada(con, "2026-10-26T02:59:59.900000+00:00", 0.25)
    _chamada(con, "2026-10-26T03:00:00+00:00", 9.0)  # já é 26/10 em São Paulo

    caminho = relatorio.gerar_resumo(cfg, con, DIA)

    assert caminho == cfg.dados / "relatorios" / "2026-10-26.md"
    texto = caminho.read_text(encoding="utf-8")
    titulos = ["Produzidos", "Agendados", "Publicados", "Quarentena", "Falhas", "Alertas",
               "Supervisão", "Consumo de IA"]
    posicoes = [texto.index(f"\n## {t}\n") for t in titulos]
    assert posicoes == sorted(posicoes)
    assert agendado.id in _secao(texto, "Produzidos")
    ag = _secao(texto, "Agendados")
    assert "https://youtu.be/abc" in ag and "26/10 12:00" in ag and "Céu azul explicado" in ag
    pub = _secao(texto, "Publicados")
    assert "https://youtu.be/xyz" in pub and "velho" not in texto
    q = _secao(texto, "Quarentena")
    assert quarentena.id in q and "sem fonte para X" in q and "título enganoso" in q
    assert "motivo antigo" not in q
    assert "tts caiu" in _secao(texto, "Falhas")
    al = _secao(texto, "Alertas")
    assert "invalid_grant" in al and "autocanal auth teste" in al
    assert "Cota do YouTube esgotada" in al and "limite de uso" in al
    assert "Planejamento automático adiado" in al
    assert f"autocanal vetar {agendado.id}" in _secao(texto, "Supervisão")
    consumo = _secao(texto, "Consumo de IA")
    assert consumo.strip() == "- claude: 2 chamadas, — tokens, custo equivalente de US$ 0.75"


def test_resumo_vazio(cfg, con):
    texto = relatorio.gerar_resumo(cfg, con, DIA).read_text(encoding="utf-8")
    for t in ("Produzidos", "Agendados", "Publicados", "Quarentena", "Falhas", "Alertas"):
        assert _secao(texto, t).strip() == "- nenhum"
    assert _secao(texto, "Consumo de IA").strip() == "- nenhuma chamada de IA"


def test_consumo_por_provedor(cfg, con):
    for provedor, custo, entrada, saida in (("codex", None, 200, 20), ("claude", 1.5, 100, 50)):
        con.execute(
            "INSERT INTO chamadas_ia (etapa, provedor, tokens_entrada, tokens_saida, custo_usd,"
            " duracao_s, criado_em) VALUES ('roteiro', ?, ?, ?, ?, 1, '2026-10-25T12:00:00+00:00')",
            (provedor, entrada, saida, custo),
        )
    con.commit()
    inicio, fim = datetime(2026, 10, 25, tzinfo=UTC), datetime(2026, 10, 26, tzinfo=UTC)
    assert relatorio._consumo(con, inicio, fim) == [
        "- claude: 1 chamada, 150 tokens, custo equivalente de US$ 1.50",
        "- codex: 1 chamada, 220 tokens, custo equivalente de —",
    ]


def test_supervisao_termina_apos_os_dias_configurados(cfg, con):
    primeiro = datetime(2026, 10, 1, 15, tzinfo=UTC)
    _job(cfg, con, Estado.PUBLICADO, primeiro, video="v0")
    aprovado = _job(cfg, con, Estado.APROVADO, datetime(2026, 10, 27, 15, tzinfo=UTC))
    no_prazo = relatorio.gerar_resumo(cfg, con, date(2026, 10, 14)).read_text(encoding="utf-8")
    assert f"autocanal descartar {aprovado.id}" in _secao(no_prazo, "Supervisão")
    fim = relatorio.gerar_resumo(cfg, con, date(2026, 10, 15)).read_text(encoding="utf-8")
    sup = _secao(fim, "Supervisão")
    assert "Encerrada em 15/10/2026" in sup and aprovado.id not in sup


def test_alerta_canal_sem_autorizacao_com_aprovados(cfg, con):
    _job(cfg, con, Estado.APROVADO, datetime(2026, 10, 27, 15, tzinfo=UTC))
    alertas = relatorio.alertas(cfg, con, datetime.now(UTC))
    assert len(alertas) == 1 and "autocanal auth teste" in alertas[0]


def test_alerta_agendado_preso_em_privado(cfg, con):
    con.execute("INSERT INTO canais_estado (canal, auth_ok) VALUES ('teste', 1)")
    con.commit()
    publish_at = datetime(2026, 10, 26, 15, tzinfo=UTC)
    j = _job(cfg, con, Estado.AGENDADO, publish_at, video="abc")
    assert relatorio.alertas(cfg, con, publish_at + timedelta(hours=1, minutes=59)) == []
    [aviso] = relatorio.alertas(cfg, con, publish_at + timedelta(hours=2, minutes=1))
    assert j.id in aviso and "projeto da API possivelmente não auditado" in aviso


def test_alerta_visuais_so_procedurais(cfg, con):
    so_proc = _job(cfg, con, Estado.QUARENTENA, datetime(2026, 10, 27, 15, tzinfo=UTC))
    misto = _job(cfg, con, Estado.QUARENTENA, datetime(2026, 10, 28, 15, tzinfo=UTC))
    casos = ((so_proc, ["procedural", "procedural"]), (misto, ["pixabay", "procedural"]))
    for job, origens in casos:
        pasta = cfg.dados / "jobs" / job.id
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "fontes_midia.json").write_text(json.dumps([{"origem": o} for o in origens]))
    alertas = relatorio.alertas(cfg, con, datetime.now(UTC))
    assert len(alertas) == 1
    assert so_proc.id in alertas[0] and "PIXABAY_API_KEY" in alertas[0]
    assert "PEXELS" not in alertas[0]


def test_alerta_pausa_por_queda_geral(cfg, con):
    agora = datetime.now(UTC)
    produtor.pausar_sistema(cfg, "não consegui subir o serviço kokoro: OSError", agora)
    alertas = relatorio.alertas(cfg, con, agora)
    assert len(alertas) == 1
    assert "queda geral" in alertas[0] and "kokoro" in alertas[0]
    assert relatorio.alertas(cfg, con, agora + timedelta(hours=2)) == []


def test_aviso_por_provedor(cfg, con):
    agora = datetime(2026, 10, 26, 15, tzinfo=UTC)  # 12:00 em São Paulo
    ia.pausar(cfg, "claude", "limite de uso", agora)
    assert relatorio.alertas(cfg, con, agora) == [
        "IA `claude` pausada até 26/10 13:00 (limite de uso): as etapas que usam esse provedor"
        " retomam depois."
    ]
    assert relatorio.alertas(cfg, con, agora + timedelta(hours=2)) == []


def test_notificar_sem_notify_send(monkeypatch):
    monkeypatch.setattr(relatorio.shutil, "which", lambda nome: None)
    monkeypatch.setattr(relatorio.subprocess, "run", pytest.fail)
    relatorio.notificar("título", "texto")


def test_notificar_chama_notify_send_e_engole_erros(monkeypatch):
    chamadas = []

    def run(argv, **kw):
        chamadas.append(argv)
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(relatorio.shutil, "which", lambda nome: "/usr/bin/notify-send")
    monkeypatch.setattr(relatorio.subprocess, "run", run)
    relatorio.notificar("título", "texto")
    assert chamadas == [["/usr/bin/notify-send", "título", "texto"]]


def test_alerta_thumbnail_nao_enviada(cfg, con):
    j = _job(cfg, con, Estado.AGENDADO, datetime(2026, 10, 29, 15, tzinfo=UTC), video="abc")
    pasta = cfg.dados / "jobs" / j.id
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "thumbnail_envio.json").write_text(json.dumps({"ok": False, "erro": "forbidden"}))
    avisos = relatorio.alertas(cfg, con, datetime(2026, 10, 26, 12, tzinfo=UTC))
    assert any(j.id in a and "youtube.com/verify" in a for a in avisos)
