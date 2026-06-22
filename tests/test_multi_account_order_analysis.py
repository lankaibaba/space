import importlib.util
import threading
import time
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


def test_get_token_does_not_reuse_qitao_cache_for_wangyou(monkeypatch):
    login_calls = []

    def fake_login(account_key=None):
        login_calls.append(account_key)
        return f"token-{account_key}"

    panel._token_cache.clear()
    monkeypatch.setattr(panel, "login", fake_login)

    assert panel.get_token("qitao") == "token-qitao"
    assert panel.get_token("wangyou") == "token-wangyou"
    assert login_calls == ["qitao", "wangyou"]


def test_get_token_caches_wangyou_and_qitao_separately(monkeypatch):
    login_calls = []

    def fake_login(account_key=None):
        login_calls.append(account_key)
        return f"token-{account_key}"

    panel._token_cache.clear()
    monkeypatch.setattr(panel, "login", fake_login)

    assert panel.get_token("wangyou") == "token-wangyou"
    assert panel.get_token("qitao") == "token-qitao"
    assert panel.get_token("wangyou") == "token-wangyou"
    assert panel.get_token("qitao") == "token-qitao"
    assert login_calls == ["wangyou", "qitao"]


def test_current_account_api_defaults_to_wangyou():
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    response = panel.app.test_client().get('/api/current-account')
    data = response.get_json()

    assert response.status_code == 200
    assert data == {"success": True, "account_key": "wangyou", "label": "王友小助手"}


def test_account_switch_toggles_between_wangyou_and_qitao(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    panel._token_cache.clear()
    panel._token_cache["wangyou"] = {"token": "old-wangyou-token", "expire_time": 9999999999}
    panel._token_cache["qitao"] = {"token": "old-qitao-token", "expire_time": 9999999999}
    monkeypatch.setattr(panel, "refresh_all_data", lambda *args, **kwargs: True)

    client = panel.app.test_client()
    first = client.post('/api/account-switch').get_json()
    second = client.post('/api/account-switch').get_json()

    assert first["success"] is True
    assert first["account_key"] == "qitao"
    assert first["label"] == "齐涛小助手"
    assert second["success"] is True
    assert second["account_key"] == "wangyou"
    assert second["label"] == "王友小助手"
    assert panel._token_cache == {}


def test_account_switch_clears_token_cache_under_token_lock(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    panel._token_cache.clear()
    panel._token_cache["wangyou"] = {"token": "old-token", "expire_time": 9999999999}
    monkeypatch.setattr(panel, "refresh_all_data", lambda *args, **kwargs: True)

    panel._token_lock.acquire()
    response_holder = {}

    def call_switch():
        response_holder["response"] = panel.app.test_client().post('/api/account-switch')

    worker = threading.Thread(target=call_switch)
    worker.start()
    time.sleep(0.05)

    assert panel._token_cache["wangyou"]["token"] == "old-token"
    assert panel._token_cache["wangyou"]["expire_time"] == 9999999999

    panel._token_lock.release()
    worker.join(timeout=2)

    assert response_holder["response"].status_code == 200
    assert panel._token_cache == {}


def test_account_switch_explicit_account_key_is_idempotent(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    monkeypatch.setattr(panel, "refresh_all_data", lambda *args, **kwargs: True)

    client = panel.app.test_client()
    first = client.post('/api/account-switch', json={"account_key": "qitao"}).get_json()
    second = client.post('/api/account-switch', json={"account_key": "qitao"}).get_json()

    assert first["success"] is True
    assert first["account_key"] == "qitao"
    assert second["success"] is True
    assert second["account_key"] == "qitao"
    assert panel.CURRENT_ACCOUNT_KEY == "qitao"


def test_account_switch_rejects_non_local_request(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    monkeypatch.setattr(panel, "refresh_all_data", lambda *args, **kwargs: True)

    response = panel.app.test_client().post(
        '/api/account-switch',
        json={"account_key": "qitao"},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )

    assert response.status_code == 403
    assert panel.CURRENT_ACCOUNT_KEY == "wangyou"


def test_repeated_account_switches_schedule_only_one_refresh(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    created_threads = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            created_threads.append(self)

        def start(self):
            return None

    monkeypatch.setattr(panel.threading, "Thread", FakeThread)

    client = panel.app.test_client()
    client.post('/api/account-switch', json={"account_key": "qitao"})
    client.post('/api/account-switch', json={"account_key": "wangyou"})
    client.post('/api/account-switch', json={"account_key": "qitao"})

    assert len(created_threads) == 1


def test_account_refresh_processes_latest_pending_switch(monkeypatch):
    panel.CURRENT_ACCOUNT_KEY = "wangyou"
    panel.account_generation = 0
    panel.account_refresh_in_progress = False
    calls = []
    created_threads = []

    def fake_refresh_all_data(account_key=None, generation=None):
        calls.append((account_key, generation))
        if len(calls) == 1:
            panel.app.test_client().post('/api/account-switch', json={"account_key": "wangyou"})
            panel.app.test_client().post('/api/account-switch', json={"account_key": "qitao"})
        return True

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon
            created_threads.append(self)

        def start(self):
            self.target()

    monkeypatch.setattr(panel, "refresh_all_data", fake_refresh_all_data)
    monkeypatch.setattr(panel.threading, "Thread", FakeThread)

    response = panel.app.test_client().post('/api/account-switch', json={"account_key": "qitao"})

    assert response.status_code == 200
    assert len(created_threads) == 1
    assert calls == [("qitao", 1), ("qitao", 3)]
    assert panel.account_refresh_in_progress is False
