import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "零担面板.py"

spec = importlib.util.spec_from_file_location("panel", MODULE_PATH)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)


def test_parse_version_accepts_plain_and_v_prefix():
    assert panel.parse_version("1.2.3") == (1, 2, 3)
    assert panel.parse_version("v1.2.3") == (1, 2, 3)
    assert panel.parse_version("V2.0") == (2, 0, 0)


def test_parse_version_rejects_invalid_values():
    assert panel.parse_version("") is None
    assert panel.parse_version("latest") is None
    assert panel.parse_version("1.x.0") is None


def test_compare_versions():
    assert panel.compare_versions("1.0.1", "1.0.0") == 1
    assert panel.compare_versions("v1.0.0", "1.0.0") == 0
    assert panel.compare_versions("1.0.0", "1.0.1") == -1
    assert panel.compare_versions("1.2", "1.1.9") == 1


def test_build_update_info_detects_newer_release():
    release = {
        "tag_name": "v1.0.1",
        "html_url": "https://github.com/lankaibaba/space/releases/tag/v1.0.1",
        "body": "修复导出问题",
        "assets": [
            {
                "name": "王友小助手.exe",
                "browser_download_url": "https://github.com/lankaibaba/space/releases/download/v1.0.1/王友小助手.exe",
            }
        ],
    }

    info = panel.build_update_info(release, current_version="1.0.0")

    assert info["success"] is True
    assert info["has_update"] is True
    assert info["latest_version"] == "1.0.1"
    assert info["download_url"].endswith("王友小助手.exe")
    assert info["notes"] == "修复导出问题"


def test_build_update_info_reports_missing_asset():
    release = {
        "tag_name": "v1.0.1",
        "html_url": "https://github.com/lankaibaba/space/releases/tag/v1.0.1",
        "body": "",
        "assets": [],
    }

    info = panel.build_update_info(release, current_version="1.0.0")

    assert info["success"] is False
    assert "缺少程序文件" in info["message"]


def test_build_update_bat_content_contains_retry_and_restart():
    content = panel.build_update_bat_content(
        app_exe="D:\\app\\王友小助手.exe",
        new_exe="D:\\app\\王友小助手.exe.new"
    )

    assert "chcp 65001" in content
    assert "王友小助手.exe.new" in content
    assert ":retry" in content
    assert "goto retry" in content
    assert "start \"\"" in content
    assert "del \"%~f0\"" in content


def test_is_github_release_download_url_accepts_percent_encoded_asset():
    url = "https://github.com/lankaibaba/space/releases/download/v1.0.1/%E7%8E%8B%E5%8F%8B%E5%B0%8F%E5%8A%A9%E6%89%8B.exe"

    assert panel.is_github_release_download_url(url) is True


def test_build_update_bat_content_escapes_metacharacters_and_checks_rename():
    content = panel.build_update_bat_content(
        app_exe="D:\\app & 100%\\王友^小助手.exe",
        new_exe="D:\\app & 100%\\王友^小助手.exe.new"
    )

    assert "100%%" in content
    assert "app ^& 100%%" in content
    assert "王友^^小助手.exe" in content
    assert "if not exist \"%APP_EXE%\"" in content
    assert "exit /b 1" in content


def test_build_update_bat_content_avoids_parenthesized_path_blocks():
    content = panel.build_update_bat_content(
        app_exe="C:\\Program Files (x86)\\王友小助手.exe",
        new_exe="C:\\Program Files (x86)\\王友小助手.exe.new"
    )

    assert 'if exist "%APP_EXE%" (' not in content
    assert 'if not exist "%APP_EXE%" (' not in content
    assert 'if exist "%NEW_EXE%" (' not in content
    assert 'if not exist "%NEW_EXE%" (' not in content
    assert "goto fail" in content
    assert ":fail" in content


def test_consume_pending_update_token_is_one_time():
    panel.pending_update_token = "token-123"

    assert panel.consume_pending_update_token("token-123") is True
    assert panel.pending_update_token is None
    assert panel.consume_pending_update_token("token-123") is False


def test_check_update_allows_local_request_without_origin(monkeypatch):
    monkeypatch.setattr(panel, "check_update_info", lambda: {"success": True, "has_update": False})

    response = panel.app.test_client().get('/api/check-update')
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert data["has_update"] is False


def test_download_update_rejects_non_local_remote_addr(monkeypatch):
    monkeypatch.setattr(panel, "validate_latest_update_download_url", lambda url: (_ for _ in ()).throw(AssertionError("should not validate")))
    monkeypatch.setattr(panel, "download_update_asset", lambda url: (_ for _ in ()).throw(AssertionError("should not download")))

    response = panel.app.test_client().post(
        '/api/download-update',
        json={"download_url": "https://github.com/lankaibaba/space/releases/download/v1.0.2/王友小助手.exe"},
        environ_overrides={"REMOTE_ADDR": "192.168.1.10"},
    )
    data = response.get_json()

    assert response.status_code == 403
    assert data["success"] is False


def test_apply_update_rejects_local_request_with_untrusted_origin(monkeypatch):
    monkeypatch.setattr(panel.sys, "frozen", True, raising=False)
    panel.pending_update_token = "token-evil"
    monkeypatch.setattr(panel, "write_update_bat", lambda: (_ for _ in ()).throw(AssertionError("should not write bat")))

    response = panel.app.test_client().post(
        '/api/apply-update',
        json={"update_token": "token-evil"},
        headers={"Origin": "http://evil.example"},
    )
    data = response.get_json()

    assert response.status_code == 403
    assert data["success"] is False
    assert panel.pending_update_token == "token-evil"


