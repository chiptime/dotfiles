#!/usr/bin/env python3
"""Presentation regressions for the Seguimiento (tracker) page of the Ecosistema portal.

Runs scripts/projects-html.py with runpy against an isolated HOME and PROJECTS.md,
a mocked urllib and no live services. Never reads the real catalog, ledger, hub
state or git history.
"""
import contextlib
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parent
RENDERER = SCRIPTS / "projects-html.py"
CATALOGUE = SCRIPTS.parent / "tools/launchpad/static/index.html"
PREFIX = "http://localhost:4097/server/aHR0cDovL2xvY2FsaG9zdDo0MDk3/session"

MARKDOWN = """# tracker
## Active (2)
| compare-prices | `main` | 2026-10-01 ⚠️3 ↑2 | — | |
| dotfiles | `master` | 2026-10-18 | — | |
## Paused (1)
| old-idea | `main` | 2026-01-01 | — | |
"""
CATALOG = """compare-prices:
  status: active
  desc: Comparador de precios
  next: Cerrar la cobertura de supermercados
dotfiles:
  status: active
  desc: Entorno personal
old-idea:
  status: paused
  desc: Idea aparcada
  next: Retomar cuando haya tiempo
"""
NODE_NAV = """
const vm = require('node:vm');
const [src, hostname, protocol] = process.argv.slice(1);
const links = [['47625', '/'], ['47624', '/PROYECTOS.html'], ['47624', '/PROJECTS.html']].map(([p, q]) => ({
  attrs: { 'data-portal-port': p, 'data-portal-path': q },
  getAttribute(n) { return this.attrs[n]; },
  setAttribute(n, v) { this.attrs[n] = v; },
}));
vm.runInNewContext(src, { document: { querySelectorAll: () => links },
  window: { location: { protocol, hostname } } });
console.log(JSON.stringify(links.map(l => l.attrs.href)));
"""


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.in_header_nav, self.in_main = [], False, False
        self.header_text, self._header = [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "header" and "site" in (a.get("class") or ""):
            self._header = True
        if tag == "nav" and self._header:
            self.in_header_nav = True
        if tag == "a" and self.in_header_nav:
            self.links.append(a)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.in_header_nav = False
        if tag == "header":
            self._header = False

    def handle_data(self, data):
        if self._header:
            self.header_text.append(data)


def nav_script(document):
    match = re.search(r'<script id="portal-nav">(.*?)</script>', document, re.S)
    return match.group(1) if match else None


class TrackerPortalTests(unittest.TestCase):
    def render(self, markdown=MARKDOWN, catalog=CATALOG, sessions=None, hub=None):
        sessions = sessions if sessions is not None else {}
        urls = []

        def urlopen(url, timeout):
            full = url.full_url if hasattr(url, "full_url") else url
            urls.append(full)
            parsed = urlsplit(full)
            if parsed.path == "/project":
                return io.BytesIO(json.dumps([{"worktree": d} for d in sessions]).encode())
            directory = parse_qs(parsed.query)["directory"][0]
            return io.BytesIO(json.dumps(sessions[directory]).encode())

        with tempfile.TemporaryDirectory(prefix="portal-test-") as tmp:
            home = Path(tmp)
            (home / ".dotfiles/scripts").mkdir(parents=True)
            (home / ".dotfiles/scripts/projects.yaml").write_text(catalog)
            md = home / "PROJECTS.md"
            md.write_text(markdown)
            if hub is not None:
                (home / "hub-state.json").write_text(json.dumps(hub))
            with patch.dict(os.environ, {"HOME": tmp}), patch("sys.argv", [str(RENDERER), str(md)]), \
                    patch("urllib.request.urlopen", side_effect=urlopen), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                runpy.run_path(str(RENDERER), run_name="__main__")
            document = (home / "PROJECTS.html").read_text()
            sensor = json.loads((home / "projects.json").read_text())
            self.assertEqual((home / ".dotfiles/scripts/projects.yaml").read_text(), catalog)
        self.stdout = out.getvalue()
        self.urls = urls
        return document, sensor

    def test_ecosistema_header_and_navigation_mark_seguimiento_current(self):
        document, _ = self.render()
        parser = Anchors()
        parser.feed(document)
        self.assertIn("Ecosistema", "".join(parser.header_text))
        self.assertEqual([a.get("data-portal-port") for a in parser.links], ["47625", "47624", "47624"])
        self.assertEqual([a.get("data-portal-path") for a in parser.links], ["/", "/PROYECTOS.html", "/PROJECTS.html"])
        self.assertEqual([a.get("aria-current") for a in parser.links], [None, None, "page"])
        self.assertEqual([a.get("href") for a in parser.links][2], "/PROJECTS.html")
        self.assertRegex(document, r'<nav[^>]+aria-label="[^"]+"')
        self.assertEqual(len(re.findall(r"<h1[ >]", document)), 1)
        self.assertIn(">Seguimiento<", document)
        self.assertRegex(document, r'<a class="skip" href="#main">')
        self.assertIn('<main id="main"', document)
        self.assertIn("<title>Seguimiento", document)

    def test_navigation_script_is_the_catalogue_script_and_follows_the_hostname(self):
        document, _ = self.render()
        script = nav_script(document)
        self.assertIsNotNone(script)
        self.assertEqual(script, nav_script(CATALOGUE.read_text()),
                         "both pages share one navigation script")
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        cases = {("192.168.1.20", "http:"): ["http://192.168.1.20:47625/", "http://192.168.1.20:47624/PROYECTOS.html", "http://192.168.1.20:47624/PROJECTS.html"],
                 ("portal.lan", "https:"): ["https://portal.lan:47625/", "https://portal.lan:47624/PROYECTOS.html", "https://portal.lan:47624/PROJECTS.html"],
                 ("[::1]", "http:"): ["http://[::1]:47625/", "http://[::1]:47624/PROYECTOS.html", "http://[::1]:47624/PROJECTS.html"],
                 ("", "file:"): ["http://localhost:47625/", "http://localhost:47624/PROYECTOS.html", "http://localhost:47624/PROJECTS.html"]}
        for (hostname, protocol), expected in cases.items():
            with self.subTest(hostname=hostname, protocol=protocol):
                result = subprocess.run([node, "-e", NODE_NAV, script, hostname, protocol],
                                        capture_output=True, text=True, check=True, timeout=20)
                self.assertEqual(json.loads(result.stdout), expected)

    def test_anchors_for_projects_and_sections_are_preserved(self):
        document, _ = self.render()
        self.assertIn('id="compare-prices"', document)
        self.assertIn('<a class="proj" href="#compare-prices">compare-prices</a>', document)
        for key in ("active", "paused"):
            self.assertIn(f'<a class="chip" href="#sec-{key}">', document)
            self.assertIn(f'<section id="sec-{key}">', document)
        self.assertIn('<section id="sec-active"><details open>', document)
        self.assertIn('<section id="sec-paused"><details>', document)
        self.assertIn('id="sec-hub"', document)
        self.assertIn('id="sec-opencode"', document)
        self.assertIn("3 dirty", document)
        self.assertIn("↑2 sin push", document)

    def test_session_links_keep_their_hrefs_and_safe_targets(self):
        sessions = {"/repos/compare-prices": [
            {"id": "ses_a", "title": "Primera", "directory": "/repos/compare-prices", "time": {"updated": 2000}},
            {"id": "ses_b", "title": "Segunda", "directory": "/repos/compare-prices", "time": {"updated": 1000}},
            {"id": "ses_child", "title": "Sub", "parentID": "ses_a", "time": {"updated": 3000}}]}
        document, sensor = self.render(sessions=sessions)
        for ses, title in (("ses_a", "Primera"), ("ses_b", "Segunda")):
            self.assertRegex(document, rf'<a href="{re.escape(PREFIX)}/{ses}" target="_blank" rel="noopener">')
            self.assertIn(f"{title}</a>", document)
        self.assertNotIn("ses_child", document)
        entry = next(p for p in sensor["projects"] if p["name"] == "compare-prices")
        self.assertEqual([s["id"] for s in entry["sessions"]], ["ses_a", "ses_b"])

    def test_hub_freshness_markers_are_unchanged(self):
        old = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
        document, _ = self.render(hub={"schema": "hub-state/v2", "generated": old, "inbox": {}, "triage": 0,
                                       "projects": [{"slug": "001-compare-prices", "unit": "compare-prices",
                                                     "estado": "activo", "proxima_accion": "Revisar"}]})
        self.assertRegex(document, r'<span class="hubbadge stale" id="hub-fresh" data-generated="[^"]+">')
        self.assertIn("datos del hub de hace 11 días", document)
        self.assertIn("document.getElementById('hub-fresh')", document)

        fresh = datetime.now(timezone.utc).isoformat()
        document, _ = self.render(hub={"generated": fresh, "inbox": {}, "triage": 0, "projects": []})
        self.assertIn('class="hubbadge ok" id="hub-fresh"', document)

        document, _ = self.render(hub=None)
        self.assertIn('<span class="hubbadge none">sin datos del hub</span>', document)
        self.assertNotIn('id="hub-fresh"', document)

    def test_presentation_rules_and_no_external_assets(self):
        document, _ = self.render()
        style = re.search(r"<style>(.*?)</style>", document, re.S).group(1)
        self.assertIn(":focus-visible", style)
        self.assertRegex(style, r"prefers-reduced-motion:\s*reduce")
        self.assertRegex(style, r"minmax\(min\(100%")
        self.assertRegex(style, r"max-width:\s*\d+(\.\d+)?rem")
        self.assertNotRegex(document, r"(?i)<iframe|<link\b[^>]*stylesheet|<script[^>]*\bsrc=|@import|url\(\s*[\"']?https?:")
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', document)

    def test_legacy_outputs_and_remote_calls_are_unchanged(self):
        _, sensor = self.render()
        self.assertEqual(sensor["schema"], "projects-sensor/v1")
        names = {p["name"] for p in sensor["projects"]}
        self.assertEqual(names, {"compare-prices", "dotfiles", "old-idea"})
        self.assertIn("HTML written:", self.stdout)
        self.assertIn("JSON written:", self.stdout)
        self.assertTrue(all(url.startswith("http://127.0.0.1:4096/") for url in self.urls), self.urls)


if __name__ == "__main__":
    unittest.main()
