"""Update-Modul: Versionsprüfung, Changelog, git pull.

Funktionsweise:
- Versionsnummer aus dem aktuellen git-Tag (oder commit SHA)
- Prüft via ``git fetch`` + ``git rev-parse origin/<branch>``, ob es neue Commits gibt
- Changelog: ``git log HEAD..origin/<branch>`` (nach fetch)
- git pull für Update (öffentliches Repo → keine Auth nötig)
- ADMIN_PASSWORD: nur aus Env-Var (via .env)

**Wichtig:** ``check_for_update()`` nutzt ``origin/<branch>`` statt
roher SHAs aus ``git ls-remote``. Das garantiert, dass ``git log`` und
``git rev-list`` konsistent arbeiten, da ``origin/<branch>`` nach dem
Fetch ein gültiger lokaler Ref ist.
"""

import os
import subprocess
from typing import Tuple


def get_admin_password() -> str:
    """Liest ADMIN_PASSWORD aus der Umgebungsvariable.

    Wird bei jedem Aufruf neu gelesen, damit Änderungen sofort wirksam
    werden (auch mit --reload).
    """
    return os.getenv("ADMIN_PASSWORD", "")


# Legacy alias – wird in main.py und Tests referenziert
ADMIN_PASSWORD = get_admin_password()

# GitHub Repo – kann via Env-Var überschrieben werden
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "Jaymbo")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Temperatursensor-Server")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# Branch der verwendet wird
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")
# CWD für git-Befehle: /project im Container, lokal Fallback auf Parent-Dir (Repo-Root)
GIT_CWD = os.getenv("GIT_CWD", os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))


