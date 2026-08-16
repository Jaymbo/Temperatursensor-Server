"""Tests für die Update-Endpoints (/version, /update/check, /update/pull, /setup)."""

import os
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import db as dbmod


def _make_client(temp_db_path):
    """Client mit gepatchter DB erstellen und main neu laden."""
    dbmod.initialize_db()
    import importlib
    import main
    import update
    importlib.reload(update)
    importlib.reload(main)
    return TestClient(main.app), main, update


# ── /version ──────────────────────────────────────────────────

def test_version_endpoint(temp_db_path, monkeypatch):
    """GET /version sollte die aktuelle Version zurückgeben."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(update, "_run_git", return_value=("v1.2.3", "", 0)):
        response = client.get("/version")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "v1.2.3"


# ── /setup ────────────────────────────────────────────────────

def test_setup_not_configured(temp_db_path, monkeypatch):
    """GET /setup: ADMIN_PASSWORD nicht gesetzt → is_configured=False."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(update, "get_admin_password", return_value=""):
        with patch.object(update, "_run_git", return_value=("/app", "", 0)):
            response = client.get("/setup")
            assert response.status_code == 200
            data = response.json()
            assert data["is_configured"] is False
            assert "ADMIN_PASSWORD" in data["missing"]


def test_setup_configured(temp_db_path, monkeypatch):
    """GET /setup: Alles gesetzt → is_configured=True."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(update, "get_admin_password", return_value="testpass"):
        with patch.object(update, "_run_git", return_value=("/app", "", 0)):
            response = client.get("/setup")
            assert response.status_code == 200
            data = response.json()
            assert data["is_configured"] is True
            assert "version" in data


def test_setup_no_git_repo(temp_db_path, monkeypatch):
    """GET /setup: Kein Git-Repo → git_repo in missing."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(update, "get_admin_password", return_value="testpass"):
        with patch.object(update, "_run_git", return_value=("", "fatal", 1)):
            response = client.get("/setup")
            assert response.status_code == 200
            data = response.json()
            assert data["is_configured"] is False
            assert "git_repo" in data["missing"]


# ── /update/check ────────────────────────────────────────────