def test_check_update_allows_localhost_origin(monkeypatch):
    monkeypatch.setattr(panel, "check_update_info", lambda: {"success": True, "has_update": False})

    response = panel.app.test_client().get(
        '/api/check-update',
        headers={"Origin": "http://localhost:5000"},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert data["has_update"] is False


def test_download_update_api_rejects_non_latest_matching_asset(monkeypatch):
    latest_url = "https://github.com/lankaibaba/space/releases/download/v1.0.2/王友小助手.exe"
    historical_url = "https://github.com/lankaibaba/space/releases/download/v1.0.1/王友小助手.exe"

    monkeypatch.setattr(panel, "check_update_info", lambda: {
        "success": True,
        "has_update": True,
        "download_url": latest_url,
    })
    monkeypatch.setattr(panel, "download_update_asset", lambda url: (_ for _ in ()).throw(AssertionError("should not download")))

    response = panel.app.test_client().post('/api/download-update', json={"download_url": historical_url})
    data = response.get_json()

    assert data["success"] is False
    assert "不是最新版本" in data["message"]


def test_download_update_api_generates_pending_update_token(monkeypatch, tmp_path):
    latest_url = "https://github.com/lankaibaba/space/releases/download/v1.0.2/王友小助手.exe"
    downloaded = tmp_path / "王友小助手.exe.new"
    downloaded.write_bytes(b"exe")

    monkeypatch.setattr(panel, "check_update_info", lambda: {
        "success": True,
        "has_update": True,
        "download_url": latest_url,
    })
    monkeypatch.setattr(panel, "download_update_asset", lambda url: str(downloaded))
    monkeypatch.setattr(panel.secrets, "token_urlsafe", lambda size: "token-123")

    response = panel.app.test_client().post('/api/download-update', json={"download_url": latest_url})
    data = response.get_json()

    assert data["success"] is True
    assert data["update_token"] == "token-123"
    assert panel.pending_update_token == "token-123"


def test_apply_update_rejects_invalid_update_token_in_frozen_mode(monkeypatch):
    monkeypatch.setattr(panel.sys, "frozen", True, raising=False)
    panel.pending_update_token = "expected-token"
    monkeypatch.setattr(panel, "write_update_bat", lambda: (_ for _ in ()).throw(AssertionError("should not write bat")))

    response = panel.app.test_client().post('/api/apply-update', json={"update_token": "wrong-token"})
    data = response.get_json()

    assert data["success"] is False
    assert "更新令牌无效" in data["message"]
    assert panel.pending_update_token == "expected-token"


def test_launch_update_bat_uses_startfile_for_cmd_metacharacter_paths(monkeypatch):
    calls = []
    bat_path = r"D:\app&tools\update.bat"

    monkeypatch.setattr(panel.os, "startfile", lambda path: calls.append(path), raising=False)

    panel.launch_update_bat(bat_path)

    assert calls == [bat_path]


def test_launch_update_bat_uses_startfile_for_spaces_and_parentheses(monkeypatch):
    calls = []
    bat_path = r"D:\app dir (x86)\safe&name\update.bat"

    monkeypatch.setattr(panel.os, "startfile", lambda path: calls.append(path), raising=False)

    panel.launch_update_bat(bat_path)

    assert calls == [bat_path]


def test_apply_update_uses_safe_bat_launcher(monkeypatch):
    calls = {"write_bat": 0, "launch": 0, "shutdown": 0}
    launched = {}

    def fake_write_update_bat():
        calls["write_bat"] += 1
        return "D:\\app dir (x86)\\safe&name\\update.bat"

    def fake_launch_update_bat(bat_path):
        calls["launch"] += 1
        launched["path"] = bat_path

    def fake_schedule_shutdown(*args, **kwargs):
        calls["shutdown"] += 1

    monkeypatch.setattr(panel.sys, "frozen", True, raising=False)
    panel.pending_update_token = "token-1"
    monkeypatch.setattr(panel, "write_update_bat", fake_write_update_bat)
    monkeypatch.setattr(panel, "launch_update_bat", fake_launch_update_bat)
    monkeypatch.setattr(panel, "schedule_shutdown", fake_schedule_shutdown)

    response = panel.app.test_client().post('/api/apply-update', json={"update_token": "token-1"})
    data = response.get_json()

    assert data["success"] is True
    assert panel.pending_update_token is None
    assert calls == {"write_bat": 1, "launch": 1, "shutdown": 1}
    assert launched["path"] == "D:\\app dir (x86)\\safe&name\\update.bat"


def test_apply_update_frozen_success_consumes_token_once(monkeypatch):
    calls = {"write_bat": 0, "launch": 0, "shutdown": 0}

    def fake_write_update_bat():
        calls["write_bat"] += 1
        return "D:\\app\\update.bat"

    def fake_launch_update_bat(*args, **kwargs):
        calls["launch"] += 1

    def fake_schedule_shutdown(*args, **kwargs):
        calls["shutdown"] += 1

    monkeypatch.setattr(panel.sys, "frozen", True, raising=False)
    panel.pending_update_token = "token-1"
    monkeypatch.setattr(panel, "write_update_bat", fake_write_update_bat)
    monkeypatch.setattr(panel, "launch_update_bat", fake_launch_update_bat)
    monkeypatch.setattr(panel, "schedule_shutdown", fake_schedule_shutdown)

    client = panel.app.test_client()
    first_response = client.post('/api/apply-update', json={"update_token": "token-1"})
    first_data = first_response.get_json()

    assert first_data["success"] is True
    assert panel.pending_update_token is None
    assert calls == {"write_bat": 1, "launch": 1, "shutdown": 1}

    second_response = client.post('/api/apply-update', json={"update_token": "token-1"})
    second_data = second_response.get_json()

    assert second_data["success"] is False
    assert "令牌无效" in second_data["message"]
    assert calls == {"write_bat": 1, "launch": 1, "shutdown": 1}
