import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from autocanal import fila, publicador
from autocanal.db import conectar
from autocanal.fila import Estado
from autocanal.youtube import oauth, upload

AGORA = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
META = {"titulo": "T", "texto_thumb": "x", "descricao_final": "D", "tags": ["a"],
        "capitulos": [], "consultas_alvo": [], "nota_titulo": 80,
        "problemas_titulo": [], "sintetico": True}


@pytest.fixture
def con(tmp_path):
    return conectar(tmp_path / "dados" / "autocanal.db")


@pytest.fixture
def cfg2(cfg):
    cfg.canais["outro"] = cfg.canais["teste"].model_copy(update={"slug": "outro"})
    return cfg


def _aprovado(cfg, con, publish_at, canal="teste", formato="short", thumb=False):
    j = fila.criar_job(con, canal, formato, "tema", publish_at)
    for e in (Estado.PRODUZINDO, Estado.EM_REVISAO, Estado.APROVADO):
        fila.mudar_estado(con, j.id, e)
    con.execute("INSERT OR IGNORE INTO canais_estado (canal, auth_ok) VALUES (?, 1)", (canal,))
    con.commit()
    pasta = cfg.dados / "jobs" / j.id
    pasta.mkdir(parents=True)
    (pasta / "video.mp4").write_bytes(b"v")
    (pasta / "metadados.json").write_text(json.dumps(META), encoding="utf-8")
    if thumb:
        (pasta / "thumbnail.png").write_bytes(b"p")
    return j


def _pub(cfg, con, yt, **kw):
    kw.setdefault("relogio", lambda: AGORA)
    return publicador.publicar_aprovados(cfg, con, AGORA, youtube_por_canal=yt, **kw)


@pytest.fixture
def envios(monkeypatch):
    chamadas = []

    def enviar(yt, video, corpo, **kw):
        chamadas.append((yt, video, corpo))
        return f"vid{len(chamadas)}"

    monkeypatch.setattr(publicador.upload, "enviar", enviar)
    monkeypatch.setattr(publicador.upload, "definir_thumbnail", MagicMock())
    return chamadas


def _auth(con, canal, ok=1):
    con.execute("INSERT OR REPLACE INTO canais_estado (canal, auth_ok) VALUES (?,?)", (canal, ok))
    con.commit()


def test_publicar_envia_e_agenda(cfg, con, envios):
    j = _aprovado(cfg, con, AGORA + timedelta(days=2))
    yt = MagicMock()
    ids = _pub(cfg, con, lambda c: yt)
    assert ids == ["vid1"]
    novo = fila.obter(con, j.id)
    assert novo.estado == Estado.AGENDADO and novo.youtube_video_id == "vid1"
    corpo = envios[0][2]
    assert corpo["status"]["publishAt"] == "2026-10-26T12:00:00Z"
    assert corpo["snippet"]["categoryId"] == "27"
    assert corpo["status"]["containsSyntheticMedia"] is True
    r = con.execute("SELECT dia, unidades FROM cota WHERE bucket='upload'").fetchall()
    assert len(r) == 1 and r[0]["unidades"] == 100
    assert r[0]["dia"] == "2026-10-24"  # 05:00 em Los Angeles
    publicador.upload.definir_thumbnail.assert_not_called()


def test_publicar_canal_removido_da_config_falha_o_job(cfg, con, envios):
    sumiu = _aprovado(cfg, con, AGORA + timedelta(days=2), canal="antigo")
    ok = _aprovado(cfg, con, AGORA + timedelta(days=3))
    yt = MagicMock()
    assert _pub(cfg, con, lambda c: yt) == ["vid1"]
    j = fila.obter(con, sumiu.id)
    assert j.estado == Estado.FALHOU and "antigo" in j.erro
    assert fila.obter(con, ok.id).estado == Estado.AGENDADO


def test_publicar_thumbnail_so_nao_short(cfg, con, envios):
    cfg.canais["teste"].grade.append(type(cfg.canais["teste"].grade[0])(formato="longo", dias=["seg"], hora="12:00"))
    _aprovado(cfg, con, AGORA + timedelta(days=2), formato="longo", thumb=True)
    _aprovado(cfg, con, AGORA + timedelta(days=3), formato="short", thumb=True)
    _pub(cfg, con, lambda c: MagicMock())
    assert publicador.upload.definir_thumbnail.call_count == 1


