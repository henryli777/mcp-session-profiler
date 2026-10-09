"""Bounded observation of newline-delimited MCP JSON-RPC frames.

This object belongs to one event-loop thread. It receives copies of relayed
bytes, never changes protocol traffic, and retains metadata rather than bodies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
import time
from typing import Callable


DIAGNOSTIC_CATEGORIES = frozenset(
    {
        "malformed_json", "invalid_utf8", "invalid_message", "invalid_id",
        "invalid_tool_name", "unsupported_batch", "oversized_frame",
        "truncated_frame", "duplicate_id", "orphan_response", "calls_limit",
        "pending_limit", "metadata_limit", "clock_error", "transport_failure",
        "observer_failure", "interrupted", "shutdown_timeout", "server_failed",
        "stderr_not_forwarded",
    }
)
_INFORMATIONAL = frozenset({"stderr_not_forwarded"})
_MAX_METADATA_BYTES = 256


def _reject_constant(_value: str) -> None:
    raise ValueError("Nonstandard JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass
class _Pending:
    started: float
    calls: list[dict] = field(default_factory=list)
    ambiguous: bool = False


class Observer:
    """Classify bounded frames and correlate client tool calls by typed ID.

    String IDs and tool names are limited to 256 UTF-8 bytes; integer IDs and
    error codes have the same decimal-byte bound. MCP IDs are strings or
    integers, so booleans, null, and floating-point IDs are ineligible.

    Duplicate active keys remain quarantined for the rest of the session.
    Pending storage exhaustion disables admission of new correlation keys,
    while already tracked keys can still complete. These conservative rules
    avoid assigning old responses to later requests after observation loss.
    An opaque frame quarantines all active keys and disables new correlation
    for both directions until finish, since it may hide a request or response.
    """

    def __init__(
        self,
        max_frame_bytes: int = 1048576,
        max_calls: int = 10000,
        max_pending: int = 20000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for value in (max_frame_bytes, max_calls, max_pending):
            if type(value) is not int or value < 1:
                raise ValueError("Observation limits must be positive integers")
        self._limits = {
            "max_frame_bytes": max_frame_bytes,
            "max_calls": max_calls,
            "max_pending": max_pending,
            "max_metadata_bytes": _MAX_METADATA_BYTES,
        }
        self._clock = clock
        self._epoch = clock()
        if isinstance(self._epoch, bool) or not isinstance(self._epoch, (float, int)) or not math.isfinite(self._epoch):
            raise ValueError("Clock must return finite monotonic seconds")
        self._last_stamp = self._epoch
        self._buffers = {"client": bytearray(), "server": bytearray()}
        self._discarding = {"client": False, "server": False}
        self._traffic = {direction: {"bytes": 0, "frames": 0} for direction in self._buffers}
        self._calls: list[dict] = []
        self._pending: dict[tuple[str, str, object], _Pending] = {}
        self._pending_limited = False
        self._correlation_lost = False
        self._diagnostics: dict[str, int] = {}
        self._complete = True
        self._observed = 0
        self._finished = False

    def diagnostic(self, category: str, count: int = 1) -> None:
        """Count a fixed diagnostic; arbitrary text is never retained."""
        if type(count) is not int or count < 0:
            raise ValueError("Diagnostic count must be a nonnegative integer")
        if not count:
            return
        if not isinstance(category, str) or category not in DIAGNOSTIC_CATEGORIES:
            category = "observer_failure"
        self._diagnostics[category] = self._diagnostics.get(category, 0) + count
        if category not in _INFORMATIONAL:
            self._complete = False

    def _lose_correlation(self) -> None:
        self._correlation_lost = True
        for pending in self._pending.values():
            for call in pending.calls:
                call["status"] = "ambiguous"
                call["latency_ms"] = None
                call["response_message_bytes"] = None
                call["error_code"] = None
        self._pending.clear()

    def _unclassified(self, category: str) -> None:
        self.diagnostic(category)
        self._lose_correlation()

    def feed(self, direction: str, data: bytes) -> None:
        """Observe bytes with at most one bounded unfinished frame per side."""
        if direction not in self._buffers:
            raise ValueError("Direction must be client or server")
        if self._finished:
            raise RuntimeError("Observation is already finished")
        if not isinstance(data, bytes):
            raise TypeError("Observation input must be bytes")
        self._traffic[direction]["bytes"] += len(data)
        offset = 0
        buffer = self._buffers[direction]
        while offset < len(data):
            newline = data.find(b"\n", offset)
            end = len(data) if newline < 0 else newline
            length = end - offset
            if not self._discarding[direction]:
                if len(buffer) + length > self._limits["max_frame_bytes"]:
                    buffer.clear()
                    self._discarding[direction] = True
                    self._unclassified("oversized_frame")
                else:
                    buffer.extend(memoryview(data)[offset:end])
            if newline < 0:
                return
            self._traffic[direction]["frames"] += 1
            if not self._discarding[direction]:
                self._frame(direction, buffer)
            buffer.clear()
            self._discarding[direction] = False
            offset = newline + 1

    def _timestamp(self) -> float | None:
        try:
            stamp = self._clock()
            if (
                isinstance(stamp, bool)
                or not isinstance(stamp, (float, int))
                or not math.isfinite(stamp)
                or stamp < self._last_stamp
                or not math.isfinite((stamp - self._epoch) * 1000)
            ):
                raise ValueError("Clock did not advance monotonically")
        except Exception:
            self._unclassified("clock_error")
            return None
        self._last_stamp = stamp
        return stamp

    def _frame(self, direction: str, data: bytearray) -> None:
        stamp = self._timestamp()
        if stamp is None:
            return
        try:
            decoded = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            self._unclassified("invalid_utf8")
            return
        try:
            message = json.loads(decoded, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            self._unclassified("malformed_json")
            return
        if isinstance(message, list):
            self._unclassified("unsupported_batch")
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._unclassified("invalid_message")
            return
        if "method" in message:
            if (
                not isinstance(message["method"], str)
                or not message["method"]
                or "result" in message
                or "error" in message
                or ("params" in message and not isinstance(message["params"], (dict, list)))
            ):
                self._unclassified("invalid_message")
                return
            if "id" in message:
                self._request(direction, message, stamp)
            elif message["method"] == "tools/call":
                self._request(direction, message, stamp)
            return  # Notifications have no response correlation.
        if "params" in message or ("result" in message) == ("error" in message):
            self._unclassified("invalid_message")
            return
        self._response(direction, message, stamp, len(data))

    def _metadata(self, value: str) -> bool:
        try:
            length = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            self.diagnostic("invalid_utf8")
            return False
        if length > _MAX_METADATA_BYTES:
            self.diagnostic("metadata_limit")
            return False
        return True

    def _id(self, message: dict) -> tuple[str, object] | None:
        value = message.get("id")
        if isinstance(value, str):
            if self._metadata(value):
                return ("string", value)
            self._lose_correlation()
            return None
        if type(value) is int:
            if self._metadata(str(value)):
                return ("number", value)
            self._lose_correlation()
            return None
        self._unclassified("invalid_id")
        return None

    def _request(self, origin: str, message: dict, stamp: float) -> None:
        is_tool = origin == "client" and message["method"] == "tools/call"
        if is_tool:
            self._observed += 1
        typed_id = self._id(message)
        if typed_id is None:
            return
        call = None
        if is_tool:
            params = message.get("params")
            tool = params.get("name") if isinstance(params, dict) else None
            if not isinstance(tool, str) or not tool:
                self.diagnostic("invalid_tool_name")
            elif self._metadata(tool):
                if len(self._calls) >= self._limits["max_calls"]:
                    self.diagnostic("calls_limit")
                else:
                    call = {
                        "sequence": self._observed,
                        "tool": tool,
                        "request_id": {"type": typed_id[0], "value": typed_id[1]},
                        "origin": origin,
                        "started_ms": (stamp - self._epoch) * 1000,
                        "status": "pending",
                        "latency_ms": None,
                        "response_message_bytes": None,
                        "error_code": None,
                    }
                    self._calls.append(call)
        if self._correlation_lost:
            if call is not None:
                call["status"] = "unresolved"
            return
        key = (origin, *typed_id)
        pending = self._pending.get(key)
        if pending is not None:
            self.diagnostic("duplicate_id")
            pending.ambiguous = True
            if call is not None:
                pending.calls.append(call)
            for related in pending.calls:
                related["status"] = "ambiguous"
                related["latency_ms"] = None
                related["response_message_bytes"] = None
                related["error_code"] = None
            return
        if self._pending_limited or len(self._pending) >= self._limits["max_pending"]:
            self._pending_limited = True
            self.diagnostic("pending_limit")
            if call is not None:
                call["status"] = "unresolved"
            return
        self._pending[key] = _Pending(stamp, [] if call is None else [call])

    def _response(self, direction: str, message: dict, stamp: float, size: int) -> None:
        typed_id = self._id(message)
        if typed_id is None:
            return
        error_code = None
        status = "success"
        if "error" in message:
            error = message["error"]
            if (
                not isinstance(error, dict)
                or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)
            ):
                self._unclassified("invalid_message")
                return
            status = "rpc_error"
            if self._metadata(str(error["code"])):
                error_code = error["code"]
        else:
            result = message["result"]
            if isinstance(result, dict) and "isError" in result:
                if type(result["isError"]) is not bool:
                    self._unclassified("invalid_message")
                    return
                if result["isError"]:
                    status = "tool_error"
        origin = "client" if direction == "server" else "server"
        key = (origin, *typed_id)
        pending = self._pending.get(key)
        if pending is None:
            self.diagnostic("orphan_response")
            return
        if pending.ambiguous:
            return
        del self._pending[key]
        for call in pending.calls:
            call["status"] = status
            call["latency_ms"] = (stamp - pending.started) * 1000
            call["response_message_bytes"] = size
            call["error_code"] = error_code

    def finish(self) -> None:
        """Finalize once, keeping unmatched tool requests visibly unresolved."""
        if self._finished:
            return
        for direction, buffer in self._buffers.items():
            if buffer or self._discarding[direction]:
                self.diagnostic("truncated_frame")
            buffer.clear()
            self._discarding[direction] = False
        for call in self._calls:
            if call["status"] == "pending":
                call["status"] = "unresolved"
        self._pending.clear()
        self._finished = True

    def snapshot(self) -> dict:
        """Return detached, JSON-ready metadata with no protocol bodies."""
        return copy.deepcopy(
            {
                "calls": self._calls,
                "diagnostics": self._diagnostics,
                "coverage": {"complete": self._complete, "limits": self._limits},
                "traffic": self._traffic,
                "tool_calls_observed": self._observed,
            }
        )
