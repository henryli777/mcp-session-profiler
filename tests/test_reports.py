import json
import tempfile
import unittest
from pathlib import Path

from mcp_session_profiler.reports import build_summary, load_report, render_html, save_new, validate_report


def call(status="success", latency=10, size=42, tool="echo"):
    return {"sequence": 1, "tool": tool, "request_id": {"type": "number", "value": 1},
            "origin": "client", "started_ms": 0, "status": status,
            "latency_ms": latency, "response_message_bytes": size, "error_code": None}


def report(calls=None):
    calls = [call()] if calls is None else calls
    return {"schema_version": 1, "profiler_version": "0.1.0", "session": {
        "status": "completed", "duration_ms": 12, "server_exit_code": 0,
        "stderr_bytes": 0, "python_version": "3.11", "platform": "Linux"},
        "calls": calls, "tool_calls_observed": len(calls),
        "diagnostics": {}, "coverage": {"complete": True, "limits": {}},
        "traffic": {}, "summary": build_summary(calls, len(calls))}


class ReportTests(unittest.TestCase):
    def test_nearest_rank_and_unresolved_exclusion(self):
        calls = [call(latency=x) for x in (1, 2, 3, 100)]
        calls += [call("unresolved", None, None), call("ambiguous", None, None),
                  call("rpc_error", 5, 30), call("tool_error", 6, 31)]
        summary = build_summary(calls, 10)
        self.assertEqual((summary["observed_calls"], summary["retained_calls"]), (10, 8))
        tool = summary["tools"][0]
        self.assertEqual(tool["latency"]["samples"], 6)
        self.assertEqual(tool["latency"]["median_ms"], 3)
        self.assertEqual(tool["latency"]["p95_ms"], 100)
        self.assertEqual(tool["response_bytes"], 229)
        self.assertEqual((tool["unresolved"], tool["ambiguous"], tool["rpc_errors"], tool["tool_errors"]), (1, 1, 1, 1))

    def test_empty_summary(self):
        summary = build_summary([], 0)
        self.assertEqual(summary["tools"], [])

    def test_maximum_cli_retention_fits_reader_with_escaped_metadata(self):
        from mcp_session_profiler.reports import MAX_REPORT_BYTES
        longest = call(tool="\x01" * 256)
        longest["request_id"] = {"type": "string", "value": "\x02" * 256}
        longest["status"] = "rpc_error"
        longest["error_code"] = int("9" * 256)
        item = report([longest] * 10000)
        validate_report(item)
        encoded = json.dumps(item, ensure_ascii=True, indent=2).encode()
        self.assertLess(len(encoded), MAX_REPORT_BYTES)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "largest.json"
            dest.write_bytes(encoded)
            self.assertEqual(len(load_report(dest)["calls"]), 10000)
        item["calls"].append(longest)
        with self.assertRaises(ValueError):
            validate_report(item)

    def test_html_is_escaped_and_offline(self):
        item = report([call(tool='<script src="https://evil.test/x"></script>')])
        html = render_html(item)
        self.assertNotIn('<script', html)
        self.assertIn('&lt;script', html)
        self.assertNotIn('src="https:', html)
        self.assertNotIn('href="https:', html)
        self.assertIn('Transport bytes', html)

    def test_partial_coverage_visible(self):
        item = report()
        item["coverage"]["complete"] = False
        item["diagnostics"]["oversized_frame"] = 1
        self.assertIn("Partial observation", render_html(item))

    def test_new_output_refuses_existing_and_hardlink(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "report.json"
            save_new(dest, "original")
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                save_new(dest, "changed")
            self.assertEqual(dest.read_text(), "original")
            link = Path(td) / "link"
            link.hardlink_to(dest)
            with self.assertRaises(FileExistsError):
                save_new(link, "changed")
            self.assertEqual(dest.read_text(), "original")

    def test_schema_rejects_version_and_impossible_metrics(self):
        item = report()
        item["schema_version"] = 2
        with self.assertRaises(ValueError):
            validate_report(item)
        item = report()
        item["calls"][0]["latency_ms"] = -1
        with self.assertRaises(ValueError):
            validate_report(item)
        item = report()
        item["calls"][0]["status"] = "unresolved"
        with self.assertRaises(ValueError):
            validate_report(item)

    def test_load_rebuilds_summary_from_calls(self):
        with tempfile.TemporaryDirectory() as td:
            item = report()
            item["summary"] = {"fake": 1}
            dest = Path(td) / "session.json"
            dest.write_text(json.dumps(item))
            loaded = load_report(dest)
            self.assertEqual(loaded["summary"]["tools"][0]["matched"], 1)

    def test_nonfinite_and_unknown_fields_rejected(self):
        item = report()
        item["session"]["duration_ms"] = float("nan")
        with self.assertRaises(ValueError):
            validate_report(item)

    def test_contradictory_session_and_coverage_are_rejected(self):
        for status, code in (("completed", 17), ("completed", None), ("server_failed", 0), ("server_failed", None)):
            item = report()
            item["session"].update(status=status, server_exit_code=code)
            with self.subTest(status=status, code=code), self.assertRaises(ValueError):
                validate_report(item)
        item = report()
        item["diagnostics"]["oversized_frame"] = 1
        with self.assertRaises(ValueError):
            validate_report(item)
        item["coverage"]["complete"] = False
        validate_report(item)
        item["diagnostics"]["PRIVATE_ARBITRARY_KEY"] = 1
        with self.assertRaises(ValueError):
            validate_report(item)
        item = report()
        item["raw_results"] = "private"
        with self.assertRaises(ValueError):
            validate_report(item)


if __name__ == "__main__":
    unittest.main()
