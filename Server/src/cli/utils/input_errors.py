"""Typed local input failures rendered by the shared CLI command boundary."""

from typing import Literal


class CLIInputError(ValueError):
    """A command callback rejected input before attempting Unity dispatch."""

    def __init__(
        self,
        code: Literal["invalid_json_params", "missing_code", "invalid_scene_index"],
        message: str,
    ) -> None:
        self.code = code
        super().__init__(message)
