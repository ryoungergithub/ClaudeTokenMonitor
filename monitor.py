#!/usr/bin/env python3
"""Claude Code Token Usage Monitor.

Queries the Anthropic OAuth usage API for real-time weekly utilization
percentages, projects usage to the reset time, and sends email alerts
when projected usage exceeds configured thresholds.

Designed to run via cron every 30 minutes.
"""

import argparse
import datetime
import json
import logging
import os
import pathlib
import smtplib
import subprocess
import sys
import urllib.request
import urllib.error
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from logging.handlers import RotatingFileHandler

SCRIPT_DIR = pathlib.Path(__file__).parent.resolve()
ALERT_STATE_FILE = SCRIPT_DIR / "alert_state.json"
LOG_FILE = SCRIPT_DIR / "monitor.log"
ENV_FILE = SCRIPT_DIR / ".env"

REQUIRED_CONFIG_KEYS = [
    "SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "ALERT_RECIPIENT",
    "WARNING_THRESHOLD", "ALARM_THRESHOLD", "ALERT_COOLDOWN_HOURS",
]

USAGE_API_URL = "https://api.anthropic.com/api/oauth/usage"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(env_path: pathlib.Path) -> dict:
    """Parse .env file into a dict. Skips comments and blank lines."""
    config = {}
    if not env_path.exists():
        raise FileNotFoundError(f"Config file not found: {env_path}")

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")

    # Cast numeric values
    config["SMTP_PORT"] = int(config["SMTP_PORT"])
    config["WARNING_THRESHOLD"] = float(config["WARNING_THRESHOLD"])
    config["ALARM_THRESHOLD"] = float(config["ALARM_THRESHOLD"])
    config["ALERT_COOLDOWN_HOURS"] = float(config["ALERT_COOLDOWN_HOURS"])

    return config


# ---------------------------------------------------------------------------
# OAuth token retrieval
# ---------------------------------------------------------------------------

def get_oauth_token() -> str:
    """Retrieve the Claude Code OAuth token from the macOS Keychain.

    Tries the login keychain explicitly first (needed for cron/non-interactive),
    then falls back to the default search.
    """
    home = os.path.expanduser("~")
    login_keychain = os.path.join(home, "Library", "Keychains", "login.keychain-db")

    # Try with explicit keychain path first (works in cron), then without
    commands = []
    if os.path.exists(login_keychain):
        commands.append([
            "security", "find-generic-password",
            "-s", "Claude Code-credentials",
            "-w", login_keychain,
        ])
    commands.append([
        "security", "find-generic-password",
        "-s", "Claude Code-credentials",
        "-a", os.environ.get("USER", ""),
        "-w",
    ])

    last_error = ""
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                creds = json.loads(result.stdout.strip())
                token = creds.get("claudeAiOauth", {}).get("accessToken")
                if token:
                    return token
                last_error = "No accessToken found in Keychain credentials"
            else:
                last_error = result.stderr.strip()
        except json.JSONDecodeError as e:
            last_error = f"Failed to parse Keychain credentials: {e}"

    raise RuntimeError(f"Keychain lookup failed: {last_error}")


# ---------------------------------------------------------------------------
# Usage API
# ---------------------------------------------------------------------------

def fetch_usage(token: str) -> dict:
    """Call the Anthropic OAuth usage API and return the response."""
    req = urllib.request.Request(
        USAGE_API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"Usage API returned HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Usage API connection error: {e.reason}")


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def parse_reset_time(iso_str: str) -> datetime.datetime:
    """Parse an ISO 8601 reset timestamp into a local datetime.

    Rounds to the nearest minute to avoid spurious state resets caused by
    the API returning slightly different microsecond values on each call.
    """
    # The API returns timezone-aware strings like "2026-02-27T13:59:59.850201+00:00"
    dt = datetime.datetime.fromisoformat(iso_str)
    # Convert to local time, strip timezone, round to nearest minute
    local = dt.astimezone(tz=None).replace(tzinfo=None, second=0, microsecond=0)
    return local


