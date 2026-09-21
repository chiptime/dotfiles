"""Milestone-3 gate logic: parsers, checks, boundaries, ledger stop rule."""

from __future__ import annotations

import unittest

from tiktok_ingest.gates import (
    GATE_FREE_VRAM,
    GATE_OFFLOAD,
    GATE_SWAP_DELTA,
    GATE_UNLOAD,
    GATE_VRAM_DELTA,
    GateLedger,
    GateParseError,
    GateViolation,
    check_free_vram,
    check_no_offload,
    check_swap_delta,
    check_unload,
    check_vram_delta,
    parse_layer_report,
    parse_nvidia_smi_mib,
    parse_ollama_ps,
    parse_vmstat_swap,
)

MIB = 1024 * 1024


class NvidiaSmiParsingTests(unittest.TestCase):
    def test_parses_benchmark_query_output(self) -> None:
        gpu = parse_nvidia_smi_mib("24564, 1432, 23132\n")
        self.assertEqual(gpu.total_mib, 24564)
        self.assertEqual(gpu.used_mib, 1432)
        self.assertEqual(gpu.free_mib, 23132)

    def test_tolerates_extra_whitespace(self) -> None:
        gpu = parse_nvidia_smi_mib("  100 ,  40 ,  60  ")
        self.assertEqual((gpu.total_mib, gpu.used_mib, gpu.free_mib), (100, 40, 60))

    def test_empty_output_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("")

    def test_wrong_field_count_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("24564, 1432")
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("24564, 1432, 23132, 7")

    def test_non_integer_or_negative_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("24564, abcd, 23132")
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("24564, -5, 23132")

    def test_multi_gpu_row_fails_closed(self) -> None:
        with self.assertRaises(GateParseError):
            parse_nvidia_smi_mib("24564, 1432, 23132\n24564, 10, 100")


class VmstatParsingTests(unittest.TestCase):
    VMSTAT = (
        "nr_free_pages 29138857\n"
        "pswpin 12094382\n"
        "pgfault 912345678\n"
        "pswpout 3321890\n"
    )

    def test_parses_swap_counters_and_computes_bytes(self) -> None:
        snapshot = parse_vmstat_swap(self.VMSTAT, page_size_bytes=4096)
        self.assertEqual(snapshot.pswpin_pages, 12094382)
        self.assertEqual(snapshot.pswpout_pages, 3321890)
        self.assertEqual(
            snapshot.swap_io_bytes, (12094382 + 3321890) * 4096
        )

    def test_missing_counter_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_vmstat_swap("pswpin 1\n", page_size_bytes=4096)

    def test_non_integer_counter_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_vmstat_swap("pswpin x\npswpout 2\n", page_size_bytes=4096)

    def test_bad_page_size_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_vmstat_swap(self.VMSTAT, page_size_bytes=0)


class FreeVramGateTests(unittest.TestCase):
    def _gpu(self, free: int, used: int = 1432, total: int = 24564):
        return parse_nvidia_smi_mib(f"{total}, {used}, {free}")

    def test_above_requirement_passes(self) -> None:
        result = check_free_vram(self._gpu(free=23000), 20480)
        self.assertTrue(result.passed)

    def test_exact_boundary_passes(self) -> None:
        result = check_free_vram(self._gpu(free=20480), 20480)
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, GATE_FREE_VRAM)

    def test_below_requirement_fails(self) -> None:
        result = check_free_vram(self._gpu(free=20479), 20480)
        self.assertFalse(result.passed)
        self.assertIn("20479", result.reason)


class VramDeltaGateTests(unittest.TestCase):
    def test_within_limit_passes(self) -> None:
        result = check_vram_delta(current_used_mib=8400, baseline_used_mib=2443, limit_mib=12288)
        self.assertTrue(result.passed)
        self.assertEqual(result.detail["delta_mib"], 5957)

    def test_exact_boundary_passes(self) -> None:
        result = check_vram_delta(2443 + 12288, 2443, 12288)
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, GATE_VRAM_DELTA)

    def test_over_limit_fails(self) -> None:
        result = check_vram_delta(2443 + 12289, 2443, 12288)
        self.assertFalse(result.passed)
        self.assertIn("12289", result.reason)

    def test_measured_from_immediate_baseline_not_session(self) -> None:
        # The rule: delta = current - IMMEDIATE baseline. With a lower
        # session baseline the numbers differ; the gate must use the value
        # it is given (callers pass the immediate one).
        immediate = 6000
        result = check_vram_delta(15000, immediate, 12288)
        self.assertTrue(result.passed)  # 9000 <= 12288 from immediate
        from_session = check_vram_delta(15000, 2443, 12288)
        self.assertFalse(from_session.passed)


