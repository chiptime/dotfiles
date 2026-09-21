"""Taxonomy accumulation: normalization and idempotent re-adds."""

from __future__ import annotations

import tempfile
import unittest

from tiktok_ingest.tests import fixtures
from tiktok_ingest.contracts import TAXONOMY_ROOTS, ContractError
from tiktok_ingest.state import Taxonomy, normalize_subgroup


class NormalizeSubgroupTests(unittest.TestCase):
    def test_basic_normalization(self) -> None:
        cases = {
            "CLI Agentes": "cli-agentes",
            "cli-agentes": "cli-agentes",
            "CLI_Agentes": "cli-agentes",
            "  cli   agentes  ": "cli-agentes",
            "agent--tools": "agent-tools",
            "Modelos/AI": "modelos/ai",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_subgroup(raw), expected)

    def test_normalization_is_idempotent(self) -> None:
        for raw in ("CLI Agentes", "  Ruido--Social  ", "agentes_cli"):
            with self.subTest(raw=raw):
                once = normalize_subgroup(raw)
                self.assertEqual(normalize_subgroup(once), once)

    def test_empty_input_is_rejected(self) -> None:
        for raw in ("", "   ", "---"):
            with self.subTest(raw=raw):
                with self.assertRaises(ContractError):
                    normalize_subgroup(raw)


class TaxonomyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = fixtures.make_state_root(self._tmp.name)
        self.taxonomy = Taxonomy(self.state)

    def test_variant_spellings_collapse_to_one_entry(self) -> None:
        results = [
            self.taxonomy.add("tecnología", spelling)
            for spelling in ("CLI Agentes", "cli-agentes", "  CLI   agentes ", "CLI_Agentes")
        ]
        self.assertEqual(results[0][1], "added")
        self.assertTrue(all(status == "existing" for _, status in results[1:]))
        self.assertEqual(len(self.taxonomy.entries()), 1)
        self.assertEqual(self.taxonomy.entries()[0].subgroup, "cli-agentes")

    def test_readd_is_idempotent_and_preserves_provenance(self) -> None:
        first, _ = self.taxonomy.add(
            "educativo", "concepto-tecnica", origin_video_id="111", added_at=fixtures.FIXED_NOW
        )
        second, status = self.taxonomy.add("educativo", "Concepto-tecnica", origin_video_id="222")
        self.assertEqual(status, "existing")
        self.assertEqual(first, second)
        self.assertEqual(first.added_at, fixtures.FIXED_NOW)
        self.assertEqual(first.origin_video_id, "111")

    def test_accented_spelling_stays_distinct_by_design(self) -> None:
        """Basic normalization preserves diacritics (lowercase + hyphens only).

        Two source spellings that differ by an accent are therefore distinct
        subgroups; folding accents would be a separate, explicit decision.
        """
        self.taxonomy.add("educativo", "concepto-tecnica")
        self.taxonomy.add("educativo", "concepto-técnica")
        self.assertEqual(len(self.taxonomy.entries()), 2)

    def test_lookup_normalizes_the_query(self) -> None:
        self.taxonomy.add("consumo", "apps-personales")
        found = self.taxonomy.lookup("APPS__personales".replace("__", "_"))
        self.assertIsNotNone(found)
        self.assertEqual(found.subgroup, "apps-personales")
        self.assertIsNone(self.taxonomy.lookup("never-added"))

    def test_invalid_root_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            self.taxonomy.add("tecnología-2", "nuevo-grupo")
        self.assertEqual(self.taxonomy.entries(), [])

    def test_roots_remain_fixed(self) -> None:
        self.assertEqual(
            TAXONOMY_ROOTS,
            ("tecnología", "recurso", "educativo", "consumo", "ruido"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
