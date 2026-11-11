"""JSON-observing socket doubles accepting the production preencoded wire path."""

import json
from types import MethodType, SimpleNamespace


class JsonSocket(SimpleNamespace):
    async def send_text(self, text):
        await self.send_json(json.loads(text))


def bind_json_text(socket):
    """Keep a mock's existing JSON observations behind the text wire boundary."""
    socket.send_text = MethodType(JsonSocket.send_text, socket)
