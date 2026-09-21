#!/usr/bin/env python3
"""Blind entity-level concordance of local VLM outputs vs. reference labels.

CPU-only, textual. Reads:
  - ground-truth/human-labels.json (reference; VLM-assisted, human-verified)
  - results/vision/<run>/<NNN>-<frame|hybrid>-response.json
Writes ONLY new files:
  - ground-truth/ground-truth-comparison.json
  - results/comparison-ground-truth.md
Never modifies inputs, comparison.md, or human-labels.json.
"""

import hashlib
import json
import re
import statistics
import string
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path("/tmp/opencode/tiktok-validation-v2")
REF_FILE = ROOT / "ground-truth" / "human-labels.json"
VISION = ROOT / "results" / "vision"
OUT_JSON = ROOT / "ground-truth" / "ground-truth-comparison.json"
OUT_MD = ROOT / "results" / "comparison-ground-truth.md"

RUNS = {
    "qwen2.5vl": {"model": "qwen2.5vl:7b", "dir": "phase-3a-qwen2.5vl-14", "phase": "3-A"},
    "qwen3.5": {"model": "qwen3.5:9b", "dir": "phase-4-1-qwen3.5-14", "phase": "4-1"},
    "qwen3.8": {"model": "qwen3.8:27b", "dir": "phase-4-2-qwen3.8-14", "phase": "4-2"},
}
REGIONS = ["top", "middle", "bottom", "code_or_url", "overlays"]

MARKER_CUT = "[cut]"
MARKER_ILL = "[illegible]"

CONNECTORS = {
    "on", "of", "de", "da", "do", "das", "dos", "e", "y", "a", "in", "for",
    "the", "and", "+", "&", "by", "pra",
}
# Template/boilerplate words that appear in EVERY structured VLM report and are
# not on-screen content. Applied ONLY to the output side when collecting
# invented candidates; the reference side is never filtered.
BOILERPLATE = {
    "context", "visible", "headings", "titles", "code", "commands", "identified",
    "names", "urls", "legibility", "ambiguity", "analysis", "extraction",
    "information", "based", "provided", "here", "report", "markdown",
    "transcribe", "verbatim", "note", "none", "unclear", "blurry", "text",
    "window", "page", "section", "main", "labels", "button", "footer",
    "browser", "terminal", "editor", "desktop", "environment", "panel",
    "area", "region", "truncated", "ambiguous", "uncertain", "illegible",
    "title", "heading", "tab", "application", "app", "image", "screen",
    "framework", "web", "linux", "system", "info", "top", "right", "left",
    "bottom", "center", "file", "path", "version", "os", "ui", "osd",
    "link", "website", "subheading", "creator", "author", "tools",
    "platforms", "mentioned", "label", "element", "elements", "content",
    "developer", "name", "package", "service", "services", "manager",
    "de", "da", "do", "das", "dos", "pra", "para", "com", "em", "um", "uma",
    "project", "projects",
    "tool", "model", "company", "organization", "hero", "badge", "navigation",
    "tabs", "tagline", "sponsorship", "support", "clock", "breadcrumb",
    "bar", "tooltip", "provider", "keywords", "queries", "statuses",
    "hardware", "software", "branding", "logs", "links", "references",
    "integrations", "technologies", "owner", "user", "diagram", "details",
    "small", "blurry", "partial", "teal", "cyan", "format", "selector",
    "competitive", "description", "slogan", "body", "transcription", "due",
    "therefore", "architecture", "runtime",
    "sub", "product", "pink", "purple", "bin", "ci", "cd", "tags", "models",
    "model", "chromium", "based", "ide", "bash", "tagline", "hero",
    "repository",
    "the", "and", "for", "with", "from", "this", "that", "there", "it", "is",
    "are", "was", "were", "be", "been", "not", "no", "any", "all", "some",
    "very", "also", "only", "but", "or", "if", "then", "than", "into", "on",
    "in", "at", "by", "to", "of", "as", "an", "a",
}

# Function words that as STANDALONE name entities are prose-fragment artifacts,
# never real on-screen labels. Whole-entity match only; applied to both sides.
PURE_PROSE = {
    "how", "these", "those", "does", "each", "every", "never", "what",
    "want", "thanks", "per", "foi", "sempre", "nunca", "velho", "vira",
}


def is_boilerplate(entity_norm: str) -> bool:
    """True when every alphanumeric token of the entity is template scaffolding
    (report section labels, generic UI vocabulary)."""
    toks = [t for t in re.split(r"[^a-z0-9]+", entity_norm) if t]
    return bool(toks) and all(t in BOILERPLATE for t in toks)

