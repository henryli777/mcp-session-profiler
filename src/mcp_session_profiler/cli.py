"""CLI: stdout belongs to the MCP connection during `run`."""

import argparse
import json
import math
import platform
import sys
import time

from . import __version__
from .reports import build_summary, load_report, open_new, render_html, save_new, summarize, validate_report


def _bounded_int(low, high):
    def parse(value):
        try:
            result = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("expected an integer") from None
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"expected a value between {low} and {high}")
        return result
    return parse


def _timeout(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected seconds") from None
    if not math.isfinite(result) or not .05 <= result <= 60:
        raise argparse.ArgumentTypeError("shutdown timeout must be 0.05–60 seconds")
    return result


def _parser():
    parser = argparse.ArgumentParser(prog="mcp-profiler", description="Local metadata-only MCP stdio diagnostics (macOS/Linux).")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="Launch and observe one stdio server; stdout is protocol only")
    run.add_argument("--output", required=True, help="New JSON path; existing destinations are refused")
    run.add_argument("--max-frame-bytes", type=_bounded_int(64, 32 * 1024 * 1024), default=1024 * 1024)
    run.add_argument("--max-calls", type=_bounded_int(1, 10000), default=10000)
    run.add_argument("--max-pending", type=_bounded_int(1, 100000), default=20000)
    run.add_argument("--shutdown-timeout", type=_timeout, default=3.0, help="Grace after input EOF or shutdown (default 3 seconds)")
    run.add_argument("--forward-stderr", action="store_true", help="Forward raw server stderr (may contain secrets); default discards it")
    run.add_argument("command", nargs=argparse.REMAINDER, help="-- executable [arguments...]")
    summary = sub.add_parser("summarize", help="Summarize an existing metadata report")
    summary.add_argument("report")
    html = sub.add_parser("html", help="Render an offline HTML report")
    html.add_argument("report")
    html.add_argument("--output", required=True)
    return parser


def _run(args):
    from .observer import Observer
    from .transport import run_proxy

    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        print("mcp-profiler: a server command is required after --", file=sys.stderr)
        return 2
    if sys.platform not in {"darwin", "linux"}:
        print("mcp-profiler: this release supports macOS and Linux", file=sys.stderr)
        return 2
    try:
        destination = open_new(args.output)
    except OSError:
        print("mcp-profiler: cannot create a new report; check destination and permissions", file=sys.stderr)
        return 2
    observer = Observer(max_frame_bytes=args.max_frame_bytes, max_calls=args.max_calls, max_pending=args.max_pending)
    start = time.monotonic()
    report_failed = False
    try:
        try:
            session = run_proxy(command, observer, shutdown_timeout=args.shutdown_timeout, forward_stderr=args.forward_stderr)
        except KeyboardInterrupt:
            observer.diagnostic("interrupted")
            session = {"status": "interrupted", "server_exit_code": None, "stderr_bytes": 0}
        except Exception:
            observer.diagnostic("observer_failure")
            session = {"status": "profiler_failed", "server_exit_code": None, "stderr_bytes": 0}
        observer.finish()
        session.update(duration_ms=round((time.monotonic() - start) * 1000, 6),
                       python_version=platform.python_version(), platform=platform.system())
        data = {"schema_version": 1, "profiler_version": __version__, "session": session, **observer.snapshot()}
        data["summary"] = build_summary(data["calls"], data["tool_calls_observed"])
        validate_report(data)
        destination.write(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2) + "\n")
        destination.flush()
    except (OSError, ValueError, TypeError):
        report_failed = True
    finally:
        try:
            destination.close()
        except OSError:
            report_failed = True
    if report_failed:
        print("mcp-profiler: report could not be finalized", file=sys.stderr)
        return 2
    status = session["status"]
    if status == "interrupted":
        return 130
    if status == "shutdown_timeout":
        return 124
    if status in {"transport_failed", "profiler_failed"}:
        return 3
    if status == "server_failed":
        return 1
    if not data["coverage"]["complete"] or any(call["status"] in {"unresolved", "ambiguous", "pending"} for call in data["calls"]):
        return 4
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.action == "run":
        return _run(args)
    try:
        data = load_report(args.report)
        if args.action == "summarize":
            sys.stdout.write(summarize(data))
        else:
            save_new(args.output, render_html(data))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        print("mcp-profiler: cannot read a valid report or create a new output", file=sys.stderr)
        return 2
    return 0
