#!/bin/bash
set -e

# Claude Token Monitor — Installer
# Monitors your Claude weekly usage and shows projected usage in the macOS menu bar.

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_step() { echo -e "\n${BOLD}${GREEN}==>${NC} ${BOLD}$1${NC}"; }
print_warn() { echo -e "${YELLOW}Warning:${NC} $1"; }
print_err()  { echo -e "${RED}Error:${NC} $1"; }

# Install to wherever the script lives (the git clone directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$SCRIPT_DIR"

print_step "Claude Token Monitor — Installer"
echo "This will set up the menu bar usage monitor and optional email alerts."
echo ""

# --- Check prerequisites ---

print_step "Checking prerequisites..."

# Homebrew
if ! command -v brew &>/dev/null; then
    print_err "Homebrew is required. Install it from https://brew.sh"
    exit 1
fi
echo "  Homebrew: OK"

# Python 3 — find the right path for this machine
PYTHON=""
if command -v /opt/homebrew/bin/python3 &>/dev/null; then
    PYTHON="/opt/homebrew/bin/python3"
elif command -v /usr/local/bin/python3 &>/dev/null; then
    PYTHON="/usr/local/bin/python3"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    print_step "Installing Python 3..."
    brew install python3
    # Re-detect after install
    if command -v /opt/homebrew/bin/python3 &>/dev/null; then
        PYTHON="/opt/homebrew/bin/python3"
    elif command -v /usr/local/bin/python3 &>/dev/null; then
        PYTHON="/usr/local/bin/python3"
    else
        PYTHON="$(command -v python3)"
    fi
fi
echo "  Python 3: OK ($PYTHON)"

# matplotlib
if ! "$PYTHON" -c "import matplotlib" &>/dev/null; then
    print_step "Installing matplotlib..."
    "$PYTHON" -m pip install matplotlib --break-system-packages -q 2>/dev/null || \
    "$PYTHON" -m pip install matplotlib -q 2>/dev/null || \
    brew install python-matplotlib
fi
echo "  matplotlib: OK"

# SwiftBar
if [ ! -d "/Applications/SwiftBar.app" ]; then
    print_step "Installing SwiftBar..."
    brew install --cask swiftbar
fi
echo "  SwiftBar: OK"

# Claude Code OAuth token
if ! security find-generic-password -s "Claude Code-credentials" -w &>/dev/null 2>&1 && \
   ! security find-generic-password -s "Claude Code-credentials" -w "$HOME/Library/Keychains/login.keychain-db" &>/dev/null 2>&1; then
    print_warn "No Claude Code OAuth token found in Keychain."
    echo "  You must log in to Claude Code at least once before the monitor will work."
    echo "  Run: claude (and complete the login flow)"
    echo ""
fi

# --- Install files ---

print_step "Installing to: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# Copy core files if installing from a different location
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    cp "$SCRIPT_DIR/monitor.py" "$INSTALL_DIR/monitor.py"
    cp "$SCRIPT_DIR/claude-usage.30m.py" "$INSTALL_DIR/claude-usage.30m.py"
    cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/.env.example"
fi

# Verify required files exist
for f in monitor.py claude-usage.30m.py .env.example; do
    if [ ! -f "$INSTALL_DIR/$f" ]; then
        print_err "Missing file: $INSTALL_DIR/$f"
        print_err "Make sure you cloned the full repo."
        exit 1
    fi
done

# Patch the shebang in the plugin to use the detected Python path
sed -i '' "1s|.*|#!${PYTHON}|" "$INSTALL_DIR/claude-usage.30m.py"
chmod +x "$INSTALL_DIR/claude-usage.30m.py"

echo "  Files installed (using $PYTHON)"

# --- Configure .env ---

