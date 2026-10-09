"""Exercise the relay through real pipes and child processes, independent of parsing."""

import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = r"""
import hashlib, json, os, resource, signal, sys
from pathlib import Path
from mcp_session_profiler.transport import run_proxy

class Observer:
    def __init__(self):
        self.hashes = {direction: hashlib.sha256() for direction in ('client', 'server')}
        self.bytes = {'client': 0, 'server': 0}
        self.diagnostics = {}
    def feed(self, direction, data):
        if sys.argv[5] == 'fail':
            raise RuntimeError('OBSERVER_EXCEPTION_SECRET')
        self.hashes[direction].update(data)
        self.bytes[direction] += len(data)
    def diagnostic(self, category, count=1):
        if sys.argv[5] == 'fail_diagnostic' and category == 'server_failed':
            raise RuntimeError('DIAGNOSTIC_EXCEPTION_SECRET')
        self.diagnostics[category] = self.diagnostics.get(category, 0) + count

observer = Observer()
handlers = [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
blocking = [os.get_blocking(fd) for fd in (0, 1, 2)]
result = run_proxy(json.loads(sys.argv[2]), observer,
                   shutdown_timeout=float(sys.argv[3]), forward_stderr=sys.argv[4] == 'yes')
result['observed_bytes'] = observer.bytes
result['hashes'] = {direction: value.hexdigest() for direction, value in observer.hashes.items()}
result['diagnostics'] = observer.diagnostics
result['restored'] = (
    handlers == [signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)]
    and blocking == [os.get_blocking(fd) for fd in (0, 1, 2)]
)
if sys.platform.startswith('linux'):
    # Linux rusage may retain the controller's pre-exec RSS highwater.
    # VmHWM belongs to this exec's memory map, so it measures the relay itself.
    for line in Path('/proc/self/status').read_text().splitlines():
        fields = line.split()
        if fields and fields[0] == 'VmHWM:':
            if len(fields) != 3 or fields[2] != 'kB':
                raise RuntimeError('Unexpected VmHWM format')
            result['peak_rss_bytes'] = int(fields[1]) * 1024
            break
    else:
        raise RuntimeError('VmHWM is unavailable')
else:
    # macOS reports bytes, and its exec peak does not inherit this highwater.
    result['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
Path(sys.argv[1]).write_text(json.dumps(result))
"""


