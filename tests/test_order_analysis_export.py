import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "零担面板.py"

spec = importlib.util.spec_from_file_location("panel_order_export", MODULE_PATH)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def test_order_analysis_export_uses_source_async_export_flow(monkeypatch, tmp_path):
    calls = []
    exported = tmp_path / "0608-0617签收-讯服、广宏网点.xlsx"
    exported.write_bytes(b"xlsx")

    monkeypatch.setattr(panel, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(panel, "get_token", lambda: "token-1")
    monkeypatch.setattr(
        panel,
        "query_stowage_orders",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should use source async-export, not local page query")),
    )
    monkeypatch.setattr(
        panel,
        "submit_order_analysis_export",
        lambda token, rules: calls.append(("submit", token, rules)) or "task-1",
        raising=False,
    )
    monkeypatch.setattr(
        panel,
        "poll_order_analysis_export",
        lambda token, task_id: calls.append(("poll", token, task_id)) or ("file-key-1", "我的配载单明细.xlsx"),
        raising=False,
    )
    monkeypatch.setattr(
        panel,
        "download_order_analysis_export_file",
        lambda token, file_key, filename: calls.append(("download", token, file_key, filename)) or str(exported),
        raising=False,
    )

    response = panel.app.test_client().post('/api/order-analysis/export', json={
        "rules": [
            {"field": "network", "values": ["讯服", "广宏"]},
            {"field": "sign", "values": ["2026-06-08", "2026-06-17"]},
        ],
        "file_name": "0608-0617签收-讯服、广宏网点.xlsx",
    })
    data = response.get_json()

    assert data["success"] is True
    assert data["mode"] == "single"
    assert data["downloads"][0]["filename"] == "0608-0617签收-讯服、广宏网点.xlsx"
    assert data["downloads"][0]["account_key"] == "wangyou"
    assert data["downloads"][0]["download_url"].startswith("/api/order-analysis/export-download?token=")
    assert calls[0][0] == "download"
    assert calls[0][1] == "wangyou"
    assert {rule["field"] for rule in calls[0][2]} == {"k_contract_line_a.network", "receive_time"}


def test_build_order_analysis_export_payload_matches_source_default_shape():
    rules = [{"field": "receive_time", "option": "BTS", "values": ["a", "b"]}]

    payload = panel.build_order_analysis_export_payload(rules)

    assert payload["direction"] == "DESC"
    assert payload["property"] == "id"
    assert payload["fromClientType"] == "pc"
    assert payload["number"] == 0
    assert payload["sorts"] == []
    assert payload["rules"] == rules
    assert payload["size"] == 15
    assert payload["specialConditions"] == []
    assert payload["dynamicFormCode"] == "stowage_sign_receipt"
    assert payload["developmentSystemId"] is None
    assert payload["debugFlag"] is False
    assert payload["exportSortFields"] == panel.ORDER_ANALYSIS_EXPORT_SORT_FIELDS
    assert payload["exportSortFields"][:5] == [
        "source_order_no",
        "exe_pur_order_b.order_date",
        "delivery_date",
        "receive_time",
        "stowage_all_weight",
    ]
