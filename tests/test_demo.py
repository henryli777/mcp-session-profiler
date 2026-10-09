"""Exercise the example server over its real stdio boundary."""

import json
import importlib.metadata
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "fixture_server.py"


def exchange(messages):
    payload = b"".join(
        json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        for message in messages
    )
    process = subprocess.run(
        [sys.executable, str(FIXTURE), "--secret-argv", "ARGV_SECRET_SENTINEL"],
        input=payload,
        capture_output=True,
        timeout=5,
        check=True,
    )
    return [json.loads(line) for line in process.stdout.splitlines()], process.stderr


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(FIXTURE.is_file(), "The executable MCP fixture is missing")

    def test_initialize_and_list_advertise_tools(self):
        responses, stderr = exchange([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "fixture-test", "version": "1"},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(
            [tool["name"] for tool in responses[1]["result"]["tools"]],
            ["fast_echo", "slow_echo", "failing_tool"],
        )
        self.assertIn(b"STDERR_SECRET_SENTINEL", stderr)

    def test_calls_keep_unicode_and_distinguish_tool_from_rpc_errors(self):
        responses, _ = exchange([
            {"jsonrpc": "2.0", "id": "fast", "method": "tools/call", "params": {
                "name": "fast_echo", "arguments": {"text": "你好 🌍"},
            }},
            {"jsonrpc": "2.0", "id": "slow", "method": "tools/call", "params": {
                "name": "slow_echo", "arguments": {"text": "delayed"},
            }},
            {"jsonrpc": "2.0", "id": "fail", "method": "tools/call", "params": {
                "name": "failing_tool", "arguments": {},
            }},
            {"jsonrpc": "2.0", "id": "unknown", "method": "tools/call", "params": {
                "name": "unknown_tool", "arguments": {},
            }},
        ])
        self.assertIn("你好 🌍", responses[0]["result"]["content"][0]["text"])
        self.assertFalse(responses[0]["result"].get("isError", False))
        self.assertIn("delayed", responses[1]["result"]["content"][0]["text"])
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertEqual(responses[3]["error"]["code"], -32602)
        self.assertIn("ERROR_SECRET_SENTINEL", responses[3]["error"]["message"])


class DemoTests(unittest.TestCase):
    def test_showcase_writes_complete_private_reports(self):
        demo = ROOT / "examples" / "demo.py"
        self.assertTrue(demo.is_file(), "The executable client showcase is missing")
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.run(
                [sys.executable, str(demo), "--output-dir", directory, "--include-rpc-error"],
                capture_output=True, timeout=20,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            report_path = Path(directory) / "session.json"
            html_path = Path(directory) / "report.html"
            self.assertTrue(html_path.is_file())
            report = json.loads(report_path.read_text())
            self.assertTrue(report["coverage"]["complete"])
            self.assertEqual(report["tool_calls_observed"], 4)
            self.assertEqual(
                [call["status"] for call in report["calls"]],
                ["success", "success", "tool_error", "rpc_error"],
            )
            for path in (report_path, html_path):
                self.assertNotIn("SECRET_SENTINEL", path.read_text())


def has_pinned_sdk():
    try:
        return bool(importlib.util.find_spec("mcp")) and importlib.metadata.version("mcp") == "2.3.0"
    except importlib.metadata.PackageNotFoundError:
        return False


@unittest.skipUnless(has_pinned_sdk(), "Optional official mcp==2.3.0 SDK is not installed")
class SdkTests(unittest.TestCase):
    def test_official_sdk_client_and_server_pass_through_relay(self):
        checker = ROOT / "examples" / "sdk_check.py"
        self.assertTrue(checker.is_file(), "The SDK compatibility check is missing")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "session.json"
            process = subprocess.run(
                [sys.executable, str(checker), "--output", str(output)],
                capture_output=True, timeout=30,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            verification = json.loads(process.stdout)
            self.assertEqual(verification["negotiated_protocol"], "2025-11-25")
            self.assertTrue(verification["unicode_preserved"])
            report = json.loads(output.read_text())
            self.assertTrue(report["coverage"]["complete"])
            self.assertEqual([call["status"] for call in report["calls"]],
                             ["success", "success", "tool_error"])
            self.assertNotIn("SECRET_SENTINEL", output.read_text())


if __name__ == "__main__":
    unittest.main()
