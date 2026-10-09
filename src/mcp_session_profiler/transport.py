"""Bounded, byte-preserving stdio supervision for macOS and Linux.

Each relay queue holds at most 256 KiB, in addition to OS pipe buffers. Reads
pause when a queue is full; individual reads are at most 64 KiB. Neither
protocol nor stderr is decoded here. A connected, stalled client can wait
indefinitely: imposing an idle timeout would also interrupt legitimate tools.
EOF, direct-child exit, transport failure, or interruption starts a bounded
shutdown instead. Descendants that remain in the launched process group are
terminated during cleanup, including descendants holding output pipes open.
"""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from typing import Protocol, Sequence


MAX_RELAY_BYTES = 256 * 1024
READ_CHUNK_BYTES = 64 * 1024
POLL_INTERVAL = 0.1


class ObserverLike(Protocol):
    def feed(self, direction: str, data: bytes) -> None: ...

    def diagnostic(self, category: str, count: int = 1) -> None: ...


def run_proxy(
    command: Sequence[str],
    observer: ObserverLike,
    *,
    shutdown_timeout: float = 3.0,
    forward_stderr: bool = False,
) -> dict[str, str | int | None]:
    """Relay a shell-free argv and return sanitized session metadata.

    Status is ``completed`` for a clean exit, ``server_failed`` for a nonzero
    server exit, ``interrupted`` for SIGINT/SIGTERM, ``transport_failed`` for
    launch/relay/observer failure, or ``shutdown_timeout`` if EOF or child exit
    cannot finish within the grace period. Interruption and transport failure
    keep their status if forced cleanup becomes necessary. Cleanup adds at
    most a 250 ms reap wait after the deadline. Call from the main thread to
    intercept signals; original signal handlers and descriptor flags are
    restored. The caller owns observer finalization and report writing.

    Stderr is counted and discarded unless explicitly forwarded. Diagnostics
    contain only fixed categories and counts, never argv or exception text.
    Process-group supervision cannot capture children that deliberately leave
    the group with setsid/setpgid. No stdout diagnostics are emitted.
    """
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    old_blocking: dict[int, bool] = {}
    old_signals: dict[int, object] = {}
    status: str | None = None
    stderr_bytes = 0
    interrupted = False
    observer_broken = False
    deadline: float | None = None
    termination_sent = False

    def record(category: str, count: int = 1) -> None:
        nonlocal observer_broken
        try:
            observer.diagnostic(category, count)
        except Exception:
            already_broken = observer_broken
            observer_broken = True
            if not already_broken:
                try:
                    observer.diagnostic('observer_failure')
                except Exception:
                    pass

    def interrupt_handler(_signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = True

    def signal_group(signum: int) -> None:
        if process is not None:
            try:
                os.killpg(process.pid, signum)
            except ProcessLookupError:
                pass
            except OSError:
                record('transport_failure')

    def start_shutdown(reason: str | None = None, *, terminate: bool = False) -> None:
        nonlocal status, deadline, termination_sent
        if reason == 'interrupted' or (status != 'interrupted' and reason is not None):
            status = reason
        if deadline is None:
            deadline = time.monotonic() + shutdown_timeout
        if terminate and not termination_sent:
            termination_sent = True
            signal_group(signal.SIGTERM)

    def observe(direction: str, data: bytes) -> None:
        nonlocal observer_broken
        if observer_broken:
            return
        try:
            observer.feed(direction, data)
        except Exception:
            observer_broken = True
            record('observer_failure')
            start_shutdown('transport_failed', terminate=True)

    def nonblocking(fd: int, *, inherited: bool = False) -> None:
        if inherited:
            old_blocking[fd] = os.get_blocking(fd)
        os.set_blocking(fd, False)

    try:
        if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
            raise ValueError('unsupported platform')
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError('argv required')
        if not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0:
            raise ValueError('positive finite timeout required')

        input_fd = sys.stdin.fileno()
        output_fd = sys.stdout.fileno()
        error_fd = sys.stderr.fileno() if forward_stderr else None
        for fd in {input_fd, output_fd, error_fd} - {None}:
            nonblocking(fd, inherited=True)
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                old_signals[sig] = signal.signal(sig, interrupt_handler)

        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, start_new_session=True,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        server_input_fd = process.stdin.fileno()
        server_output_fd = process.stdout.fileno()
        server_error_fd = process.stderr.fileno()
        for fd in (server_input_fd, server_output_fd, server_error_fd):
            nonblocking(fd)
        # poll supports redirected regular files as well as pipes on both
        # release platforms; Linux epoll rejects regular-file descriptors.
        selector = selectors.PollSelector()
        to_server = bytearray()
        to_client = bytearray()
        to_error = bytearray()
        input_open = server_input_open = server_output_open = server_error_open = True
        output_open = True
        forward_error = forward_stderr
        exit_seen = False
        interruption_seen = False
        failure_seen = False

        while True:
            if interrupted and not interruption_seen:
                interruption_seen = True
                record('interrupted')
                input_open = False
                to_server.clear()
                start_shutdown('interrupted', terminate=True)
            if observer_broken:
                input_open = False
                to_server.clear()
                start_shutdown('transport_failed', terminate=True)
            if process.poll() is not None and not exit_seen:
                exit_seen = True
                input_open = False
                if to_server:
                    to_server.clear()
                    failure_seen = True
                    record('transport_failure')
                    start_shutdown('transport_failed')
                start_shutdown()
            if not input_open and not to_server and server_input_open:
                process.stdin.close()
                server_input_open = False

            if (exit_seen and not server_output_open and not server_error_open
                    and not to_client and not to_error):
                break
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                record('shutdown_timeout')
                if status is None:
                    status = 'shutdown_timeout'
                break

            desired: dict[int, tuple[int, str]] = {}
            if input_open and server_input_open and len(to_server) < MAX_RELAY_BYTES:
                desired[input_fd] = (selectors.EVENT_READ, 'client_input')
            if server_input_open and to_server:
                desired[server_input_fd] = (selectors.EVENT_WRITE, 'server_input')
            if server_output_open and output_open and len(to_client) < MAX_RELAY_BYTES:
                desired[server_output_fd] = (selectors.EVENT_READ, 'server_output')
            if output_open and to_client:
                desired[output_fd] = (selectors.EVENT_WRITE, 'client_output')
            if server_error_open and (not forward_error or len(to_error) < MAX_RELAY_BYTES):
                desired[server_error_fd] = (selectors.EVENT_READ, 'server_error')
            if forward_error and to_error and error_fd is not None:
                desired[error_fd] = (selectors.EVENT_WRITE, 'terminal_error')

            for fd in list(selector.get_map()):
                if fd not in desired:
                    selector.unregister(fd)
            for fd, (events, tag) in desired.items():
                key = selector.get_map().get(fd)
                if key is None:
                    selector.register(fd, events, tag)
                elif key.events != events or key.data != tag:
                    selector.modify(fd, events, tag)
            wait = POLL_INTERVAL if deadline is None else min(POLL_INTERVAL, max(0, deadline - now))
            for key, _events in selector.select(wait):
                tag = key.data
                if tag in ('server_input', 'client_output', 'terminal_error'):
                    queue = {'server_input': to_server, 'client_output': to_client,
                             'terminal_error': to_error}[tag]
                    if not queue:
                        continue
                    try:
                        written = os.write(key.fd, queue)
                        if written <= 0:
                            raise OSError('write made no progress')
                        del queue[:written]
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError:
                        if tag == 'terminal_error':
                            # Optional stderr failure does not corrupt protocol.
                            record('stderr_not_forwarded', len(to_error))
                            to_error.clear()
                            forward_error = False
                            continue
                        queue.clear()
                        input_open = False
                        to_server.clear()
                        if not failure_seen:
                            failure_seen = True
                            record('transport_failure')
                        start_shutdown('transport_failed', terminate=True)
                        if tag == 'client_output':
                            output_open = False
                            if server_output_open:
                                process.stdout.close()
                                server_output_open = False
                        elif server_input_open:
                            process.stdin.close()
                            server_input_open = False
                    continue

                queue = to_server if tag == 'client_input' else to_client
                limit = READ_CHUNK_BYTES
                if tag != 'server_error':
                    limit = min(limit, MAX_RELAY_BYTES - len(queue))
                elif forward_error:
                    limit = min(limit, MAX_RELAY_BYTES - len(to_error))
                if limit <= 0:
                    continue
                try:
                    data = os.read(key.fd, limit)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    if not failure_seen:
                        failure_seen = True
                        record('transport_failure')
                    start_shutdown('transport_failed', terminate=True)
                    data = b''

                if tag == 'server_error':
                    if data:
                        stderr_bytes += len(data)
                        if forward_error:
                            to_error.extend(data)
                        else:
                            record('stderr_not_forwarded', len(data))
                    else:
                        process.stderr.close()
                        server_error_open = False
                elif data:
                    queue.extend(data)
                    observe('client' if tag == 'client_input' else 'server', data)
                elif tag == 'client_input':
                    input_open = False
                    start_shutdown()
                else:
                    process.stdout.close()
                    server_output_open = False
                    input_open = False
                    start_shutdown()

    except KeyboardInterrupt:
        interrupted = True
        status = 'interrupted'
        record('interrupted')
    except Exception:
        status = 'interrupted' if interrupted else 'transport_failed'
        record('interrupted' if interrupted else 'transport_failure')
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            # Kill the group even after the direct child exits: surviving
            # descendants can retain pipes or silently continue background work.
            signal_group(signal.SIGTERM)
            signal_group(signal.SIGKILL)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                record('transport_failure')
                if status is None:
                    status = 'transport_failed'
        for fd, blocking in old_blocking.items():
            try:
                os.set_blocking(fd, blocking)
            except OSError:
                record('transport_failure')
                if status is None:
                    status = 'transport_failed'
        for sig, handler in old_signals.items():
            signal.signal(sig, handler)

    if interrupted:
        status = 'interrupted'
    elif observer_broken:
        status = 'transport_failed'
    code = process.returncode if process is not None else None
    if status is None:
        status = 'completed' if code == 0 else 'server_failed'
    if status == 'server_failed':
        record('server_failed')
        if observer_broken:
            status = 'transport_failed'
    return {'status': status, 'server_exit_code': code, 'stderr_bytes': stderr_bytes}
