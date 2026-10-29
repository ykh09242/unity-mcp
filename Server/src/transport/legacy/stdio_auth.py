"""Bounded mutual launch authentication before stdio command admission."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import socket
import struct
import time
from typing import Callable

from transport.native_arbitration import CAPABILITY, valid_generation
from transport.command_resume import CAPABILITY as RESUME_CAPABILITY, ORIGIN_CAPABILITY


class StdioAuthenticationError(RuntimeError):
    """Authentication failed permanently; no downgrade or command retry is safe."""


@dataclass(frozen=True, slots=True)
class StdioAuthentication:
    token_provider: Callable[[str], str | None]
    deadline: float


@dataclass(frozen=True, slots=True)
class StdioAuthenticatedSession:
    server_generation: str
    session_id: str
    capabilities: frozenset[str]
    arbitration_generation: str | None = None
    resume_generation: str | None = None

    @property
    def session_generation(self) -> str:
        return self.server_generation + ":" + self.session_id


def _optional_capabilities(value: object) -> frozenset[str]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) > 16
        or any(type(item) is not str or not 1 <= len(item) <= 64 for item in value)
    ):
        return frozenset()
    return frozenset(value).intersection(
        {"command_progress_v1", CAPABILITY, RESUME_CAPABILITY, ORIGIN_CAPABILITY}
    )


def authentication_proof(token: str, fields: list[str]) -> str:
    """Use role-separated transcripts so client and server proofs cannot reflect."""
    transcript = "\n".join(["unity-mcp-stdio-v2", *fields])
    return hmac.new(token.encode("utf-8"), transcript.encode("ascii"), hashlib.sha256).hexdigest()


def _read_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StdioAuthenticationError("Stdio authentication deadline expired")
        sock.settimeout(remaining)
        chunk = sock.recv(count - len(data))
        if time.monotonic() >= deadline:
            raise StdioAuthenticationError("Stdio authentication deadline expired")
        if not chunk:
            raise StdioAuthenticationError("Stdio authentication peer closed")
        data.extend(chunk)
    return bytes(data)


def authenticate_stdio(sock: socket.socket, banner: str, options: StdioAuthentication) -> str:
    """Authenticate a fresh socket and return its verified connection generation."""
    return authenticate_stdio_session(sock, banner, options).session_generation


def authenticate_stdio_session(
    sock: socket.socket,
    banner: str,
    options: StdioAuthentication,
    *,
    capabilities: tuple[str, ...] = (),
) -> StdioAuthenticatedSession:
    """Keep required authentication unchanged; optional capabilities grant no work authority."""
    match = re.fullmatch(
        r"WELCOME UNITY-MCP 2 FRAMING=1 AUTH=HMAC-SHA256 "
        r"SERVER=([0-9a-f]{32}) CHALLENGE=([0-9a-f]{64})",
        banner,
    )
    if match is None:
        raise StdioAuthenticationError("Unsupported stdio authentication greeting")
    generation, challenge = match.groups()
    token = options.token_provider(generation)
    if token is None or not 32 <= len(token) <= 256:
        raise StdioAuthenticationError("Stdio launch credential unavailable")
    client_nonce = secrets.token_hex(32)
    offered = _optional_capabilities(capabilities)
    request = {
        "type": "authenticate",
        "version": 2,
        "client_nonce": client_nonce,
        "proof": authentication_proof(token, ["client", generation, challenge, client_nonce]),
    }
    if offered:
        request["capabilities"] = sorted(offered)
    payload = json.dumps(
        request,
        separators=(",", ":"),
    ).encode("ascii")
    remaining = options.deadline - time.monotonic()
    if remaining <= 0:
        raise StdioAuthenticationError("Stdio authentication deadline expired")
    sock.settimeout(remaining)
    sock.sendall(struct.pack(">Q", len(payload)) + payload)
    length = struct.unpack(">Q", _read_exact(sock, 8, options.deadline))[0]
    if not 1 <= length <= 1024:
        raise StdioAuthenticationError("Invalid stdio authentication frame size")
    try:
        reply = json.loads(_read_exact(sock, length, options.deadline).decode("ascii"))
    except (ValueError, UnicodeError) as exc:
        raise StdioAuthenticationError("Invalid stdio authentication reply") from exc
    if (
        not isinstance(reply, dict)
        or reply.get("type") != "authenticated"
        or type(reply.get("version")) is not int
        or reply["version"] != 2
    ):
        raise StdioAuthenticationError("Stdio authentication refused")
    session, proof = reply.get("session_id"), reply.get("proof")
    if (
        not isinstance(session, str)
        or re.fullmatch(r"[0-9a-f]{32}", session) is None
        or not isinstance(proof, str)
        or re.fullmatch(r"[0-9a-f]{64}", proof) is None
    ):
        raise StdioAuthenticationError("Invalid stdio authentication reply")
    expected = authentication_proof(token, ["server", generation, challenge, client_nonce, session])
    if not hmac.compare_digest(proof, expected):
        raise StdioAuthenticationError("Stdio server authentication failed")
    if time.monotonic() >= options.deadline:
        raise StdioAuthenticationError("Stdio authentication deadline expired")
    accepted = offered.intersection(_optional_capabilities(reply.get("capabilities")))
    arbitration_generation = reply.get("arbitration_generation") if CAPABILITY in accepted else None
    if CAPABILITY in accepted and not valid_generation(arbitration_generation):
        raise StdioAuthenticationError("Invalid native arbitration generation")
    resume_generation = reply.get("resume_generation") if RESUME_CAPABILITY in accepted else None
    if RESUME_CAPABILITY in accepted and (
        CAPABILITY not in accepted or not valid_generation(resume_generation)
    ):
        raise StdioAuthenticationError("Invalid command continuation generation")
    if ORIGIN_CAPABILITY in accepted and RESUME_CAPABILITY not in accepted:
        raise StdioAuthenticationError("Command origin requires continuation support")
    return StdioAuthenticatedSession(
        generation, session, accepted, arbitration_generation, resume_generation
    )
