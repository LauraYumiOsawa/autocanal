import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.errors import HttpError

from autocanal.youtube import upload


def _erro(status, reason=None):
    corpo = {"error": {"errors": [{"reason": reason}] if reason else []}}
    return HttpError(resp=httplib2.Response({"status": status}), content=json.dumps(corpo).encode())


def _yt(*respostas):
    yt = MagicMock()
    req = yt.videos.return_value.insert.return_value
    req.next_chunk.side_effect = list(respostas)
    return yt


def test_formatar_publish_at():
    dt = datetime(2026, 10, 25, 15, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert upload.formatar_publish_at(dt) == "2026-10-25T18:00:00Z"


def test_montar_corpo_campos():
    meta = {"titulo": "T", "descricao_final": "D", "tags": ["a"]}
    dt = datetime(2026, 10, 25, 15, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    c = upload.montar_corpo(meta, publish_at=dt, categoria_id="27", idioma="pt-BR", sintetico=True,
                            feito_para_criancas=False)
    assert c["snippet"] == {
        "title": "T", "description": "D", "tags": ["a"], "categoryId": "27",
        "defaultLanguage": "pt-BR", "defaultAudioLanguage": "pt-BR",
    }
    assert c["status"] == {
        "privacyStatus": "private", "publishAt": "2026-10-25T18:00:00Z",
        "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True,
    }
    assert c["status"]["selfDeclaredMadeForKids"] is False


def test_enviar_retenta_503(tmp_path):
    yt = _yt(_erro(503), (None, {"id": "abc"}))
    dormir = MagicMock()
    media = MagicMock()
    vid = upload.enviar(yt, tmp_path / "v.mp4", {"x": 1}, dormir=dormir, media_factory=media)
    assert vid == "abc"
    dormir.assert_called_once_with(1)
    media.assert_called_once_with(
        str(tmp_path / "v.mp4"), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True
    )
    yt.videos.return_value.insert.assert_called_once_with(
        part="snippet,status", body={"x": 1}, media_body=media.return_value
    )


def test_enviar_retenta_oserror(tmp_path):
    yt = _yt(OSError("rede"), (None, {"id": "z"}))
    assert upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock()) == "z"


def test_enviar_esgota_tentativas(tmp_path):
    yt = _yt(*[_erro(503)] * 3)
    dormir = MagicMock()
    with pytest.raises(upload.ErroUpload):
        upload.enviar(yt, tmp_path / "v", {}, max_tentativas=3, dormir=dormir, media_factory=MagicMock())
    assert [c.args[0] for c in dormir.call_args_list] == [1, 2]


def test_enviar_progresso_nao_conta_tentativa(tmp_path):
    yt = _yt((MagicMock(), None), (MagicMock(), None), (None, {"id": "k"}))
    assert upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock()) == "k"


@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"])
def test_enviar_cota_esgotada(tmp_path, reason):
    yt = _yt(_erro(403, reason))
    with pytest.raises(upload.CotaEsgotada):
        upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock())


def test_enviar_outro_erro(tmp_path):
    yt = _yt(_erro(400, "badRequest"))
    with pytest.raises(upload.ErroUpload):
        upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock())


def test_vetar_mantem_made_for_kids():
    yt = MagicMock()
    upload.vetar(yt, "abc")
    yt.videos.return_value.update.assert_called_once_with(
        part="status",
        body={"id": "abc", "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                      "containsSyntheticMedia": False}},
    )


def test_vetar_repassa_sintetico():
    yt = MagicMock()
    upload.vetar(yt, "abc", True)
    corpo = yt.videos.return_value.update.call_args.kwargs["body"]
    assert corpo["status"]["containsSyntheticMedia"] is True


def test_vetar_cota():
    yt = MagicMock()
    yt.videos.return_value.update.return_value.execute.side_effect = _erro(403, "quotaExceeded")
    with pytest.raises(upload.CotaEsgotada):
        upload.vetar(yt, "abc")


def test_definir_thumbnail(tmp_path):
    yt = MagicMock()
    img = tmp_path / "t.png"
    img.write_bytes(b"x")
    upload.definir_thumbnail(yt, "abc", img, media_factory=MagicMock())
    assert yt.thumbnails.return_value.set.call_args.kwargs["videoId"] == "abc"


def test_status():
    yt = MagicMock()
    yt.videos.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "a", "status": {"privacyStatus": "public"}}]
    }
    assert upload.status(yt, ["a", "b"]) == {"a": {
        "privacyStatus": "public", "uploadStatus": None, "rejectionReason": None, "failureReason": None}}
    assert yt.videos.return_value.list.call_args.kwargs["part"] == "status"
    assert upload.status(yt, []) == {}


def test_enviar_refresh_error_vira_auth_expirada(tmp_path):
    from google.auth.exceptions import RefreshError
    from autocanal.youtube.oauth import AuthExpirada

    yt = _yt(RefreshError("invalid_grant"))
    with pytest.raises(AuthExpirada):
        upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock())


def test_enviar_transport_error_retenta(tmp_path):
    from google.auth.exceptions import TransportError

    yt = _yt(TransportError("rede"), (None, {"id": "t"}))
    assert upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock()) == "t"


def test_refresh_error_em_chamadas_simples():
    from google.auth.exceptions import RefreshError
    from autocanal.youtube.oauth import AuthExpirada

    yt = MagicMock()
    yt.videos.return_value.list.return_value.execute.side_effect = RefreshError("x")
    yt.videos.return_value.update.return_value.execute.side_effect = RefreshError("x")
    yt.thumbnails.return_value.set.return_value.execute.side_effect = RefreshError("x")
    with pytest.raises(AuthExpirada):
        upload.status(yt, ["a"])
    with pytest.raises(AuthExpirada):
        upload.vetar(yt, "a")
    with pytest.raises(AuthExpirada):
        upload.definir_thumbnail(yt, "a", Path("x.png"), media_factory=MagicMock())


def test_cota_carrega_motivo(tmp_path):
    yt = _yt(_erro(403, "uploadLimitExceeded"))
    with pytest.raises(upload.CotaEsgotada) as ei:
        upload.enviar(yt, tmp_path / "v", {}, dormir=MagicMock(), media_factory=MagicMock())
    assert ei.value.motivo == "uploadLimitExceeded"


def test_montar_corpo_feito_para_criancas():
    meta = {"titulo": "T", "descricao_final": "D", "tags": []}
    dt = datetime(2026, 10, 25, 15, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    c = upload.montar_corpo(meta, publish_at=dt, categoria_id="27", idioma="pt-BR",
                            sintetico=False, feito_para_criancas=True)
    assert c["status"]["selfDeclaredMadeForKids"] is True


def test_vetar_repassa_feito_para_criancas():
    # videos.update substitui o status inteiro: o veto não pode desmarcar o conteúdo infantil
    yt = MagicMock()
    upload.vetar(yt, "abc", False, True)
    corpo = yt.videos.return_value.update.call_args.kwargs["body"]
    assert corpo["status"]["selfDeclaredMadeForKids"] is True