def test_cota_soma_no_mesmo_dia(cfg, con, envios):
    _aprovado(cfg, con, AGORA + timedelta(days=2))
    _aprovado(cfg, con, AGORA + timedelta(days=3))
    _pub(cfg, con, lambda c: MagicMock())
    assert con.execute("SELECT unidades FROM cota").fetchone()[0] == 200


def test_publicar_reagenda_publish_at_passado(cfg, con, envios):
    j = _aprovado(cfg, con, AGORA - timedelta(days=1))
    _pub(cfg, con, lambda c: MagicMock())
    novo = fila.obter(con, j.id)
    assert novo.publish_at >= AGORA + timedelta(hours=1)
    assert novo.estado == Estado.AGENDADO
    assert envios[0][2]["status"]["publishAt"] == upload.formatar_publish_at(novo.publish_at)
    # 2026-10-26 é segunda: slot 12:00 local = 15:00Z
    assert novo.publish_at == datetime(2026, 10, 26, 15, tzinfo=UTC)


def test_reagendar_pula_slot_ocupado(cfg, con, envios):
    ocupado = fila.criar_job(con, "teste", "short", "x", datetime(2026, 10, 26, 15, tzinfo=UTC))
    j = _aprovado(cfg, con, AGORA - timedelta(days=1))
    _pub(cfg, con, lambda c: MagicMock())
    assert ocupado.publish_at != fila.obter(con, j.id).publish_at
    assert fila.obter(con, j.id).publish_at == datetime(2026, 10, 28, 15, tzinfo=UTC)


def test_publicar_auth_expirada_isola_canal(cfg2, con, envios):
    a = _aprovado(cfg2, con, AGORA + timedelta(days=2), canal="teste")
    b = _aprovado(cfg2, con, AGORA + timedelta(days=3), canal="outro")
    _auth(con, "teste")

    def por_canal(c):
        if c == "teste":
            raise oauth.AuthExpirada("invalid_grant")
        return MagicMock()

    ids = _pub(cfg2, con, por_canal)
    assert ids == ["vid1"]
    assert fila.obter(con, a.id).estado == Estado.APROVADO
    assert fila.obter(con, b.id).estado == Estado.AGENDADO
    r = con.execute("SELECT auth_ok, ultimo_erro FROM canais_estado WHERE canal='teste'").fetchone()
    assert r["auth_ok"] == 0 and "invalid_grant" in r["ultimo_erro"]


def test_publicar_pula_canal_com_auth_off(cfg, con, envios):
    j = _aprovado(cfg, con, AGORA + timedelta(days=2))
    _auth(con, "teste", 0)
    assert _pub(cfg, con, lambda c: MagicMock()) == []
    assert fila.obter(con, j.id).estado == Estado.APROVADO


def test_publicar_para_na_cota(cfg, con, monkeypatch):
    j1 = _aprovado(cfg, con, AGORA + timedelta(days=2))
    j2 = _aprovado(cfg, con, AGORA + timedelta(days=3))
    monkeypatch.setattr(publicador.upload, "enviar", MagicMock(side_effect=upload.CotaEsgotada("x")))
    assert _pub(cfg, con, lambda c: MagicMock()) == []
    assert publicador.upload.enviar.call_count == 1
    assert fila.obter(con, j1.id).estado == Estado.APROVADO
    assert fila.obter(con, j2.id).estado == Estado.APROVADO


def test_publicar_erro_upload_falha_job(cfg, con, monkeypatch):
    j1 = _aprovado(cfg, con, AGORA + timedelta(days=2))
    j2 = _aprovado(cfg, con, AGORA + timedelta(days=3))
    monkeypatch.setattr(publicador.upload, "enviar",
                        MagicMock(side_effect=[upload.ErroUpload("ruim"), "ok2"]))
    assert _pub(cfg, con, lambda c: MagicMock()) == ["ok2"]
    f = fila.obter(con, j1.id)
    assert f.estado == Estado.FALHOU and "ruim" in f.erro
    assert fila.obter(con, j2.id).estado == Estado.AGENDADO


