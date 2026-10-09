"""Bounded metadata report validation and offline presentation."""

import html
import json
import math
import os
from pathlib import Path

from .observer import DIAGNOSTIC_CATEGORIES

MATCHED = {"success", "rpc_error", "tool_error"}
STATUSES = MATCHED | {"pending", "unresolved", "ambiguous"}
MAX_REPORT_BYTES = 64 * 1024 * 1024


def build_summary(calls, observed):
    tools = {}
    for call in calls:
        tool = tools.setdefault(call["tool"], {
            "tool": call["tool"], "calls": 0, "matched": 0, "unresolved": 0,
            "ambiguous": 0, "rpc_errors": 0, "tool_errors": 0,
            "response_bytes": 0, "_latencies": []})
        tool["calls"] += 1
        status = call["status"]
        if status in MATCHED:
            tool["matched"] += 1
            tool["response_bytes"] += call["response_message_bytes"]
            tool["_latencies"].append(call["latency_ms"])
            if status == "rpc_error":
                tool["rpc_errors"] += 1
            elif status == "tool_error":
                tool["tool_errors"] += 1
        elif status == "ambiguous":
            tool["ambiguous"] += 1
        else:
            tool["unresolved"] += 1
    for tool in tools.values():
        times = sorted(tool.pop("_latencies"))
        count = len(times)
        tool["latency"] = {
            "samples": count,
            "min_ms": times[0] if count else None,
            "median_ms": times[math.ceil(count * .5) - 1] if count else None,
            "p95_ms": times[math.ceil(count * .95) - 1] if count else None,
            "max_ms": times[-1] if count else None,
        }
    return {"observed_calls": observed, "retained_calls": len(calls),
            "quantile_method": "nearest_rank", "tools": [tools[k] for k in sorted(tools)]}


def _number(value, *, integer=False, nullable=False):
    if nullable and value is None:
        return
    if type(value) not in ((int,) if integer else (int, float)) or value < 0 or not math.isfinite(value):
        raise ValueError("invalid numeric metadata")


def _text(value, limit=256):
    try:
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= limit
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError("invalid text metadata")


def _keys(value, allowed, required=None):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required or allowed) - set(value):
        raise ValueError("invalid metadata fields")


def validate_report(report):
    _keys(report, {"schema_version", "profiler_version", "session", "calls", "tool_calls_observed",
                   "diagnostics", "coverage", "traffic", "summary"})
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        raise ValueError("unsupported report schema")
    _text(report["profiler_version"])
    session = report["session"]
    _keys(session, {"status", "server_exit_code", "duration_ms", "stderr_bytes", "python_version", "platform"})
    if session["status"] not in {"completed", "server_failed", "interrupted", "transport_failed", "shutdown_timeout", "profiler_failed"}:
        raise ValueError("invalid session status")
    if session["server_exit_code"] is not None and type(session["server_exit_code"]) is not int:
        raise ValueError("invalid server exit code")
    if session["status"] == "completed" and session["server_exit_code"] != 0:
        raise ValueError("completed session requires zero server exit")
    if session["status"] == "server_failed" and session["server_exit_code"] in {0, None}:
        raise ValueError("server failure requires nonzero server exit")
    _number(session["duration_ms"])
    _number(session["stderr_bytes"], integer=True)
    _text(session["python_version"])
    _text(session["platform"])
    calls = report["calls"]
    if not isinstance(calls, list) or len(calls) > 10000:
        raise ValueError("invalid or oversized call list")
    for call in calls:
        _keys(call, {"sequence", "tool", "request_id", "origin", "started_ms", "status",
                     "latency_ms", "response_message_bytes", "error_code"})
        _number(call["sequence"], integer=True)
        _text(call["tool"])
        _number(call["started_ms"])
        if call["origin"] != "client" or call["status"] not in STATUSES:
            raise ValueError("invalid call metadata")
        req_id = call["request_id"]
        _keys(req_id, {"type", "value"})
        if req_id["type"] == "string":
            _text(req_id["value"])
        elif req_id["type"] == "number" and type(req_id["value"]) is int:
            if len(str(req_id["value"])) > 256:
                raise ValueError("oversized request ID")
        else:
            raise ValueError("invalid typed ID")
        _number(call["latency_ms"], nullable=True)
        _number(call["response_message_bytes"], integer=True, nullable=True)
        if call["error_code"] is not None and type(call["error_code"]) is not int:
            raise ValueError("invalid error code")
        if call["error_code"] is not None and len(str(call["error_code"])) > 256:
            raise ValueError("oversized error code")
        if call["status"] in MATCHED:
            if call["latency_ms"] is None or call["response_message_bytes"] is None:
                raise ValueError("missing matched metrics")
        elif any(call[k] is not None for k in ("latency_ms", "response_message_bytes", "error_code")):
            raise ValueError("unmatched call has matched metrics")
    _number(report["tool_calls_observed"], integer=True)
    if report["tool_calls_observed"] < len(calls):
        raise ValueError("observed count below retained count")
    diagnostics = report["diagnostics"]
    if not isinstance(diagnostics, dict) or len(diagnostics) > 64:
        raise ValueError("invalid diagnostics")
    for key, count in diagnostics.items():
        if key not in DIAGNOSTIC_CATEGORIES:
            raise ValueError("unknown diagnostic category")
        _number(count, integer=True)
    coverage = report["coverage"]
    _keys(coverage, {"complete", "limits"})
    if type(coverage["complete"]) is not bool or not isinstance(coverage["limits"], dict):
        raise ValueError("invalid coverage")
    if coverage["complete"] and any(count for key, count in diagnostics.items() if key != "stderr_not_forwarded"):
        raise ValueError("diagnostic loss contradicts complete coverage")
    for key, value in coverage["limits"].items():
        _text(key, 64)
        _number(value, integer=True)
    traffic = report["traffic"]
    if not isinstance(traffic, dict) or set(traffic) - {"client", "server"}:
        raise ValueError("invalid traffic metadata")
    for values in traffic.values():
        _keys(values, {"bytes", "frames"})
        for count in values.values():
            _number(count, integer=True)
    return report