def _run_git(args: list[str]) -> Tuple[str, str, int]:
    """Führt einen git-Befehl aus und gibt (stdout, stderr, returncode) zurück."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=GIT_CWD,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "git-Befehl Zeitüberschreitung", 1


def get_current_version() -> str:
    """Gibt die aktuelle Versionsnummer als git-Tag zurück.

    Fallback: commit SHA (short), wenn kein Tag vorhanden ist.
    """
    stdout, _, rc = _run_git(["describe", "--tags", "--always"])
    if rc == 0 and stdout:
        return stdout
    stdout, _, rc = _run_git(["rev-parse", "--short", "HEAD"])
    if rc == 0 and stdout:
        return stdout
    return "unknown"


def _build_remote_url() -> str:
    """Erstellt die Remote-URL für das GitHub-Repo."""
    url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
    if GITHUB_TOKEN:
        url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_OWNER}/{GITHUB_REPO}.git"
    return url


def _ensure_origin() -> bool:
    """Stellt sicher, dass ``origin`` auf die korrekte Remote-URL zeigt.

    Liest die aktuelle URL aus und setzt sie ggf. neu (z.B. nach
    Änderungen von ``GITHUB_OWNER``/``GITHUB_REPO`` in der .env).
    Gibt ``True`` zurück, wenn die URL gesetzt werden konnte.
    """
    remote_url = _build_remote_url()
    _, _, rc = _run_git(["remote", "set-url", "origin", remote_url])
    return rc == 0


def _fetch_remote() -> bool:
    """Holt die neuesten Daten von origin.

    Gibt ``True`` zurück, wenn der Fetch erfolgreich war.
    """
    stdout, stderr, rc = _run_git(["fetch", "origin"])
    if rc != 0:
        print(f"git fetch failed: {stderr}")
        return False
    return True


def _get_remote_commit() -> str:
    """Holt den commit SHA von ``origin/<branch>``.

    Liest direkt den lokalen Tracking-Ref nach ``git fetch``.
    Fallback: ``git ls-remote`` auf die direkte URL.
    """
    ref = f"origin/{GIT_BRANCH}"
    stdout, _, rc = _run_git(["rev-parse", ref])
    if rc == 0 and stdout:
        return stdout

    # Fallback: ``git ls-remote`` ohne lokalen origin
    remote_url = _build_remote_url()
    stdout, _, rc = _run_git(["ls-remote", remote_url, GIT_BRANCH])
    if rc == 0 and stdout:
        return stdout.split()[0]

    return ""


def _get_local_commit() -> str:
    """Holt den commit SHA von HEAD."""
    stdout, _, rc = _run_git(["rev-parse", "HEAD"])
    if rc == 0 and stdout:
        return stdout
    return ""


def get_changelog(local_sha: str, remote_sha: str) -> str:
    """Holt die commit messages zwischen zwei SHAs."""
    stdout, stderr, rc = _run_git([
        "log", "--oneline", "--no-merges", f"{local_sha}..{remote_sha}"
    ])
    if rc == 0 and stdout:
        lines = stdout.split("\n")
        # Format: "abcdef Feature beschreibung"
        formatted = []
        for line in lines:
            if line.strip():
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    formatted.append(f"  • {parts[1]}")
                else:
                    formatted.append(f"  • {line}")
        return "\n".join(formatted)
    return "(Kein Changelog verfügbar)"


def _get_remote_ref() -> str:
    """Gibt den lokalen Ref für origin/<branch> zurück."""
    return f"origin/{GIT_BRANCH}"


def check_for_update() -> dict:
    """Prüft, ob es neue Commits auf origin/<branch> gibt.

    **Vorgehen:**
    1. ``origin``-URL setzen (``_ensure_origin``)
    2. ``git fetch origin`` (holt neue Commits)
    3. ``HEAD`` vs ``origin/<branch>`` vergleichen
    4. ``git log HEAD..origin/<branch>`` für Changelog

    Damit ``origin/<branch>`` ein gültiger Ref ist, funktionieren
    ``git log`` und ``git rev-list`` garantiert korrekt.
    """
    current = get_current_version()

    # 1. Origin-URL sicherstellen
    if not _ensure_origin():
        return {
            "current_version": current,
            "latest_version": None,
            "update_available": False,
            "changelog": "",
            "commits_behind": 0,
            "error": "Konnte Remote-URL nicht setzen. Prüfe Git-Konfiguration.",
        }

    # 2. Remote-Daten holen
    if not _fetch_remote():
        return {
            "current_version": current,
            "latest_version": None,
            "update_available": False,
            "changelog": "",
            "commits_behind": 0,
            "error": "Konnte Remote-Daten nicht holen. Prüfe Netzwerkverbindung.",
        }

    # 3. HEAD vs origin/<branch> vergleichen
    local_sha = _get_local_commit()
    remote_sha = _get_remote_commit()
    remote_ref = _get_remote_ref()

    if not local_sha or not remote_sha:
        return {
            "current_version": current,
            "latest_version": None,
            "update_available": False,
            "changelog": "",
            "commits_behind": 0,
            "error": "Konnte lokale oder Remote-Version nicht ermitteln.",
        }

    if local_sha == remote_sha:
        return {
            "current_version": current,
            "latest_version": remote_sha[:7],
            "update_available": False,
            "changelog": "",
            "commits_behind": 0,
        }

    # 4. Commits zählen — ``origin/<branch>`` ist nach fetch ein gültiger Ref
    stdout, _, rc = _run_git([
        "rev-list", "--count", f"{local_sha}..{remote_ref}"
    ])
    commits = int(stdout) if rc == 0 and stdout.isdigit() else "?"

    # 5. Changelog — ``origin/<branch>`` statt rohem SHA
    changelog = get_changelog(local_sha, remote_ref)

    return {
        "current_version": current,
        "latest_version": remote_sha[:7],
        "update_available": True,
        "changelog": changelog,
        "commits_behind": commits,
    }


def pull_update(password: str) -> dict:
    """Zieht die neueste Version via git pull und startet neu.

    Args:
        password: Admin-Passwort (muss ADMIN_PASSWORD entsprechen)

    Gibt zurück:
        - success: bool
        - message: str
        - output: str (git pull Ausgabe)
        - new_version: str
    """
    admin_pw = get_admin_password()
    if not admin_pw:
        return {
            "success": False,
            "message": "ADMIN_PASSWORD ist nicht gesetzt. Bitte setze die Umgebungsvariable im Container.",
            "output": "",
        }

    if password != admin_pw:
        return {"success": False, "message": "Falsches Passwort", "output": ""}

    # Origin-URL sicherstellen
    if not _ensure_origin():
        return {
            "success": False,
            "message": "Fehler beim Aktualisieren der Remote-URL",
            "output": "",
        }

    # Pull
    stdout, stderr, rc = _run_git(["pull", "origin", GIT_BRANCH])
    output = stdout if stdout else stderr

    # Tags holen, damit neue Versionen sofort erkannt werden
    _run_git(["fetch", "--tags"])

    if rc != 0:
        return {"success": False, "message": "git pull fehlgeschlagen", "output": output}

    # Dateien erzwingen (verhindert stehende Dateien nach Pull durch Rechte- oder Checkout-Probleme)
    _, _, rc_reset = _run_git(["reset", "--hard", "HEAD"])
    if rc_reset != 0:
        return {"success": False, "message": "git reset fehlgeschlagen", "output": output}

    new_version = get_current_version()

    result = {
        "success": True,
        "message": f"Update erfolgreich. Neue Version: {new_version}. Neustart...",
        "output": output,
        "new_version": new_version,
    }

    # Vollständiger System-Rebuild: Frontend + Backend
    import threading, subprocess
    def _get_host_project_dir() -> str:
        """Ermittelt den echten Host-Pfad für das gemountete Repo."""
        result = subprocess.run(
            ["docker", "inspect", "temp-backend", "--format",
             "{{range .Mounts}}{{if eq .Destination \"/project\"}}{{.Source}}{{end}}{{end}}"],
            capture_output=True, text=True, timeout=10,
        )
        host_dir = result.stdout.strip()
        return host_dir if host_dir else "/home/server2/temperatursensor-server"

    def _rebuild_system():
        import time, os as osmod
        time.sleep(2)
        host_dir = _get_host_project_dir()
        proc = subprocess.Popen(
            [
                "docker", "run", "--rm",
                "-v", "/var/run/docker.sock:/var/run/docker.sock:rw",
                "-v", f"{host_dir}:{host_dir}",
                "-w", host_dir,
                "-e", "COMPOSE_PROJECT_NAME=temperatursensor-server",
                "temperatursensor-server-backend",
                "bash", "-c", "docker compose down && docker compose up --build -d",
            ],
        )
        proc.wait()
        osmod._exit(0)

    if os.getenv("PYTEST_CURRENT_TEST") is None:
        threading.Thread(target=_rebuild_system, daemon=True).start()

    return result


def check_setup() -> dict:
    """Prüft, ob das Setup korrekt ist (ADMIN_PASSWORD gesetzt, Git-Repo vorhanden).

    Gibt zurück:
    - is_configured: bool
    - missing: list[str] (was fehlt)
    """
    missing = []
    if not get_admin_password():
        missing.append("ADMIN_PASSWORD")

    # Git-Repo vorhanden?
    _, _, rc = _run_git(["rev-parse", "--show-toplevel"])
    if rc != 0:
        missing.append("git_repo")

    return {
        "is_configured": len(missing) == 0,
        "missing": missing,
    }