if [ ! -f "$INSTALL_DIR/.env" ]; then
    print_step "Setting up configuration..."

    # Copy template
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"

    echo ""
    echo "  Do you want to set up email alerts? (optional)"
    read -p "  Set up email alerts? [y/N]: " setup_email

    if [[ "$setup_email" =~ ^[Yy] ]]; then
        read -p "  Gmail address: " gmail_user
        read -s -p "  Gmail App Password (hidden): " gmail_pass
        echo ""
        read -p "  Send alerts to (default: $gmail_user): " alert_to
        alert_to="${alert_to:-$gmail_user}"

        # Update .env
        sed -i '' "s|SMTP_USER=.*|SMTP_USER=$gmail_user|" "$INSTALL_DIR/.env"
        sed -i '' "s|SMTP_PASSWORD=.*|SMTP_PASSWORD=$gmail_pass|" "$INSTALL_DIR/.env"
        sed -i '' "s|ALERT_RECIPIENT=.*|ALERT_RECIPIENT=$alert_to|" "$INSTALL_DIR/.env"
        echo "  Email alerts configured."
    else
        echo "  Skipped. You can edit $INSTALL_DIR/.env later to add email alerts."
    fi
else
    echo "  .env already exists — keeping your current configuration."
fi

# --- Set up SwiftBar plugin ---

print_step "Configuring SwiftBar plugin..."

# SwiftBar plugin dir must be SEPARATE from install dir,
# otherwise SwiftBar tries to run install.sh and monitor.py as plugins.
SWIFTBAR_DIR=$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || echo "")

# If SwiftBar's plugin dir is the install dir itself, or not set, use a dedicated folder
if [ -z "$SWIFTBAR_DIR" ] || [ "$SWIFTBAR_DIR" = "$INSTALL_DIR" ]; then
    SWIFTBAR_DIR="$HOME/SwiftBarPlugins"
    # Update SwiftBar's preference to point to the correct directory
    defaults write com.ameba.SwiftBar PluginDirectory -string "$SWIFTBAR_DIR"
    echo "  Set SwiftBar plugin directory to: $SWIFTBAR_DIR"
fi

mkdir -p "$SWIFTBAR_DIR"
ln -sf "$INSTALL_DIR/claude-usage.30m.py" "$SWIFTBAR_DIR/claude-usage.30m.py"

# Remove install.sh symlink/copy from plugin dir if it ended up there
rm -f "$SWIFTBAR_DIR/install.sh" 2>/dev/null
rm -f "$SWIFTBAR_DIR/monitor.py" 2>/dev/null

echo "  Plugin symlinked to: $SWIFTBAR_DIR"

# --- Set up cron job for email alerts ---

print_step "Setting up email alert schedule (cron)..."

CRON_LINE="*/30 * * * * $PYTHON \"$INSTALL_DIR/monitor.py\" >> \"$INSTALL_DIR/cron.log\" 2>&1"

if crontab -l 2>/dev/null | grep -q "monitor.py"; then
    echo "  Cron job already exists — skipping."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "  Cron job installed (runs every 30 minutes)."
fi

# --- Launch SwiftBar ---

print_step "Launching SwiftBar..."
# Quit first if running so it picks up the new plugin directory
osascript -e 'tell application "SwiftBar" to quit' 2>/dev/null || true
sleep 1
open -a SwiftBar

# --- Done ---

echo ""
echo -e "${BOLD}${GREEN}Installation complete!${NC}"
echo ""
echo "  You should see your Claude usage in the menu bar."
echo "  It shows:  Now:XX%  Fri:YY%"
echo "    - Now = current 7-day usage"
echo "    - Fri = projected usage at weekly reset"
echo ""
echo "  Colors:"
echo "    - White-green = all good (projected < 90%)"
echo "    - Yellow      = warning  (projected 90-95%)"
echo "    - Red         = alarm    (projected > 95%)"
echo ""
echo "  Click the menu bar item for a detailed chart and stats."
echo ""
echo "  Files installed to: $INSTALL_DIR"
echo "  Edit thresholds:    $INSTALL_DIR/.env"
echo "  View logs:          $INSTALL_DIR/monitor.log"
echo ""
