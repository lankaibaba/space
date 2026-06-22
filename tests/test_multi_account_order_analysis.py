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