def test_update_check_no_git(temp_db_path, monkeypatch):
    """GET /update/check: Kein Remote-Zugriff (fetch fehlschlägt) → error."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def git_side_effect(args):
        if args[0] == "describe":
            return ("v1.0.0", "", 0)
        elif args[0] == "remote":
            return ("", "", 0)  # set-url succeeds
        elif args[0] == "fetch":
            return ("", "fatal: unable to connect", 1)  # fetch fails
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        response = client.get("/update/check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is False
        assert "error" in data


def test_update_check_up_to_date(temp_db_path, monkeypatch):
    """GET /update/check: Lokales == Remote → kein Update."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def git_side_effect(args):
        if args[0] == "describe":
            return ("v1.0.0", "", 0)
        elif args[0] == "remote":
            return ("", "", 0)  # set-url succeeds
        elif args[0] == "fetch":
            return ("", "", 0)  # fetch succeeds
        elif args[0] == "rev-parse" and args[-1] == "HEAD":
            return ("abc1234", "", 0)
        elif args[0] == "rev-parse" and "origin/" in str(args[-1]):
            return ("abc1234", "", 0)  # same as HEAD
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        response = client.get("/update/check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is False
        assert data["latest_version"] == "abc1234"


def test_update_check_update_available(temp_db_path, monkeypatch):
    """GET /update/check: Remote ahead → update_available=True."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def git_side_effect(args):
        if args[0] == "describe":
            return ("v1.0.0", "", 0)
        elif args[0] == "remote":
            return ("", "", 0)  # set-url succeeds
        elif args[0] == "fetch":
            return ("", "", 0)  # fetch succeeds
        elif args[0] == "rev-parse" and args[-1] == "HEAD":
            return ("abc1234", "", 0)
        elif args[0] == "rev-parse" and "origin/" in str(args[-1]):
            return ("def5678", "", 0)  # different → update available
        elif args[0] == "rev-list":
            return ("3", "", 0)
        elif args[0] == "log":
            return ("abc1234 Neue Feature\n1112223 Bugfix", "", 0)
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        response = client.get("/update/check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is True
        assert data["commits_behind"] == 3
        assert data["latest_version"] == "def5678"
        assert "Neue Feature" in data["changelog"]


def test_update_check_ensure_origin_fails(temp_db_path, monkeypatch):
    """GET /update/check: remote set-url fehlschlägt → error."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def git_side_effect(args):
        if args[0] == "describe":
            return ("v1.0.0", "", 0)
        elif args[0] == "remote":
            return ("", "error", 1)  # set-url fails
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        response = client.get("/update/check")
        assert response.status_code == 200
        data = response.json()
        assert data["update_available"] is False
        assert "error" in data
        assert "Remote-URL" in data["error"]


# ── /update/pull ──────────────────────────────────────────────

def test_update_pull_no_admin_password(temp_db_path, monkeypatch):
    """POST /update/pull: ADMIN_PASSWORD nicht gesetzt → Fehler."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(update, "get_admin_password", return_value=""):
        response = client.post("/update/pull", json={"password": "testpass"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "ADMIN_PASSWORD" in data["message"]


def test_update_pull_wrong_password(temp_db_path, monkeypatch):
    """POST /update/pull mit falschem Passwort → Fehler."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def side_effect_get_pw():
        return "correctpassword"

    with patch.object(update, "get_admin_password", side_effect=side_effect_get_pw):
        response = client.post("/update/pull", json={"password": "FALSCH"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Passwort" in data["message"]


def test_update_pull_success(temp_db_path, monkeypatch):
    """POST /update/pull mit richtigem Passwort → git pull."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def side_effect_get_pw():
        return "testpassword"

    with patch.object(update, "get_admin_password", side_effect=side_effect_get_pw):
        with patch.object(update, "_run_git", return_value=("", "", 0)):
            response = client.post("/update/pull", json={"password": "testpassword"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert "Update erfolgreich" in data["message"]


def test_update_pull_git_fails(temp_db_path, monkeypatch):
    """POST /update/pull: git pull fehlschlägt → Fehlermeldung."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def side_effect_get_pw():
        return "testpassword"

    def git_side_effect(args):
        if args[0] == "remote":
            return ("", "", 0)
        elif args[0] == "pull":
            return ("", "fatal: could not read from remote", 1)
        elif args[0] == "rev-parse":
            return ("abc1234", "", 0)
        return ("", "", 0)

    with patch.object(update, "get_admin_password", side_effect=side_effect_get_pw):
        with patch.object(update, "_run_git", side_effect=git_side_effect):
            response = client.post("/update/pull", json={"password": "testpassword"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False


def test_update_pull_with_token(temp_db_path, monkeypatch):
    """POST /update/pull mit GITHUB_TOKEN → Token-URL Pfad wird bedeckt."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def side_effect_get_pw():
        return "testpassword"

    monkeypatch.setattr(update, "GITHUB_TOKEN", "gh_test_token")

    with patch.object(update, "get_admin_password", side_effect=side_effect_get_pw):
        with patch.object(update, "_run_git", return_value=("", "", 0)):
            response = client.post("/update/pull", json={"password": "testpassword"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True


def test_update_pull_remote_seturl_fails(temp_db_path, monkeypatch):
    """POST /update/pull: remote set-url fehlschlägt → Fehler."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    def side_effect_get_pw():
        return "testpassword"

    with patch.object(update, "get_admin_password", side_effect=side_effect_get_pw):
        with patch.object(update, "_run_git", return_value=("", "error", 1)):
            response = client.post("/update/pull", json={"password": "testpassword"})
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "Remote-URL" in data["message"]


# ── Unit-Tests für update.py Funktionen ──────────────────────

def test_get_current_version_fallback_to_sha():
    """get_current_version: kein Tag → SHA Fallback."""
    import update

    def git_side_effect(args):
        if args[0] == "describe":
            return ("", "no tag", 128)
        elif args[0] == "rev-parse":
            return ("abc1234", "", 0)
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        version = update.get_current_version()
        assert version == "abc1234"


def test_get_current_version_unknown():
    """get_current_version: alles fehlschlägt → 'unknown'."""
    import update

    with patch.object(update, "_run_git", return_value=("", "error", 1)):
        version = update.get_current_version()
        assert version == "unknown"


def test_get_changelog():
    """get_changelog: Commit Messages formatieren."""
    import update

    def git_side_effect(args):
        if args[0] == "log":
            return ("abc123 Neue Feature\n111223 Bugfix kritisch", "", 0)
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        cl = update.get_changelog("aaa", "bbb")
        assert "Neue Feature" in cl
        assert "Bugfix kritisch" in cl


def test_get_changelog_empty():
    """get_changelog: Keine Commits → Standard-Meldung."""
    import update

    with patch.object(update, "_run_git", return_value=("", "", 1)):
        cl = update.get_changelog("aaa", "bbb")
        assert "(Kein Changelog verfügbar)" in cl


def test_run_git_timeout():
    """_run_git mit TimeoutExpired → Fehler."""
    import update
    from subprocess import TimeoutExpired

    with patch("subprocess.run", side_effect=TimeoutExpired("git", 30)):
        stdout, stderr, rc = update._run_git(["status"])
        assert rc == 1
        assert "Zeitüberschreitung" in stderr


def test_run_git_success():
    """_run_git Erfolgspfad mit echtem git-Call bedecken."""
    import update
    stdout, stderr, rc = update._run_git(["version"])
    assert rc == 0
    assert "git" in stdout


def test_get_local_commit_fallback():
    """_get_local_commit: rev-parse fehlschlägt → leerer String."""
    import update
    with patch.object(update, "_run_git", return_value=("", "error", 1)):
        assert update._get_local_commit() == ""


def test_get_changelog_no_space():
    """get_changelog: Commit ohne Spaltentrennung."""
    import update
    def git_side_effect(args):
        if args[0] == "log":
            return ("abcdef", "", 0)  # kein Leerzeichen → else-Zweig
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        cl = update.get_changelog("a", "b")
        assert "abcdef" in cl


def test_get_admin_password_from_env(monkeypatch):
    """get_admin_password: Liest aus Env-Var."""
    import update
    monkeypatch.setenv("ADMIN_PASSWORD", "fromenv")
    pw = update.get_admin_password()
    assert pw == "fromenv"


def test_get_admin_password_empty(monkeypatch):
    """get_admin_password: Keine Env-Var → leerer String."""
    import update
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    pw = update.get_admin_password()
    assert pw == ""


def test_check_setup_complete():
    """check_setup: alles vorhanden → is_configured=True."""
    import update

    with patch.object(update, "get_admin_password", return_value="test"):
        with patch.object(update, "_run_git", return_value=("/app", "", 0)):
            result = update.check_setup()
            assert result["is_configured"] is True
            assert len(result["missing"]) == 0


def test_check_setup_missing_password():
    """check_setup: ADMIN_PASSWORD fehlt."""
    import update

    with patch.object(update, "get_admin_password", return_value=""):
        with patch.object(update, "_run_git", return_value=("/app", "", 0)):
            result = update.check_setup()
            assert result["is_configured"] is False
            assert "ADMIN_PASSWORD" in result["missing"]


def test_check_setup_no_git():
    """check_setup: Kein Git-Repo."""
    import update

    with patch.object(update, "get_admin_password", return_value="test"):
        with patch.object(update, "_run_git", return_value=("", "error", 1)):
            result = update.check_setup()
            assert result["is_configured"] is False
            assert "git_repo" in result["missing"]


# ── Helper-Tests für update.py ────────────────────────────────

def test_build_remote_url_default():
    """_build_remote_url: Standard-URL ohne Token."""
    import update
    with patch.object(update, "GITHUB_OWNER", "Jaymbo"):
        with patch.object(update, "GITHUB_REPO", "temperatursensor-server"):
            with patch.object(update, "GITHUB_TOKEN", ""):
                url = update._build_remote_url()
                assert url == "https://github.com/Jaymbo/temperatursensor-server.git"


def test_build_remote_url_with_token():
    """_build_remote_url: URL mit Token."""
    import update
    with patch.object(update, "GITHUB_OWNER", "Jaymbo"):
        with patch.object(update, "GITHUB_REPO", "temperatursensor-server"):
            with patch.object(update, "GITHUB_TOKEN", "gh_test"):
                url = update._build_remote_url()
                assert "gh_test" in url


def test_ensure_origin_success():
    """_ensure_origin: set-url erfolgreich."""
    import update
    with patch.object(update, "_run_git", return_value=("", "", 0)):
        assert update._ensure_origin() is True


def test_ensure_origin_fails():
    """_ensure_origin: set-url fehlschlägt."""
    import update
    with patch.object(update, "_run_git", return_value=("", "error", 1)):
        assert update._ensure_origin() is False


def test_fetch_remote_success():
    """_fetch_remote: fetch erfolgreich."""
    import update
    with patch.object(update, "_run_git", return_value=("", "", 0)):
        assert update._fetch_remote() is True


def test_fetch_remote_fails():
    """_fetch_remote: fetch fehlschlägt."""
    import update
    with patch.object(update, "_run_git", return_value=("", "fatal", 1)):
        assert update._fetch_remote() is False


def test_get_remote_ref():
    """_get_remote_ref: Gibt origin/<branch> zurück."""
    import update
    with patch.object(update, "GIT_BRANCH", "main"):
        ref = update._get_remote_ref()
        assert ref == "origin/main"


def test_get_remote_commit_from_origin():
    """_get_remote_commit: Liest origin/<branch> direkt."""
    import update
    with patch.object(update, "_run_git", return_value=("abc1234", "", 0)):
        sha = update._get_remote_commit()
        assert sha == "abc1234"


def test_get_remote_commit_fallback_ls_remote():
    """_get_remote_commit: Fallback auf ls-remote, wenn origin-Ref fehlt."""
    import update
    call_count = [0]
    def git_side_effect(args):
        call_count[0] += 1
        if call_count[0] == 1:
            # Erster Aufruf: rev-parse origin/main → nicht gefunden
            return ("", "fatal: ambiguous argument", 128)
        else:
            # Zweiter Aufruf: ls-remote → SHA
            return ("def5678\trefs/heads/main", "", 0)
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        sha = update._get_remote_commit()
        assert sha == "def5678"


def test_check_for_update_with_fetch_fail():
    """check_for_update: fetch fehlschlägt → error."""
    import update

    def git_side_effect(args):
        if args[0] == "describe":
            return ("v1.0.0", "", 0)
        elif args[0] == "remote":
            return ("", "", 0)
        elif args[0] == "fetch":
            return ("", "fatal: unable to connect", 1)
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        result = update.check_for_update()
        assert result["update_available"] is False
        assert "error" in result


def test_check_for_update_missing_local_commit():
    """check_for_update: Lokaler Commit fehlt → error."""
    import update

    call_count = [0]
    def git_side_effect(args):
        call_count[0] += 1
        if call_count[0] == 1:  # describe
            return ("v1.0.0", "", 0)
        elif call_count[0] == 2:  # remote set-url
            return ("", "", 0)
        elif call_count[0] == 3:  # fetch
            return ("", "", 0)
        elif call_count[0] == 4:  # rev-parse HEAD
            return ("", "", 1)  # kein HEAD
        return ("", "", 0)

    with patch.object(update, "_run_git", side_effect=git_side_effect):
        result = update.check_for_update()
        assert result["update_available"] is False
        assert "error" in result


# ── /verify_password ────────────────────────────────────────

def test_verify_password_correct(temp_db_path, monkeypatch):
    """POST /verify_password: Richtiges Passwort → valid=True."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(main, "get_admin_password", return_value="testpass"):
        response = client.post("/verify_password", json={"password": "testpass"})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True


def test_verify_password_wrong(temp_db_path, monkeypatch):
    """POST /verify_password: Falsches Passwort → valid=False."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(main, "get_admin_password", return_value="testpass"):
        response = client.post("/verify_password", json={"password": "falsch"})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False


def test_verify_password_no_password(temp_db_path, monkeypatch):
    """POST /verify_password: Kein Passwort → valid=False."""
    monkeypatch.setattr(dbmod, "DB_PATH", str(temp_db_path), raising=False)
    client, main, update = _make_client(temp_db_path)

    with patch.object(main, "get_admin_password", return_value="testpass"):
        response = client.post("/verify_password", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False