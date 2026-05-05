#!/usr/bin/env python3
"""
YouSee Driftsinformation Checker
Monitors https://yousee.dk/hjaelp/driftsinformation for active outages
and posts changes to a Discord webhook.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

STATE_FILE = "state.json"
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")


def _require_env(name: str) -> str:
    """Read a required environment variable, exiting with a clear message if missing."""
    value = os.environ.get(name, "")
    if not value:
        print(f"[ERROR] Required environment variable '{name}' is not set.")
        sys.exit(1)
    return value


def _build_url() -> str:
    """Build the address-specific YouSee driftsinformation URL from secrets."""
    return (
        "https://yousee.dk/hjaelp/driftsinformation"
        f"?madId={_require_env('YOUSEE_MAD_ID')}"
        f"&namId={_require_env('YOUSEE_NAM_ID')}"
        f"&dawaId={_require_env('YOUSEE_DAWA_ID')}"
        f"&amsId={_require_env('YOUSEE_AMS_ID')}"
    )

# Discord embed colours
COLOR_NEW     = 0xE74C3C  # red   – new issue
COLOR_UPDATED = 0xF39C12  # amber – ETA / detail updated
COLOR_RESOLVED = 0x2ECC71 # green – issue resolved

# ── HTTP helpers ───────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def fetch_page() -> str:
    url = _build_url()
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    if DEBUG:
        with open("debug_page.html", "w", encoding="utf-8") as f:
            f.write(response.text)
        print("[DEBUG] Page saved to debug_page.html")
    return response.text


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_issues(html: str) -> dict:
    """
    Return a dict of {issue_id: issue_dict} for all currently active issues.
    issue_id is a stable short hash of the issue title/heading.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strip noise
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()

    body_text = soup.get_text(separator="\n", strip=True)

    # If the page contains no active issue signal, return empty immediately
    active_signals = ["aktuel driftforstyrrelse", "aktuel forstyrrelse", "driftforstyrrelse"]
    if not any(sig in body_text.lower() for sig in active_signals):
        print("No active issues found on page.")
        return {}

    issues = {}

    # ── Strategy 1: look for labelled issue cards ──────────────────────────────
    # YouSee's React/CMS frontend often wraps each issue in a named container.
    # Try common class-name fragments that Danish ISPs use.
    candidate_classes = [
        "drifts", "outage", "disruption", "forstyrrelse", "issue", "alert",
        "incident", "status-item", "drift-item",
    ]
    containers = soup.find_all(
        lambda tag: tag.name in ("article", "section", "li", "div")
        and any(
            cls and any(frag in cls.lower() for frag in candidate_classes)
            for cls in tag.get("class", [])
        )
    )

    if containers:
        for container in containers:
            issue = _extract_issue(container)
            if issue:
                issues[issue["id"]] = issue
        if issues:
            return issues

    # ── Strategy 2: find headings that mention driftforstyrrelse ──────────────
    headings = soup.find_all(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5")
        and any(sig in tag.get_text().lower() for sig in active_signals)
    )
    for heading in headings:
        # Grab the nearest ancestor that looks like a card/section
        parent = heading.find_parent(["article", "section", "li", "div"])
        if parent:
            issue = _extract_issue(parent)
            if issue:
                issues[issue["id"]] = issue

    if issues:
        return issues

    # ── Strategy 3: broad text search fallback ────────────────────────────────
    # Look for any <div> or <section> whose text contains the signal phrase.
    for tag in soup.find_all(["div", "section", "article"]):
        tag_text = tag.get_text(separator="\n", strip=True)
        if any(sig in tag_text.lower() for sig in active_signals) and len(tag_text) > 80:
            # Avoid huge wrapper elements – prefer the smallest matching container
            children_match = any(
                any(sig in c.get_text().lower() for sig in active_signals)
                for c in tag.find_all(["div", "section"], recursive=False)
            )
            if not children_match:
                issue = _extract_issue(tag)
                if issue:
                    issues[issue["id"]] = issue

    if not issues and DEBUG:
        print("[DEBUG] Could not extract structured issues. Check debug_page.html.")

    return issues


