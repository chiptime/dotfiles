"""Isolated renderer regressions; never read or modify the real catalog or ledger."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


RENDERER = Path(__file__).with_name("projects-html.py")
DEFAULT_PREFIX = "http://localhost:4097/server/aHR0cDovL2xvY2FsaG9zdDo0MDk3/session"


def session(number, directory="/repos/known", **extra):
    return dict(id=f"ses_{number}", title=f"Session {number}", directory=directory,
                time={"updated": number * 1000}, **extra)


class RendererTests(unittest.TestCase):
    def render(self, responses, catalog="", markdown="", webbase=None, unavailable=False,
               project_roots=None):
        def urlopen(url, timeout):
            headers = dict(url.header_items()) if hasattr(url, "header_items") else {}
            parsed = urlsplit(url.full_url if hasattr(url, "full_url") else url)
            if parsed.path == "/project":
                if unavailable:
                    raise OSError("Offline")
                result = [{"worktree": directory} for directory in
                          (project_roots if project_roots is not None else responses)]
            else:
                self.assertEqual(parsed.path, "/session")
                query = parse_qs(parsed.query)
                self.assertEqual(query["roots"], ["true"])
                self.assertGreater(int(query["limit"][0]), 100)
                if "directory" not in query:
                    self.assertEqual(headers.get("X-opencode-directory"), "/")
                    result = responses[None]
                else:
                    result = responses[query["directory"][0]]
                if result is None:
                    raise OSError("Offline")
            return io.BytesIO(json.dumps(result).encode())

        with tempfile.TemporaryDirectory(prefix="dashboard-test-", dir="/tmp/opencode") as tmp:
            home = Path(tmp)
            annotations = home / ".dotfiles/scripts/projects.yaml"
            annotations.parent.mkdir(parents=True)
            annotations.write_text(catalog)
            md = home / "PROJECTS.md"
            md.write_text(markdown)
            if webbase:
                config = home / ".local/share/time-ledger/webbase"
                config.parent.mkdir(parents=True)
                config.write_text(webbase)
            with patch.dict(os.environ, {"HOME": tmp}), patch("sys.argv", [str(RENDERER), str(md)]), \
                    patch("urllib.request.urlopen", side_effect=urlopen), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(RENDERER), run_name="__main__")
            document = md.with_suffix(".html").read_text()
            self.assertEqual(annotations.read_text(), catalog)
            self.assertFalse((home / ".local/share/time-ledger/entries.csv").exists())
        panel = document.split('id="sec-opencode"', 1)[1].split("</section>", 1)[0]
        return document, panel

    def test_latest_four_and_native_reveal_in_updated_order(self):
        _, panel = self.render({"/repos/known": [session(n) for n in (2, 6, 1, 5, 3, 4)]})
        preview, remaining = panel.split('<details class="session-more">')
        self.assertEqual(preview.count('target="_blank"'), 4)
        self.assertEqual(remaining.count('target="_blank"'), 2)
        self.assertNotIn(" open", remaining.split(">", 1)[0])
        self.assertIn("<summary>Ver todas (6)</summary>", remaining)
        positions = [panel.index(f"Session {n}</a>") for n in (6, 5, 4, 3, 2, 1)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(f'{DEFAULT_PREFIX}/ses_6', panel)

    def test_old_roots_included_subagents_excluded(self):
        _, panel = self.render({"/repos/known": [session(1), session(9, parentID="ses_parent")]})
        self.assertIn("Session 1</a>", panel)
        self.assertNotIn("Session 9", panel)
        self.assertNotIn("Ver todas", panel)

    def test_global_worktree_groups_actual_directories(self):
        _, panel = self.render({None: [session(1, "/home/bruno"),
                                       session(2, "/scratch/other"),
                                       session(3, "/home/bruno", parentID="ses_parent")]},
                               project_roots=["/"])
        self.assertIn("<h3>bruno</h3>", panel)
        self.assertIn("<h3>other</h3>", panel)
        self.assertIn(f'{DEFAULT_PREFIX}/ses_1', panel)
        self.assertIn(f'{DEFAULT_PREFIX}/ses_2', panel)
        self.assertNotIn("Session 3", panel)
        self.assertEqual(panel.count('target="_blank"'), 2)

    def test_uncatalogued_project_and_custom_webbase(self):
        _, panel = self.render({"/repos/absent": [session(1, "/repos/absent")]},
                               webbase="http://example.invalid/session/")
        self.assertIn("<h3>absent</h3>", panel)
        self.assertIn('href="http://example.invalid/session/ses_1"', panel)

    def test_groups_and_status_cards_preserved(self):
        catalog = ("sis:\n  name: Clece SIS\n  status: paused\n"
                   "known:\n  group: sis\nother:\n  group: sis\n"
                   "products:\n  name: Products/Planificador\n  status: cold\n"
                   "planner:\n  group: products\n")
        markdown = ("## Paused (2)\n| known | `main` | 2020-01-01 | — | |\n"
                    "| other | `main` | 2020-01-01 | — | |\n"
                    "## Cold / legacy (1)\n| planner | `main` | 2020-01-01 | — | |\n")
        doc, panel = self.render({f"/repos/{name}": [session(n, f"/repos/{name}")]
                                  for n, name in enumerate(("known", "other", "planner"), 1)},
                                 catalog=catalog, markdown=markdown)
        self.assertEqual(panel.count("<h3>Clece SIS</h3>"), 1)
        self.assertIn("<h3>Products/Planificador</h3>", panel)
        self.assertIn('id="sec-paused"><details>', doc)
        self.assertIn('id="sec-cold"><details>', doc)
        self.assertIn('id="Clece SIS"', doc)
        self.assertNotIn("Sesiones activas ahora", doc)

    def test_unavailable_api_not_reported_as_empty(self):
        _, panel = self.render({}, unavailable=True)
        self.assertIn("OpenCode no disponible", panel)
        self.assertNotIn("No hay proyectos", panel)

    def test_partial_failure_preserves_successful_sessions(self):
        _, panel = self.render({"/repos/known": [session(1)], "/repos/offline": None})
        self.assertIn("datos incompletos", panel)
        self.assertIn("Session 1</a>", panel)

    def test_empty_api_and_subagent_only_project(self):
        for responses in ({}, {"/repos/known": [session(1, parentID="ses_parent")]}):
            with self.subTest(responses=bool(responses)):
                _, panel = self.render(responses)
                self.assertIn("No hay proyectos con sesiones de OpenCode", panel)
                self.assertNotIn("no disponible", panel)


if __name__ == "__main__":
    unittest.main()