class SwapDeltaGateTests(unittest.TestCase):
    LIMIT_BYTES = 512 * MIB

    def test_within_limit_passes(self) -> None:
        result = check_swap_delta(
            current_bytes=100 * MIB, baseline_bytes=60 * MIB, limit_bytes=self.LIMIT_BYTES
        )
        self.assertTrue(result.passed)

    def test_exact_boundary_passes(self) -> None:
        result = check_swap_delta(512 * MIB, 0, self.LIMIT_BYTES)
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, GATE_SWAP_DELTA)

    def test_over_limit_fails_with_stop_language(self) -> None:
        result = check_swap_delta(512 * MIB + 1, 0, self.LIMIT_BYTES)
        self.assertFalse(result.passed)
        self.assertIn("final for this authorization window", result.reason)


class OffloadGateTests(unittest.TestCase):
    def test_full_residency_passes(self) -> None:
        result = check_no_offload((66, 66))
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, GATE_OFFLOAD)

    def test_any_offload_fails_as_a_failed_run(self) -> None:
        result = check_no_offload((65, 66))
        self.assertFalse(result.passed)
        self.assertIn("failed run, not a slow run", result.reason)

    def test_missing_report_fails_closed(self) -> None:
        result = check_no_offload(None)
        self.assertFalse(result.passed)
        self.assertIn("failing closed", result.reason)


class LayerReportParsingTests(unittest.TestCase):
    def test_parses_offloaded_and_offloading_forms(self) -> None:
        self.assertEqual(parse_layer_report("offloaded 29/29 layers to GPU"), (29, 29))
        self.assertEqual(parse_layer_report("offloading 12/34 layers to GPU"), (12, 34))

    def test_last_match_wins(self) -> None:
        text = "offloaded 29/29 layers to GPU\noffloaded 28/29 layers to GPU"
        self.assertEqual(parse_layer_report(text), (28, 29))

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(parse_layer_report("nothing relevant here"))

    def test_implausible_report_raises(self) -> None:
        with self.assertRaises(GateParseError):
            parse_layer_report("offloaded 30/29 layers to GPU")


class OllamaPsParsingTests(unittest.TestCase):
    def test_empty_residency(self) -> None:
        self.assertEqual(parse_ollama_ps('{"models": []}'), [])

    def test_resident_models(self) -> None:
        document = '{"models": [{"name": "qwen3.5:9b", "size": 1}]}'
        entries = parse_ollama_ps(document)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "qwen3.5:9b")

    def test_malformed_fails(self) -> None:
        with self.assertRaises(GateParseError):
            parse_ollama_ps("not json")
        with self.assertRaises(GateParseError):
            parse_ollama_ps('{"nope": []}')


class UnloadVerificationTests(unittest.TestCase):
    def test_empty_residency_and_released_vram_passes(self) -> None:
        result = check_unload([], post_used_mib=2514, session_baseline_used_mib=2443, tolerance_mib=512)
        self.assertTrue(result.passed)
        self.assertEqual(result.gate, GATE_UNLOAD)

    def test_boundary_overshoot_passes(self) -> None:
        result = check_unload([], 2443 + 512, 2443, 512)
        self.assertTrue(result.passed)

    def test_overshoot_beyond_tolerance_fails(self) -> None:
        result = check_unload([], 2443 + 513, 2443, 512)
        self.assertFalse(result.passed)
        self.assertIn("not verified", result.reason)

    def test_resident_model_fails_even_with_released_vram(self) -> None:
        result = check_unload(
            [{"name": "qwen3.5:9b"}], 2443, 2443, 512
        )
        self.assertFalse(result.passed)
        self.assertIn("qwen3.5:9b", result.reason)


class GateLedgerTests(unittest.TestCase):
    def test_passing_records_keep_window_open(self) -> None:
        ledger = GateLedger()
        ledger.record(check_free_vram(parse_nvidia_smi_mib("24564, 1, 24563"), 20480))
        ledger.ensure_open()  # no violation yet
        self.assertFalse(ledger.closed)

    def test_failing_gate_raises_and_closes_window(self) -> None:
        ledger = GateLedger()
        with self.assertRaises(GateViolation) as ctx:
            ledger.record(check_free_vram(parse_nvidia_smi_mib("24564, 1, 100"), 20480))
        self.assertEqual(ctx.exception.gate, GATE_FREE_VRAM)
        self.assertTrue(ledger.closed)
        with self.assertRaises(GateViolation):
            ledger.ensure_open()  # no retries after a gate fires

    def test_ensure_open_blocks_every_subsequent_action(self) -> None:
        ledger = GateLedger()
        with self.assertRaises(GateViolation):
            ledger.record(check_swap_delta(600 * MIB, 0, 512 * MIB))
        # Even a PASSING check cannot reopen the window:
        with self.assertRaises(GateViolation):
            ledger.record(check_swap_delta(1, 0, 512 * MIB))

    def test_jsonable_records_are_audit_friendly(self) -> None:
        ledger = GateLedger()
        ledger.record(check_no_offload((66, 66)))
        with self.assertRaises(GateViolation):
            ledger.record(check_no_offload((1, 66)))
        records = ledger.to_jsonable()
        self.assertEqual([r["passed"] for r in records], [True, False])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
