"""Run one Python revision against one compiled peer. Each line carries one fixture operation."""

from collections import Counter
from dataclasses import dataclass, replace
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Timer
import time

# -I and an explicit archive root keep installed/candidate modules out of the stable reader.
sys.path.insert(0, sys.argv[1])

from transport.command_resume import CommandResumeInvocation, CommandResumeProtocolError
from transport.json_decoder import decode_json
from transport.models import CommandResultMessage
from transport.native_arbitration import (
    NativeArbitrationProtocolError,
    NativeArbitrationRequest,
    decode_native_reply,
)

EVENTS = ("request", "start", "cancel", "disconnect", "complete")
MAX_EVENTS = 6
PARAMS = {"action": "save", "label": "2026-01-01T00:00:00Z", "optional": None}
TERMINAL = {"completed", "canceled"}


class Divergence(Exception):
    def __init__(self, events, detail):
        super().__init__(str(detail))
        self.receipt = {"events": list(events), "detail": detail}


def require(equal, events, **detail):
    if not equal:
        raise Divergence(events, detail)


class Peer:
    def __init__(self, process):
        self.process = process

    def call(self, op, **fields):
        self.process.stdin.write(json.dumps({"op": op, **fields}) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"C# peer exited during {op}")
        result = json.loads(line)
        if "peer_error" in result:
            raise ValueError(f"{op}: {result['peer_error']}: {result['message']}")
        return result


class Exchange:
    def __init__(self, peer):
        self.peer = peer
        identity = peer.call("reset")
        self.inv = CommandResumeInvocation.create(identity["session"], time.monotonic() + 60)
        self.generation = identity["generation"]
        self.attempts = {}
        self.actions = []
        self.connection = 1
        self.connected = True
        self.settled = None
        self.events = []

    def send(self, *, observer=False, request_fault=None, reply_fault=None):
        # A status observation must not change the original sender's cancellation/knowledge.
        owner = replace(self.inv, action="status", cancelled=Event()) if observer else self.inv
        resume = owner.to_wire()
        if not observer:
            self.actions.append(resume["action"])
        if request_fault == "omitted":
            del resume["action"]
        elif request_fault == "null":
            resume["action"] = None
        elif request_fault == "unknown":
            resume["action"] = "future-action"
        attempt = NativeArbitrationRequest.create(self.generation)
        self.attempts[attempt.request_nonce] = attempt
        command = {
            "type": "manage_scene",
            "params": PARAMS,
            "resume": resume,
            "arbitration": attempt.to_wire(),
        }
        owner.dispatch()
        frame = self.peer.call(
            "request", connection=self.connection, wire=json.dumps(command), fault=reply_fault
        )
        return frame, owner

    def decode(self, frame, owner):
        stdio = json.loads(frame["stdio"])
        ws = CommandResultMessage(**decode_json(frame["websocket"]))
        require(
            ws.type == "command_result"
            and ws.id == frame["attempt"]
            and ws.id in self.attempts
            and ws.result == stdio,
            self.events,
            reason="stdio and WebSocket response envelopes differ",
        )
        try:
            reply = decode_native_reply(owner.unwrap(ws.result), self.attempts[ws.id])
            owner.validate_reply(reply)
            reply = owner.normalize_reply("manage_scene", reply)
        except (CommandResumeProtocolError, NativeArbitrationProtocolError) as error:
            return {"python": str(error), "decoding": "rejected"}
        return {
            "python": owner.state,
            "retryable": reply.retryable,
            "next_action": owner.to_wire()["action"] if owner.waiting else None,
            "not_started": reply.not_started,
            "payload": reply.payload,
        }

    def deliver(self, frame):
        if frame["connection"] != self.connection or not self.connected or frame["accepted"]:
            return
        received = self.decode(frame, self.inv)
        state = received["python"]
        require(
            state == frame["state"] and (self.settled is None or self.settled == state),
            self.events,
            python=state,
            native=frame["state"],
            previously_settled=self.settled,
        )
        if state in TERMINAL:
            self.settled = state

    def step(self, event):
        self.events.append(event)
        if event == "request":
            if not self.connected:
                self.connection += 1
                self.connected = True
            frame, _ = self.send()
            self.deliver(frame)
        elif event == "cancel":
            self.inv.cancel()
            if self.connected:
                frame, _ = self.send()
                self.deliver(frame)
        elif event == "disconnect":
            self.connected = False
            self.inv.interrupted()
        elif event == "start":
            self.peer.call("start")
        elif event == "complete":
            for frame in self.peer.call("complete")["frames"]:
                self.deliver(frame)
        else:
            raise ValueError(f"Unknown event {event!r}")

    def observe(self):
        frame, owner = self.send(observer=True)
        return {"native": frame["state"], "writes": frame["writes"], **self.decode(frame, owner)}


@dataclass
class Model:
    """One logical write, with a cancellable queue and an immutable terminal phase."""

    state: str = "unknown"
    connected: bool = True
    cancel_requested: bool = False
    writes: int = 0

    def step(self, event):
        if event == "disconnect":
            self.connected = False
        elif event == "cancel":
            self.cancel_requested = True
            if self.connected and self.state != "completed":
                self.state = "canceled"
        elif event == "request":
            self.connected = True
            if self.cancel_requested and self.state != "completed":
                self.state = "canceled"
            elif self.state == "unknown":
                self.state = "queued"
        elif event == "start" and self.state == "queued":
            self.writes += 1
            self.state = "executing"
        elif event == "complete" and self.state == "executing":
            self.state = "completed"