@unittest.skipUnless(sys.platform.startswith('linux') or sys.platform == 'darwin', 'POSIX release platforms')
class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.result_path = Path(self.temp.name) / 'result.json'
        self.env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))

    def start_proxy(self, code=None, *, command=None, timeout=3.0, forward_stderr=False,
                    observer_fails=False, diagnostic_fails=False,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, runner_prefix=''):
        if command is None:
            command = [sys.executable, '-c', code]
        failure_mode = 'fail' if observer_fails else 'fail_diagnostic' if diagnostic_fails else 'ok'
        proxy = subprocess.Popen(
            [sys.executable, '-c', runner_prefix + RUNNER, str(self.result_path), json.dumps(command),
             str(timeout), 'yes' if forward_stderr else 'no', failure_mode],
            stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=self.env,
        )
        self.addCleanup(self.cleanup_proxy, proxy)
        return proxy

    @staticmethod
    def cleanup_proxy(proxy):
        if proxy.poll() is None:
            proxy.kill()
        proxy.wait(timeout=3)
        for stream in (proxy.stdin, proxy.stdout, proxy.stderr):
            if stream is not None:
                stream.close()

    def result(self, proxy):
        self.assertEqual(proxy.returncode, 0, 'the proxy runner must finalize its result')
        self.assertTrue(self.result_path.exists(), 'transport must return for report finalization')
        return json.loads(self.result_path.read_text())

    def assert_traffic(self, result, direction, payload):
        self.assertEqual(result['observed_bytes'][direction], len(payload))
        self.assertEqual(result['hashes'][direction], hashlib.sha256(payload).hexdigest())

    def read_ready(self, proxy):
        """Wait for the fixture handshake without a potentially hanging readline."""
        expected = b'ready\n'
        received = bytearray()
        deadline = time.monotonic() + 3
        with selectors.DefaultSelector() as selector:
            selector.register(proxy.stdout, selectors.EVENT_READ)
            while len(received) < len(expected):
                self.assertTrue(selector.select(max(0, deadline - time.monotonic())),
                                'server fixture did not become ready')
                data = os.read(proxy.stdout.fileno(), len(expected) - len(received))
                self.assertTrue(data, 'proxy exited before the fixture handshake')
                received.extend(data)
        self.assertEqual(received, expected)

    def assert_dead(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            # Orphaned zombies are already dead; their adopter controls reap timing.
            state = Path('/proc') / str(pid) / 'stat'
            if state.exists() and state.read_text().split(') ', 1)[1].startswith('Z'):
                return
            time.sleep(0.02)
        self.fail('a supervised descendant survived cleanup')

    def test_split_unicode_and_nonterminated_bytes_are_relayed_exactly(self):
        payload = '{"text":"汉字🙂"}\ntrailing\x00without-newline'.encode()
        proxy = self.start_proxy(
            "import os\nwhile True:\n data=os.read(0,3)\n if not data: break\n"
            " for byte in data: os.write(1,bytes([byte]))\n"
        )
        output, stderr = proxy.communicate(payload, timeout=5)
        result = self.result(proxy)
        self.assertEqual(output, payload)
        self.assertEqual(stderr, b'')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['server_exit_code'], 0)
        self.assertTrue(result['restored'])
        self.assert_traffic(result, 'client', payload)
        self.assert_traffic(result, 'server', payload)

    def test_large_input_with_server_backpressure_is_lossless_and_bounded(self):
        payload = (b'large\x00' + '中文'.encode()) * (1024 * 1024)
        proxy = self.start_proxy(
            "import os,time\ntime.sleep(0.15)\nwhile True:\n data=os.read(0,8192)\n"
            " if not data: break\n os.write(1,data)\n", timeout=4,
        )
        output, _ = proxy.communicate(payload, timeout=10)
        result = self.result(proxy)
        self.assertEqual(output, payload)
        self.assertEqual(result['status'], 'completed')
        self.assert_traffic(result, 'client', payload)
        self.assert_traffic(result, 'server', payload)
        self.assertLess(result['peak_rss_bytes'], 48 * 1024 * 1024)

    def test_linux_peak_measurement_uses_vm_highwater_and_converts_kib_to_bytes(self):
        # Linux's inherited rusage highwater can exceed this exec's own memory.
        # Distinct VmRSS/VmPeak values catch selection of the wrong proc field.
        prefix = r'''
import resource, sys
from pathlib import Path
from types import SimpleNamespace
resource.getrusage = lambda _who: SimpleNamespace(ru_maxrss=160000)
original_read_text = Path.read_text
def read_status(path, *args, **kwargs):
    if str(path) == '/proc/self/status':
        return 'Name:\tpython\nVmPeak:\t30000 kB\nVmHWM:\t16384 kB\nVmRSS:\t8192 kB\n'
    return original_read_text(path, *args, **kwargs)
Path.read_text = read_status
sys.platform = 'linux'
'''
        proxy = self.start_proxy('pass', runner_prefix=prefix)
        proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['peak_rss_bytes'], 16 * 1024 * 1024)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux exec/rusage regression')
    def test_linux_proxy_peak_does_not_inherit_controller_memory(self):
        controller_memory = bytearray(96 * 1024 * 1024)
        for offset in range(0, len(controller_memory), 4096):
            controller_memory[offset] = 1  # Fault in every page before fork/exec.
        proxy = self.start_proxy('pass')
        proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'completed')
        self.assertLess(result['peak_rss_bytes'], 48 * 1024 * 1024)
        self.assertEqual(controller_memory[0], 1)  # Keep pages live until sampling finishes.

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux proc peak characterization')
    def test_linux_proxy_peak_still_detects_its_own_memory_growth(self):
        prefix = '''
runner_memory = bytearray(64 * 1024 * 1024)
for offset in range(0, len(runner_memory), 4096):
    runner_memory[offset] = 1
'''
        proxy = self.start_proxy('pass', runner_prefix=prefix)
        proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertGreaterEqual(result['peak_rss_bytes'], 64 * 1024 * 1024)

    def test_slow_client_applies_server_backpressure_without_unbounded_buffering(self):
        marker = Path(self.temp.name) / 'server-finished'
        command = [sys.executable, '-c',
                   "import os,sys\nfrom pathlib import Path\nchunk=b'x'*65536\n"
                   "for _ in range(512): os.write(1,chunk)\nPath(sys.argv[1]).touch()\n",
                   str(marker)]
        proxy = self.start_proxy(command=command, timeout=4)
        proxy.stdin.close()
        proxy.stdin = None
        time.sleep(0.25)
        self.assertIsNone(proxy.poll())
        self.assertFalse(marker.exists(), 'server writes must block when the client stops reading')
        output, _ = proxy.communicate(timeout=10)
        result = self.result(proxy)
        self.assertEqual(len(output), 32 * 1024 * 1024)
        self.assertEqual(hashlib.sha256(output).hexdigest(),
                         hashlib.sha256(b'x' * (32 * 1024 * 1024)).hexdigest())
        self.assertEqual(result['status'], 'completed')
        self.assertLess(result['peak_rss_bytes'], 48 * 1024 * 1024)

    def test_regular_file_streams_work_and_restore_descriptor_flags(self):
        source = Path(self.temp.name) / 'input'
        target = Path(self.temp.name) / 'output'
        payload = b'file input\n'
        source.write_bytes(payload)
        with source.open('rb') as incoming, target.open('wb') as outgoing:
            proxy = self.start_proxy('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',
                                     stdin=incoming, stdout=outgoing)
            _, stderr = proxy.communicate(timeout=5)
        result = self.result(proxy)
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(stderr, b'')
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['restored'])

    def test_argv_has_no_shell_and_stderr_is_counted_but_discarded(self):
        secret = 'ARGUMENT_SECRET $HOME; `echo injected` with spaces'
        command = [sys.executable, '-c',
                   "import os,sys; os.write(1,sys.argv[1].encode()); os.write(2,b'STDERR_SECRET')",
                   secret]
        proxy = self.start_proxy(command=command)
        output, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(output, secret.encode())
        self.assertEqual(stderr, b'')
        self.assertEqual(result['stderr_bytes'], 13)
        self.assertEqual(result['diagnostics']['stderr_not_forwarded'], 13)
        self.assertNotIn('SECRET', self.result_path.read_text())

    def test_explicit_stderr_forwarding_preserves_raw_bytes_separately(self):
        proxy = self.start_proxy("import os; os.write(1,b'protocol\\n'); os.write(2,b'raw\\xff\\x00stderr')",
                                 forward_stderr=True)
        output, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(output, b'protocol\n')
        self.assertEqual(stderr, b'raw\xff\x00stderr')
        self.assertEqual(result['stderr_bytes'], 11)
        self.assertEqual(result['status'], 'completed')

    def test_nonzero_exit_is_distinct_from_launch_failure_without_sensitive_text(self):
        proxy = self.start_proxy('import sys; sys.exit(17)')
        _, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'server_failed')
        self.assertEqual(result['server_exit_code'], 17)
        self.assertEqual(stderr, b'')
        proxy = self.start_proxy(command=['/nonexistent/EXECUTABLE_SECRET'])
        output, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'transport_failed')
        self.assertIsNone(result['server_exit_code'])
        self.assertEqual(result['diagnostics']['transport_failure'], 1)
        self.assertEqual(output, b'')
        self.assertEqual(stderr, b'')
        self.assertNotIn('SECRET', self.result_path.read_text())

    def test_stdin_eof_times_out_a_server_that_ignores_termination(self):
        proxy = self.start_proxy('import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)',
                                 timeout=0.2)
        started = time.monotonic()
        _, stderr = proxy.communicate(b'', timeout=3)
        result = self.result(proxy)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result['status'], 'shutdown_timeout')
        self.assertEqual(result['diagnostics']['shutdown_timeout'], 1)
        self.assertEqual(stderr, b'')

    def test_server_exit_cleans_descendants_that_keep_pipes_open(self):
        pid_path = Path(self.temp.name) / 'descendant.pid'
        descendant = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
        command = [sys.executable, '-c',
                   "import os,subprocess,sys\nfrom pathlib import Path\n"
                   "child=subprocess.Popen([sys.executable,'-c',sys.argv[2]])\n"
                   "Path(sys.argv[1]).write_text(str(child.pid))\nos.write(1,b'ready\\n')\n",
                   str(pid_path), descendant]
        proxy = self.start_proxy(command=command, timeout=0.2)
        self.read_ready(proxy)
        proxy.communicate(b'', timeout=3)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'shutdown_timeout')
        self.assertEqual(result['server_exit_code'], 0)
        self.assert_dead(int(pid_path.read_text()))

    def test_client_stdout_broken_pipe_stops_and_finalizes(self):
        proxy = self.start_proxy("import os,signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                                 "while True: os.write(1,b'x'*65536)\n")
        proxy.stdout.close()
        proxy.stdout = None
        _, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'transport_failed')
        self.assertEqual(result['diagnostics']['transport_failure'], 1)
        self.assertEqual(stderr, b'')

    def test_server_closing_stdin_stops_a_live_client_without_waiting_for_eof(self):
        proxy = self.start_proxy("import os,signal,time\nos.close(0)\n"
                                 "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                                 "os.write(1,b'ready\\n')\ntime.sleep(60)\n", timeout=0.2)
        self.read_ready(proxy)
        proxy.stdin.write(b'undeliverable input\n')
        proxy.stdin.flush()
        proxy.wait(timeout=3)
        stderr = proxy.stderr.read()
        result = self.result(proxy)
        self.assertEqual(result['status'], 'transport_failed')
        self.assertEqual(result['diagnostics']['transport_failure'], 1)
        self.assertEqual(stderr, b'')

    def test_server_closing_stdout_times_out_even_with_client_stdin_open(self):
        proxy = self.start_proxy("import os,signal,time\nos.close(1)\n"
                                 "signal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(60)\n",
                                 timeout=0.2)
        proxy.wait(timeout=3)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'shutdown_timeout')
        self.assertEqual(result['diagnostics']['shutdown_timeout'], 1)

    def test_server_stderr_eof_does_not_end_an_active_protocol_connection(self):
        proxy = self.start_proxy("import os,time\nos.close(2)\ntime.sleep(0.1)\n"
                                 "os.write(1,os.read(0,100))\n")
        output, stderr = proxy.communicate(b'after stderr closed\n', timeout=5)
        result = self.result(proxy)
        self.assertEqual(output, b'after stderr closed\n')
        self.assertEqual(stderr, b'')
        self.assertEqual(result['status'], 'completed')

    def test_sigterm_returns_instead_of_aborting_report_finalization(self):
        proxy = self.start_proxy("import os,time; os.write(1,b'ready\\n'); time.sleep(60)", timeout=0.2)
        self.read_ready(proxy)
        proxy.send_signal(signal.SIGTERM)
        proxy.communicate(b'', timeout=3)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['diagnostics']['interrupted'], 1)
        self.assertTrue(result['restored'])

    def test_interrupt_returns_partial_observation_and_kills_process_group(self):
        pid_path = Path(self.temp.name) / 'descendant.pid'
        descendant = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"
        command = [sys.executable, '-c',
                   "import os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
                   "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                   "child=subprocess.Popen([sys.executable,'-c',sys.argv[2]])\n"
                   "Path(sys.argv[1]).write_text(str(child.pid))\nos.write(1,b'ready\\n')\ntime.sleep(60)\n",
                   str(pid_path), descendant]
        proxy = self.start_proxy(command=command, timeout=0.2)
        self.read_ready(proxy)
        proxy.send_signal(signal.SIGINT)
        output, stderr = proxy.communicate(b'', timeout=3)
        result = self.result(proxy)
        self.assertEqual(output, b'')
        self.assertEqual(stderr, b'')
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['diagnostics']['interrupted'], 1)
        self.assert_traffic(result, 'server', b'ready\n')
        self.assertTrue(result['restored'])
        self.assert_dead(int(pid_path.read_text()))

    def test_observer_failure_is_categorized_and_never_leaks_exception_text(self):
        proxy = self.start_proxy('import os; os.write(1,b"bytes")', observer_fails=True)
        _, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'transport_failed')
        self.assertEqual(result['diagnostics']['observer_failure'], 1)
        self.assertEqual(stderr, b'')
        self.assertNotIn('SECRET', self.result_path.read_text())

    def test_diagnostic_callback_failure_cannot_report_a_valid_server_status(self):
        proxy = self.start_proxy('import sys; sys.exit(17)', diagnostic_fails=True)
        _, stderr = proxy.communicate(b'', timeout=5)
        result = self.result(proxy)
        self.assertEqual(result['status'], 'transport_failed')
        self.assertEqual(stderr, b'')
        self.assertNotIn('SECRET', self.result_path.read_text())


if __name__ == '__main__':
    unittest.main()