URL_RE = re.compile(r"https?://[^\s`)\]>\"']+", re.I)
DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:org|com|net|io|dev|me|ai|es|sh|cc|gg|app|so|xyz|info|us|uk|br|pt)\b"
    r"(?:/[^\s`)\]>\"']*)?", re.I)
REPO_RE = re.compile(r"\b([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)\b")
COMMAND_WORDS = {
    "wget", "curl", "npx", "npm", "pnpm", "yarn", "bash", "sh", "zsh", "sudo",
    "git", "pip", "pip3", "pipx", "docker", "podman", "cargo", "go", "make",
    "systemctl", "journalctl", "pacman", "yay", "apt", "brew", "omarchy",
    "orca", "claude", "codex", "ollama", "llama", "python", "python3", "node",
    "deno", "bun", "rustc", "gcc", "hyprctl", "kitty", "nvim", "vim", "vi",
}
FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\n(.*?)```", re.S)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(s: str) -> str:
    """Normalize for comparison: strip markers/fences/backticks, casefold,
    collapse whitespace, strip edge punctuation."""
    s = s.replace(MARKER_CUT, " ").replace(MARKER_ILL, " ")
    s = s.replace("`", " ")
    s = s.casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(string.punctuation + " ")


def strict_norm(s: str) -> str:
    """Case-sensitive normalization (markers/fences/backticks/whitespace only)."""
    s = s.replace(MARKER_CUT, " ").replace(MARKER_ILL, " ").replace("`", " ")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- extraction

def extract_urls(text: str):
    out = set()
    for m in URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;:")
        out.add(u)
    # bare domains (incl. inside URLs is fine; dedupe by normalized form later)
    plain = URL_RE.sub(" ", text)
    for m in DOMAIN_RE.finditer(plain):
        out.add(m.group(0).rstrip(".,;:"))
    return out


def extract_repos(text: str):
    t = URL_RE.sub(" ", text)
    t = DOMAIN_RE.sub(" ", t)
    out = set()
    for m in REPO_RE.finditer(t):
        seg = m.group(1)
        if "..." in seg or seg.lower().endswith((".png", ".jpg", ".md", ".json", ".sh", ".py", ".yaml", ".yml", ".toml")):
            continue
        org, name = seg.split("/", 1)
        if org and name and not org.isdigit() and len(name) >= 2:
            out.add(seg)
    return out


SHELL_SIGNAL_RE = re.compile(r"(--?[A-Za-z0-9]\b|\||>|<|\$\(|://|&&|\bsudo\b|\bbash\b)")


def looks_like_command(line: str, allow_bare: bool = False) -> bool:
    first = line.split(" ", 1)[0].strip("`*").lower()
    if first not in COMMAND_WORDS:
        return False
    rest = line[len(first):].strip()
    if not rest:
        return False
    if allow_bare:
        return True
    # A bare capitalized-prose continuation ("Omarchy   Copy link") is not a
    # shell line; require a shell signal (flag/pipe/redirect/URL/second cmd).
    return bool(SHELL_SIGNAL_RE.search(rest)) or rest[:1].islower() and " " not in rest


def extract_commands(text: str, allow_bare: bool = False):
    out = set()
    for block in FENCE_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            # Fence lines still must look like commands: a ```markdown fence
            # wraps the whole report and its prose lines are not shell.
            if line and (looks_like_command(line, allow_bare=True) or
                         SHELL_SIGNAL_RE.search(line)):
                out.add(line)
    for line in text.splitlines():
        line = line.strip().strip("*").strip()
        line = re.sub(r"^[-*+]\s+", "", line)
        if looks_like_command(line, allow_bare=allow_bare):
            out.add(line.strip("`"))
    return {c for c in out if 3 <= len(c) <= 300}


CAP_SEQ_RE = re.compile(
    r"(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñÁÉÍÓÚÑ&.+-]*"
    r"(?:['’][A-Za-z]+)?"
    r"(?:[/+& -]+(?:(?:on|of|de|da|do|das|dos|e|y|a|in|for|the|and|by|pra)\b|"
    r"[A-ZÁÉÍÓÚÑ][\wáéíóúñÁÉÍÓÚÑ&.+-]*(?:['’][A-Za-z]+)?))*)"
)
CAPS_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9]{2,}\b")


def valid_name(tok: str) -> bool:
    """Reject extraction artifacts: digit-led clock/counter fragments,
    entities too short to be a name, sentence-boundary splices, and
    standalone function-word fragments."""
    t = tok.strip()
    if not t or t[0].isdigit():
        return False
    # a captured span crossing a sentence boundary ('LibreOffice. Each')
    # is two sentences glued; keep only the first sentence's segment
    if ". " in t:
        t = t.split(". ")[0].strip()
        if not t:
            return False
    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÑáéíóúñ]", "", t)
    if len(letters) < 3:
        return False
    if len(t.split()) == 1 and t.lower() in PURE_PROSE:
        return False
    return True


