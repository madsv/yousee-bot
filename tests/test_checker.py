"""
Tests for checker.py

Run with:  pytest tests/ -v
"""

import json
import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# ── Import the module under test ───────────────────────────────────────────────
# The refactored checker reads env vars lazily (inside functions), so importing
# it here requires no secrets to be set.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import checker


# ══════════════════════════════════════════════════════════════════════════════
# _find_eta
# ══════════════════════════════════════════════════════════════════════════════

class TestFindEta:
    def test_returns_none_when_no_eta(self):
        text = "Der er problemer med internetforbindelsen i dit område."
        assert checker._find_eta(text) is None

    def test_finds_forventes_loest(self):
        text = "Fejlen forventes løst inden kl. 18:00\nMere info følger."
        assert checker._find_eta(text) == "Fejlen forventes løst inden kl. 18:00"

    def test_finds_forventes_afsluttet(self):
        text = "Arbejdet forventes afsluttet mandag den 6. maj."
        assert checker._find_eta(text) == "Arbejdet forventes afsluttet mandag den 6. maj."

    def test_finds_kl_keyword(self):
        text = "Opgaven afsluttes kl. 22:00 i aften."
        assert checker._find_eta(text) == "Opgaven afsluttes kl. 22:00 i aften."

    def test_finds_inden_for(self):
        text = "Problemet løses inden for 2 timer."
        assert checker._find_eta(text) == "Problemet løses inden for 2 timer."

    def test_returns_first_matching_line(self):
        text = "Noget tekst\nForventes løst kl. 12:00\nForventes løst kl. 14:00"
        result = checker._find_eta(text)
        assert result == "Forventes løst kl. 12:00"

    def test_case_insensitive(self):
        text = "FORVENTES LØST inden kl. 09:00"
        assert checker._find_eta(text) is not None

    def test_finds_eta_label(self):
        text = "ETA: 2026-05-05 20:00"
        assert checker._find_eta(text) == "ETA: 2026-05-05 20:00"


# ══════════════════════════════════════════════════════════════════════════════
# _extract_issue
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractIssue:
    def _tag(self, html: str):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").find()

    def test_returns_none_for_short_text(self):
        tag = self._tag("<div>Kort</div>")
        assert checker._extract_issue(tag) is None

    def test_extracts_title_from_heading(self):
        tag = self._tag(
            "<div>"
            "<h2>Aktuel driftforstyrrelse</h2>"
            "<p>Der er problemer med internetforbindelsen i dit område. Vi arbejder på sagen.</p>"
            "</div>"
        )
        issue = checker._extract_issue(tag)
        assert issue is not None
        assert issue["title"] == "Aktuel driftforstyrrelse"

    def test_falls_back_to_first_line_for_title(self):
        tag = self._tag(
            "<div>"
            "<p>Driftforstyrrelse i område 4700</p>"
            "<p>Vi oplever problemer og arbejder på en løsning.</p>"
            "</div>"
        )
        issue = checker._extract_issue(tag)
        assert issue is not None
        assert "Driftforstyrrelse" in issue["title"]

    def test_id_is_stable_for_same_title(self):
        html = (
            "<div><h2>Aktuel driftforstyrrelse</h2>"
            "<p>Detaljer om problemet her. Vi arbejder på det.</p></div>"
        )
        tag1 = self._tag(html)
        tag2 = self._tag(html)
        assert checker._extract_issue(tag1)["id"] == checker._extract_issue(tag2)["id"]

    def test_id_differs_for_different_title(self):
        tag1 = self._tag("<div><h2>Problem A</h2><p>Beskrivelse af problem A i dit område nu.</p></div>")
        tag2 = self._tag("<div><h2>Problem B</h2><p>Beskrivelse af problem B i dit område nu.</p></div>")
        assert checker._extract_issue(tag1)["id"] != checker._extract_issue(tag2)["id"]

    def test_extracts_eta(self):
        tag = self._tag(
            "<div>"
            "<h2>Aktuel driftforstyrrelse</h2>"
            "<p>Der er fejl på linjen.</p>"
            "<p>Forventes løst kl. 17:00 i dag.</p>"
            "</div>"
        )
        issue = checker._extract_issue(tag)
        assert issue["eta"] == "Forventes løst kl. 17:00 i dag."

    def test_eta_is_none_when_absent(self):
        tag = self._tag(
            "<div>"
            "<h2>Aktuel driftforstyrrelse</h2>"
            "<p>Vi arbejder på problemet og vender tilbage med info.</p>"
            "</div>"
        )
        issue = checker._extract_issue(tag)
        assert issue["eta"] is None

    def test_description_strips_leading_title(self):
        tag = self._tag(
            "<div>"
            "<h2>Aktuel driftforstyrrelse</h2>"
            "<p>Internettet er nede i dit område.</p>"
            "</div>"
        )
        issue = checker._extract_issue(tag)
        assert not issue["description"].startswith("Aktuel driftforstyrrelse")


