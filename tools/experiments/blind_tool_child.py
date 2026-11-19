"""Bind an ephemeral Codex child using only IDs observed on the owned live host."""

from hashlib import sha256
import re

from tools.check_codex_interop import InteropFailure
from tools.experiments.blind_tool_host import _host_id, direct_child, model_settings


def child_permissions(item):
    """Project the host-supplied read-only permission block without retaining its contents."""
    if item.get("type") != "message" or item.get("role") != "developer":
        return None
    blocks = []
    for part in item.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "input_text":
            body = part.get("text")
            if isinstance(body, str):
                blocks.extend(
                    re.findall(
                        r"<permissions instructions>(.*?)</permissions instructions>", body, re.S
                    )
                )
    if len(blocks) != 1:
        return None
    block = blocks[0]
    if (
        "`sandbox_mode` is `read-only`" not in block
        or "Approval policy is currently never." not in block
    ):
        return None
    return {
        "approval": "never",
        "sandbox": "readOnly",
        "source": "host_delivered_permissions_instructions",
        "digest": sha256(block.encode()).hexdigest(),
    }


async def verify_live_child(client, parent, parent_session, actor):
    """Return child settings only when its exact host metadata confirms both relationships."""
    if (
        not _host_id(parent)
        or not _host_id(parent_session)
        or not _host_id(actor)
        or actor == parent
    ):
        return None
    try:
        reply = await client.request("thread/read", {"threadId": actor, "includeTurns": False})
    except (InteropFailure, TimeoutError):
        return None
    thread = reply.get("thread") if isinstance(reply, dict) else None
    if (
        not isinstance(thread, dict)
        or thread.get("id") != actor
        or thread.get("sessionId") != parent_session
        or direct_child(thread, parent) != actor
    ):
        return None
    return model_settings({"thread": thread})
