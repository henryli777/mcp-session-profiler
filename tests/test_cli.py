import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_session_profiler.cli import main

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}


def cli(*args, input=b"", env=None):
    return subprocess.run([sys.executable, "-m", "mcp_session_profiler", *map(str, args)],
                          input=input, capture_output=True, env=env or ENV, timeout=10)


class CliTests(unittest.TestCase):
    def test_report_write_and_close_failures_are_sanitized(self):
        class FullDisk:
            def write(self, value):
                raise OSError("PRIVATE_FILESYSTEM_DETAIL")

            def close(self):
                raise OSError("PRIVATE_FILESYSTEM_DETAIL")

        with patch("mcp_session_profiler.cli.open_new", return_value=FullDisk()), \
             patch("mcp_session_profiler.transport.run_proxy", return_value={
                 "status": "completed", "server_exit_code": 0, "stderr_bytes": 0}), \
             patch("sys.stderr") as stderr:
            self.assertEqual(main(["run", "--output", "unused", "--", "unused"]), 2)
        self.assertNotIn("PRIVATE_FILESYSTEM_DETAIL", str(stderr.write.call_args_list))

    def test_protocol_stdout_and_metadata_privacy(self):
        secret = "TEST_PRIVATE_MARKER_94f03"
        server = '''import json,sys,os
for line in sys.stdin.buffer:
    req=json.loads(line)
    if req.get("method")=="tools/call":
        if req["params"]["name"]=="fail":
            obj={"jsonrpc":"2.0","id":req["id"],"error":{"code":-32000,"message":os.environ["MCP_PRIVATE"],"data":sys.argv}}
        else:
            obj={"jsonrpc":"2.0","id":req["id"],"result":{"content":[{"type":"text","text":req["params"]["arguments"]["text"]+"你好"}]}}
        sys.stdout.buffer.write((json.dumps(obj,ensure_ascii=False)+"\\n").encode());sys.stdout.buffer.flush()
        print(os.environ["MCP_PRIVATE"], file=sys.stderr, flush=True)
'''
        messages = [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {
            "name": name, "arguments": {"text": secret}}} for i, name in enumerate(("echo", "fail"), 1)]
        wire = b"".join((json.dumps(x) + "\n").encode() for x in messages)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "session.json"
            result = cli("run", "--output", dest, "--", sys.executable, "-c", server, secret,
                         input=wire, env={**ENV, "MCP_PRIVATE": secret})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(result.stdout.splitlines()), 2)
            self.assertIn(secret.encode(), result.stdout)
            self.assertNotIn(secret.encode(), result.stderr)
            item = json.loads(dest.read_text())
            self.assertEqual(item["summary"]["observed_calls"], 2)
            self.assertEqual([x["status"] for x in item["calls"]], ["success", "rpc_error"])
            self.assertNotIn(secret, dest.read_text())
            self.assertNotIn('"arguments"', dest.read_text())
            html = Path(td) / "session.html"
            html_result = cli("html", dest, "--output", html)
            self.assertEqual(html_result.returncode, 0, html_result.stderr)
            self.assertNotIn(secret, html.read_text())
            summary = cli("summarize", dest)
            self.assertEqual(summary.returncode, 0)
            self.assertIn(b"Calls", summary.stdout)

    def test_existing_report_refused_before_server_launch(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "session.json"
            marker = Path(td) / "started"
            dest.write_text("original")
            code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
            result = cli("run", "--output", dest, "--", sys.executable, "-c", code)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(marker.exists())
            self.assertEqual(dest.read_text(), "original")

    def test_partial_observation_nonzero_with_report(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "session.json"
            result = cli("run", "--output", dest, "--", sys.executable, "-c", "print('not-json')")
            self.assertEqual(result.stdout, b"not-json\n")
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertFalse(json.loads(dest.read_text())["coverage"]["complete"])

    def test_missing_server_has_partial_report_and_generic_error(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "session.json"
            result = cli("run", "--output", dest, "--", "/missing_PRIVATE_EXECUTABLE")
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(dest.exists())
            self.assertNotIn(b"PRIVATE_EXECUTABLE", result.stderr)

    def test_cli_limits_fail_before_launch(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "session.json"
            result = cli("run", "--output", dest, "--max-calls", "0", "--", sys.executable)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
