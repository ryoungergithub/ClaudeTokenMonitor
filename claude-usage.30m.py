#!/usr/bin/env python3

# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>false</swiftbar.hideSwiftBar>

"""SwiftBar plugin: Claude Token Usage Monitor.

Shows current 7-day usage % in the menu bar with color coding.
Dropdown displays a trend graph with projection, plus detailed stats.
Refreshes every 30 minutes (per filename convention).
"""

import base64
import datetime
import io
import json
import os
import pathlib
import sys

# Add project directory so we can import from monitor.py
PROJECT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from monitor import (
    get_oauth_token,
    fetch_usage,
    parse_reset_time,
    calculate_projection,
    fmt_pct,
    fmt_hours,
)

HISTORY_FILE = PROJECT_DIR / "usage_history.json"
MAX_HISTORY_DAYS = 14


# ---------------------------------------------------------------------------
# History management
# ---------------------------------------------------------------------------

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return {"version": 1, "readings": []}


def save_history(history: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)


def append_reading(history: dict, now: datetime.datetime, usage: dict, projection: dict):
    """Append a reading, deduplicating within 5 minutes."""
    readings = history["readings"]
    now_str = now.isoformat()

    # Deduplicate: skip if last reading is within 5 minutes
    if readings:
        last_ts = datetime.datetime.fromisoformat(readings[-1]["timestamp"])
        if (now - last_ts).total_seconds() < 300:
            return

    seven_day = usage.get("seven_day", {})
    five_hour = usage.get("five_hour", {})
    sonnet = usage.get("seven_day_sonnet") or {}

    readings.append({
        "timestamp": now_str,
        "seven_day_pct": seven_day.get("utilization") or 0,
        "five_hour_pct": five_hour.get("utilization"),
        "sonnet_pct": sonnet.get("utilization"),
        "resets_at": seven_day.get("resets_at", ""),
        "burn_rate": projection.get("burn_rate_pct_per_hour", 0),
        "projected_pct": projection.get("projected_pct", 0),
    })


def prune_history(history: dict, max_days: int):
    cutoff = datetime.datetime.now() - datetime.timedelta(days=max_days)
    history["readings"] = [
        r for r in history["readings"]
        if datetime.datetime.fromisoformat(r["timestamp"]) > cutoff
    ]


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def severity_color(pct: float) -> str:
    """Return a hex color based on usage severity.

    Thresholds match .env: WARNING=90%, ALARM=95%.
    """
    if pct < 90:
        return "#E8FFF0"  # almost white with green tint — all good
    elif pct < 95:
        return "#FFFF00"  # yellow — warning level
    return "#FF3333"      # red — alarm level


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------

