#!/usr/bin/env python3
"""Unit tests for scripts/projects_unified.py deterministic join and unified cards."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts directory to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import projects_unified


class TestProjectsUnifiedJoin(unittest.TestCase):
    def setUp(self):
        self.sample_tiles = [
            {
                "id": "compara-precios",
                "name": "ComparaPrecios",
                "kind": "web",
                "url": "http://127.0.0.1:4200",
                "icon": "🛒",
                "description": "Comparador de precios local",
                "tags": ["personal", "ia"]
            },
            {
                "id": "Planificador-backend",
                "name": "Planificador Backend",
                "kind": "project",
                "location": "/home/user/Code/Work/Stratesys/Products/Planificador/backend",
                "icon": "📦",
                "description": "Backend del planificador",
                "tags": ["trabajo"]
            },
            {
                "id": "associations",
                "name": "Clece Associations",
                "kind": "project",
                "location": "/home/user/Code/Work/Stratesys/Clece/apps-workspaces/associations",
                "icon": "🏢",
                "description": "Módulo de asociaciones",
                "tags": ["trabajo"]
            },
            {
                "id": "untracked-repo",
                "name": "Repo Sin Seguimiento",
                "kind": "project",
                "location": "/home/user/Code/personal/untracked-repo",
                "icon": "📁",
                "description": "Proyecto nuevo descubierto",
                "tags": ["personal"]
            }
        ]

        self.sample_yaml = {
            "compare-prices": {
                "name": "Compara Precios",
                "scope": "personal",
                "status": "active",
                "desc": "Price comparator service",
                "next": "Cerrar baselines de scraping"
            },
            "planificador": {
                "name": "Products/Planificador",
                "scope": "work",
                "status": "cold",
                "desc": "Planificador de turnos",
                "group": "planificador"
            },
            "sis": {
                "name": "Clece SIS",
                "scope": "work",
                "status": "active",
                "desc": "Monorepo Clece SIS",
                "group": "sis",
                "streams": "associations · entities-portal"
            },
            "tracker-only-project": {
                "name": "Proyecto Solo Tracker",
                "scope": "personal",
                "status": "proposal",
                "desc": "Idea anotada pero sin código todavía",
                "next": "Redactar spec"
            }
        }

        self.sample_md = {
            "compare-prices": {
                "section": "Active",
                "branch": "master",
                "dirty": "3",
                "hours_week": "2.5",
                "hours_total": "14.2"
            },
            "sis": {
                "section": "Active",
                "branch": "main",
                "dirty": "0",
                "hours_week": "0.0",
                "hours_total": "45.0"
            }
        }

        self.sample_sessions = {
            "compare-prices": [
                {"id": "ses_123", "title": "Refactor scrapper", "updated": "2026-10-18T10:00:00"}
            ]
        }

    def test_full_join_counts(self):
        cards = projects_unified.build_unified_cards(
            tiles=self.sample_tiles,
            projects_yaml=self.sample_yaml,
            projects_md=self.sample_md,
            sessions=self.sample_sessions
        )
        # Should include:
        # 1. compare-prices (matched with compara-precios tile)
        # 2. planificador (matched with Planificador-backend tile)
        # 3. sis (matched with associations tile)
        # 4. untracked-repo (tile without tracker)
        # 5. tracker-only-project (tracker without tile)
        ids = {c["id"] for c in cards}
        self.assertIn("compare-prices", ids)
        self.assertIn("planificador", ids)
        self.assertIn("sis", ids)
        self.assertIn("untracked-repo", ids)
        self.assertIn("tracker-only-project", ids)
        self.assertEqual(len(cards), 5)

    def test_disambiguation_mapping(self):
        cards = projects_unified.build_unified_cards(
            tiles=self.sample_tiles,
            projects_yaml=self.sample_yaml,
            projects_md=self.sample_md,
            sessions=self.sample_sessions
        )
        card_by_id = {c["id"]: c for c in cards}

        # compare-prices should be enriched with web tile info
        cp = card_by_id["compare-prices"]
        self.assertEqual(cp["tile"]["url"], "http://127.0.0.1:4200")
        self.assertEqual(cp["tile"]["kind"], "web")
        self.assertEqual(cp["status"], "active")
        self.assertEqual(cp["scope"], "personal")
        self.assertEqual(cp["next"], "Cerrar baselines de scraping")
        self.assertEqual(len(cp["tracker"]["sessions"]), 1)

        # planificador should join with Planificador-backend
        plan = card_by_id["planificador"]
        self.assertEqual(plan["status"], "cold")
        self.assertEqual(plan["scope"], "work")
        self.assertIn("Planificador/backend", plan["tile"]["location"])

        # sis should join with associations tile
        sis = card_by_id["sis"]
        self.assertEqual(sis["status"], "active")
        self.assertEqual(sis["scope"], "work")
        self.assertIn("Clece", sis["tile"]["name"])

    def test_asymmetric_untracked_tile(self):
        cards = projects_unified.build_unified_cards(
            tiles=self.sample_tiles,
            projects_yaml=self.sample_yaml,
            projects_md=self.sample_md,
            sessions=self.sample_sessions
        )
        card = next(c for c in cards if c["id"] == "untracked-repo")
        self.assertEqual(card["status"], "unclassified")
        self.assertEqual(card["status_label"], "sin clasificar")
        self.assertEqual(card["scope"], "personal")
        self.assertIsNone(card["next"])
        self.assertEqual(card["tile"]["location"], "/home/user/Code/personal/untracked-repo")
        self.assertEqual(card["tracker"]["sessions"], [])

    def test_asymmetric_tracker_only(self):
        cards = projects_unified.build_unified_cards(
            tiles=self.sample_tiles,
            projects_yaml=self.sample_yaml,
            projects_md=self.sample_md,
            sessions=self.sample_sessions
        )
        card = next(c for c in cards if c["id"] == "tracker-only-project")
        self.assertEqual(card["status"], "proposal")
        self.assertEqual(card["status_label"], "propuesta")
        self.assertEqual(card["scope"], "personal")
        self.assertEqual(card["next"], "Redactar spec")
        self.assertIsNone(card["tile"])

    def test_freshness_metadata(self):
        cards = projects_unified.build_unified_cards(
            tiles=self.sample_tiles,
            projects_yaml=self.sample_yaml,
            projects_md=self.sample_md,
            sessions=self.sample_sessions,
            snapshot_time="2026-10-19 17:00",
            hub_age_label="hace 2 h"
        )
        cp = next(c for c in cards if c["id"] == "compare-prices")
        self.assertEqual(cp["freshness"]["snapshot_time"], "2026-10-19 17:00")
        self.assertEqual(cp["freshness"]["hub_age"], "hace 2 h")
        self.assertTrue(cp["freshness"]["has_live_health"])

        untracked = next(c for c in cards if c["id"] == "untracked-repo")
        self.assertFalse(untracked["freshness"]["has_live_health"])


class TestProyectosHtmlRender(unittest.TestCase):
    def setUp(self):
        self.cards = [
            {
                "id": "compare-prices",
                "anchor": "compare-prices",
                "name": "Compara <Precios>",
                "scope": "personal",
                "scope_label": "Personal",
                "status": "active",
                "status_label": "activo",
                "desc": "Comparador & scraper",
                "next": "Fix <script> injection",
                "group": None,
                "streams": None,
                "tile": {
                    "id": "compare-prices",
                    "name": "ComparaPrecios",
                    "kind": "web",
                    "icon": "🛒",
                    "url": "http://127.0.0.1:4200"
                },
                "tracker": {
                    "branch": "master",
                    "dirty": 2,
                    "hours_week": "3.5",
                    "hours_total": "12.0",
                    "sessions": [{"id": "ses_001", "title": "Session 1"}]
                },
                "freshness": {
                    "snapshot_time": "19/10/2026 17:00",
                    "hub_age": "hace 2 días",
                    "has_live_health": True
                }
            }
        ]

    def test_html_structure_and_accessibility(self):
        html = projects_unified.render_proyectos_html(
            self.cards,
            snapshot_time="19/10/2026 17:00",
            hub_age_label="hace 2 días"
        )
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<title>Proyectos · Ecosistema</title>", html)
        self.assertIn('<a class="skip" href="#main">', html)
        self.assertIn('<main id="main"', html)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html)
        self.assertIn('aria-label="Secciones del ecosistema"', html)
        self.assertIn('data-portal-path="/PROYECTOS.html" aria-current="page">Proyectos</a>', html)

    def test_escaping_prevents_xss(self):
        html = projects_unified.render_proyectos_html(self.cards)
        self.assertIn("Compara &lt;Precios&gt;", html)
        self.assertIn("Comparador &amp; scraper", html)
        self.assertIn("Fix &lt;script&gt; injection", html)
        self.assertNotIn("<script> injection", html)

    def test_card_anchors_and_health_meta(self):
        html = projects_unified.render_proyectos_html(self.cards)
        self.assertIn('id="compare-prices"', html)
        self.assertIn('href="#compare-prices"', html)
        self.assertIn('data-tile-id="compare-prices"', html)
        self.assertIn('href="http://127.0.0.1:4200"', html)
        self.assertIn("3.5h esta semana", html)
        self.assertIn("⚠️ 2 dirty", html)

    def test_no_external_resources(self):
        html = projects_unified.render_proyectos_html(self.cards)
        import re
        self.assertNotRegex(html, r"(?i)<iframe|<link\b[^>]*stylesheet|<script[^>]*\bsrc=|@import|url\(\s*[\"']?https?:")

    def test_nav_script_matches_catalogue(self):
        catalogue_path = Path(__file__).resolve().parent.parent / "tools/launchpad/static/index.html"
        if catalogue_path.exists():
            cat_content = catalogue_path.read_text()
            import re
            m = re.search(r'<script id="portal-nav">(.*?)</script>', cat_content, re.S)
            self.assertIsNotNone(m)
            self.assertEqual(projects_unified.NAV_JS.strip(), m.group(1).strip())


if __name__ == "__main__":
    unittest.main()

