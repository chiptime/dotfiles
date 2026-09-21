#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone


PROMPT = (
    "You are an expert OCR and code extraction analyst examining a single frame from a technical video screencast.\n"
    "Analyze what is visible on the screen with rigorous precision.\n"
    "Extract and report in markdown format:\n"
    "1. **Context**: Application/window (IDE, terminal, browser, presentation, etc.)\n"
    "2. **Visible Headings & Titles**: Any window titles, tab titles, or prominent on-screen text.\n"
    "3. **Code & Commands**: Transcribe verbatim any visible code snippets, shell commands, or configuration lines. Maintain exact characters, symbols, and formatting.\n"
    "4. **Identified Names & URLs**: Tool names, repository names, package names, web addresses, or file paths visible on screen.\n"
    "5. **Legibility & Ambiguity**: Explicitly note any text that is blurry, truncated, or ambiguous as [illegible] or [uncertain]. NEVER guess, infer, or complete URLs, repository names, or code syntax from prior knowledge."
)


def shell(args):
    return subprocess.run(args, text=True, capture_output=True)


def gpu():
    result = shell(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"])
    total, used, free = [int(x.strip()) for x in result.stdout.strip().split(",")]
    return {"total_mib": total, "used_mib": used, "free_mib": free}


def vmstat():
    values = {}
    with open("/proc/vmstat", "r", encoding="utf-8") as handle:
        for line in handle:
            key, value = line.split()
            if key in {"pswpin", "pswpout"}:
                values[key] = int(value)
    values["page_size_bytes"] = os.sysconf("SC_PAGE_SIZE")
    values["swap_io_bytes"] = (values["pswpin"] + values["pswpout"]) * values["page_size_bytes"]
    return values


def request(url, payload, timeout):
    encoded = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return raw, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:11435/api/generate")
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--think", choices=["true", "false"], default=None)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--vram-limit-mib", type=int, required=True)
    parser.add_argument("--require-free-mib", type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    frames = sorted(name for name in os.listdir(args.frames_dir) if name.endswith(".png")) if args.frames == "*" else args.frames.split(",")
    baseline = gpu()
    if baseline["free_mib"] < args.require_free_mib:
        raise RuntimeError("free VRAM gate failed: %s" % baseline)
    swap_before = vmstat()
    entries = []
    peak = baseline["used_mib"]
    for index, name in enumerate(frames, 1):
        path = os.path.join(args.frames_dir, name)
        raw_image = open(path, "rb").read()
        payload = {
            "model": args.model,
            "prompt": PROMPT,
            "images": [base64.b64encode(raw_image).decode("ascii")],
            "stream": False,
            "keep_alive": "15m",
            "options": {"temperature": 0.1, "num_predict": 1024, "num_ctx": args.num_ctx},
        }
        if args.think is not None:
            payload["think"] = args.think == "true"
        payload_file = os.path.join(args.output_dir, "%03d-%s-payload.json" % (index, name[:-4]))
        raw_file = os.path.join(args.output_dir, "%03d-%s-response.json" % (index, name[:-4]))
        with open(payload_file, "w", encoding="utf-8") as handle:
            payload_record = dict(payload)
            payload_record["images"] = ["base64:sha256:" + hashlib.sha256(raw_image).hexdigest()]
            json.dump(payload_record, handle, indent=2)
        before = gpu()
        raw, elapsed = request(args.url, payload, args.timeout)
        with open(raw_file, "w", encoding="utf-8") as handle:
            handle.write(raw)
        after = gpu()
        peak = max(peak, after["used_mib"])
        parsed = json.loads(raw)
        entries.append({
            "frame": name,
            "sha256": hashlib.sha256(raw_image).hexdigest(),
            "payload_file": payload_file,
            "raw_response_file": raw_file,
            "elapsed_seconds": elapsed,
            "vram_before": before,
            "vram_after": after,
            "response_text": parsed.get("response", ""),
            "done_reason": parsed.get("done_reason"),
            "load_duration_ns": parsed.get("load_duration"),
            "prompt_eval_duration_ns": parsed.get("prompt_eval_duration"),
            "eval_duration_ns": parsed.get("eval_duration"),
        })
        if after["used_mib"] - baseline["used_mib"] > args.vram_limit_mib:
            raise RuntimeError("VRAM delta gate exceeded after %s" % name)
        current_swap = vmstat()
        if current_swap["swap_io_bytes"] - swap_before["swap_io_bytes"] > 536870912:
            raise RuntimeError("swap I/O gate exceeded after %s" % name)
    final_swap = vmstat()
    summary = {
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "model": args.model,
        "frames": frames,
        "prompt": PROMPT,
        "options": {"temperature": 0.1, "num_predict": 1024, "num_ctx": args.num_ctx, "think": None if args.think is None else args.think == "true"},
        "baseline_gpu": baseline,
        "peak_vram_total_mib": peak,
        "peak_vram_delta_mib": peak - baseline["used_mib"],
        "swap_before": swap_before,
        "swap_after": final_swap,
        "swap_io_delta_bytes": final_swap["swap_io_bytes"] - swap_before["swap_io_bytes"],
        "entries": entries,
    }
    with open(os.path.join(args.output_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
