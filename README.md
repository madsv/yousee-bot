# YouSee Driftsstatus Bot

Monitors your address-specific YouSee outage page and posts to Discord when something changes — new issues, resolved issues, or updated ETAs.

Runs on **GitHub Actions** for free, every 15 minutes.

---

## Quick setup

### 1. Create a GitHub repository

1. Go to [github.com/new](https://github.com/new) and create a new **private** repository (e.g. `yousee-bot`).
2. Upload all files from this folder into the root of the repo, preserving the `.github/` folder structure:
   ```
   yousee-bot/
   ├── .github/
   │   └── workflows/
   │       └── check_yousee.yml
   ├── checker.py
   ├── state.json
   └── README.md
   ```

### 2. Create a Discord webhook

1. Open Discord and go to the channel where you want alerts posted.
2. Click the gear icon ⚙️ → **Integrations** → **Webhooks** → **New Webhook**.
3. Give it a name (e.g. "YouSee Bot"), copy the **Webhook URL**.

### 3. Add secrets to GitHub

1. In your GitHub repo go to **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** and add each of the following:

| Secret name | Value |
|---|---|
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `YOUSEE_MAD_ID` | `YOUR_MAD_ID_HERE` |
| `YOUSEE_NAM_ID` | `YOUR_NAM_ID_HERE` |
| `YOUSEE_DAWA_ID` | `your-dawa-id-here` |
| `YOUSEE_AMS_ID` | `YOUR_AMS_ID_HERE` |

These are your address-specific IDs from the YouSee URL. Keeping them in secrets means no personal address data is stored in the code.

### 4. Enable GitHub Actions write permissions

The bot commits `state.json` back to the repo to remember what it last saw.

1. Go to **Settings** → **Actions** → **General**.
2. Scroll to **Workflow permissions** and select **Read and write permissions**.
3. Click **Save**.

### 5. Run it manually to test

1. Go to the **Actions** tab in your repo.
2. Click **Check YouSee Driftsstatus** → **Run workflow** → **Run workflow**.
3. Watch the logs — you should see "No active issues." or a list of any current problems.
4. Check your Discord channel for a notification if anything was found.

After that, GitHub will run it automatically every 15 minutes.

---

## What it posts

| Situation | Discord colour |
|---|---|
| New outage detected | 🔴 Red embed |
| Outage resolved | ✅ Green embed |
| ETA added or changed | 🟠 Amber embed |

---

## Troubleshooting: scraper not finding issues

YouSee's website is a modern JavaScript-rendered app, which means the HTML fetched by the bot may not always contain the outage data (it might be loaded by JavaScript after the page opens).

**To diagnose:**
1. Set the `DEBUG` environment variable to `1` in the workflow step:
   ```yaml
   env:
     DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
     DEBUG: "1"
   ```
2. Run the workflow manually.
3. In the Actions log, the raw HTML will be saved as `debug_page.html`. Download it via the workflow artifact or add an upload step.
4. Search the HTML for "aktuel" or "driftforstyrrelse" — if it's not there, the content is JavaScript-rendered.

**Fix for JavaScript-rendered pages:**

Replace the `pip install` step and the script's `fetch_page()` function with a Playwright-based approach:

```yaml
- name: Install dependencies
  run: |
    pip install playwright beautifulsoup4
    playwright install chromium --with-deps
```

```python
# Replace fetch_page() in checker.py with:
from playwright.sync_api import sync_playwright

def fetch_page() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(YOUSEE_URL, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
    return html
```

This runs a real headless browser and waits for JavaScript to finish loading before grabbing the HTML.

---

## Facebook Group (future)

Posting to a Facebook Group via the API requires:

1. A **Facebook Developer App** at [developers.facebook.com](https://developers.facebook.com).
2. The **`publish_to_groups`** permission — this requires Facebook's formal **App Review** process (takes days to weeks, and Facebook may reject it for bots).
3. A long-lived **User Access Token** from an admin of the group.

Because of the App Review requirement, the simplest path is to use an automation tool like **[Make.com](https://make.com)** (free tier available) or **Zapier** to watch a Discord channel and cross-post new messages to Facebook automatically — no API review needed on your part.

A guide for Facebook native API integration can be added here once App Review is approved.