def load_report(path):
    with open(path, "rb") as source:
        data = source.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ValueError("report exceeds 64 MiB")
    report = json.loads(data)
    validate_report(report)
    report["summary"] = build_summary(report["calls"], report["tool_calls_observed"])
    return report


def open_new(path):
    """Refuse existing files, links and aliases; do not truncate anything."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(Path(path), flags, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n")


def save_new(path, text):
    with open_new(path) as destination:
        destination.write(text)


def _display(value):
    return "—" if value is None else f"{value:.2f}"


def summarize(report):
    summary = build_summary(report["calls"], report["tool_calls_observed"])
    lines = [f"Session: {report['session']['status']} | observation: " +
             ("complete" if report["coverage"]["complete"] else "partial"),
             f"Tool calls observed: {summary['observed_calls']} | retained: {summary['retained_calls']}",
             "Tool (JSON escaped) | Calls | Matched | Errors | Unresolved/ambiguous | p95 ms | Response bytes"]
    for tool in summary["tools"]:
        lines.append(f"{json.dumps(tool['tool'], ensure_ascii=True)} | {tool['calls']} | {tool['matched']} | "
                     f"{tool['rpc_errors'] + tool['tool_errors']} | {tool['unresolved']}/{tool['ambiguous']} | "
                     f"{_display(tool['latency']['p95_ms'])} (n={tool['latency']['samples']}) | {tool['response_bytes']}")
    lines.append("Transport bytes are not model tokens or billing. Latency includes proxy and transport effects.")
    if report["diagnostics"]:
        lines.append("Diagnostics: " + json.dumps(report["diagnostics"], sort_keys=True, ensure_ascii=True))
    return "\n".join(lines) + "\n"


def render_html(report):
    validate_report(report)
    summary = build_summary(report["calls"], report["tool_calls_observed"])
    esc = lambda value: html.escape(str(value), quote=True)
    rows = []
    for tool in summary["tools"]:
        latency = tool["latency"]
        cells = [tool["tool"], tool["calls"], tool["matched"], tool["rpc_errors"], tool["tool_errors"],
                 tool["unresolved"], tool["ambiguous"], latency["samples"],
                 _display(latency["median_ms"]), _display(latency["p95_ms"]), tool["response_bytes"]]
        rows.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    headers = ["Tool", "Calls", "Matched", "RPC errors", "Tool errors", "Unresolved", "Ambiguous", "Samples", "Median ms", "p95 ms", "Response bytes"]
    complete = report["coverage"]["complete"]
    diagnostics = "".join(f"<li>{esc(key)}: {value}</li>" for key, value in sorted(report["diagnostics"].items()))
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>MCP session profiler</title><style>
body{font:16px system-ui,sans-serif;background:#101820;color:#e8f0f4;margin:0;padding:32px;line-height:1.6}
main{max-width:1200px;margin:auto}h1{font-size:32px;margin-bottom:0}p{color:#b7ccd8}.status{padding:12px 18px;border:1px solid #628697;border-radius:8px}
.partial{border-color:#dfab59;background:#392d1d}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:12px;border-bottom:1px solid #34505e;white-space:nowrap}th{color:#8bdfc5}td:first-child{white-space:normal;overflow-wrap:anywhere;min-width:160px}code{color:#8bdfc5}
</style></head><body><main><h1>MCP session profiler</h1><p>Local metadata report · schema 1</p>""" + (
        f'<div class="status {"" if complete else "partial"}">{"Complete observation" if complete else "Partial observation"} · session: {esc(report["session"]["status"])}'
        f' · {summary["observed_calls"]} calls observed / {summary["retained_calls"]} retained</div>'
        '<h2>Tools</h2><div class="table"><table><thead><tr>' +
        "".join(f"<th>{esc(h)}</th>" for h in headers) + '</tr></thead><tbody>' +
        ("".join(rows) or '<tr><td colspan="11">No retained tool calls.</td></tr>') +
        '</tbody></table></div><h2>Measurement limits</h2><p>Transport bytes are complete response JSON frame bytes, excluding the newline; they are not model tokens or billing. Latency is measured at this proxy and includes transport and scheduling. Only matched calls enter latency statistics; quantiles use nearest rank. Tool names and request IDs may be sensitive.</p>' +
        '<p>Only this stdio connection is observed. Completed results do not prove application success. Incomplete or ambiguous calls are shown separately. Observation limits: <code>' +
        esc(json.dumps(report["coverage"]["limits"], sort_keys=True)) + '</code>.</p>' +
        '<h2>Diagnostics</h2><ul>' + (diagnostics or '<li>None</li>') + '</ul>' +
        f'<p>Python {esc(report["session"]["python_version"])} · {esc(report["session"]["platform"])} · duration {report["session"]["duration_ms"]:.2f} ms · server exit {esc(report["session"]["server_exit_code"])} · stderr bytes {report["session"]["stderr_bytes"]}</p>' +
        '</main></body></html>\n')
