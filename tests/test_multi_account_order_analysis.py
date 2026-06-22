import importlib.util
import threading
import time
from pathlib import Path

import pytest

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


def test_split_order_analysis_networks_routes_jiangnan_jiangbei_to_qitao():
    result = panel.split_order_analysis_networks(["零担", "江南", "江北", "讯服"])

    assert result == {
        "wangyou": ["零担", "讯服"],
        "qitao": ["江南", "江北"],
    }


def test_query_order_analysis_merges_two_accounts(monkeypatch):
    login_calls = []
    query_calls = []

    monkeypatch.setattr(panel, "login", lambda account_key=None: login_calls.append(account_key) or f"token-{account_key}")

    def fake_query(token, rules, size=5000):
        query_calls.append((token, rules))
        if token == "token-wangyou":
            return [{"id": "1", "source_order_no": "WY001", "stowage_all_weight": 10, "k_contract_line_a": {"network_show": "零担", "network": panel.ALL_NETWORKS["零担"]}}]
        if token == "token-qitao":
            return [{"id": "2", "source_order_no": "QT001", "stowage_all_weight": 20, "k_contract_line_a": {"network_show": "江南", "network": panel.ALL_NETWORKS["江南"]}}]
        return []

    monkeypatch.setattr(panel, "query_stowage_orders_strict", fake_query)

    result = panel.query_order_analysis_orders({"networks": ["零担", "江南"]})

    assert [o["source_order_no"] for o in result["orders"]] == ["WY001", "QT001"]
    assert result["sources"] == {"wangyou": 1, "qitao": 1}
    assert result["orders"][0]["source_account"] == "wangyou"
    assert result["orders"][1]["source_account"] == "qitao"
    assert login_calls == ["wangyou", "qitao"]


def test_order_analysis_query_api_returns_sources(monkeypatch):
    monkeypatch.setattr(panel, "query_order_analysis_orders", lambda data: {
        "orders": [{"source_order_no": "A", "stowage_weight": 3, "source_account": "wangyou"}],
        "sources": {"wangyou": 1}
    })

    response = panel.app.test_client().post('/api/order-analysis/query', json={"networks": ["零担"]})
    data = response.get_json()

    assert data["success"] is True
    assert data["total"] == 1
    assert data["total_weight"] == 3
    assert data["sources"] == {"wangyou": 1}


def test_query_order_analysis_raises_when_one_account_query_fails(monkeypatch):
    monkeypatch.setattr(panel, "login", lambda account_key=None: f"token-{account_key}")

    def fake_query(token, rules, size=5000):
        if token == "token-qitao":
            raise TimeoutError("timeout")
        return [{"id": "1", "source_order_no": "WY001", "stowage_all_weight": 10}]

    monkeypatch.setattr(panel, "query_stowage_orders_strict", fake_query)

    with pytest.raises(RuntimeError) as exc_info:
        panel.query_order_analysis_orders({"networks": ["零担", "江南"]})

    message = str(exc_info.value)
    assert "齐涛小助手" in message
    assert "查询失败" in message


def test_order_analysis_query_api_returns_failure_when_helper_raises(monkeypatch):
    monkeypatch.setattr(panel, "query_order_analysis_orders", lambda data: (_ for _ in ()).throw(RuntimeError("齐涛小助手订单分析查询失败：timeout")))

    response = panel.app.test_client().post('/api/order-analysis/query', json={"networks": ["零担", "江南"]})
    data = response.get_json()

    assert data["success"] is False
    assert "齐涛小助手" in data["message"]
    assert "查询失败" in data["message"]


def test_query_stowage_orders_strict_raises_on_business_error_without_content(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "error", "message": "token expired"}

    class FakeSession:
        def post(self, url, json, headers, timeout):
            return FakeResponse()

    monkeypatch.setattr(panel, "_sess", lambda: FakeSession())

    with pytest.raises(RuntimeError) as exc_info:
        panel.query_stowage_orders_strict("token", [])

    assert "token expired" in str(exc_info.value)


def test_query_stowage_orders_strict_returns_empty_list_for_real_no_data(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": []}

    class FakeSession:
        def post(self, url, json, headers, timeout):
            return FakeResponse()

    monkeypatch.setattr(panel, "_sess", lambda: FakeSession())

    assert panel.query_stowage_orders_strict("token", []) == []


def test_build_order_analysis_export_plan_returns_two_downloads(monkeypatch, tmp_path):
    calls = []

    def fake_export(account_key, rules, file_prefix):
        calls.append((account_key, rules, file_prefix))
        path = tmp_path / f"{file_prefix}.xlsx"
        path.write_bytes(b"xlsx")
        return str(path)

    monkeypatch.setattr(panel, "download_order_analysis_export_file", fake_export)
    monkeypatch.setattr(panel.secrets, "token_urlsafe", lambda size: f"token-{len(panel.order_analysis_export_files) + 1}")

    panel.order_analysis_export_files.clear()
    result = panel.build_order_analysis_export_plan({"rules": [{"field": "network", "values": ["零担", "江南"]}]})

    assert result["success"] is True
    assert result["mode"] == "multiple"
    assert len(result["downloads"]) == 2
    assert result["downloads"][0]["account_key"] == "wangyou"
    assert result["downloads"][1]["account_key"] == "qitao"
    assert calls[0][0] == "wangyou"
    assert calls[1][0] == "qitao"


def test_export_download_token_is_one_time(tmp_path):
    path = tmp_path / "订单分析.xlsx"
    path.write_bytes(b"xlsx")
    panel.order_analysis_export_files.clear()
    panel.order_analysis_export_files["token-1"] = {"path": str(path), "filename": "订单分析.xlsx"}

    first = panel.consume_order_analysis_export_file("token-1")
    second = panel.consume_order_analysis_export_file("token-1")

    assert first["filename"] == "订单分析.xlsx"
    assert second is None


def test_convert_export_rules_to_query_data():
    data = panel.convert_export_rules_to_query_data([
        {"field": "network", "values": ["零担", "江南"]},
        {"field": "created", "values": ["2026-06-01", "2026-06-02"]},
        {"field": "sign", "values": ["2026-06-03", "2026-06-04"]},
        {"field": "status", "values": ["已签收"]},
    ])
    assert data["networks"] == ["零担", "江南"]
    assert data["created_start"] == "2026-06-01"
    assert data["created_end"] == "2026-06-02"
    assert data["sign_start"] == "2026-06-03"
    assert data["sign_end"] == "2026-06-04"
    assert data["statuses"] == ["已签收"]