def generate_chart(readings: list, projection: dict, resets_at: datetime.datetime, is_dark: bool) -> str:
    """Generate a usage trend chart and return it as a base64 PNG string."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    now = datetime.datetime.now()
    window_hours = 168.0  # 7 days
    window_start = resets_at - datetime.timedelta(hours=window_hours)

    # Colors
    if is_dark:
        text_color = "#D0D0D0"
        grid_color = "#404040"
        usage_color = "#4FC3F7"
        proj_color = "#FF9800"
        pace_color = "#666666"
        reset_color = "#F44336"
        fill_alpha = 0.15
        annotation_bg = "#333333"
    else:
        text_color = "#333333"
        grid_color = "#DDDDDD"
        usage_color = "#1976D2"
        proj_color = "#E65100"
        pace_color = "#BBBBBB"
        reset_color = "#D32F2F"
        fill_alpha = 0.10
        annotation_bg = "#F5F5F5"

    fig, ax = plt.subplots(figsize=(5.5, 2.8), dpi=144)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")

    # Filter readings to current window (+ a bit before for context)
    display_start = window_start - datetime.timedelta(hours=12)
    times = []
    values = []
    for r in readings:
        ts = datetime.datetime.fromisoformat(r["timestamp"])
        if ts >= display_start:
            times.append(ts)
            values.append(r["seven_day_pct"])

    # Plot historical usage
    if len(times) >= 2:
        ax.plot(times, values, color=usage_color, linewidth=2, label="Usage", zorder=3)
        ax.fill_between(times, values, alpha=fill_alpha, color=usage_color, zorder=2)
    elif len(times) == 1:
        ax.scatter(times, values, color=usage_color, s=40, zorder=3, label="Usage")

    # Plot projection line from now to reset
    current_pct = projection.get("current_pct", 0)
    projected_pct = projection.get("projected_pct", 0)
    proj_display = min(projected_pct, 120)
    ax.plot(
        [now, resets_at], [current_pct, proj_display],
        color=proj_color, linewidth=2, linestyle="--", label=f"Projected {projected_pct:.0f}%", zorder=3,
    )

    # Even pace reference line
    ax.plot(
        [window_start, resets_at], [0, 100],
        color=pace_color, linewidth=1, linestyle=":", alpha=0.5, label="Even pace", zorder=1,
    )

    # Reset marker
    ax.axvline(x=resets_at, color=reset_color, linestyle="--", linewidth=1, alpha=0.7, zorder=1)
    y_top = max(105, proj_display + 5)
    ax.text(
        resets_at, y_top - 3, " Reset",
        color=reset_color, fontsize=7, ha="left", va="top",
    )

    # "Now" marker
    ax.axvline(x=now, color=text_color, linestyle="-", linewidth=0.5, alpha=0.3, zorder=1)

    # Current usage annotation
    if current_pct > 0:
        ax.annotate(
            f" {current_pct:.0f}%",
            xy=(now, current_pct),
            fontsize=9, fontweight="bold", color=usage_color,
            va="bottom", ha="left",
        )

    # Formatting
    ax.set_ylim(0, y_top)
    chart_start = max(window_start, display_start)
    ax.set_xlim(chart_start, resets_at + datetime.timedelta(hours=4))

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d"))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.yaxis.set_major_locator(plt.MultipleLocator(25))

    ax.grid(axis="y", color=grid_color, linewidth=0.5, alpha=0.5)
    ax.grid(axis="x", color=grid_color, linewidth=0.3, alpha=0.3)
    ax.tick_params(colors=text_color, labelsize=8)

    for spine in ax.spines.values():
        spine.set_visible(False)

    legend = ax.legend(
        fontsize=7, loc="upper left", framealpha=0.5,
        labelcolor=text_color, facecolor=annotation_bg, edgecolor="none",
    )

    # Collecting data message
    if len(times) < 2:
        ax.text(
            0.5, 0.5, "Collecting data...\nChart populates over time",
            transform=ax.transAxes, ha="center", va="center",
            fontsize=10, color=text_color, alpha=0.5,
        )

    # Save to base64
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True, pad_inches=0.1)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# SwiftBar output
# ---------------------------------------------------------------------------

def format_remaining(hours: float) -> str:
    if hours >= 48:
        return f"{hours / 24:.1f} days"
    elif hours >= 1:
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}h {m}m"
    else:
        return f"{int(hours * 60)}m"


def print_error(msg: str):
    """Output a fallback menu bar item on error."""
    print("C:-- | sfimage=exclamationmark.triangle sfcolor=#F44336 size=13")
    print("---")
    print(f"Error: {msg[:120]} | color=#F44336 size=12")
    print("---")
    print("Refresh | refresh=true sfimage=arrow.clockwise")


def main():
    is_dark = os.environ.get("OS_APPEARANCE", "Dark") == "Dark"
    now = datetime.datetime.now()

    # Fetch usage data
    try:
        token = get_oauth_token()
        usage = fetch_usage(token)
    except Exception as e:
        print_error(str(e))
        return

    # Parse 7-day data
    seven_day_raw = usage.get("seven_day", {})
    seven_day_pct = seven_day_raw.get("utilization") or 0
    try:
        resets_at = parse_reset_time(seven_day_raw["resets_at"])
    except (KeyError, ValueError) as e:
        print_error(f"Bad reset time: {e}")
        return

    projection = calculate_projection(seven_day_pct, resets_at, now)

    five_hour_raw = usage.get("five_hour", {})
    five_hour_pct = five_hour_raw.get("utilization")

    sonnet_raw = usage.get("seven_day_sonnet") or {}
    sonnet_pct = sonnet_raw.get("utilization")

    extra = usage.get("extra_usage") or {}

    # Update history
    history = load_history()
    append_reading(history, now, usage, projection)
    prune_history(history, MAX_HISTORY_DAYS)
    save_history(history)

    # Generate chart
    try:
        chart_b64 = generate_chart(history["readings"], projection, resets_at, is_dark)
    except Exception:
        chart_b64 = None

    # --- Menu bar line --- show current and projected usage
    projected_pct = projection["projected_pct"]
    color = severity_color(projected_pct)
    print(f"Now:{seven_day_pct:.0f}%  Fri:{projected_pct:.0f}% | sfimage=cpu sfcolor={color} color={color} size=12")

    # --- Dropdown ---
    print("---")

    tc = "#FFFFFF" if is_dark else "#000000"
    print(f"Claude Token Usage | size=15 font=.AppleSystemUIFontBold color={tc}")
    print("---")

    # Chart
    if chart_b64:
        print(f"| image={chart_b64}")
        print("---")

    # Stats
    proj_color = severity_color(projection["projected_pct"])
    mono = "font=Menlo size=12"

    print(f"7-Day Usage     {seven_day_pct:>6.1f}%     | {mono} color={severity_color(seven_day_pct)}")
    if five_hour_pct is not None:
        print(f"5-Hour Burst    {five_hour_pct:>6.1f}%     | {mono} color={severity_color(five_hour_pct)}")
    if sonnet_pct is not None:
        print(f"Sonnet (7-Day)  {sonnet_pct:>6.1f}%     | {mono}")
    print("---")

    burn = projection["burn_rate_pct_per_hour"]
    proj = projection["projected_pct"]
    print(f"Burn Rate       {burn:>5.2f}%/hr   | {mono}")
    print(f"Projected       {proj:>6.1f}%     | {mono} color={proj_color}")
    print("---")

    reset_str = resets_at.strftime("%a %b %-d, %-I:%M %p")
    remaining = format_remaining(projection["remaining_hours"])
    pct_elapsed = projection["percent_elapsed"]
    print(f"Resets   {reset_str} | {mono}")
    print(f"Remaining       {remaining:>10s}   | {mono}")
    print(f"Week Elapsed    {pct_elapsed:>5.1f}%     | {mono}")

    # Extra usage
    if extra.get("is_enabled"):
        used = extra.get("used_credits", 0)
        limit = extra.get("monthly_limit", 0)
        print("---")
        print(f"Extra Credits   ${used:>7.2f} / ${limit:.0f} | {mono}")

    print("---")
    print("Refresh Now | refresh=true sfimage=arrow.clockwise")
    now_str = now.strftime("%-I:%M %p")
    print(f"Updated {now_str} | size=10 color=gray")


if __name__ == "__main__":
    main()