def calculate_projection(
    current_pct: float,
    resets_at: datetime.datetime,
    now: datetime.datetime,
    window_hours: float = 168.0,  # 7 days
) -> dict:
    """Project usage percentage at reset time based on current burn rate.

    The key insight: if you've used current_pct% with X hours remaining out of
    a Y-hour window, then elapsed = Y - X hours, and the burn rate is
    current_pct / elapsed. Projected = burn_rate * Y.
    """
    remaining = resets_at - now
    remaining_hours = remaining.total_seconds() / 3600
    elapsed_hours = window_hours - remaining_hours

    if elapsed_hours <= 0:
        return {
            "current_pct": current_pct,
            "projected_pct": current_pct,
            "burn_rate_pct_per_hour": 0.0,
            "elapsed_hours": 0.0,
            "remaining_hours": remaining_hours,
            "percent_elapsed": 0.0,
            "resets_at": resets_at,
        }

    burn_rate = current_pct / elapsed_hours  # % per hour
    projected_pct = current_pct + burn_rate * remaining_hours

    return {
        "current_pct": current_pct,
        "projected_pct": projected_pct,
        "burn_rate_pct_per_hour": burn_rate,
        "elapsed_hours": elapsed_hours,
        "remaining_hours": remaining_hours,
        "percent_elapsed": (elapsed_hours / window_hours) * 100,
        "resets_at": resets_at,
    }


def determine_alert_level(
    projected_pct: float, warn_threshold: float, alarm_threshold: float
) -> str:
    if projected_pct >= alarm_threshold:
        return "ALARM"
    elif projected_pct >= warn_threshold:
        return "WARNING"
    return "OK"


# ---------------------------------------------------------------------------
# Alert state
# ---------------------------------------------------------------------------

def load_alert_state() -> dict:
    if ALERT_STATE_FILE.exists():
        with open(ALERT_STATE_FILE) as f:
            return json.load(f)
    return {
        "last_alert_level": "OK",
        "last_alert_time": None,
        "last_warning_time": None,
        "last_alarm_time": None,
        "last_resets_at": None,
        "last_check_time": None,
    }


