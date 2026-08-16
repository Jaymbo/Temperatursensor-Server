#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  setup-server.sh  —  Temperature-Monitoring Server Setup
#
#  Kopiere dieses Skript per USB/SCP auf den Zielsystem (z.B. Raspberry Pi)
#  und führe es aus:  bash setup-server.sh
#
#  Es überprüft alle Voraussetzungen, klonst das Deploy-Repo und
#  startet den Server mit Docker Compose.
# ═══════════════════════════════════════════════════════════════
set -euo pipefail

# ── Konfiguration ─────────────────────────────────────────────
REPO_URL="https://github.com/Jaymbo/Temperatursensor-Server.git"
DEPLOY_DIR="$HOME/temperatursensor-server"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.yml"
ENV_FILE="$DEPLOY_DIR/.env"

# ── Farben ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ── Abhängigkeiten prüfen ─────────────────────────────────────

check_command() {
    if command -v "$1" &>/dev/null; then
        info "$1 ist installiert ($(command -v "$1"))"
        return 0
    else
        return 1
    fi
}

echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Temperatur-Monitoring Server — Setup                  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── Git ───────────────────────────────────────────────────────
if check_command git; then
    :
else
    error "Git ist nicht installiert."
    echo ""
    echo "Installation (Debian/Ubuntu/Raspberry Pi OS):"
    echo "  sudo apt update && sudo apt install -y git"
    echo ""
    echo "Installation (Alpine Linux):"
    echo "  apk add git"
    echo ""
    exit 1
fi

# ── Docker ────────────────────────────────────────────────────
if check_command docker; then
    :
else
    error "Docker ist nicht installiert."
    echo ""
    echo "Installation (Debian/Ubuntu/Raspberry Pi OS):"
    echo "  curl -fsSL https://get.docker.com | sudo sh"
    echo "  sudo usermod -aG docker \$USER"
    echo "  # Nach Installation: Sitzung neu starten oder 'newgrp docker' ausführen"
    echo ""
    exit 1
fi

# ── Docker Compose ────────────────────────────────────────────
if docker compose version &>/dev/null; then
    info "Docker Compose Plugin ist verfügbar"
elif check_command docker-compose; then
    info "Standalone docker-compose ist installiert"
else
    error "Docker Compose ist nicht installiert."
    echo ""
    echo "Installation:"
    echo "  sudo apt install -y docker-compose-plugin"
    echo ""
    exit 1
fi

# ── Repo klonen ───────────────────────────────────────────────
if [ -d "$DEPLOY_DIR/.git" ]; then
    info "Repo existiert bereits: $DEPLOY_DIR"
    read -rp "Soll das Repository aktualisiert werden? (y/N): " -r ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        cd "$DEPLOY_DIR"
        git pull
        info "Repository aktualisiert."
    fi
else
    if [ -d "$DEPLOY_DIR" ]; then
        warn "Verzeichnis $DEPLOY_DIR existiert bereits, ist aber kein Git-Repo."
        read -rp "Verzeichnis löschen und neu klonen? (y/N): " -r ans
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            rm -rf "$DEPLOY_DIR"
        else
            error "Abgebrochen. Bitte lösche $DEPLOY_DIR manuell."
            exit 1
        fi
    fi

    info "Klone Repository..."
    git clone "$REPO_URL" "$DEPLOY_DIR"
    info "Fertig."
fi

cd "$DEPLOY_DIR"

# ── .env erstellen ────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo ""
    info "Erstelle .env Konfigurationsdatei..."
    echo "-----------------------------------------------------------"
    echo "Gib ein Passwort ein, das für folgende Aktionen erforderlich ist:"
    echo "  - Update-Check im Frontend"
    echo "  - Update-Installation"
    echo "  - Kalibrierungsmodus"
    echo "-----------------------------------------------------------"

    read -rs -p "Admin-Passwort: " ADMIN_PW
    echo ""
    read -rs -p "Passwort wiederholen: " ADMIN_PW2
    echo ""

    if [ "$ADMIN_PW" != "$ADMIN_PW2" ]; then
        error "Passwörter stimmen nicht überein!"
        exit 1
    fi

    if [ ${#ADMIN_PW} -lt 4 ]; then
        error "Passwort muss mindestens 4 Zeichen lang sein!"
        exit 1
    fi

    cat > "$ENV_FILE" <<EOF
# Admin-Passwort (Update, Kalibrierung)
ADMIN_PASSWORD=$ADMIN_PW

# Deploy-Repo (öffentlich → kein Token nötig)
GITHUB_OWNER=Jaymbo
GITHUB_REPO=Temperatursensor-Server
EOF

    chmod 600 "$ENV_FILE"
    info ".env erstellt (Berechtigungen: 600)."
else
    info ".env existiert bereits."
    read -rp ".env überschreiben? (y/N): " -r ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm "$ENV_FILE"
        warn ".env gelöscht. Neustart dieses Skripts zum Erneut-Erstellen."
        exit 0
    fi
fi

# ── Datenbank ─────────────────────────────────────────────────
# database.db wird nicht versioniert und beim ersten Start erstellt.
if [ -f "$DEPLOY_DIR/backend/database.db" ]; then
    warn "Existierende Datenbank gefunden: backend/database.db"
    read -rp "Bestehende Datenbank löschen (alle Messdaten verloren)? (y/N): " -r ans
    if [[ "$ans" =~ ^[Yy]$ ]]; then
        rm "$DEPLOY_DIR/backend/database.db"
        info "Datenbank gelöscht."
    else
        info "Datenbank beibehalten."
    fi
fi

# ── Docker Compose starten ───────────────────────────────────
echo ""
info "Baue und starte Container..."

# Prüfe ob .env im compose context liegt
if [ ! -f "$ENV_FILE" ]; then
    error ".env nicht gefunden in $DEPLOY_DIR"
    exit 1
fi

docker compose up --build -d

# ── Status ───────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  Server gestartet!                                     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
info "Frontend:  http://$(hostname -I | awk '{print $1}'):5173"
info "Backend:   http://$(hostname -I | awk '{print $1}'):8000"
info "Logs:      docker compose logs -f"
echo ""
warn "Beim ersten Zugriff musst du im Frontend das Admin-Passwort eingeben."
echo ""
echo "╚══════════════════════════════════════════════════════════╝"