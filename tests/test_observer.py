"""Behavioral checks for the bounded metadata observer."""

import json
import tracemalloc
import unittest

try:
    from mcp_session_profiler.observer import Observer
except ImportError:
    Observer = None


class ManualClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def frame(message):
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def request(request_id=1, tool="demo"):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool}}


def response(request_id=1, result=None):
    return {"jsonrpc": "2.0", "id": request_id, "result": {} if result is None else result}


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Observer, "The bounded frame observer has not been implemented")
        self.clock = ManualClock()
        self.observer = Observer(clock=self.clock)

    def send(self, direction, message):
        self.observer.feed(direction, frame(message))

    def test_latency_starts_and_ends_at_completed_frames(self):
        outgoing = frame(request())
        self.observer.feed("client", outgoing[:-1])
        self.clock.now = 101.0
        self.observer.feed("client", outgoing[-1:])
        self.clock.now = 101.25
        incoming = b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"\xe9\x9b\xaa"}]}}\r\n'
        self.observer.feed("server", incoming[:-1])
        self.clock.now = 101.5
        self.observer.feed("server", incoming[-1:])
        snapshot = self.observer.snapshot()
        call = snapshot["calls"][0]
        self.assertEqual(call["status"], "success")
        self.assertEqual(call["started_ms"], 1000.0)
        self.assertEqual(call["latency_ms"], 500.0)
        self.assertEqual(call["response_message_bytes"], len(incoming) - 1)
        self.assertEqual(snapshot["traffic"]["client"], {"bytes": len(outgoing), "frames": 1})
        self.assertEqual(snapshot["traffic"]["server"], {"bytes": len(incoming), "frames": 1})

    def test_numeric_and_string_ids_match_out_of_order(self):
        self.send("client", request(1, "numeric"))
        self.clock.now = 101.0
        self.send("client", request("1", "string"))
        self.clock.now = 102.0
        self.send("server", response("1"))
        self.clock.now = 103.0
        self.send("server", response(1))
        calls = self.observer.snapshot()["calls"]
        self.assertEqual([c["latency_ms"] for c in calls], [3000.0, 1000.0])
        self.assertEqual([c["request_id"] for c in calls], [{"type": "number", "value": 1}, {"type": "string", "value": "1"}])
        self.assertEqual([c["sequence"] for c in calls], [1, 2])

    def test_server_request_with_same_id_does_not_complete_client_call(self):
        self.send("client", request(7))
        self.send("server", {"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage", "params": {}})
        self.clock.now = 101.0
        self.send("client", response(7))
        self.assertEqual(self.observer.snapshot()["calls"][0]["status"], "pending")
        self.clock.now = 102.0
        self.send("server", response(7))
        call = self.observer.snapshot()["calls"][0]
        self.assertEqual(call["origin"], "client")
        self.assertEqual(call["latency_ms"], 2000.0)
        self.assertEqual(self.observer.snapshot()["diagnostics"].get("orphan_response", 0), 0)

    def test_notifications_and_cancellation_do_not_complete_calls(self):
        self.send("client", request())
        self.send("client", {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}})
        self.send("server", {"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "private"}})
        self.observer.finish()
        call = self.observer.snapshot()["calls"][0]
        self.assertEqual(call["status"], "unresolved")
        self.assertIsNone(call["latency_ms"])

    def test_tool_error_and_rpc_error_are_separate_without_error_text(self):
        self.send("client", request(1, "tool"))
        self.send("client", request(2, "rpc"))
        self.send("server", response(1, {"isError": True, "content": [{"type": "text", "text": "RESULT_SECRET"}]}))
        self.send("server", {"jsonrpc": "2.0", "id": 2, "error": {"code": -32603, "message": "ERROR_SECRET", "data": "DATA_SECRET"}})
        snapshot = self.observer.snapshot()
        self.assertEqual([c["status"] for c in snapshot["calls"]], ["tool_error", "rpc_error"])
        self.assertEqual(snapshot["calls"][1]["error_code"], -32603)
        self.assertNotIn("SECRET", json.dumps(snapshot))

    def test_completed_id_can_be_reused(self):
        for tool in ("first", "second"):
            self.send("client", request(1, tool))
            self.clock.now += 1.0
            self.send("server", response(1))
        calls = self.observer.snapshot()["calls"]
        self.assertEqual([c["status"] for c in calls], ["success", "success"])
        self.assertEqual([c["latency_ms"] for c in calls], [1000.0, 1000.0])

    def test_duplicate_active_ids_mark_every_related_call_ambiguous(self):
        self.send("client", request(1, "first"))
        self.send("client", request(1, "second"))
        self.send("server", response(1))
        self.send("server", response(1))
        self.send("client", request(1, "later"))
        self.send("server", response(1))
        self.observer.finish()
        snapshot = self.observer.snapshot()
        self.assertEqual([c["status"] for c in snapshot["calls"]], ["ambiguous"] * 3)
        for call in snapshot["calls"]:
            self.assertIsNone(call["latency_ms"])
            self.assertIsNone(call["response_message_bytes"])
            self.assertIsNone(call["error_code"])
        self.assertFalse(snapshot["coverage"]["complete"])
        self.assertGreaterEqual(snapshot["diagnostics"]["duplicate_id"], 1)

    def test_non_tool_duplicate_also_poisons_tool_correlation(self):
        self.send("client", request())
        self.send("client", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.send("server", response())
        self.assertEqual(self.observer.snapshot()["calls"][0]["status"], "ambiguous")

    def test_orphan_response_records_no_invented_call(self):
        self.send("server", response(9))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(snapshot["diagnostics"]["orphan_response"], 1)
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_malformed_utf8_and_batch_are_diagnostics_without_payloads(self):
        self.observer.feed("server", b"LOG_SECRET\n\xff\n")
        self.send("server", [response(1), response(2)])
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["diagnostics"]["malformed_json"], 1)
        self.assertEqual(snapshot["diagnostics"]["invalid_utf8"], 1)
        self.assertEqual(snapshot["diagnostics"]["unsupported_batch"], 1)
        self.assertEqual(snapshot["traffic"]["server"]["frames"], 3)
        self.assertFalse(snapshot["coverage"]["complete"])
        self.assertNotIn("SECRET", json.dumps(snapshot))

    def test_invalid_response_envelopes_never_complete_call(self):
        invalid = [
            {"id": 1, "result": {}},
            {"jsonrpc": "1.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": "bad"}},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "method": "ping"},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "params": {}},
            {"jsonrpc": "2.0", "id": 1, "error": {"message": "bad"}},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": True, "message": "bad"}},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": 1}},
            {"jsonrpc": "2.0", "id": 1, "result": {"isError": "false"}},
        ]
        for message in invalid:
            with self.subTest(message=message):
                observer = Observer(clock=self.clock)
                observer.feed("client", frame(request()))
                observer.feed("server", frame(message))
                observer.finish()
                call = observer.snapshot()["calls"][0]
                self.assertEqual(call["status"], "ambiguous")
                self.assertIsNone(call["latency_ms"])
                self.assertFalse(observer.snapshot()["coverage"]["complete"])

    def test_invalid_ids_never_alias_numeric_id(self):
        self.send("client", request(1))
        for invalid_id in (True, None, 1.0, [], {}):
            self.send("server", response(invalid_id))
        self.observer.finish()
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"][0]["status"], "ambiguous")
        self.assertEqual(snapshot["diagnostics"]["invalid_id"], 5)

    def test_tool_call_without_id_is_counted_but_excluded_from_metrics(self):
        message = request()
        del message["id"]
        self.send("client", message)
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(snapshot["tool_calls_observed"], 1)
        self.assertEqual(snapshot["diagnostics"].get("invalid_id", 0), 1)
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_clock_overflow_cannot_enter_json_metadata(self):
        clock = ManualClock(-1e308)
        observer = Observer(clock=clock)
        clock.now = 1e308
        observer.feed("client", frame(request()))
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(snapshot["diagnostics"].get("clock_error", 0), 1)
        json.dumps(snapshot, allow_nan=False)

    def test_invalid_request_names_and_shapes_are_excluded(self):
        for params in ({}, {"name": None}, {"name": ""}, {"name": 9}, []):
            message = request()
            message["params"] = params
            self.send("client", message)
        self.send("client", {"jsonrpc": "2.0", "id": 2, "method": 7})
        self.assertEqual(self.observer.snapshot()["calls"], [])
        self.assertFalse(self.observer.snapshot()["coverage"]["complete"])

    def test_oversized_frame_resynchronizes_without_resuming_attribution(self):
        observer = Observer(max_frame_bytes=128, clock=self.clock)
        for _ in range(10):
            observer.feed("client", b"X" * 100)
        observer.feed("client", b"\n" + frame(request()))
        observer.feed("server", frame(response()))
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["diagnostics"]["oversized_frame"], 1)
        self.assertEqual(snapshot["traffic"]["client"]["frames"], 2)
        self.assertEqual(snapshot["calls"][0]["status"], "unresolved")
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_opaque_frame_cannot_hide_a_duplicate_or_a_completed_response(self):
        opaque_frames = (
            ("client", b"X" * 129 + b"\n"),
            ("client", b"malformed-secret\n"),
            ("client", b"\xff\n"),
            ("client", frame([request(1)])),
            ("client", frame(request(None))),
            ("client", frame(request("x" * 257))),
            ("server", b"malformed-response\n"),
            ("server", frame([response(1)])),
        )
        for direction, opaque in opaque_frames:
            with self.subTest(direction=direction, opaque=opaque[:20]):
                observer = Observer(max_frame_bytes=128, clock=self.clock)
                observer.feed("client", frame(request(1, "original")))
                observer.feed(direction, opaque)
                observer.feed("server", frame(response(1)))
                observer.feed("client", frame(request(1, "reuse")))
                observer.feed("server", frame(response(1)))
                observer.finish()
                calls = observer.snapshot()["calls"]
                self.assertEqual([c["status"] for c in calls], ["ambiguous", "unresolved"])
                self.assertEqual([c["latency_ms"] for c in calls], [None, None])
                self.assertEqual([c["response_message_bytes"] for c in calls], [None, None])
                self.assertFalse(observer.snapshot()["coverage"]["complete"])

    def test_completed_metrics_before_opaque_frame_remain_valid(self):
        self.send("client", request(1))
        self.send("server", response(1))
        self.observer.feed("client", b"malformed\n")
        self.send("client", request(2))
        self.send("server", response(2))
        calls = self.observer.snapshot()["calls"]
        self.assertEqual([c["status"] for c in calls], ["success", "unresolved"])
        self.assertIsNotNone(calls[0]["latency_ms"])
        self.assertIsNone(calls[1]["latency_ms"])

    def test_recognizable_tool_calls_with_invalid_ids_are_observed(self):
        for invalid_id in (None, True, 1.0, [], {}):
            self.send("client", request(invalid_id))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["tool_calls_observed"], 5)
        self.assertEqual(snapshot["calls"], [])
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_unusable_id_metadata_quarantines_existing_calls(self):
        for invalid_id in ("x" * 257, 10 ** 256, "\ud800"):
            with self.subTest(invalid_id_type=type(invalid_id).__name__):
                observer = Observer(clock=self.clock)
                observer.feed("client", frame(request(1)))
                raw = json.dumps(request(invalid_id)).encode("utf-8") + b"\n"
                observer.feed("client", raw)
                observer.feed("server", frame(response(1)))
                observer.feed("client", frame(request(2)))
                observer.feed("server", frame(response(2)))
                calls = observer.snapshot()["calls"]
                self.assertEqual([c["status"] for c in calls], ["ambiguous", "unresolved"])
                self.assertEqual([c["latency_ms"] for c in calls], [None, None])

    def test_frame_at_exact_limit_is_classified(self):
        outgoing = frame(request())
        observer = Observer(max_frame_bytes=len(outgoing) - 1, clock=self.clock)
        observer.feed("client", outgoing)
        observer.feed("server", frame(response()))
        self.assertEqual(observer.snapshot()["calls"][0]["status"], "success")
        self.assertTrue(observer.snapshot()["coverage"]["complete"])

    def test_calls_limit_keeps_observed_count_and_existing_correlation(self):
        observer = Observer(max_calls=1, clock=self.clock)
        observer.feed("client", frame(request(1, "retained")) + frame(request(2, "dropped")))
        observer.feed("server", frame(response(2)) + frame(response(1)))
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["tool_calls_observed"], 2)
        self.assertEqual(len(snapshot["calls"]), 1)
        self.assertEqual(snapshot["calls"][0]["status"], "success")
        self.assertEqual(snapshot["diagnostics"]["calls_limit"], 1)
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_pending_limit_does_not_admit_future_false_matches(self):
        observer = Observer(max_pending=1, clock=self.clock)
        observer.feed("client", frame(request(1)) + frame(request(2, "untracked")))
        observer.feed("server", frame(response(1)))
        observer.feed("client", frame(request(2, "reused-untracked")))
        observer.feed("server", frame(response(2)))
        observer.finish()
        snapshot = observer.snapshot()
        self.assertEqual([c["status"] for c in snapshot["calls"]], ["success", "unresolved", "unresolved"])
        self.assertEqual(snapshot["diagnostics"]["pending_limit"], 2)
        self.assertFalse(snapshot["coverage"]["complete"])

    def test_dropped_call_duplicate_still_marks_retained_call_ambiguous(self):
        observer = Observer(max_calls=1, clock=self.clock)
        observer.feed("client", frame(request(1)) + frame(request(1, "dropped")))
        observer.feed("server", frame(response(1)))
        self.assertEqual(observer.snapshot()["calls"][0]["status"], "ambiguous")

    def test_long_id_and_tool_metadata_are_excluded(self):
        limit = self.observer.snapshot()["coverage"]["limits"]["max_metadata_bytes"]
        self.send("client", request("ID_SECRET" + "x" * limit))
        self.send("client", request(2, "TOOL_SECRET" + "x" * limit))
        self.send("client", request(3, "雪" * limit))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"], [])
        self.assertGreaterEqual(snapshot["diagnostics"]["metadata_limit"], 3)
        self.assertFalse(snapshot["coverage"]["complete"])
        self.assertNotIn("SECRET", json.dumps(snapshot))

    def test_snapshot_has_no_bodies_and_cannot_mutate_observer(self):
        message = request()
        message["params"]["arguments"] = {"password": "ARGUMENT_SECRET"}
        self.send("client", message)
        self.send("server", response(1, {"content": [{"text": "RESULT_SECRET"}]}))
        snapshot = self.observer.snapshot()
        self.assertNotIn("SECRET", json.dumps(snapshot))
        snapshot["calls"][0]["request_id"]["value"] = "MUTATION_SECRET"
        snapshot["coverage"]["limits"]["max_calls"] = 0
        self.assertEqual(self.observer.snapshot()["calls"][0]["request_id"]["value"], 1)
        self.assertGreater(self.observer.snapshot()["coverage"]["limits"]["max_calls"], 0)

    def test_finish_marks_incomplete_tail_and_is_idempotent(self):
        self.send("client", request())
        self.observer.feed("server", b'{"jsonrpc":"2.0","id":1,"result":{}}')
        self.observer.finish()
        self.observer.finish()
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["diagnostics"]["truncated_frame"], 1)
        self.assertEqual(snapshot["calls"][0]["status"], "unresolved")
        self.assertEqual(snapshot["traffic"]["server"]["frames"], 0)
        self.assertFalse(snapshot["coverage"]["complete"])
        with self.assertRaises(RuntimeError):
            self.observer.feed("server", b"\n")

    def test_diagnostic_categories_do_not_accept_arbitrary_private_text(self):
        self.observer.diagnostic("stderr_not_forwarded", 2)
        self.observer.diagnostic("DIAGNOSTIC_SECRET")
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["diagnostics"]["stderr_not_forwarded"], 2)
        self.assertEqual(snapshot["diagnostics"]["observer_failure"], 1)
        self.assertNotIn("SECRET", json.dumps(snapshot))

    def test_nonstandard_json_numbers_and_duplicate_keys_are_rejected(self):
        self.send("client", request())
        self.observer.feed("server", b'{"jsonrpc":"2.0","id":1,"result":NaN}\n')
        self.observer.feed("server", b'{"jsonrpc":"2.0","id":2,"id":1,"result":{}}\n')
        self.observer.finish()
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"][0]["status"], "ambiguous")
        self.assertEqual(snapshot["diagnostics"]["malformed_json"], 2)

    def test_deeply_nested_json_is_diagnostic_not_observer_crash(self):
        self.observer.feed("server", b'[' * 2000 + b'0' + b']' * 2000 + b'\n')
        snapshot = self.observer.snapshot()
        self.assertFalse(snapshot["coverage"]["complete"])
        self.assertEqual(sum(snapshot["diagnostics"].values()), 1)
        self.assertTrue(set(snapshot["diagnostics"]) <= {"malformed_json", "unsupported_batch"})

    def test_large_input_does_not_allocate_an_unbounded_observation_copy(self):
        observer = Observer(max_frame_bytes=128, clock=self.clock)
        oversized = b"X" * 4_000_000
        completed_oversized = oversized + b"\n"
        tracemalloc.start()
        try:
            observer.feed("server", oversized)
            observer.feed("server", completed_oversized)
            retained, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(retained, 100_000)
        self.assertLess(peak, 250_000)
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["traffic"]["server"]["bytes"], 8_000_001)
        self.assertEqual(snapshot["diagnostics"]["oversized_frame"], 1)

    def test_many_unanswered_requests_bound_retained_records_and_pending_memory(self):
        observer = Observer(max_calls=4, max_pending=4, clock=self.clock)
        tracemalloc.start()
        try:
            for request_id in range(5000):
                observer.feed("client", frame(request(request_id)))
            retained, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(retained, 100_000)
        self.assertLess(peak, 250_000)
        snapshot = observer.snapshot()
        self.assertEqual(len(snapshot["calls"]), 4)
        self.assertEqual(snapshot["tool_calls_observed"], 5000)
        self.assertEqual(snapshot["diagnostics"]["calls_limit"], 4996)
        self.assertEqual(snapshot["diagnostics"]["pending_limit"], 4996)

    def test_backwards_clock_cannot_generate_negative_latency(self):
        self.send("client", request())
        self.clock.now = 99.0
        self.send("server", response())
        self.observer.finish()
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["diagnostics"]["clock_error"], 1)
        self.assertEqual(snapshot["calls"][0]["status"], "ambiguous")
        self.assertIsNone(snapshot["calls"][0]["latency_ms"])

    def test_stderr_default_policy_keeps_coverage_complete(self):
        self.observer.diagnostic("stderr_not_forwarded", 5)
        self.assertTrue(self.observer.snapshot()["coverage"]["complete"])

    def test_invalid_utf8_surrogate_metadata_is_excluded(self):
        self.observer.feed("client", b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"\\ud800"}}\n')
        self.assertEqual(self.observer.snapshot()["calls"], [])
        self.assertFalse(self.observer.snapshot()["coverage"]["complete"])

    def test_server_tool_request_is_tracked_without_client_tool_metrics(self):
        self.send("server", request(1))
        self.send("client", response(1))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["calls"], [])
        self.assertEqual(snapshot["tool_calls_observed"], 0)
        self.assertTrue(snapshot["coverage"]["complete"])

    def test_invalid_limits_directions_and_diagnostic_counts_are_rejected(self):
        for field in ("max_frame_bytes", "max_calls", "max_pending"):
            for value in (0, -1, True, 1.5):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    Observer(**{field: value})
        with self.assertRaises(ValueError):
            self.observer.feed("private-secret", b"\n")
        for count in (-1, True, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.observer.diagnostic("invalid_message", count)


if __name__ == "__main__":
    unittest.main()