def save_alert_state(state: dict):
    with open(ALERT_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


LEVEL_SEVERITY = {"OK": 0, "WARNING": 1, "ALARM": 2}


def should_send_alert(
    current_level: str,
    state: dict,
    cooldown_hours: float,
    now: datetime.datetime,
    resets_at_str: str,
) -> bool:
    if current_level == "OK":
        return False

    # New reset window → always alert if level warrants it
    if state.get("last_resets_at") != resets_at_str:
        return True

    prev_level = state.get("last_alert_level", "OK")
    prev_severity = LEVEL_SEVERITY.get(prev_level, 0)
    curr_severity = LEVEL_SEVERITY[current_level]

    # Escalation
    if curr_severity > prev_severity:
        return True

    # Same level → check cooldown
    if curr_severity == prev_severity and curr_severity > 0:
        time_key = "last_alarm_time" if current_level == "ALARM" else "last_warning_time"
        last_time_str = state.get(time_key)
        if last_time_str is None:
            return True
        last_time = datetime.datetime.fromisoformat(last_time_str)
        if (now - last_time).total_seconds() >= cooldown_hours * 3600:
            return True

    return False


# ---------------------------------------------------------------------------
# Email formatting
# ---------------------------------------------------------------------------

def fmt_pct(n: float) -> str:
    return f"{n:.1f}%"


def fmt_hours(h: float) -> str:
    if h >= 24:
        days = h / 24
        return f"{days:.1f} days"
    return f"{h:.1f} hours"


def format_plain_text(level: str, usage: dict, seven_day: dict, five_hour: dict, config: dict) -> str:
    now_str = datetime.datetime.now().strftime("%A, %b %d, %Y at %I:%M %p")
    reset_str = seven_day["resets_at"].strftime("%A, %b %d at %I:%M %p")

    lines = [
        "Claude Code Usage Alert",
        "=" * 40,
        f"Level: {level}",
        f"Time:  {now_str}",
        "",
        "7-Day Usage Window",
        "-" * 30,
        f"  Current usage:       {fmt_pct(seven_day['current_pct'])}",
        f"  Burn rate:           {seven_day['burn_rate_pct_per_hour']:.2f}% per hour",
        f"  Projected at reset:  {fmt_pct(seven_day['projected_pct'])}",
        f"  Time elapsed:        {fmt_hours(seven_day['elapsed_hours'])} ({fmt_pct(seven_day['percent_elapsed'])} of window)",
        f"  Time remaining:      {fmt_hours(seven_day['remaining_hours'])}",
        f"  Resets at:           {reset_str}",
        "",
    ]

    if five_hour["current_pct"] is not None:
        five_reset_str = five_hour["resets_at"].strftime("%I:%M %p") if five_hour["resets_at"] else "N/A"
        lines.extend([
            "5-Hour Burst Window",
            "-" * 30,
            f"  Current usage:       {fmt_pct(five_hour['current_pct'])}",
            f"  Resets at:           {five_reset_str}",
            "",
        ])

    # Sonnet-specific if available
    sonnet = usage.get("seven_day_sonnet")
    if sonnet and sonnet.get("utilization") is not None:
        lines.extend([
            "7-Day Sonnet Window",
            "-" * 30,
            f"  Current usage:       {fmt_pct(sonnet['utilization'])}",
            "",
        ])

    if level == "WARNING":
        lines.extend([
            "--- WARNING ---",
            f"At the current burn rate, you are projected to reach "
            f"{fmt_pct(seven_day['projected_pct'])} usage by reset time.",
            "Consider slowing down to preserve capacity for the rest of the week.",
        ])
    elif level == "ALARM":
        lines.extend([
            "*** ALARM ***",
            f"At the current burn rate, you are projected to reach "
            f"{fmt_pct(seven_day['projected_pct'])} usage by reset time!",
            "You are at serious risk of hitting your weekly limit.",
            "Reduce or pause usage immediately if you need capacity later.",
        ])

    return "\n".join(lines)


def format_html(level: str, usage: dict, seven_day: dict, five_hour: dict, config: dict) -> str:
    now_str = datetime.datetime.now().strftime("%A, %b %d, %Y at %I:%M %p")
    reset_str = seven_day["resets_at"].strftime("%A, %b %d at %I:%M %p")

    color = "#e67e22" if level == "WARNING" else "#e74c3c"
    bg_color = "#fdf2e9" if level == "WARNING" else "#fdedec"

    # Progress bar colors
    bar_color = "#e67e22" if seven_day["current_pct"] >= 50 else "#3498db"
    if seven_day["current_pct"] >= 75:
        bar_color = "#e74c3c"

    pct_bar = min(seven_day["current_pct"], 100)

    # Action text
    if level == "WARNING":
        action_text = (
            f"At the current burn rate, you are projected to reach "
            f"<strong>{fmt_pct(seven_day['projected_pct'])}</strong> usage by reset time. "
            f"Consider slowing down to preserve capacity."
        )
    else:
        action_text = (
            f"At the current burn rate, you are projected to reach "
            f"<strong>{fmt_pct(seven_day['projected_pct'])}</strong> usage by reset time! "
            f"Reduce or pause usage immediately."
        )

    # 5-hour section
    five_hour_html = ""
    if five_hour["current_pct"] is not None:
        five_bar = min(five_hour["current_pct"], 100)
        five_color = "#3498db" if five_hour["current_pct"] < 50 else "#e67e22"
        if five_hour["current_pct"] >= 75:
            five_color = "#e74c3c"
        five_reset = five_hour["resets_at"].strftime("%I:%M %p") if five_hour["resets_at"] else "N/A"
        five_hour_html = f"""
  <h3 style="margin:20px 0 8px;color:#555">5-Hour Burst Window</h3>
  <div style="background:#f0f0f0;border-radius:8px;height:16px;margin:8px 0;overflow:hidden">
    <div style="background:{five_color};height:100%;width:{five_bar}%"></div>
  </div>
  <table style="width:100%;border-collapse:collapse;margin:8px 0">
    <tr><td style="padding:4px 0;color:#666">Current usage</td>
        <td style="padding:4px 0;text-align:right;font-weight:bold">{fmt_pct(five_hour['current_pct'])}</td></tr>
    <tr><td style="padding:4px 0;color:#666">Resets at</td>
        <td style="padding:4px 0;text-align:right">{five_reset}</td></tr>
  </table>"""

    # Sonnet section
    sonnet_html = ""
    sonnet = usage.get("seven_day_sonnet")
    if sonnet and sonnet.get("utilization") is not None:
        s_bar = min(sonnet["utilization"], 100)
        s_color = "#3498db" if sonnet["utilization"] < 50 else "#e67e22"
        sonnet_html = f"""
  <h3 style="margin:20px 0 8px;color:#555">7-Day Sonnet Window</h3>
  <div style="background:#f0f0f0;border-radius:8px;height:16px;margin:8px 0;overflow:hidden">
    <div style="background:{s_color};height:100%;width:{s_bar}%"></div>
  </div>
  <p style="margin:4px 0;"><strong>{fmt_pct(sonnet['utilization'])}</strong> used</p>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333">
  <div style="background:{bg_color};border-left:4px solid {color};padding:16px;margin-bottom:20px">
    <h2 style="margin:0;color:{color}">{level}: Claude Usage Alert</h2>
    <p style="margin:8px 0 0;color:#666">{now_str}</p>
  </div>

  <h3 style="margin:0 0 8px;color:#555">7-Day Usage Window</h3>
  <div style="background:#f0f0f0;border-radius:8px;height:24px;margin:8px 0;overflow:hidden;position:relative">
    <div style="background:{bar_color};height:100%;width:{pct_bar}%"></div>
  </div>

  <table style="width:100%;border-collapse:collapse;margin:16px 0">
    <tr style="border-bottom:1px solid #ddd">
      <td style="padding:8px 0;color:#666">Current usage</td>
      <td style="padding:8px 0;text-align:right;font-weight:bold;font-size:1.2em">{fmt_pct(seven_day['current_pct'])}</td>
    </tr>
    <tr style="border-bottom:1px solid #ddd">
      <td style="padding:8px 0;color:#666">Burn rate</td>
      <td style="padding:8px 0;text-align:right">{seven_day['burn_rate_pct_per_hour']:.2f}% per hour</td>
    </tr>
    <tr style="border-bottom:1px solid #ddd">
      <td style="padding:8px 0;color:#666">Projected at reset</td>
      <td style="padding:8px 0;text-align:right;font-weight:bold;color:{color};font-size:1.2em">{fmt_pct(seven_day['projected_pct'])}</td>
    </tr>
    <tr style="border-bottom:1px solid #ddd">
      <td style="padding:8px 0;color:#666">Time remaining</td>
      <td style="padding:8px 0;text-align:right">{fmt_hours(seven_day['remaining_hours'])}</td>
    </tr>
    <tr>
      <td style="padding:8px 0;color:#666">Resets at</td>
      <td style="padding:8px 0;text-align:right">{reset_str}</td>
    </tr>
  </table>

  {five_hour_html}
  {sonnet_html}

  <div style="background:{bg_color};border-left:4px solid {color};padding:12px;margin-top:20px">
    <p style="margin:0">{action_text}</p>
  </div>

  <p style="margin-top:24px;font-size:0.8em;color:#999">
    Sent by Claude Token Monitor | Resets {reset_str}
  </p>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_alert(
    config: dict, level: str, usage: dict, seven_day: dict, five_hour: dict,
):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[{level}] Claude Usage: {fmt_pct(seven_day['current_pct'])} used, projected {fmt_pct(seven_day['projected_pct'])} by reset"
    msg["From"] = config["SMTP_USER"]
    msg["To"] = config["ALERT_RECIPIENT"]

    plain = format_plain_text(level, usage, seven_day, five_hour, config)
    html = format_html(level, usage, seven_day, five_hour, config)

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(config["SMTP_SERVER"], config["SMTP_PORT"]) as server:
        server.starttls()
        server.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        server.sendmail(config["SMTP_USER"], config["ALERT_RECIPIENT"], msg.as_string())


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("token_monitor")
    logger.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_048_576, backupCount=3, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(file_handler)

    if verbose:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logger.addHandler(console_handler)

    return logger


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude Code Token Usage Monitor")
    parser.add_argument(
        "--test-email", action="store_true",
        help="Send a test alert email regardless of thresholds and exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run calculations and log results, but do not send email or update state",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print log output to stdout in addition to the log file",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    logger = setup_logging(verbose=args.verbose)
    now = datetime.datetime.now()

    logger.info("=" * 50)
    logger.info("Token monitor run started")

    # Load config
    try:
        config = load_config(ENV_FILE)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Get OAuth token from Keychain
    try:
        oauth_token = get_oauth_token()
        logger.info("OAuth token retrieved from Keychain")
    except RuntimeError as e:
        logger.error(f"Failed to get OAuth token: {e}")
        sys.exit(1)

    # Fetch real usage data from Anthropic API
    try:
        usage = fetch_usage(oauth_token)
        logger.info("Usage data fetched from Anthropic API")
    except RuntimeError as e:
        logger.error(f"Failed to fetch usage data: {e}")
        sys.exit(1)

    # Parse 7-day window
    seven_day_raw = usage.get("seven_day", {})
    seven_day_pct = seven_day_raw.get("utilization", 0) or 0
    seven_day_resets = parse_reset_time(seven_day_raw["resets_at"])
    logger.info(f"7-day usage: {fmt_pct(seven_day_pct)}")
    logger.info(f"7-day resets at: {seven_day_resets.strftime('%Y-%m-%d %I:%M %p')}")

    # Project 7-day usage to reset
    seven_day = calculate_projection(seven_day_pct, seven_day_resets, now)
    logger.info(f"7-day burn rate: {seven_day['burn_rate_pct_per_hour']:.2f}%/hr")
    logger.info(f"7-day projected at reset: {fmt_pct(seven_day['projected_pct'])}")
    logger.info(f"7-day window: {fmt_pct(seven_day['percent_elapsed'])} elapsed, "
                f"{fmt_hours(seven_day['remaining_hours'])} remaining")

    # Parse 5-hour window
    five_hour_raw = usage.get("five_hour", {})
    five_hour_pct = five_hour_raw.get("utilization")
    five_hour_resets = None
    if five_hour_raw.get("resets_at"):
        five_hour_resets = parse_reset_time(five_hour_raw["resets_at"])
    five_hour = {
        "current_pct": five_hour_pct,
        "resets_at": five_hour_resets,
    }
    if five_hour_pct is not None:
        logger.info(f"5-hour burst usage: {fmt_pct(five_hour_pct)}")

    # Log sonnet-specific if available
    sonnet = usage.get("seven_day_sonnet")
    if sonnet and sonnet.get("utilization") is not None:
        logger.info(f"7-day Sonnet usage: {fmt_pct(sonnet['utilization'])}")

    # Log extra usage info if available
    extra = usage.get("extra_usage")
    if extra:
        logger.info(f"Extra usage: enabled={extra.get('is_enabled')}, "
                     f"used={extra.get('used_credits', 0)}/{extra.get('monthly_limit', 'N/A')}")

    # Determine alert level based on projected 7-day usage
    alert_level = determine_alert_level(
        seven_day["projected_pct"],
        config["WARNING_THRESHOLD"],
        config["ALARM_THRESHOLD"],
    )
    logger.info(f"Alert level: {alert_level}")

    # Handle --test-email
    if args.test_email:
        test_level = "WARNING"
        logger.info("Sending test email (--test-email mode)")
        try:
            send_alert(config, test_level, usage, seven_day, five_hour)
            logger.info("Test email sent successfully")
            print("Test email sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send test email: {e}")
            print(f"Failed to send test email: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Load alert state
    state = load_alert_state()
    resets_at_str = seven_day_resets.isoformat()

    # Check for new reset window
    if state.get("last_resets_at") != resets_at_str:
        logger.info("New reset window detected — resetting alert state")
        state = {
            "last_alert_level": "OK",
            "last_alert_time": None,
            "last_warning_time": None,
            "last_alarm_time": None,
            "last_resets_at": resets_at_str,
            "last_check_time": None,
        }

    # Decide whether to send alert
    send = should_send_alert(
        alert_level, state, config["ALERT_COOLDOWN_HOURS"], now, resets_at_str,
    )

    if send and not args.dry_run:
        logger.info(f"Sending {alert_level} alert email")
        try:
            send_alert(config, alert_level, usage, seven_day, five_hour)
            logger.info("Alert email sent successfully")
            state["last_alert_level"] = alert_level
            state["last_alert_time"] = now.isoformat()
            if alert_level == "WARNING":
                state["last_warning_time"] = now.isoformat()
            elif alert_level == "ALARM":
                state["last_alarm_time"] = now.isoformat()
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")
    elif send and args.dry_run:
        logger.info(f"DRY RUN: Would send {alert_level} alert email")
    else:
        logger.info("No alert email needed")

    # Update state (unless dry run)
    if not args.dry_run:
        state["last_resets_at"] = resets_at_str
        state["last_check_time"] = now.isoformat()
        save_alert_state(state)

    logger.info("Token monitor run completed")


if __name__ == "__main__":
    main()