def _extract_issue(tag) -> dict | None:
    """Extract a single issue dict from a BeautifulSoup tag."""
    text = tag.get_text(separator="\n", strip=True)
    if len(text) < 20:
        return None

    # Try to find the title (first substantial line or heading inside tag)
    title = ""
    heading = tag.find(["h1", "h2", "h3", "h4", "h5"])
    if heading:
        title = heading.get_text(strip=True)
    if not title:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0] if lines else text[:60]

    # Stable ID from normalised title
    issue_id = hashlib.md5(title.lower().encode()).hexdigest()[:10]

    # Extract ETA
    eta = _find_eta(text)

    # Build a clean description (strip the title from the top)
    description = text
    if title and description.startswith(title):
        description = description[len(title):].strip(" \n:-")

    return {
        "id": issue_id,
        "title": title,
        "description": description[:600],
        "eta": eta,
        "raw_text": text[:800],
    }


def _find_eta(text: str) -> str | None:
    """Return the first line that looks like an ETA estimate, or None."""
    eta_keywords = [
        "forventes løst", "forventes afsluttet", "forventes rettet",
        "forventes klar", "forventet løsning", "forventes at være",
        "eta:", "kl.", "inden kl", "inden for",
    ]
    for line in text.split("\n"):
        line_lower = line.lower().strip()
        if any(kw in line_lower for kw in eta_keywords):
            return line.strip()
    return None


# ── State persistence ──────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"issues": {}, "last_checked": None}


def save_state(issues: dict) -> None:
    state = {
        "issues": issues,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ── Discord ────────────────────────────────────────────────────────────────────

def post_discord_embed(title: str, description: str, color: int, eta: str | None = None) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("[WARN] DISCORD_WEBHOOK_URL not set – skipping Discord post.")
        return

    fields = []
    if eta:
        fields.append({"name": "⏱️ Forventet løsning", "value": eta, "inline": False})

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "YouSee Driftsstatus • yousee.dk"},
            }
        ]
    }

    resp = SESSION.post(webhook_url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"[WARN] Discord returned HTTP {resp.status_code}: {resp.text[:200]}")
    else:
        print(f"Posted to Discord: {title}")


# ── Main logic ─────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== YouSee checker running at {now} ===")

    # Fetch page
    try:
        html = fetch_page()
    except Exception as exc:
        print(f"[ERROR] Failed to fetch page: {exc}")
        sys.exit(1)

    # Parse current issues
    current = parse_issues(html)

    # Load previous state
    old_state = load_state()
    previous = old_state.get("issues", {})

    # ── Detect new issues ──────────────────────────────────────────────────────
    for issue_id, issue in current.items():
        if issue_id not in previous:
            print(f"NEW issue: {issue['title']}")
            desc = issue["description"] or issue["title"]
            post_discord_embed(
                title=f"🔴 Ny driftforstyrrelse: {issue['title']}",
                description=desc[:1000],
                color=COLOR_NEW,
                eta=issue.get("eta"),
            )
            # Store first-seen timestamp
            issue["first_seen"] = datetime.now(timezone.utc).isoformat()

    # ── Detect resolved issues ─────────────────────────────────────────────────
    for issue_id, issue in previous.items():
        if issue_id not in current:
            print(f"RESOLVED issue: {issue['title']}")
            post_discord_embed(
                title=f"✅ Driftforstyrrelse løst: {issue['title']}",
                description=(
                    f"Fejlen er ikke længere markeret som aktiv på YouSee's driftsside.\n\n"
                    f"_{issue['description'][:300]}_"
                ),
                color=COLOR_RESOLVED,
            )

    # ── Detect ETA / detail updates on existing issues ─────────────────────────
    for issue_id, issue in current.items():
        if issue_id in previous:
            prev = previous[issue_id]
            # Preserve first-seen timestamp
            issue.setdefault("first_seen", prev.get("first_seen", datetime.now(timezone.utc).isoformat()))

            old_eta = prev.get("eta")
            new_eta = issue.get("eta")
            if new_eta and new_eta != old_eta:
                change_note = (
                    f"ETA opdateret fra: _{old_eta}_"
                    if old_eta
                    else "ETA er nu tilgængelig."
                )
                print(f"ETA updated for: {issue['title']}")
                post_discord_embed(
                    title=f"🔄 Opdatering: {issue['title']}",
                    description=change_note,
                    color=COLOR_UPDATED,
                    eta=new_eta,
                )

    # ── Summary ────────────────────────────────────────────────────────────────
    if current:
        print(f"Active issues: {[v['title'] for v in current.values()]}")
    else:
        print("No active issues.")

    save_state(current)
    print("State saved. Done.")


if __name__ == "__main__":
    main()
