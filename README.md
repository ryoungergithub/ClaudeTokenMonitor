# Claude Token Monitor

A macOS menu bar app that tracks your Claude weekly usage and alerts you before you run out.

![Menu Bar](https://img.shields.io/badge/menu_bar-Now%3A13%25%20Fri%3A42%25-brightgreen)

## What it does

- Shows **current usage** and **projected usage at Friday's reset** in your Mac's menu bar
- Click for a **trend chart** with projection line, burn rate, and detailed stats
- **Color-coded**: green when safe, yellow at 90%, red at 95%
- Optional **email alerts** via Gmail when projected usage crosses thresholds
- Updates every 30 minutes automatically

## Requirements

- **macOS** (Apple Silicon or Intel)
- **Claude Code** — must be logged in at least once (the monitor reads your OAuth token from Keychain)
- **Homebrew** — the installer handles everything else

## Quick Install

```bash
git clone <this-repo> ~/Claude\ Token\ Monitor
cd ~/Claude\ Token\ Monitor
./install.sh
```

That's it. The installer will:

1. Install Python 3, matplotlib, and SwiftBar if needed
2. Ask if you want email alerts (optional)
3. Set up the menu bar plugin
4. Launch SwiftBar

## Manual Install

If you prefer to do it yourself:

```bash
# Install dependencies
brew install python3
brew install --cask swiftbar
pip3 install matplotlib

# Copy files to wherever you like
mkdir -p ~/Claude\ Token\ Monitor
cp monitor.py claude-usage.30m.py .env.example ~/Claude\ Token\ Monitor/
cd ~/Claude\ Token\ Monitor
cp .env.example .env   # edit with your settings

# Make plugin executable and link into SwiftBar
chmod +x claude-usage.30m.py
ln -sf "$PWD/claude-usage.30m.py" ~/SwiftBarPlugins/claude-usage.30m.py

# Launch SwiftBar and point it to ~/SwiftBarPlugins when prompted
open -a SwiftBar
```

## Configuration

Edit `~/Claude Token Monitor/.env`:

```ini
# Email alerts (optional — leave defaults to skip)
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
ALERT_RECIPIENT=your-email@gmail.com

# When to alert (projected usage at reset)
WARNING_THRESHOLD=90    # yellow + email
ALARM_THRESHOLD=95      # red + email

# Hours between repeat emails of the same level
ALERT_COOLDOWN_HOURS=4
```

For Gmail, you need an **App Password** (not your regular password):
https://myaccount.google.com/apppasswords

## Menu Bar Colors

| Projected Usage | Color | Meaning |
|----------------|-------|---------|
| < 90% | White-green | On track |
| 90 – 95% | Yellow | Warning — may run out |
| > 95% | Red | Alarm — likely to hit limit |

## How It Works

1. Every 30 minutes, the plugin calls Anthropic's usage API using your Claude Code OAuth token
2. It gets your actual 7-day utilization percentage (not estimated — the real number)
3. It calculates: if you keep using at this rate, what % will you be at by Friday's reset?
4. The menu bar shows both numbers; the dropdown shows a chart with the trend

## Files

| File | Purpose |
|------|---------|
| `monitor.py` | Core logic: API calls, projection math, email alerts |
| `claude-usage.30m.py` | SwiftBar plugin: menu bar display + chart |
| `.env` | Your configuration (credentials, thresholds) |
| `.env.example` | Template for new installs |
| `install.sh` | One-command installer |
| `usage_history.json` | Auto-generated: readings for the trend chart |
| `alert_state.json` | Auto-generated: email alert state |
| `monitor.log` | Auto-generated: log of email alert checks |

## Troubleshooting

**Menu bar shows "C:--" with a warning icon**
- Make sure you're logged into Claude Code (`claude` in terminal)
- Try: `security find-generic-password -s "Claude Code-credentials" -w`

**Chart says "Collecting data..."**
- Normal on first run. The chart fills in as readings accumulate every 30 minutes.

**Email alerts not sending**
- Check your Gmail App Password is correct
- Test: `python3 ~/Claude\ Token\ Monitor/monitor.py --test-email --verbose`

**Plugin not appearing in menu bar**
- Make sure SwiftBar is running (check Applications)
- Verify the symlink: `ls -la ~/SwiftBarPlugins/` (or wherever SwiftBar's plugin dir is)
- Check: `defaults read com.ameba.SwiftBar PluginDirectory`