def traces():
    # Enumerate by length. Identical event strings run once. No random seed or scheduler.
    # A second request exercises retransmission/reconnection; a second start attempts duplicate work.
    for length in range(1, MAX_EVENTS + 1):
        for events in product(EVENTS, repeat=length):
            counts = Counter(events)
            if (
                events[0] not in {"request", "cancel"}
                or counts["request"] > 2
                or counts["start"] > 2
                or any(counts[event] > 1 for event in ("disconnect", "cancel", "complete"))
                or "complete" in events
                and "start" not in events[: events.index("complete")]
            ):
                continue
            yield events


def compare_trace(peer, events):
    exchange = Exchange(peer)
    model = Model()
    for event in events:
        model.step(event)
        exchange.step(event)
        seen = exchange.observe()
        require(
            seen["python"] == seen["native"] == model.state
            and seen["writes"] == model.writes <= 1
            and seen["retryable"] == (model.state in {"queued", "executing"})
            and seen["next_action"] == {"queued": "resume", "executing": "status"}.get(model.state),
            exchange.events,
            expected={"state": model.state, "writes": model.writes},
            observed=seen,
        )
        if model.state == "completed":
            require(
                seen["payload"] == {"success": True, "data": PARAMS}, exchange.events, result=seen
            )


def compatibility(peer):
    cases = {}
    for fault in ("omitted", "null", "unknown"):
        run = Exchange(peer)
        frame, owner = run.send(request_fault=fault)
        cases["request_action_" + fault] = {
            "native": frame["state"],
            "writes": frame["writes"],
            **run.decode(frame, owner),
        }
        run = Exchange(peer)
        run.step("request")
        frame, owner = run.send(observer=True, reply_fault=fault)
        cases["receipt_state_" + fault] = {
            "native": frame["state"],
            "writes": frame["writes"],
            **run.decode(frame, owner),
        }
    run = Exchange(peer)
    for event in ("request", "start", "disconnect", "complete"):
        run.step(event)
    lost_knowledge = run.inv.state
    run.step("request")
    cases["lost_completion"] = {
        "request_actions": run.actions,
        "sender_state_before_reconnect": lost_knowledge,
        "sender_state_after_reconnect": run.inv.state,
        **run.observe(),
    }
    run = Exchange(peer)
    for event in ("request", "start", "cancel", "complete"):
        run.step(event)
    cases["late_canceled_result"] = run.observe()
    return cases


def validate_cases(cases):
    errors = []
    for name, case in cases.items():
        if name == "lost_completion":
            valid = (
                case["request_actions"] == ["new", "resume"]
                and case["sender_state_before_reconnect"] is None
                and case["sender_state_after_reconnect"]
                == case["python"]
                == case["native"]
                == "completed"
                and case["writes"] == 1
                and case["payload"] == {"success": True, "data": PARAMS}
            )
        elif name == "late_canceled_result":
            valid = (
                case["python"] == case["native"] == "canceled"
                and case["payload"] == {"success": True, "data": PARAMS}
                and not case["retryable"]
                and case["writes"] == 1
            )
        elif name == "request_action_unknown":
            valid = (
                case["python"] == case["native"] == "unavailable"
                and case["payload"].get("hint") == "inspect_state_before_retry"
                and case["writes"] == 0
                and not case["retryable"]
            )
        else:
            native = "queued" if name.startswith("receipt_") else "unavailable"
            valid = (
                case["python"] == "invalid_command_resume_response"
                and case["native"] == native
                and case["writes"] == 0
            )
        if not valid:
            errors.append(name)
    return errors


def run(peer, check_model, replay):
    count = 0
    failure = None
    if check_model:
        for events in [replay] if replay is not None else traces():
            count += 1
            try:
                compare_trace(peer, events)
            except Divergence as error:
                failure = error.receipt
                break
            except (KeyError, TypeError, ValueError) as error:
                failure = {"events": list(events), "detail": {"reader_error": str(error)}}
                break
    try:
        cases = compatibility(peer)
        errors = validate_cases(cases)
    except Divergence as error:
        cases = {}
        errors = [error.receipt]
    except (KeyError, TypeError, ValueError) as error:
        cases = {}
        errors = [{"reader_error": str(error)}]
    return {
        "cases": cases,
        "unexpected_cases": errors,
        "model_traces": count,
        "max_events": MAX_EVENTS if check_model else None,
        "divergence": failure,
    }


def main():
    dll, output, mode = sys.argv[2:5]
    replay = json.loads(sys.argv[5]) if len(sys.argv) == 6 else None
    if replay is not None and (not isinstance(replay, list) or tuple(replay) not in traces()):
        raise ValueError("Reproducer is outside the bounded lifecycle event set")
    source = Path(sys.argv[1]).resolve(strict=True)
    for name in (
        "transport.command_resume",
        "transport.native_arbitration",
        "transport.models",
        "transport.json_decoder",
    ):
        if not Path(sys.modules[name].__file__).resolve().is_relative_to(source):
            raise ValueError(f"{name} did not load from the selected server archive")
    with subprocess.Popen(
        ["dotnet", dll], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    ) as process:
        watchdog = Timer(240, process.kill)
        watchdog.start()
        try:
            report = run(Peer(process), mode == "model", replay)
        finally:
            watchdog.cancel()
            process.stdin.close()
            if process.wait(timeout=10) != 0:
                raise RuntimeError("Managed wire peer failed")
    Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
