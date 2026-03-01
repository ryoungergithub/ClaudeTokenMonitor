# Claude Token Monitor

A macOS menu bar app that tracks your Claude weekly usage and alerts you before you run out.

Shows **Now: 13% Fri: 42%** right in your menu bar — current usage and where you'll be by Friday's reset.

---

## Before You Start

You need two things on your Mac before installing:

### 1. Homebrew (the Mac package manager)

Open **Terminal** (search for "Terminal" in Spotlight) and paste this:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

If it says "already installed", you're good. Move on.

### 2. Claude Code (must be logged in)

You need to have logged into Claude Code at least once on this Mac. The monitor reads your login token from the Mac's Keychain.

If you haven't used Claude Code before, install and log in:

```bash
brew install claude-code
claude
```

Complete the login flow when prompted. Once you see the Claude Code prompt, you can quit — the monitor just needs the saved login token.

---

## Installation (3 steps)

Open **Terminal** and run these commands one at a time:

### Step 1: Download

```bash
git clone https://github.com/ryoungergithub/ClaudeTokenMonitor.git ~/ClaudeTokenMonitor
```

### Step 2: Run the installer

```bash
cd ~/ClaudeTokenMonitor && ./install.sh
```

The installer will:
- Install Python 3, matplotlib, and SwiftBar automatically if needed
- Ask if you want email alerts (optional — you can skip this)
- Set up the menu bar plugin
- Launch SwiftBar

### Step 3: Point SwiftBar to the plugin folder

When SwiftBar opens for the first time, it will ask you to **choose a plugin folder**.

**Select this folder:**

```
~/SwiftBarPlugins
```

To navigate there: in the folder picker, press **Cmd+Shift+G**, paste `~/SwiftBarPlugins`, and click **Go**, then click **Open**.

That's it! You should see **Now:XX% Fri:YY%** appear in your menu bar within a few seconds.

---

## What You'll See

### Menu Bar

You'll see something like **Now:13% Fri:42%** in your Mac's top menu bar:

- **Now** = your current 7-day usage percentage
- **Fri** = projected usage at Friday's weekly reset

### Colors

| Color | Meaning |
|-------|---------|
| White-green | All good — projected under 90% |
| Yellow | Warning — projected 90-95% |
| Red | Alarm — projected over 95% |

### Click to Expand

Click the menu bar item to see:
- A trend chart showing your usage over time with a projection line
- Burn rate (how fast you're using your allowance)
- Time remaining until reset
- 5-hour burst usage and Sonnet-specific usage

---

## Email Alerts (Optional)

If you skipped email setup during install, you can add it later.

### Get a Gmail App Password

1. Go to https://myaccount.google.com/apppasswords
2. Sign in to your Google account
3. Create a new app password (name it anything, like "Claude Monitor")
4. Copy the 16-character password it gives you

### Edit the config file

Open Terminal and run:

```bash
open ~/Claude\ Token\ Monitor/.env
```

Fill in your Gmail details:

```ini
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=the-16-char-app-password
ALERT_RECIPIENT=your-email@gmail.com
```

### Test it

```bash
python3 ~/Claude\ Token\ Monitor/monitor.py --test-email --verbose
```

You should receive a test email within a few seconds.

---

## Troubleshooting

### Menu bar shows "C:--" with a warning icon

This means the monitor can't reach the Anthropic API. Most likely:

- You haven't logged into Claude Code on this Mac yet
- Fix: run `claude` in Terminal and complete the login

To verify your login token exists:
```bash
security find-generic-password -s "Claude Code-credentials" -w
```
If this prints a long JSON string, you're logged in. If it says "not found", you need to log into Claude Code first.

### Chart says "Collecting data..."

This is normal on first install. The chart fills in as readings accumulate every 30 minutes. Give it a few hours.

### Nothing appears in the menu bar

1. Make sure SwiftBar is running — look for the SwiftBar icon in your menu bar, or open it from Applications
2. Check that SwiftBar is pointed at the right plugin folder:
   ```bash
   defaults read com.ameba.SwiftBar PluginDirectory
   ```
   This should show a path containing `SwiftBarPlugins`
3. Check that the plugin symlink exists:
   ```bash
   ls -la ~/SwiftBarPlugins/
   ```
   You should see `claude-usage.30m.py` listed

### SwiftBar shows the wrong thing (install script output, etc.)

SwiftBar's plugin folder is pointing at the wrong directory. Fix it:

```bash
osascript -e 'tell application "SwiftBar" to quit'
defaults write com.ameba.SwiftBar PluginDirectory -string "$HOME/SwiftBarPlugins"
mkdir -p ~/SwiftBarPlugins
ln -sf ~/Claude\ Token\ Monitor/claude-usage.30m.py ~/SwiftBarPlugins/claude-usage.30m.py
open -a SwiftBar
```

### Email alerts not sending

- Make sure the App Password is correct (16 characters, no spaces)
- Test with: `python3 ~/Claude\ Token\ Monitor/monitor.py --test-email --verbose`
- Check the log: `cat ~/Claude\ Token\ Monitor/monitor.log | tail -20`

---

## Updating

To get the latest version:

```bash
cd ~/ClaudeTokenMonitor
git pull
./install.sh
```

Your `.env` configuration will be preserved — the installer won't overwrite it.

---

## How It Works

1. Every 30 minutes, the plugin calls Anthropic's usage API using your Claude Code OAuth token (stored in your Mac's Keychain)
2. It gets your actual 7-day utilization percentage — the real number Anthropic uses for rate limiting
3. It calculates: at your current burn rate, what % will you be at by Friday's reset?
4. The menu bar shows both numbers; the dropdown shows a chart with the full trend
5. If email alerts are configured, you get a warning at 90% projected and an alarm at 95%
