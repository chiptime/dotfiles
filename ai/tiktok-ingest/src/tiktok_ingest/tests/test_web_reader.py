"""The evidence reader (static/reader.js): Markdown + JSON viewers.

Evidence artifacts are untrusted VLM/web text, so the reader parses
them into a plain data tree and renders it with createElement and
textContent only. The parser is pure, so its behavior is pinned by
running reader.js under Node (skipped when Node is unavailable); its
safety rules are pinned against the source like app.js.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"
READER = STATIC_DIR / "reader.js"
NODE = shutil.which("node")

FORBIDDEN_IN_READER = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "href",
    "fetch(",
)


def run_reader(expression: str, payload: object) -> object:
    """Evaluate ``expression`` with ``R`` = reader.js and ``input`` = payload."""
    script = (
        "const R = require(process.argv[1]);"
        "const input = JSON.parse(process.argv[2]);"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    done = subprocess.run(
        [NODE, "-e", script, str(READER), json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode != 0:
        raise AssertionError(f"node failed: {done.stderr}")
    return json.loads(done.stdout)


def inline_text(nodes: list[dict]) -> str:
    out = []
    for node in nodes:
        if "children" in node:
            out.append(inline_text(node["children"]))
        else:
            out.append(node.get("text", ""))
    return "".join(out)


@unittest.skipUnless(NODE, "node is required to run reader.js")
class MarkdownParserTests(unittest.TestCase):
    def parse(self, text: str) -> list[dict]:
        return run_reader("R.parseMarkdown(input)", text)

    def test_headings_keep_level_and_text(self) -> None:
        blocks = self.parse("# audio.md — 7689\n\n## Transcript (accumulated)\n")
        self.assertEqual(
            [(b["type"], b["level"], b["text"]) for b in blocks],
            [("heading", 1, "audio.md — 7689"), ("heading", 2, "Transcript (accumulated)")],
        )

    def test_key_value_bullets_become_one_list(self) -> None:
        blocks = self.parse("- pts: 0 (timestamp 0.000 s)\n- done_reason: stop\n- flags: none\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "list")
        self.assertFalse(blocks[0]["ordered"])
        texts = [item["blocks"][0]["text"] for item in blocks[0]["items"]]
        self.assertEqual(texts, ["pts: 0 (timestamp 0.000 s)", "done_reason: stop", "flags: none"])

    def test_quoted_vlm_output_keeps_nested_lists(self) -> None:
        # The real video.md shape: an ordered list inside a blockquote
        # whose items carry an indented bullet list.
        text = (
            "> Based on the image:\n"
            ">\n"
            "> 1.  **Context**: a split screen.\n"
            "> 2.  **Visible Headings & Titles**:\n"
            ">     *   Top section: `alguien acaba`\n"
            ">     *   No other headings.\n"
            "> 3.  **Code & Commands**: None visible.\n"
        )
        blocks = self.parse(text)
        self.assertEqual([b["type"] for b in blocks], ["quote"])
        inner = blocks[0]["blocks"]
        self.assertEqual([b["type"] for b in inner], ["paragraph", "list"])
        ordered = inner[1]
        self.assertTrue(ordered["ordered"])
        self.assertEqual(len(ordered["items"]), 3)
        second = ordered["items"][1]["blocks"]
        self.assertEqual([b["type"] for b in second], ["paragraph", "list"])
        self.assertEqual(len(second[1]["items"]), 2)
        self.assertEqual(second[1]["items"][0]["blocks"][0]["text"], "Top section: `alguien acaba`")

    def test_fenced_code_is_kept_verbatim(self) -> None:
        blocks = self.parse("```bash\nrm -rf / # **not bold**\n```\nafter\n")
        self.assertEqual(blocks[0], {"type": "code", "lang": "bash", "text": "rm -rf / # **not bold**"})
        self.assertEqual(blocks[1]["type"], "paragraph")

    def test_unclosed_fence_runs_to_the_end(self) -> None:
        blocks = self.parse("```\nline 1\nline 2")
        self.assertEqual(blocks, [{"type": "code", "lang": "", "text": "line 1\nline 2"}])

    def test_pipe_table(self) -> None:
        blocks = self.parse("| a | b |\n|---|:-:|\n| 1 | `x|` |\n| 2 |\n")
        self.assertEqual(blocks[0]["type"], "table")
        self.assertEqual(blocks[0]["header"], ["a", "b"])
        self.assertEqual(blocks[0]["rows"][0][0], "1")
        self.assertEqual(blocks[0]["rows"][1], ["2", ""])

    def test_horizontal_rule_and_paragraph_lines(self) -> None:
        blocks = self.parse("one\ntwo\n\n---\n\nthree")
        self.assertEqual([b["type"] for b in blocks], ["paragraph", "hr", "paragraph"])
        self.assertEqual(blocks[0]["text"], "one\ntwo")

    def test_empty_and_crlf_input(self) -> None:
        self.assertEqual(self.parse(""), [])
        blocks = self.parse("# t\r\n\r\nbody\r\n")
        self.assertEqual([b["type"] for b in blocks], ["heading", "paragraph"])
        self.assertEqual(blocks[1]["text"], "body")


@unittest.skipUnless(NODE, "node is required to run reader.js")
class InlineParserTests(unittest.TestCase):
    def parse(self, text: str) -> list[dict]:
        return run_reader("R.parseInline(input)", text)

    def test_strong_code_and_plain_text(self) -> None:
        nodes = self.parse("**Context**: the `ti web` command")
        self.assertEqual([n["type"] for n in nodes], ["strong", "text", "code", "text"])
        self.assertEqual(nodes[0]["children"], [{"type": "text", "text": "Context"}])
        self.assertEqual(nodes[2]["text"], "ti web")

    def test_snake_case_and_lone_stars_stay_literal(self) -> None:
        for text in ("media_sha256 and done_reason", "5 * 3 * 2", "a ** b"):
            with self.subTest(text=text):
                nodes = self.parse(text)
                self.assertEqual(nodes, [{"type": "text", "text": text}])

    def test_emphasis_with_star_and_underscore(self) -> None:
        nodes = self.parse("an *uncertain* and _illegible_ word")
        self.assertEqual([n["type"] for n in nodes], ["text", "em", "text", "em", "text"])

    def test_links_keep_text_and_url_without_becoming_anchors(self) -> None:
        nodes = self.parse("see [the repo](https://example.invalid/x) now")
        self.assertEqual(nodes[1]["type"], "link")
        self.assertEqual(nodes[1]["url"], "https://example.invalid/x")
        self.assertEqual(inline_text(nodes[1]["children"]), "the repo")

    def test_backslash_escape_and_unclosed_markers(self) -> None:
        self.assertEqual(self.parse(r"\*not em\*"), [{"type": "text", "text": "*not em*"}])
        self.assertEqual(self.parse("`open"), [{"type": "text", "text": "`open"}])
        self.assertEqual(self.parse("**open"), [{"type": "text", "text": "**open"}])


@unittest.skipUnless(NODE, "node is required to run reader.js")
class JsonHelperTests(unittest.TestCase):
    def test_confirmed_verdict_is_presented_as_a_record_not_a_verification(self) -> None:
        result = run_reader("input.map(R.verdictPresentation)", [
            {"verdict": "confirmed", "reason": "kept operator-supplied verdict; no candidate URL supplied", "fetches": []},
            {"verdict": "confirmed", "reason": None},
            {"verdict": "unverifiable", "reason": "no source"},
        ])
        self.assertEqual(result[0], {"label": "Recorded verdict: confirmed", "tone": "neutral"})
        self.assertEqual(result[1], result[0])
        self.assertEqual(result[2]["label"], "unverifiable")

    def test_outcome_preserves_verdict_reason_and_missing_verification(self) -> None:
        synthesis = {"classification": {"root": "food"}, "actionables": ["Try recipe"],
                     "entities": [{"name": "Q10"}], "claims": [{"claim": "Claim"}]}
        verification = {"claims": [{"claim": "Claim", "verdict": "confirmed",
                                    "reason": "kept operator-supplied verdict; no candidate URL supplied",
                                    "source": "audio", "evidence": "sample"}]}
        result = run_reader("[R.videoOutcome(input[0], input[1]), R.videoOutcome(input[0], null)]",
                            [synthesis, verification])
        self.assertEqual(result[0]["actions"], ["Try recipe"])
        self.assertEqual(result[0]["entities"], ["Q10"])
        self.assertTrue(result[0]["verification_available"])
        self.assertEqual(result[0]["claims"][0]["reason"], verification["claims"][0]["reason"])
        self.assertEqual(result[0]["claims"][0]["verdict"], "confirmed")
        self.assertFalse(result[1]["verification_available"])
        self.assertIsNone(result[1]["claims"][0]["verdict"])

    def test_summary_counts_keys_and_items(self) -> None:
        got = run_reader(
            "[R.summarizeJSON(input[0]), R.summarizeJSON(input[1]), R.summarizeJSON(input[2]), R.summarizeJSON(input[3])]",
            [{"a": 1, "b": 2}, [1], [], {}],
        )
        self.assertEqual(got, ["{2 keys}", "[1 item]", "[]", "{}"])

    def test_embedded_json_strings_decode_only_containers(self) -> None:
        got = run_reader(
            "input.map(R.decodeEmbeddedJSON)",
            ['{"passage": "x", "sha256": "ab"}', " [1, 2] ", "42", '"str"', "{broken", "plain"],
        )
        self.assertEqual(got, [{"passage": "x", "sha256": "ab"}, [1, 2], None, None, None, None])

    def test_kind_by_artifact_name(self) -> None:
        got = run_reader(
            "input.map(R.kindOf)",
            ["video.md", "synthesis.json", "VERIFICATION.JSON", "notes.txt"],
        )
        self.assertEqual(got, ["markdown", "json", "json", "text"])


class ReaderSafetyTests(unittest.TestCase):
    """Source rules: text-only rendering, no anchors, no outbound URLs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.reader = READER.read_text(encoding="utf-8")
        cls.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        cls.app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    def test_no_markup_sinks_anchors_or_requests(self) -> None:
        for token in FORBIDDEN_IN_READER:
            self.assertNotIn(token, self.reader, f"forbidden in reader.js: {token}")
        self.assertNotRegex(self.reader, r'createElement\(\s*"a"\s*\)')

    def test_no_external_urls(self) -> None:
        self.assertEqual(re.findall(r"https?://[^\s\"'<>]+", self.reader), [])

    def test_index_loads_reader_before_app(self) -> None:
        reader_at = self.html.index('src="/static/reader.js"')
        self.assertLess(reader_at, self.html.index('src="/static/app.js"'))

    def test_app_renders_evidence_and_reports_through_the_reader(self) -> None:
        self.assertIn("TIReader", self.app)
        self.assertIn("function documentViewer(", self.app)
        evidence = self.app[self.app.index("function renderEvidence(") :]
        self.assertIn("documentViewer(", evidence[: evidence.index("function lookup(") + 4000])
        run_detail = self.app[self.app.index("function renderRunDetail(") :]
        self.assertIn("documentViewer(", run_detail[: run_detail.index("function failureText(")])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
