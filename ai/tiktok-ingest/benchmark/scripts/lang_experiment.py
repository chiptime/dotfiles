#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime, timezone

import ctranslate2
import faster_whisper
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps


AUDIO = "/work/media/audio.wav"
OUTPUT = "/work/results/lang/language-results.json"
RAW_A = "/work/results/lang/raw-segments-a-autodetect.json"
RAW_B = "/work/results/lang/raw-segments-b-pinned.json"
MODEL_PATH = "/root/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3/snapshots/edaa852ec7e145841d8ffdb056a99866b5f0a478"
SAMPLE_RATE = 16000
WINDOW_SECONDS = 30.0
VAD_OPTIONS = VadOptions(
    threshold=0.5,
    neg_threshold=None,
    min_speech_duration_ms=250,
    max_speech_duration_s=float("inf"),
    min_silence_duration_ms=2000,
    speech_pad_ms=400,
)


def now():
    return datetime.now(timezone.utc).astimezone().isoformat()


def gpu_used():
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        text=True,
        capture_output=True,
        check=True,
    )
    return int(proc.stdout.strip().splitlines()[0])


def vmstat():
    fields = {}
    with open("/proc/vmstat", "r", encoding="utf-8") as handle:
        for line in handle:
            key, value = line.split()
            if key in {"pswpin", "pswpout"}:
                fields[key] = int(value)
    fields["page_size_bytes"] = os.sysconf("SC_PAGE_SIZE")
    fields["swap_io_bytes"] = (fields["pswpin"] + fields["pswpout"]) * fields["page_size_bytes"]
    return fields


def coverage(intervals, start, end):
    return sum(max(0.0, min(end, b) - max(start, a)) for a, b in intervals)


def select_windows(audio, speech_chunks):
    duration = len(audio) / SAMPLE_RATE
    intervals = [(chunk["start"] / SAMPLE_RATE, chunk["end"] / SAMPLE_RATE) for chunk in speech_chunks]
    if not intervals:
        raise RuntimeError("VAD found no useful vocal interval")

    def best(candidates):
        scored = [(coverage(intervals, start, min(duration, start + WINDOW_SECONDS)), start) for start in candidates]
        score, start = max(scored, key=lambda item: (item[0], -item[1]))
        return start, min(duration, start + WINDOW_SECONDS), score

    early_limit = min(max(0.0, duration - WINDOW_SECONDS), 60.0)
    early_candidates = np.linspace(0.0, early_limit, max(2, int(early_limit * 2) + 1))
    center_target = duration / 2.0
    center_min = max(0.0, center_target - 30.0)
    center_max = min(max(0.0, duration - WINDOW_SECONDS), center_target + 30.0)
    center_candidates = np.linspace(center_min, center_max, max(2, int(max(1.0, center_max - center_min) * 2) + 1))
    first = best(early_candidates)
    middle = best(center_candidates)
    return [first, middle], intervals


def detect(model, audio, start, end):
    sample = audio[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)]
    language, probability, probabilities = model.detect_language(
        sample,
        language_detection_segments=1,
        language_detection_threshold=0.5,
        vad_filter=False,
    )
    return {
        "start_seconds": round(start, 6),
        "end_seconds": round(end, 6),
        "duration_seconds": round(end - start, 6),
        "language": language,
        "probability_uncalibrated": probability,
        "all_probabilities_uncalibrated": probabilities,
        "detect_language_parameters": {
            "language_detection_segments": 1,
            "language_detection_threshold": 0.5,
            "vad_filter": False,
            "input_type": "decoded float32 numpy array slice",
        },
    }


def transcribe(model, audio_path, language):
    started = time.monotonic()
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=3,
        task="transcribe",
        condition_on_previous_text=False,
        vad_filter=False,
    )
    raw = []
    for segment in segments:
        raw.append({
            "id": segment.id,
            "seek": segment.seek,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "tokens": segment.tokens,
            "temperature": segment.temperature,
            "avg_logprob": segment.avg_logprob,
            "compression_ratio": segment.compression_ratio,
            "no_speech_prob": segment.no_speech_prob,
        })
    return {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "language": info.language,
        "language_probability_uncalibrated": info.language_probability,
        "duration": info.duration,
        "duration_after_vad": info.duration_after_vad,
        "full_text": "".join(item["text"] for item in raw).strip(),
        "segments": raw,
    }