def test_falha_no_banco_apos_upload_loga_id(cfg, con, envios, monkeypatch):
    _aprovado(cfg, con, AGORA + timedelta(days=2))
    monkeypatch.setattr(publicador.fila, "mudar_estado", MagicMock(side_effect=RuntimeError("db")))
    with pytest.raises(RuntimeError, match="vid1"):
        _pub(cfg, con, lambda c: MagicMock())


def test_sincronizar_marca_publicado(cfg, con, monkeypatch):
    j = _aprovado(cfg, con, AGORA - timedelta(hours=1))
    fila.atualizar(con, j.id, youtube_video_id="abc")
    fila.mudar_estado(con, j.id, Estado.AGENDADO)
    futuro = _aprovado(cfg, con, AGORA + timedelta(days=1))
    fila.atualizar(con, futuro.id, youtube_video_id="fut")
    fila.mudar_estado(con, futuro.id, Estado.AGENDADO)
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {i: {"privacyStatus": "public"} for i in ids})
    ids = publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock())
    assert ids == [j.id]
    assert fila.obter(con, j.id).estado == Estado.PUBLICADO
    assert fila.obter(con, futuro.id).estado == Estado.AGENDADO


def test_sincronizar_ainda_privado(cfg, con, monkeypatch):
    j = _aprovado(cfg, con, AGORA - timedelta(hours=1))
    fila.atualizar(con, j.id, youtube_video_id="abc")
    fila.mudar_estado(con, j.id, Estado.AGENDADO)
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {"abc": {"privacyStatus": "private"}})
    assert publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock()) == []


def test_youtube_real_sem_token(cfg):
    with pytest.raises(oauth.AuthExpirada):
        publicador.youtube_real(cfg)("teste")


def test_youtube_real_cacheia_por_canal(cfg, monkeypatch):
    (cfg.dados / "tokens").mkdir(parents=True)
    (cfg.dados / "tokens" / "teste.json").write_text("{}")
    carregar = MagicMock(return_value="creds")
    monkeypatch.setattr(publicador.oauth, "carregar", carregar)
    monkeypatch.setattr(publicador.oauth, "cliente", MagicMock(return_value="yt"))
    f = publicador.youtube_real(cfg)
    assert f("teste") == "yt" and f("teste") == "yt"
    assert carregar.call_count == 1


def _agendado(cfg, con, publish_at, vid, canal="teste"):
    j = _aprovado(cfg, con, publish_at, canal=canal)
    fila.atualizar(con, j.id, youtube_video_id=vid)
    fila.mudar_estado(con, j.id, Estado.AGENDADO)
    return j


def test_sem_linha_canais_estado_nao_publica(cfg, con, envios):
    j = _aprovado(cfg, con, AGORA + timedelta(days=2))
    con.execute("DELETE FROM canais_estado")
    con.commit()
    assert _pub(cfg, con, lambda c: MagicMock()) == []
    assert fila.obter(con, j.id).estado == Estado.APROVADO


def test_nao_reenvia_apos_falha_parcial(cfg, con, monkeypatch):
    j = _aprovado(cfg, con, AGORA + timedelta(days=2))
    n = []
    monkeypatch.setattr(publicador.upload, "enviar", lambda *a, **k: n.append(1) or f"vid{len(n)}")
    real = fila.mudar_estado
    monkeypatch.setattr(publicador.fila, "mudar_estado", MagicMock(side_effect=RuntimeError("db")))
    with pytest.raises(RuntimeError, match="vid1"):
        _pub(cfg, con, lambda c: MagicMock())
    monkeypatch.setattr(publicador.fila, "mudar_estado", real)
    _pub(cfg, con, lambda c: MagicMock())
    assert len(n) == 1
    novo = fila.obter(con, j.id)
    assert novo.youtube_video_id == "vid1" and novo.estado == Estado.AGENDADO