# ══════════════════════════════════════════════════════════════════════════════
# parse_issues
# ══════════════════════════════════════════════════════════════════════════════

class TestParseIssues:
    def test_returns_empty_when_no_signal(self):
        html = "<html><body><p>Alt fungerer normalt. Ingen problemer.</p></body></html>"
        assert checker.parse_issues(html) == {}

    def test_strategy1_class_based_detection(self):
        """Strategy 1: container with a class name containing a candidate fragment."""
        html = textwrap.dedent("""
            <html><body>
              <div class="drift-item">
                <h2>Aktuel driftforstyrrelse</h2>
                <p>Der er udfald på bredbånd i dit område. Vi arbejder på sagen.</p>
                <p>Forventes løst kl. 20:00.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) == 1
        issue = next(iter(issues.values()))
        assert "driftforstyrrelse" in issue["title"].lower()
        assert issue["eta"] is not None

    def test_strategy2_heading_based_detection(self):
        """Strategy 2: heading containing the signal phrase, inside a generic wrapper."""
        html = textwrap.dedent("""
            <html><body>
              <div class="content-block">
                <h3>Aktuel driftforstyrrelse</h3>
                <p>Fejl på tv-signal i dit postnummer. Teknikere er sendt ud.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) >= 1

    def test_strategy3_broad_text_fallback(self):
        """Strategy 3: no class hints, no heading — just text containing the signal."""
        html = textwrap.dedent("""
            <html><body>
              <div>
                <p>Aktuel driftforstyrrelse: Der er problemer med dit internet lige nu.
                   Vi beklager ulejligheden og arbejder på at løse problemet hurtigst muligt.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) >= 1

    def test_multiple_issues_detected(self):
        html = textwrap.dedent("""
            <html><body>
              <div class="drift-item">
                <h2>Driftforstyrrelse A</h2>
                <p>Aktuel driftforstyrrelse på internet. Vi arbejder på sagen nu.</p>
              </div>
              <div class="drift-item">
                <h2>Driftforstyrrelse B</h2>
                <p>Aktuel driftforstyrrelse på tv-signal. Teknikere er på vej.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) == 2

    def test_scripts_and_nav_stripped(self):
        """Script/nav tags should not pollute the extracted text."""
        html = textwrap.dedent("""
            <html><body>
              <nav>Hjem | Support | Kontakt</nav>
              <script>var x = "driftforstyrrelse";</script>
              <div class="forstyrrelse-card">
                <h2>Aktuel driftforstyrrelse</h2>
                <p>Bredbånd er nede. Vi arbejder på at løse problemet i dit område.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) == 1
        issue = next(iter(issues.values()))
        assert "<script>" not in issue["raw_text"]
        assert "Hjem | Support" not in issue["raw_text"]

    def test_alternative_signal_phrase(self):
        """'aktuel forstyrrelse' (without 'drifts') should also trigger detection."""
        html = textwrap.dedent("""
            <html><body>
              <div class="outage">
                <h2>Aktuel forstyrrelse</h2>
                <p>Der er problemer med netværket. Teknikere arbejder på sagen hurtigst muligt.</p>
              </div>
            </body></html>
        """)
        issues = checker.parse_issues(html)
        assert len(issues) >= 1


# ══════════════════════════════════════════════════════════════════════════════
# _build_url
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildUrl:
    def test_builds_correct_url(self, monkeypatch):
        monkeypatch.setenv("YOUSEE_MAD_ID",  "MAD123")
        monkeypatch.setenv("YOUSEE_NAM_ID",  "NAM456")
        monkeypatch.setenv("YOUSEE_DAWA_ID", "DAWA789")
        monkeypatch.setenv("YOUSEE_AMS_ID",  "AMS000")
        url = checker._build_url()
        assert "madId=MAD123" in url
        assert "namId=NAM456" in url
        assert "dawaId=DAWA789" in url
        assert "amsId=AMS000" in url
        assert url.startswith("https://yousee.dk/hjaelp/driftsinformation")

    def test_exits_when_env_missing(self, monkeypatch):
        for key in ("YOUSEE_MAD_ID", "YOUSEE_NAM_ID", "YOUSEE_DAWA_ID", "YOUSEE_AMS_ID"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(SystemExit):
            checker._build_url()


# ══════════════════════════════════════════════════════════════════════════════
# load_state / save_state
# ══════════════════════════════════════════════════════════════════════════════

class TestState:
    def test_load_state_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = checker.load_state()
        assert state == {"issues": {}, "last_checked": None}

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        issues = {
            "abc123": {
                "id": "abc123",
                "title": "Test issue",
                "description": "Something broke.",
                "eta": "Forventes løst kl. 18:00",
            }
        }
        checker.save_state(issues)
        loaded = checker.load_state()
        assert loaded["issues"] == issues
        assert loaded["last_checked"] is not None

    def test_save_overwrites_previous(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        checker.save_state({"aaa": {"id": "aaa", "title": "Old"}})
        checker.save_state({})
        loaded = checker.load_state()
        assert loaded["issues"] == {}


# ══════════════════════════════════════════════════════════════════════════════
# post_discord_embed
# ══════════════════════════════════════════════════════════════════════════════

class TestPostDiscordEmbed:
    def test_skips_when_no_webhook(self, monkeypatch, capsys):
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        checker.post_discord_embed("Title", "Description", 0xFF0000)
        captured = capsys.readouterr()
        assert "DISCORD_WEBHOOK_URL not set" in captured.out

    def test_posts_correct_payload(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook/test")
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.post_discord_embed(
                title="🔴 Ny driftforstyrrelse: Internet nede",
                description="Bredbåndet er nede i dit område.",
                color=0xE74C3C,
                eta="Forventes løst kl. 20:00",
            )

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]

        embed = payload["embeds"][0]
        assert embed["title"] == "🔴 Ny driftforstyrrelse: Internet nede"
        assert embed["color"] == 0xE74C3C
        assert embed["description"] == "Bredbåndet er nede i dit område."
        assert any(f["name"] == "⏱️ Forventet løsning" for f in embed["fields"])
        assert embed["footer"]["text"] == "YouSee Driftsstatus • yousee.dk"

    def test_no_fields_when_eta_absent(self, monkeypatch):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook/test")
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.post_discord_embed("Title", "Desc", 0x00FF00, eta=None)

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["embeds"][0]["fields"] == []

    def test_warns_on_non_2xx_response(self, monkeypatch, capsys):
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example.com/webhook/test")
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Bad Request"

        with patch.object(checker.SESSION, "post", return_value=mock_resp):
            checker.post_discord_embed("Title", "Desc", 0xFF0000)

        captured = capsys.readouterr()
        assert "400" in captured.out


# ══════════════════════════════════════════════════════════════════════════════
# Integration: change detection logic
# ══════════════════════════════════════════════════════════════════════════════

class TestChangeDetection:
    """
    Test the new/resolved/eta-updated detection logic by calling main() with
    mocked fetch_page and mocked Discord posting.
    """

    ISSUE_HTML = textwrap.dedent("""
        <html><body>
          <div class="drift-item">
            <h2>Aktuel driftforstyrrelse</h2>
            <p>Internet er nede i dit område. Vi arbejder på sagen.</p>
            <p>Forventes løst kl. 18:00.</p>
          </div>
        </body></html>
    """)

    NO_ISSUE_HTML = "<html><body><p>Ingen aktive driftforstyrrelser.</p></body></html>"

    def _set_required_env(self, monkeypatch):
        monkeypatch.setenv("YOUSEE_MAD_ID",  "MAD")
        monkeypatch.setenv("YOUSEE_NAM_ID",  "NAM")
        monkeypatch.setenv("YOUSEE_DAWA_ID", "DAWA")
        monkeypatch.setenv("YOUSEE_AMS_ID",  "AMS")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example.com/hook")

    def test_new_issue_triggers_discord_post(self, tmp_path, monkeypatch):
        self._set_required_env(monkeypatch)
        monkeypatch.chdir(tmp_path)

        mock_resp = MagicMock(); mock_resp.status_code = 204
        with patch("checker.fetch_page", return_value=self.ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.main()

        titles = [
            call[1]["json"]["embeds"][0]["title"]
            for call in mock_post.call_args_list
        ]
        assert any("Ny driftforstyrrelse" in t for t in titles)

    def test_resolved_issue_triggers_discord_post(self, tmp_path, monkeypatch):
        self._set_required_env(monkeypatch)
        monkeypatch.chdir(tmp_path)

        # First run: issue present → saved to state
        mock_resp = MagicMock(); mock_resp.status_code = 204
        with patch("checker.fetch_page", return_value=self.ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp):
            checker.main()

        # Second run: issue gone → should post "resolved"
        with patch("checker.fetch_page", return_value=self.NO_ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.main()

        titles = [
            call[1]["json"]["embeds"][0]["title"]
            for call in mock_post.call_args_list
        ]
        assert any("løst" in t for t in titles)

    def test_no_post_when_nothing_changes(self, tmp_path, monkeypatch):
        self._set_required_env(monkeypatch)
        monkeypatch.chdir(tmp_path)

        mock_resp = MagicMock(); mock_resp.status_code = 204
        # Run once (new issue posted)
        with patch("checker.fetch_page", return_value=self.ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp):
            checker.main()

        # Run again with same HTML — nothing should change
        with patch("checker.fetch_page", return_value=self.ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.main()

        assert mock_post.call_count == 0

    def test_eta_update_triggers_discord_post(self, tmp_path, monkeypatch):
        self._set_required_env(monkeypatch)
        monkeypatch.chdir(tmp_path)

        html_with_new_eta = textwrap.dedent("""
            <html><body>
              <div class="drift-item">
                <h2>Aktuel driftforstyrrelse</h2>
                <p>Internet er nede i dit område. Vi arbejder på sagen.</p>
                <p>Forventes løst kl. 22:00.</p>
              </div>
            </body></html>
        """)

        mock_resp = MagicMock(); mock_resp.status_code = 204
        with patch("checker.fetch_page", return_value=self.ISSUE_HTML), \
             patch.object(checker.SESSION, "post", return_value=mock_resp):
            checker.main()

        with patch("checker.fetch_page", return_value=html_with_new_eta), \
             patch.object(checker.SESSION, "post", return_value=mock_resp) as mock_post:
            checker.main()

        titles = [
            call[1]["json"]["embeds"][0]["title"]
            for call in mock_post.call_args_list
        ]
        assert any("Opdatering" in t for t in titles)
