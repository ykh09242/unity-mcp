"""Fresh connection admission uses one complete, timely handshake."""

import pytest

from .test_stdio_command_deadline import _run


AUTH_PEER = r"""
from transport.legacy import stdio_auth as auth
generation, challenge, session = "a" * 32, "b" * 64, "c" * 32
token, nonce = "owned-test-token-" + "x" * 32, "d" * 64
auth.secrets.token_hex = lambda count: nonce
banner = (f"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 "
          f"SERVER={generation} CHALLENGE={challenge}\n").encode("ascii")
reply = json.dumps({
    "type": "authenticated", "version": 2, "session_id": session,
    "proof": auth.authentication_proof(token, ["server", generation, challenge, nonce, session]),
}).encode("ascii")
conn.auth_token_provider = lambda server_generation: token
"""


@pytest.mark.parametrize("protocol", ["legacy", "authenticated"])
@pytest.mark.parametrize("outer_deadline", [None, 0.5, 2.0])
def test_final_handshake_read_at_effective_deadline_rejects_connection(
    protocol, outer_deadline, tmp_path
):
    _run(
        AUTH_PEER
        + f"""
protocol, outer_deadline = {protocol!r}, {outer_deadline!r}
effective_deadline = min(1.0, outer_deadline) if outer_deadline is not None else 1.0
if protocol == "legacy":
    reads = [(0.0, b"WELCOME UNITY-MCP 1 FRAMING=1"), (effective_deadline, b"\\n")]
else:
    reads = [(0.0, banner), (0.0, struct.pack(">Q", len(reply))),
             (0.0, reply[:-1]), (effective_deadline, reply[-1:])]
sock = ClockedSocket(clock, reads)
module.socket.create_connection = lambda endpoint, timeout: sock
assert conn.connect(deadline=outer_deadline) is False, "final bytes at the handshake deadline were admitted"
assert clock.now == effective_deadline
assert conn.sock is None and conn.session_generation is None and sock.closed
assert sock.timeout == config.connection_timeout
""",
        tmp_path,
    )


@pytest.mark.parametrize("protocol", ["legacy", "authenticated"])
def test_timely_handshake_preserves_framing_generation_and_outer_command_budget(protocol, tmp_path):
    _run(
        AUTH_PEER
        + f"""
config.command_total_timeout = 0.1
if {protocol!r} == "legacy":
    reads = [(0.0, b"WELCOME UNITY-MCP 1 FRAMING=1"), (0.5, b"\\n")]
else:
    reads = [(0.0, banner), (0.0, struct.pack(">Q", len(reply))), (0.5, reply)]
sock = ClockedSocket(clock, reads)
module.socket.create_connection = lambda endpoint, timeout: sock
assert conn.connect(deadline=2.0) is True
assert clock.now == 0.5 and clock.now > config.command_total_timeout
assert conn.sock is sock and not sock.closed and conn.use_framing
assert sock.timeout == config.connection_timeout
assert conn.session_generation == (generation + ":" + session if {protocol!r} == "authenticated" else None)
""",
        tmp_path,
    )


@pytest.mark.parametrize("greeting", [b"WELCOME UNITY-MCP 1", b"WELCOME UNITY-MCP 1 FRAMING=1"])
def test_valid_legacy_banner_without_lf_then_eof_rejects_connection(greeting, tmp_path):
    _run(
        f"""
config.require_framing = False
sock = ClockedSocket(clock, [(0.0, {greeting!r}), (0.0, b"")])
module.socket.create_connection = lambda endpoint, timeout: sock
assert conn.connect() is False, "EOF cannot terminate the required greeting line"
assert conn.sock is None and conn.session_generation is None and sock.closed
""",
        tmp_path,
    )


def test_authentication_proof_finishing_at_deadline_is_not_published(tmp_path):
    _run(
        AUTH_PEER
        + """
sock = ClockedSocket(clock, [(0.0, banner), (0.0, struct.pack(">Q", len(reply))), (0.5, reply)])
module.socket.create_connection = lambda endpoint, timeout: sock
compare = auth.hmac.compare_digest
def complete_verification_at_deadline(actual, expected):
    clock.now = 1.0
    return compare(actual, expected)
auth.hmac.compare_digest = complete_verification_at_deadline
assert conn.connect() is False, "verification completed at an expired handshake deadline"
assert conn.sock is None and conn.session_generation is None and sock.closed
""",
        tmp_path,
    )


@pytest.mark.parametrize("chunks", [[(1.0, b"abcd")], [(0.0, b"abc"), (1.0, b"d")]])
def test_authentication_exact_read_checks_deadline_after_final_receive(chunks, tmp_path):
    _run(
        AUTH_PEER
        + f"""
sock = ClockedSocket(clock, {chunks!r})
try:
    auth._read_exact(sock, 4, 1.0)
except auth.StdioAuthenticationError as exc:
    assert "deadline expired" in str(exc)
else:
    raise AssertionError("authentication frame bytes at deadline were accepted")
assert clock.now == 1.0
""",
        tmp_path,
    )