def test_margem_avalia_relogio_por_job(cfg, con, monkeypatch):
    agora = [AGORA]
    corpos = []

    def enviar(yt, video, corpo, **kw):
        corpos.append((corpo, agora[0]))
        agora[0] += timedelta(minutes=50)
        return f"v{len(corpos)}"

    monkeypatch.setattr(publicador.upload, "enviar", enviar)
    _aprovado(cfg, con, AGORA + timedelta(hours=1, minutes=10))
    j2 = _aprovado(cfg, con, AGORA + timedelta(hours=1, minutes=20))
    _pub(cfg, con, lambda c: MagicMock(), relogio=lambda: agora[0])
    assert len(corpos) == 2
    j2n = fila.obter(con, j2.id)
    assert j2n.publish_at >= corpos[1][1] + timedelta(hours=1)
    assert corpos[1][0]["status"]["publishAt"] == upload.formatar_publish_at(j2n.publish_at)


def test_refresh_error_no_upload_isola_canal(cfg2, con, monkeypatch):
    from google.auth.exceptions import RefreshError

    _aprovado(cfg2, con, AGORA + timedelta(days=2), canal="teste")
    b = _aprovado(cfg2, con, AGORA + timedelta(days=3), canal="outro")

    def enviar(yt, video, corpo, **kw):
        if yt == "A":
            # o mapeamento real RefreshError -> AuthExpirada está em upload.enviar (test_upload)
            raise oauth.AuthExpirada(str(RefreshError("invalid_grant")))
        return "vidB"

    monkeypatch.setattr(publicador.upload, "enviar", enviar)
    ids = _pub(cfg2, con, lambda c: "A" if c == "teste" else "B")
    assert ids == ["vidB"] and fila.obter(con, b.id).estado == Estado.AGENDADO
    assert con.execute("SELECT auth_ok FROM canais_estado WHERE canal='teste'").fetchone()[0] == 0


def test_auth_expirada_na_thumbnail_isola(cfg, con, envios):
    cfg.canais["teste"].grade.append(type(cfg.canais["teste"].grade[0])(formato="longo", dias=["seg"], hora="12:00"))
    _aprovado(cfg, con, AGORA + timedelta(days=2), formato="longo", thumb=True)
    publicador.upload.definir_thumbnail.side_effect = oauth.AuthExpirada("x")
    assert _pub(cfg, con, lambda c: MagicMock()) == ["vid1"]
    assert con.execute("SELECT auth_ok FROM canais_estado").fetchone()[0] == 0


def test_cota_diaria_persiste_marcador_e_bloqueia(cfg, con, monkeypatch):
    j = _aprovado(cfg, con, AGORA + timedelta(days=2))
    env = MagicMock(side_effect=upload.CotaEsgotada("x", motivo="quotaExceeded"))
    monkeypatch.setattr(publicador.upload, "enviar", env)
    assert _pub(cfg, con, lambda c: MagicMock()) == []
    marcador = (cfg.dados / "cota_esgotada_ate").read_text()
    # 2026-10-24 05:00 em Los Angeles -> próxima meia-noite 2026-10-25 00:00 PDT = 07:00Z
    assert datetime.fromisoformat(marcador) == datetime(2026, 10, 25, 7, tzinfo=UTC)
    assert _pub(cfg, con, lambda c: MagicMock()) == []
    assert env.call_count == 1  # segunda rodada nem tentou
    # depois do marcador volta a tentar
    env.side_effect = None
    env.return_value = "vX"
    assert _pub(cfg, con, lambda c: MagicMock(), relogio=lambda: datetime(2026, 10, 25, 8, tzinfo=UTC)) == ["vX"]


def test_upload_limit_pula_so_o_canal(cfg2, con, monkeypatch):
    a = _aprovado(cfg2, con, AGORA + timedelta(days=2), canal="teste")
    _aprovado(cfg2, con, AGORA + timedelta(days=3), canal="teste")
    b = _aprovado(cfg2, con, AGORA + timedelta(days=4), canal="outro")
    env = MagicMock(side_effect=[upload.CotaEsgotada("x", motivo="uploadLimitExceeded"), "vB"])
    monkeypatch.setattr(publicador.upload, "enviar", env)
    assert _pub(cfg2, con, lambda c: MagicMock()) == ["vB"]
    assert env.call_count == 2
    assert fila.obter(con, a.id).estado == Estado.APROVADO
    assert fila.obter(con, b.id).estado == Estado.AGENDADO
    assert not (cfg2.dados / "cota_esgotada_ate").exists()