def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    started_at = now()
    phase_swap_before = vmstat()
    phase_vram_before = gpu_used()
    audio = decode_audio(AUDIO, sampling_rate=SAMPLE_RATE)
    speech_chunks = get_speech_timestamps(audio, VAD_OPTIONS, sampling_rate=SAMPLE_RATE)
    windows, voiced_intervals = select_windows(audio, speech_chunks)

    load_started = time.monotonic()
    model = WhisperModel(
        MODEL_PATH,
        device="cuda",
        compute_type="float16",
        local_files_only=True,
    )
    load_seconds = round(time.monotonic() - load_started, 6)
    load_vram = gpu_used()

    a = transcribe(model, AUDIO, None)
    detections = [detect(model, audio, start, end) for start, end, _ in windows]
    if detections[0]["language"] != detections[1]["language"]:
        duration = len(audio) / SAMPLE_RATE
        used = [(item[0], item[1]) for item in windows]
        candidates = np.linspace(0.0, max(0.0, duration - WINDOW_SECONDS), max(2, int(duration * 2)))
        candidates = [x for x in candidates if all(x + WINDOW_SECONDS <= a0 or x >= b0 for a0, b0 in used)]
        if not candidates:
            raise RuntimeError("language detections disagreed and no non-overlapping third window was available")
        scored = [(coverage(voiced_intervals, x, min(duration, x + WINDOW_SECONDS)), x) for x in candidates]
        _, start = max(scored, key=lambda item: item[0])
        detections.append(detect(model, audio, start, min(duration, start + WINDOW_SECONDS)))

    votes = {}
    for item in detections:
        lang = item["language"]
        votes.setdefault(lang, []).append(item["probability_uncalibrated"])
    selected_language = max(votes, key=lambda lang: (len(votes[lang]), sum(votes[lang]) / len(votes[lang])))
    if selected_language != "es":
        raise RuntimeError("Spanish-only gate failed: selected language was %s" % selected_language)
    b = transcribe(model, AUDIO, selected_language)

    with open(RAW_A, "w", encoding="utf-8") as handle:
        json.dump(a["segments"], handle, ensure_ascii=False, indent=2)
    with open(RAW_B, "w", encoding="utf-8") as handle:
        json.dump(b["segments"], handle, ensure_ascii=False, indent=2)

    phase_swap_after = vmstat()
    phase_vram_after = gpu_used()
    result = {
        "status": "completed",
        "started_at": started_at,
        "completed_at": now(),
        "runtime": {
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
            "model": "large-v3",
            "model_path": MODEL_PATH,
            "device": "cuda",
            "compute_type": "float16",
            "beam_size": 3,
            "model_load_seconds": load_seconds,
        },
        "input": {"path": AUDIO, "decoded_samples": len(audio), "sample_rate": SAMPLE_RATE},
        "vad_selection": {
            "purpose": "select useful vocal windows only; transcription uses the untouched original WAV",
            "parameters": {
                "threshold": 0.5,
                "neg_threshold": None,
                "min_speech_duration_ms": 250,
                "max_speech_duration_s": "inf",
                "min_silence_duration_ms": 2000,
                "speech_pad_ms": 400,
            },
            "voiced_intervals_seconds": voiced_intervals,
            "selected_windows": [
                {"start_seconds": float(start), "end_seconds": float(end), "voiced_coverage_seconds": float(score)}
                for start, end, score in windows
            ],
        },
        "configuration_a_autodetect": a,
        "configuration_b_detection_samples": detections,
        "configuration_b_selected_language": selected_language,
        "configuration_b_explicit_language": b,
        "english_status": "pending_no_input",
        "metrics": {
            "vram_total_before_mib": phase_vram_before,
            "vram_total_after_mib": phase_vram_after,
            "vram_total_after_load_mib": load_vram,
            "vram_load_delta_mib": load_vram - phase_vram_before,
            "swap_before": phase_swap_before,
            "swap_after": phase_swap_after,
            "swap_io_delta_bytes": phase_swap_after["swap_io_bytes"] - phase_swap_before["swap_io_bytes"],
            "swap_io_limit_bytes": 536870912,
        },
        "parameters": {
            "transcription_input": "original full WAV",
            "task": "transcribe",
            "condition_on_previous_text": False,
            "vad_filter": False,
        },
        "raw_segment_files": [RAW_A, RAW_B],
    }
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    if result["metrics"]["swap_io_delta_bytes"] > result["metrics"]["swap_io_limit_bytes"]:
        raise RuntimeError("swap I/O gate exceeded")


if __name__ == "__main__":
    main()