def extract_names(text: str):
    """Capitalized sequences + ALL-CAPS tokens. Expects URLs/repos already
    removed by caller when desirable."""
    out = set()
    for m in CAP_SEQ_RE.finditer(text):
        tok = m.group(0).strip()
        words = tok.split()
        if words and all(w.lower() in CONNECTORS for w in words):
            continue
        if len(re.sub(r"[^A-Za-zÁÉÍÓÚÑáéíóúñ]", "", tok)) >= 2:
            out.add(tok)
    for m in CAPS_RE.finditer(text):
        out.add(m.group(0))
    out = {t for t in out if valid_name(t)}
    return out


def split_marker_entities(text: str):
    """Yield (entity, cut_flag) splitting lines on [cut] boundaries."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line in (MARKER_ILL,):
            continue
        if MARKER_ILL in line:
            continue
        cut = MARKER_CUT in line
        clean = line.replace(MARKER_CUT, "").strip()
        if clean:
            yield clean, cut


def ref_entities_for_frame(fr):
    """Extract reference entities per type with cut flags and illegible zones."""
    ents = []  # dicts: type, text, cut
    illegible_zones = []
    notes = fr.get("notes", "") or ""
    notes_illegible = ("ilegible" in notes.lower()) or ("illegible" in notes.lower())
    for region in REGIONS:
        raw = fr["text_regions"].get(region, "") if region != "overlays" else fr.get("overlays", "")
        if not raw:
            continue
        if MARKER_ILL in raw:
            illegible_zones.append(region)
            continue
        if region == "overlays":
            # overlays are Spanish descriptions; only quoted on-screen strings
            # count as entities; full text used for coverage only.
            quoted = re.findall(r"'([^']+)'", raw)
            sources = [(q, MARKER_CUT in raw) for q in quoted]
        else:
            sources = list(split_marker_entities(raw))
        for text, cut in sources:
            if cut or MARKER_CUT in text:
                cut = True
            for u in extract_urls(text):
                ents.append({"type": "url_domain", "text": u, "cut": cut, "region": region})
            nourl = URL_RE.sub(" ", text)
            for r in extract_repos(nourl):
                ents.append({"type": "repo_or_path", "text": r, "cut": cut, "region": region})
            allow_bare = region == "code_or_url"
            for c in extract_commands(text, allow_bare=allow_bare):
                ents.append({"type": "command", "text": c, "cut": cut, "region": region})
            cmd_set = extract_commands(text, allow_bare=allow_bare)
            nocmd = "\n".join(
                ln for ln in text.splitlines() if ln.strip() not in cmd_set
            )
            nocmd = FENCE_RE.sub(" ", nocmd)
            for n in extract_names(REPO_RE.sub(" ", nocmd)):
                ents.append({"type": "name", "text": n, "cut": cut, "region": region})
    # dedupe by (type, normalized)
    seen = {}
    for e in ents:
        k = (e["type"], norm(e["text"]))
        if k not in seen:
            seen[k] = e
        elif e["cut"]:
            seen[k]["cut"] = True
    if notes_illegible and not illegible_zones:
        illegible_zones.append("notes")
    return list(seen.values()), illegible_zones


def output_entities(response: str):
    ents = []
    for u in extract_urls(response):
        ents.append({"type": "url_domain", "text": u})
    nourl = URL_RE.sub(" ", response)
    for r in extract_repos(nourl):
        ents.append({"type": "repo_or_path", "text": r})
    for c in extract_commands(response):
        ents.append({"type": "command", "text": c})
    nocmd = response
    for c in extract_commands(response):
        nocmd = nocmd.replace(c, " ")
    nocmd = URL_RE.sub(" ", nocmd)
    for n in extract_names(REPO_RE.sub(" ", nocmd)):
        ents.append({"type": "name", "text": n})
    seen = {}
    for e in ents:
        k = (e["type"], norm(e["text"]))
        if k not in seen:
            seen[k] = e
    return list(seen.values())


# ---------------------------------------------------------------- matching

def lcs_len(a: str, b: str) -> int:
    m = SequenceMatcher(None, a, b)
    return max((len(bk) for bk in m.get_matching_blocks()), default=0)


def classify_entity(ent, out_norm_text, out_strict_text, out_ents_norm):
    """Return (classification, details). exact/partial/missed; cut caps at partial."""
    n = norm(ent["text"])
    s = strict_norm(ent["text"])
    cut = bool(ent.get("cut"))
    details = {}
    ci_hit = n in out_norm_text and len(n) >= 2
    strict_hit = s in out_strict_text and len(s) >= 2
    if ci_hit:
        details["case_sensitive_match"] = bool(strict_hit)
        cls = "exact"
        if cut:
            cls = "partial"
            details["capped_by_cut"] = True
        return cls, details
    # partial: entity prefix/substring appears, or a >=4-char token of the
    # entity appears, or a >=4-char common substring exists
    for oe in out_ents_norm:
        no = oe
        if len(no) >= 4 and (no in n or n in no):
            details["partial_evidence"] = f"entity-containment vs '{oe[:60]}'"
            break
        if lcs_len(n, no) >= 4:
            details["partial_evidence"] = f"common-substring vs '{oe[:60]}'"
            break
    else:
        for tok in re.split(r"[\s/]+", n):
            if len(tok) >= 4 and tok in out_norm_text:
                details["partial_evidence"] = f"token '{tok}'"
                break
        else:
            return "missed", details
    if cut:
        details["capped_by_cut"] = True
    return "partial", details


def region_coverage(ref_region: str, out_norm_text: str):
    r = norm(ref_region)
    if not r:
        return None
    toks = [t for t in re.split(r"[\s/]+", r) if len(t) >= 4]
    hit = sum(1 for t in toks if t in out_norm_text)
    ratio = SequenceMatcher(None, r, out_norm_text[: len(r) * 4 + 2000]).ratio()
    return {
        "token_coverage": round(hit / len(toks), 3) if toks else None,
        "tokens": len(toks),
        "ratio_vs_output": round(ratio, 3),
    }


# ---------------------------------------------------------------- per-frame run

def compare_frame(ref_frame, response_path: Path, comparison_type: str,
                  baseline_response_text=None):
    resp = json.load(open(response_path, encoding="utf-8"))
    text = resp.get("response", "")
    out_norm_text = norm(text)
    out_strict_text = " ".join(strict_norm(text).split())
    out_ents = output_entities(text)
    out_ents_norm = [norm(e["text"]) for e in out_ents]

    ref_ents, illegible_zones = ref_entities_for_frame(ref_frame)

    results = []
    counts = {"exact": 0, "partial": 0, "missed": 0}
    for e in ref_ents:
        cls, details = classify_entity(e, out_norm_text, out_strict_text, out_ents_norm)
        counts[cls] += 1
        row = {"type": e["type"], "reference_text": e["text"], "region": e["region"],
               "classification": cls, **details}
        if e["cut"]:
            row["reference_cut"] = True
        results.append(row)

    # invented candidates: output entities never matched to any reference entity
    # and not literally present in the full reference text (the reference is a
    # summarized labeling; strings it contains verbatim are on-screen content,
    # not hallucinations, even when not extracted as standalone entities).
    matched_out = set()
    ref_norm_all = [norm(e["text"]) for e in ref_ents]
    ref_full_norm = norm(" ".join(
        [ref_frame["text_regions"].get(r, "") for r in ("top", "middle", "bottom", "code_or_url")]
        + [ref_frame.get("overlays", "")]))
    for i, oe in enumerate(out_ents):
        no = out_ents_norm[i]
        if any(no == rn for rn in ref_norm_all):
            matched_out.add(i)
            continue
        if any(no in rn or rn in no for rn in ref_norm_all if len(rn) >= 4 and len(no) >= 4):
            matched_out.add(i)
            continue
        if any(lcs_len(no, rn) >= 4 for rn in ref_norm_all):
            matched_out.add(i)
            continue
        if len(no) >= 4 and no in ref_full_norm:
            matched_out.add(i)
    invented = []
    for i, oe in enumerate(out_ents):
        if i in matched_out:
            continue
        n = norm(oe["text"])
        if len(n) < 3:
            continue
        words = n.split()
        if is_boilerplate(n):
            continue
        invented.append({"type": oe["type"], "output_text": oe["text"],
                         "status": "unknown" if illegible_zones else "invented",
                         "note": "reference marks illegible zone(s) in this frame"
                                 if illegible_zones else None})
    inv_counts = {"invented": sum(1 for x in invented if x["status"] == "invented"),
                  "unknown": sum(1 for x in invented if x["status"] == "unknown")}
    inv_by_type = {t: sum(1 for x in invented if x["status"] == "invented" and x["type"] == t)
                   for t in ("name", "repo_or_path", "url_domain", "command")}

    coverage = {}
    for region in REGIONS:
        raw = ref_frame["text_regions"].get(region, "") if region != "overlays" else ref_frame.get("overlays", "")
        cov = region_coverage(raw, out_norm_text) if raw.strip() else None
        if cov:
            coverage[region] = cov

    total_ref = len(results)
    entry = {
        "frame": ref_frame["frame"],
        "comparison_type": comparison_type,
        "response_file": str(response_path.relative_to(ROOT)),
        "response_sha256": sha256_file(response_path),
        "response_chars": len(text),
        "reference_entities": {"total": total_ref, "exact": counts["exact"],
                               "partial": counts["partial"], "missed": counts["missed"]},
        "rates": {
            "exact_rate": round(counts["exact"] / total_ref, 3) if total_ref else None,
            "partial_rate": round(counts["partial"] / total_ref, 3) if total_ref else None,
            "missed_rate": round(counts["missed"] / total_ref, 3) if total_ref else None,
        },
        "invented_candidates": {"invented": inv_counts["invented"],
                                "unknown_due_illegible": inv_counts["unknown"],
                                "invented_by_type": inv_by_type,
                                "total_output_entities": len(out_ents)},
        "invented_rate": round(inv_counts["invented"] / len(out_ents), 3) if out_ents else 0.0,
        "illegible_zones_in_reference": illegible_zones,
        "coverage": coverage,
        "entity_comparisons": results,
        "invented_entities": invented,
    }
    if baseline_response_text is not None:
        b = baseline_response_text
        entry["baseline_vs_hybrid"] = {
            "sequence_ratio": round(SequenceMatcher(None, norm(b), norm(text)).ratio(), 3),
            "baseline_chars": len(b),
            "char_delta": len(text) - len(b),
            "entity_jaccard": _entity_jaccard(b, text),
        }
    return entry


def _entity_jaccard(a_text: str, b_text: str) -> float:
    a = {(e["type"], norm(e["text"])) for e in output_entities(a_text)}
    b = {(e["type"], norm(e["text"])) for e in output_entities(b_text)}
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def divergence_score(entry):
    r = entry["rates"]
    inv = entry["invented_rate"]
    if entry["reference_entities"]["total"] == 0:
        # blank reference frame: nothing to miss and no coverage baseline;
        # divergence is just the invented-candidate rate
        return round(inv, 3)
    cov_tokens = [c["token_coverage"] for c in entry["coverage"].values() if c["token_coverage"] is not None]
    cov = statistics.mean(cov_tokens) if cov_tokens else 0.0
    return round((r["missed_rate"] or 0) + inv + (1 - cov), 3)


# ---------------------------------------------------------------- main

def main():
    ref_doc = json.load(open(REF_FILE, encoding="utf-8"))
    ref_by_frame = {f["frame"]: f for f in ref_doc["frames"]}

    baseline_files = {}
    for key, cfg in RUNS.items():
        d = VISION / cfg["dir"]
        baseline_files[key] = {
            m.group(1) + ".png": p for p in sorted(d.glob("*-response.json"))
            if (m := re.match(r"\d+-(frame_\d+)-response\.json", p.name))
        }

    models_out = {}
    for key, cfg in RUNS.items():
        frames = []
        for frame_name in sorted(baseline_files[key]):
            entry = compare_frame(ref_by_frame[frame_name], baseline_files[key][frame_name],
                                  comparison_type="baseline-vs-reference")
            entry["divergence_score"] = divergence_score(entry)
            frames.append(entry)
        n = len(frames)
        agg_counts = {c: sum(f["reference_entities"][c] for f in frames)
                      for c in ("total", "exact", "partial", "missed")}
        inv = {c: sum(f["invented_candidates"][c] for f in frames)
               for c in ("invented", "unknown_due_illegible", "total_output_entities")}
        inv_type = {t: sum(f["invented_candidates"]["invented_by_type"][t] for f in frames)
                    for t in ("name", "repo_or_path", "url_domain", "command")}
        cov_tokens = [c["token_coverage"] for f in frames for c in f["coverage"].values()
                      if c["token_coverage"] is not None]
        models_out[key] = {
            "model": cfg["model"], "phase": cfg["phase"], "run_dir": f"results/vision/{cfg['dir']}",
            "n_frames": n,
            "aggregate_reference_entities": agg_counts,
            "aggregate_rates": {
                "exact_rate": round(agg_counts["exact"] / agg_counts["total"], 3),
                "exact_rate_strict_case": round(
                    sum(1 for f in frames for e in f["entity_comparisons"]
                        if e["classification"] == "exact" and e.get("case_sensitive_match"))
                    / agg_counts["total"], 3),
                "partial_rate": round(agg_counts["partial"] / agg_counts["total"], 3),
                "missed_rate": round(agg_counts["missed"] / agg_counts["total"], 3),
                "invented_rate": round(inv["invented"] / inv["total_output_entities"], 3)
                if inv["total_output_entities"] else 0.0,
            },
            "invented_candidates": {**inv, "invented_by_type": inv_type,
                                    "invented_specific": inv_type["repo_or_path"]
                                    + inv_type["url_domain"] + inv_type["command"]},
            "median_response_chars": int(statistics.median(f["response_chars"] for f in frames)),
            "mean_coverage_tokens": round(statistics.mean(cov_tokens), 3),
            "per_frame": frames,
        }

    # hybrid pairs (3-B) — identical frames re-sampled by qwen2.5vl
    pairs_doc = json.load(open(ROOT / "results" / "analysis-pairs.json", encoding="utf-8"))
    hybrid_out = []
    for pair in pairs_doc["pairs_exact"]:
        frame_name = pair["frame"]
        run_b = pair["run_B"]
        idx = pair["hybrid_index"]
        resp_path = VISION / run_b / f"{idx:03d}-hybrid_{idx:03d}-response.json"
        if not resp_path.exists():
            raise FileNotFoundError(resp_path)
        base_path = baseline_files["qwen2.5vl"][frame_name]
        base_text = json.load(open(base_path, encoding="utf-8")).get("response", "")
        entry = compare_frame(ref_by_frame[frame_name], resp_path,
                              comparison_type="hybrid-vs-baseline",
                              baseline_response_text=base_text)
        entry["divergence_score"] = divergence_score(entry)
        entry["hybrid"] = pair["hybrid"]
        entry["hybrid_index"] = idx
        entry["hybrid_run"] = run_b
        entry["baseline_run"] = pair["run_A"]
        entry["frame_sha256_identical_source"] = pair["sha256"]
        hybrid_out.append(entry)

    provenance = {
        "reference_file": "ground-truth/human-labels.json",
        "reference_sha256": sha256_file(REF_FILE),
        "reference_labeler": ref_doc["labeler"],
        "reference_labeling_method": ref_doc["labeling_method"],
        "reference_status": "VLM-assisted (Gemini Flash 3.8 High), human-verified; "
                            "NOT independent blind human ground truth",
        "pairs_source": "results/analysis-pairs.json",
        "pairs_source_sha256": sha256_file(ROOT / "results" / "analysis-pairs.json"),
        "generator": "scripts/compare_ground_truth.py",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": "CPU-only textual analysis; difflib (rapidfuzz unavailable)",
    }

    doc = {
        "provenance": provenance,
        "reference_status_section": (
            "The reference (human-labels.json) was transcribed with Gemini Flash 3.8 High "
            "assistance and then reviewed by the human labeler. It is a STRONG-MODEL "
            "REFERENCE verified by a human, NOT independent blind human ground truth. "
            "All rates below measure AGREEMENT/CONCORDANCE with that reference, never "
            "absolute accuracy. Circularities: (1) a cloud VLM anchors the reference, so "
            "local models that read like that cloud VLM score higher even when both are "
            "wrong; (2) the human review step is itself post-hoc against the VLM draft. "
            "Treat rankings as relative concordance only."
        ),
        "method": {
            "entity_types": ["name", "repo_or_path", "url_domain", "command"],
            "region_text_handling": "regions (top/middle/bottom/code_or_url/overlays) are "
                                    "compared via token coverage + difflib ratio, not as atomic entities",
            "markers": {
                "[cut]": "reference entity truncated -> classification capped at partial (partial-max)",
                "[illegible]": "no entities extracted from that region; output-only entities there "
                               "are marked unknown instead of invented",
            },
            "classification": {
                "exact": "entity (normalized: markers/backticks stripped, whitespace collapsed) "
                         "contained in output; case-insensitive containment counts as exact and "
                         "case_sensitive_match records the strict result (URLs/repos are "
                         "case-insensitive per canonical form)",
                "partial": "substring/prefix/token (>=4 chars) or common substring (>=4 chars) found",
                "missed": "absent from output",
                "invented": "output entity not matched to any reference entity (hallucination candidate)",
                "unknown": "invented candidate during a frame whose reference has an illegible zone",
            },
            "definitions": {
                "exact_rate": "exact / reference entities",
                "missed_rate": "missed / reference entities",
                "invented_rate": "invented / output entities (excludes unknowns)",
                "divergence_score": "missed_rate + invented_rate + (1 - mean token coverage)",
            },
            "scope": "14 baseline frames x 3 configs (42 comparisons) + 11 byte-identical "
                     "hybrid pairs re-scored for qwen2.5vl (hybrid-vs-baseline). Non-identical "
                     "3-B frames are NOT compared against reference frames they do not depict.",
        },
        "models": models_out,
        "hybrid_pairs_qwen2.5vl": hybrid_out,
    }

    OUT_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_md(doc)
    print("WROTE", OUT_JSON)
    print("WROTE", OUT_MD)
    for k, v in models_out.items():
        r = v["aggregate_rates"]
        print(f"{k}: exact={r['exact_rate']} partial={r['partial_rate']} "
              f"missed={r['missed_rate']} invented={r['invented_rate']} "
              f"cov={v['mean_coverage_tokens']} med_chars={v['median_response_chars']}")


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def write_md(doc):
    prov = doc["provenance"]
    L = []
    L.append("# VLM outputs vs. reference labels — entity-level concordance\n")
    L.append(
        "**Reference status: VLM-assisted, human-verified, not blind human ground truth.** "
        "The reference (`ground-truth/human-labels.json`) was transcribed with Gemini Flash 3.8 High "
        "and reviewed by the human labeler. Every rate below is **concordance with that reference**, "
        "never accuracy. Because the reference itself is anchored on a strong cloud VLM, the ranking "
        "carries a circularity limitation (see below).\n")
    L.append(
        "This report compares the textual outputs of the three local VLM configs "
        "(qwen2.5vl:7b phase 3-A, qwen3.5:9b phase 4-1, qwen3.8:27b phase 4-2) against the "
        "reference labels over the 14 baseline frames, plus the 11 byte-identical 3-B hybrid "
        "pairs for qwen2.5vl (sampling analysis). Full entity-by-entity data with provenance: "
        "`ground-truth/ground-truth-comparison.json`. Existing `results/comparison.md` is "
        "untouched; this file is additive.\n")

    L.append("## Quick path\n")
    L.append("1. Read the ranking table below for the headline concordance numbers.")
    L.append("2. Check per-frame divergences before drawing conclusions — medians hide outliers.")
    L.append("3. Read the sampling section if you care about 64-frame hybrid stability.")
    L.append("4. Any number can be audited in the JSON (every entity carries its classification).\n")

    L.append("## Per-model concordance with reference (14 frames each)\n")
    L.append("| Model | Phase | Exact | Strict-case exact | Partial | Missed | Invented rate | Mean token coverage | Median chars |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    ranking = sorted(doc["models"].values(),
                     key=lambda m: (-(m["aggregate_rates"]["exact_rate"]
                                      + 0.5 * m["aggregate_rates"]["partial_rate"]
                                      - m["aggregate_rates"]["missed_rate"]),
                                    -m["mean_coverage_tokens"]))
    for m in ranking:
        r = m["aggregate_rates"]
        L.append(f"| `{m['model']}` | {m['phase']} | {r['exact_rate']:.1%} | "
                 f"{r['exact_rate_strict_case']:.1%} | {r['partial_rate']:.1%} | "
                 f"{r['missed_rate']:.1%} | {r['invented_rate']:.1%} | "
                 f"{m['mean_coverage_tokens']:.1%} | {m['median_response_chars']} |")
    L.append("")
    L.append("Counts: " + " · ".join(
        f"`{m['model']}` exact/partial/missed = "
        f"{m['aggregate_reference_entities']['exact']}/{m['aggregate_reference_entities']['partial']}/"
        f"{m['aggregate_reference_entities']['missed']} of {m['aggregate_reference_entities']['total']} reference "
        f"entities; invented {m['invented_candidates']['invented']} (+"
        f"{m['invented_candidates']['unknown_due_illegible']} unknown) of "
        f"{m['invented_candidates']['total_output_entities']} output entities."
        for m in doc["models"].values()) + "\n")
    L.append(f"**Ranking by composite concordance** (exact + 0.5·partial − missed): "
             + " > ".join(f"`{m['model']}`" for m in ranking) + "\n")

    L.append("## Per-frame divergences (top 3 per model by divergence score)\n")
    for key in ("qwen2.5vl", "qwen3.5", "qwen3.8"):
        m = doc["models"][key]
        L.append(f"### `{m['model']}` ({m['phase']})\n")
        L.append("| Frame | Exact | Missed | Invented | Coverage | Divergence | What went wrong |")
        L.append("|---|---|---|---|---|---|---|")
        worst = sorted(m["per_frame"], key=lambda f: -f["divergence_score"])[:3]
        for f in worst:
            miss = [e for e in f["entity_comparisons"] if e["classification"] == "missed"]
            inv = [e for e in f["invented_entities"] if e["status"] == "invented"]
            bits = []
            if miss:
                bits.append("missed: " + "; ".join(f"{e['type']} '{e['reference_text'][:40]}'" for e in miss[:3]))
            if inv:
                bits.append("invented: " + "; ".join(f"'{e['output_text'][:40]}'" for e in inv[:3]))
            cov_tokens = [c["token_coverage"] for c in f["coverage"].values() if c["token_coverage"] is not None]
            cov = f"{statistics.mean(cov_tokens):.0%}" if cov_tokens else "n/a"
            L.append(f"| {f['frame'].replace('.png','')} | {pct(f['rates']['exact_rate'])} | "
                     f"{pct(f['rates']['missed_rate'])} | {pct(f['invented_rate'])} | {cov} | "
                     f"{f['divergence_score']:.2f} | {' — '.join(bits) if bits else '—'} |")
        L.append("")

    L.append("Note: `frame_01` is a blank reference frame (no reference entities), so exact/"
             "missed/coverage are n/a there; only invented candidates apply. On dense "
             "system-monitor frames (`frame_14`, `frame_17`, `frame_22`) the reference is a "
             "summarized labeling, so output-only fragments that are plausibly real on-screen "
             "readings (htop process rows, benchmark cells) remain flagged as invented — treat "
             "the invented rate as an upper bound there.\n")

    L.append("## Sampling findings: 11 byte-identical hybrid pairs (3-B, qwen2.5vl)\n")
    L.append(
        "These 11 hybrids are byte-identical re-samplings of baseline frames "
        "(sha256-verified in `results/analysis-pairs.json`), so any output difference is "
        "pure sampling stochasticity under temperature, not content difference. They are "
        "scored against the same reference frames as their baselines "
        "(`comparison_type: hybrid-vs-baseline`); non-identical 3-B frames were **not** "
        "compared against reference frames they do not depict.\n")
    L.append("| Frame | Hybrid | Baseline vs hybrid seq. ratio | Entity Jaccard | Hybrid exact vs ref | Baseline exact vs ref | Char delta |")
    L.append("|---|---|---|---|---|---|---|")
    base_by_frame = {f["frame"]: f for f in doc["models"]["qwen2.5vl"]["per_frame"]}
    ratios, jaccs = [], []
    for h in doc["hybrid_pairs_qwen2.5vl"]:
        b = base_by_frame[h["frame"]]
        ratios.append(h["baseline_vs_hybrid"]["sequence_ratio"])
        jaccs.append(h["baseline_vs_hybrid"]["entity_jaccard"])
        L.append(f"| {h['frame'].replace('.png','')} | {h['hybrid'].replace('.png','')} | "
                 f"{h['baseline_vs_hybrid']['sequence_ratio']:.3f} | "
                 f"{h['baseline_vs_hybrid']['entity_jaccard']:.3f} | "
                 f"{pct(h['rates']['exact_rate'])} | {pct(b['rates']['exact_rate'])} | "
                 f"{h['baseline_vs_hybrid']['char_delta']:+d} |")
    L.append("")
    L.append(f"- Median baseline-vs-hybrid sequence ratio: {statistics.median(ratios):.3f} "
             f"(min {min(ratios):.3f}, max {max(ratios):.3f}).")
    L.append(f"- Median entity-set Jaccard: {statistics.median(jaccs):.3f} "
             f"(min {min(jaccs):.3f}, max {max(jaccs):.3f}).")
    n_stable = sum(1 for r in ratios if r >= 0.9)
    L.append(f"- {n_stable}/{len(ratios)} pairs reproduce their baseline output at ≥0.9 sequence ratio.\n")

    L.append("## Reference status and circularity limitation\n")
    L.append("- **Status**: the reference is a strong-model (Gemini Flash 3.8 High) transcription "
             "reviewed post-hoc by the human labeler, who reports no significant divergence from "
             "on-screen reality. It is **not** an independent blind human ground truth.")
    L.append("- **Consequence**: all metrics here are *agreement with the reference*, not accuracy. "
             "A local model can score high by mirroring the cloud VLM's reading habits and still be wrong.")
    L.append("- **Circularity**: the human verification step checked the VLM draft against the frame, "
             "so residual cloud-VLM errors that a human would not independently reproduce are not "
             "corrected; concordance with the reference is therefore an upper bound on correctness, "
             "not an unbiased estimate of it.")
    L.append("- **Markers honored**: reference `[cut]` entities are capped at partial (partial-max); "
             "reference `[illegible]` zones suppress entity extraction and downgrade output-only "
             "entities there from invented to unknown.")
    L.append("- **Method availability**: deterministic CPU pipeline in `scripts/compare_ground_truth.py`; "
             "every entity decision is stored in `ground-truth/ground-truth-comparison.json` with the "
             "sha256 of the reference and of each response file.\n")

    L.append("## Checklist\n")
    L.append("- [ ] Ranking read as concordance, not accuracy")
    L.append("- [ ] Top divergent frames inspected before recommending a model")
    L.append("- [ ] Hybrid-pair variability considered when generalizing 14-frame results to 64 frames")
    L.append("- [ ] `ground-truth/ground-truth-comparison.json` parsed and audited spot checks\n")

    L.append("## Next step\n")
    L.append("Cross-check this concordance view against the resource/latency trade-off analysis in "
             "`results/comparison.md` and the decision in `results/recommendation.md` before finalizing "
             "the model choice.\n")

    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