def test_sincronizar_rejeitado_antes_do_horario(cfg, con, monkeypatch):
    j = _agendado(cfg, con, AGORA + timedelta(days=2), "r")
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {
        "r": {"privacyStatus": "private", "uploadStatus": "rejected", "rejectionReason": "copyright"}})
    assert publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock()) == []
    f = fila.obter(con, j.id)
    assert f.estado == Estado.FALHOU and f.erro == "youtube: rejected: copyright (vídeo r)" and f.youtube_video_id is None


def test_sincronizar_failed_usa_failure_reason(cfg, con, monkeypatch):
    j = _agendado(cfg, con, AGORA + timedelta(days=2), "r")
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {
        "r": {"uploadStatus": "failed", "failureReason": "codec"}})
    publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock())
    assert fila.obter(con, j.id).erro == "youtube: failed: codec (vídeo r)"


def test_sincronizar_video_ausente(cfg, con, monkeypatch):
    j = _agendado(cfg, con, AGORA + timedelta(days=2), "sumiu")
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {})
    publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock())
    f = fila.obter(con, j.id)
    assert f.estado == Estado.FALHOU and f.erro == "youtube: vídeo não encontrado (vídeo sumiu)"


def test_refresh_error_real_no_next_chunk_isola_canal(cfg2, con, monkeypatch):
    from google.auth.exceptions import RefreshError

    a = _aprovado(cfg2, con, AGORA + timedelta(days=2), canal="teste")
    b = _aprovado(cfg2, con, AGORA + timedelta(days=3), canal="outro")
    yt_a = MagicMock()
    yt_a.videos.return_value.insert.return_value.next_chunk.side_effect = RefreshError("invalid_grant")
    monkeypatch.setattr(publicador.upload, "MediaFileUpload", MagicMock(), raising=False)
    monkeypatch.setattr(upload, "MediaFileUpload", MagicMock())
    yt_b = MagicMock()
    yt_b.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "vidB"})
    ids = _pub(cfg2, con, lambda c: yt_a if c == "teste" else yt_b)
    assert ids == ["vidB"]
    assert fila.obter(con, a.id).estado == Estado.APROVADO
    assert fila.obter(con, b.id).estado == Estado.AGENDADO
    assert con.execute("SELECT auth_ok FROM canais_estado WHERE canal='teste'").fetchone()[0] == 0


def test_reaprovar_apos_rejeicao_reenvia(cfg, con, monkeypatch):
    j = _agendado(cfg, con, AGORA + timedelta(days=2), "velho")
    monkeypatch.setattr(publicador.upload, "status", lambda yt, ids: {
        "velho": {"uploadStatus": "rejected", "rejectionReason": "copyright"}})
    publicador.sincronizar_publicados(cfg, con, AGORA, youtube_por_canal=lambda c: MagicMock())
    f = fila.obter(con, j.id)
    assert f.estado == Estado.FALHOU and f.youtube_video_id is None
    assert f.erro == "youtube: rejected: copyright (vídeo velho)"
    fila.mudar_estado(con, j.id, Estado.APROVADO)
    env = MagicMock(return_value="novo")
    monkeypatch.setattr(publicador.upload, "enviar", env)
    assert _pub(cfg, con, lambda c: MagicMock()) == ["novo"]
    assert env.call_count == 1
    n = fila.obter(con, j.id)
    assert n.estado == Estado.AGENDADO and n.youtube_video_id == "novo"


def test_publicar_repassa_feito_para_criancas(cfg, con, envios):
    cfg.canais["teste"].feito_para_criancas = True
    _aprovado(cfg, con, AGORA + timedelta(days=2))
    _pub(cfg, con, lambda c: MagicMock())
    assert envios[0][2]["status"]["selfDeclaredMadeForKids"] is True
