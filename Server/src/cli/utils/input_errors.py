"""Typed local input failures rendered by the shared CLI command boundary."""

from typing import Literal


class CLIInputError(Exception):
    """Input rejected before Unity dispatch, separate from domain ValueError adapters."""

    def __init__(
        self,
        code: Literal[
            "invalid_json_params",
            "missing_code",
            "invalid_scene_index",
            "invalid_numeric_input",
            "invalid_json_input",
            "invalid_batch_size",
            "invalid_source_input",
        ],
        message: str,
        *,
        hints: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.hints = hints
        super().__init__(message)
