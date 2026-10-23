import stat
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from autocanal.youtube import oauth


def test_salvar_chmod_600(tmp_path):
    token = tmp_path / "tokens" / "teste.json"
    creds = MagicMock()
    creds.to_json.return_value = '{"a": 1}'
    oauth.salvar(creds, token)
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert stat.S_IMODE(token.parent.stat().st_mode) == 0o700
    token.chmod(0o644)
    oauth.salvar(creds, token)  # sobrescrever mantém 600
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert token.read_text() == '{"a": 1}'


def test_carregar_refresh_error_vira_auth_expirada(tmp_path):
    token = tmp_path / "t.json"
    token.write_text("{}")
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.refresh.side_effect = RefreshError("invalid_grant")
    with patch.object(oauth.Credentials, "from_authorized_user_file", return_value=creds):
        with pytest.raises(oauth.AuthExpirada):
            oauth.carregar(token)


def test_carregar_renova_e_salva(tmp_path):
    token = tmp_path / "t.json"
    token.write_text("{}")
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.to_json.return_value = '{"novo": 1}'
    with patch.object(oauth.Credentials, "from_authorized_user_file", return_value=creds):
        oauth.carregar(token, requisicao="req")
    creds.refresh.assert_called_once_with("req")
    assert token.read_text() == '{"novo": 1}'


def test_carregar_sem_arquivo(tmp_path):
    with pytest.raises(oauth.AuthExpirada):
        oauth.carregar(tmp_path / "nao_existe.json")


def test_autorizar_pede_offline_e_consent(tmp_path):
    creds = MagicMock()
    creds.to_json.return_value = "{}"
    fluxo = MagicMock()
    fluxo.run_local_server.return_value = creds
    fabrica = MagicMock(return_value=fluxo)
    destino = tmp_path / "tokens" / "c.json"
    out = oauth.autorizar(tmp_path / "cs.json", destino, fluxo_factory=fabrica)
    fabrica.assert_called_once_with(str(tmp_path / "cs.json"), oauth.ESCOPOS)
    fluxo.run_local_server.assert_called_once_with(
        port=8080, access_type="offline", prompt="consent"
    )
    assert out is creds and destino.exists()


def test_canal_autenticado_sem_canal():
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.return_value = {"items": []}
    with pytest.raises(oauth.AuthExpirada, match="conta sem canal"):
        oauth.canal_autenticado(yt)


def test_canal_autenticado():
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "UC1", "snippet": {"title": "Meu Canal"}}]
    }
    assert oauth.canal_autenticado(yt) == ("UC1", "Meu Canal")
    yt.channels.return_value.list.assert_called_once_with(part="snippet", mine=True)
