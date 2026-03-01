# Claude Token Monitor

A macOS menu bar app that tracks your Claude weekly usage and alerts you before you run out.

Shows four key metrics right in your Mac's menu bar:

**Resets In:3h22m  Usage:10%  Weekly:13%  Predicted:42%**

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

**You MUST select this folder (not the ClaudeTokenMonitor folder):**

```
~/SwiftBarPlugins
```

To navigate there: in the folder picker, press **Cmd+Shift+G**, type `~/SwiftBarPlugins`, click **Go**, then click **Open**.

> **Important:** Do NOT point SwiftBar at `~/ClaudeTokenMonitor`. That folder contains the install script and other files that SwiftBar will try to run as plugins, which will cause errors. The installer already created `~/SwiftBarPlugins` and placed the correct plugin file there.

After a few seconds, you should see the monitor appear in your menu bar.

---

## What You'll See

### Menu Bar

Your Mac's top menu bar will show four values:

**Resets In:3h22m  Usage:10%  Weekly:13%  Predicted:42%**

| Field | What it means |
|-------|--------------|
| **Resets In** | Time until your 5-hour burst window resets |
| **Usage** | Current 5-hour burst window usage % |
| **Weekly** | Current 7-day usage % |
| **Predicted** | Projected 7-day usage at Friday's weekly reset |

### Colors

The text and icon color tells you your status at a glance:

| Color | Meaning |
|-------|---------|
| White-green | All good — predicted usage under 90% |
| Yellow | Warning — predicted usage 90-95% |
| Red | Alarm — predicted usage over 95% |

### Click to Expand

Click the menu bar item to see a detailed dropdown:
- **Trend chart** — your usage over time with a projection line to Friday's reset
- **Burn rate** — how fast you're consuming your allowance (% per hour)
- **Detailed stats** — 7-day, 5-hour, and Sonnet-specific usage breakdowns
- **Reset time** — exactly when your weekly window resets
- **Extra usage credits** — if enabled on your account

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
open ~/ClaudeTokenMonitor/.env
```

Fill in your Gmail details:

```ini
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=the-16-char-app-password
ALERT_RECIPIENT=your-email@gmail.com
```

Save and close the file. The email alerts run automatically via a cron job every 30 minutes.

### Test it

```bash
python3 ~/ClaudeTokenMonitor/monitor.py --test-email --verbose
```

You should receive a test email within a few seconds.

---

## Troubleshooting

### Menu bar shows "C:--" with a warning icon

This means the monitor can't reach the Anthropic API. Most likely you haven't logged into Claude Code on this Mac yet.

**Fix:** Run `claude` in Terminal and complete the login flow.

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
   This should show a path ending in `SwiftBarPlugins`
3. Check that the plugin symlink exists:
   ```bash
   ls -la ~/SwiftBarPlugins/
   ```
   You should see `claude-usage.30m.py` listed

### SwiftBar shows the wrong thing (install script output, raw text, etc.)

This happens when SwiftBar's plugin folder is pointing at the wrong directory (the clone folder instead of the plugins folder). Fix it:

```bash
osascript -e 'tell application "SwiftBar" to quit'
defaults write com.ameba.SwiftBar PluginDirectory -string "$HOME/SwiftBarPlugins"
mkdir -p ~/SwiftBarPlugins
ln -sf ~/ClaudeTokenMonitor/claude-usage.30m.py ~/SwiftBarPlugins/claude-usage.30m.py
open -a SwiftBar
```

### Email alerts not sending

- Make sure the App Password is correct (16 characters, no spaces)
- Test with: `python3 ~/ClaudeTokenMonitor/monitor.py --test-email --verbose`
- Check the log: `tail -20 ~/ClaudeTokenMonitor/monitor.log`

---

## Updating

To get the latest version:

```bash
cd ~/ClaudeTokenMonitor
git stash
git pull
./install.sh
```

Your `.env` configuration will be preserved — the installer won't overwrite it.

---

## How It Works

1. Every 30 minutes, the plugin calls Anthropic's usage API using your Claude Code OAuth token (stored in your Mac's Keychain)
2. It gets your actual utilization percentages — the real numbers Anthropic uses for rate limiting
3. It calculates: at your current burn rate, what % will you be at by Friday's reset?
4. The menu bar shows: 5-hour reset countdown, 5-hour burst usage, weekly usage, and the predicted usage at reset
5. The dropdown shows a trend chart with a projection line so you can see the trajectory
6. If email alerts are configured, you get a warning email at 90% predicted and an alarm at 95%
