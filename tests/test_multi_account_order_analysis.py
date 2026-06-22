import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "零担面板.py"

spec = importlib.util.spec_from_file_location("panel", MODULE_PATH)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def test_accounts_include_wangyou_and_qitao():
    assert panel.ACCOUNTS["wangyou"]["label"] == "王友小助手"
    assert panel.ACCOUNTS["wangyou"]["account"] == "V0013992"
    assert panel.ACCOUNTS["qitao"]["label"] == "齐涛小助手"
    assert panel.ACCOUNTS["qitao"]["account"] == "V0006384"


def test_login_uses_selected_account_credentials(monkeypatch):
    captured = {}

    class FakeResponse:
        def json(self):
            return {"status": "login", "token": "token-qitao"}

    class FakeSession:
        def post(self, url, json, headers, timeout):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(panel, "_sess", lambda: FakeSession())
    monkeypatch.setattr(panel, "rsa_encrypt", lambda password: f"encrypted:{password}")

    token = panel.login("qitao")

    assert token == "token-qitao"
    assert captured["payload"]["name"] == "V0006384"
    assert captured["payload"]["password"] == "encrypted:123456"


def test_current_account_api_defaults_to_wangyou():
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    response = panel.app.test_client().get('/api/current-account')
    data = response.get_json()

    assert response.status_code == 200
    assert data == {"success": True, "account_key": "wangyou", "label": "王友小助手"}


def test_account_switch_toggles_between_wangyou_and_qitao(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    panel._token_cache["token"] = "old-token"
    panel._token_cache["expire_time"] = 9999999999
    monkeypatch.setattr(panel, "refresh_all_data", lambda: True)

    client = panel.app.test_client()
    first = client.post('/api/account-switch').get_json()
    second = client.post('/api/account-switch').get_json()

    assert first["success"] is True
    assert first["account_key"] == "qitao"
    assert first["label"] == "齐涛小助手"
    assert second["success"] is True
    assert second["account_key"] == "wangyou"
    assert second["label"] == "王友小助手"
    assert panel._token_cache["token"] is None
    assert panel._token_cache["expire_time"] == 0
