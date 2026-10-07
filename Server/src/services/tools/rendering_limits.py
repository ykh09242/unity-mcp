"""Known request limits; the Editor checks dimensions from the actual viewport."""

from collections.abc import Sequence
import math
from typing import Final

MAX_RENDER_SIDE: Final[int] = 8192
MAX_FRAME_PIXELS: Final[int] = 33_554_432
MAX_SUPER_SIZE: Final[int] = 4
MAX_ORBIT_ELEVATIONS: Final[int] = 16
MAX_ORBIT_SHOTS: Final[int] = 128


def render_dimensions_error(width: int, height: int) -> str | None:
    """Check explicit dimensions without allocating or narrowing integer products."""
    if width < 1 or height < 1 or width > MAX_RENDER_SIDE or height > MAX_RENDER_SIDE:
        return f"Render width and height must be between 1 and {MAX_RENDER_SIDE}."
    if width * height > MAX_FRAME_PIXELS:
        return f"Render dimensions exceed the {MAX_FRAME_PIXELS} pixel frame budget."
    return None


def screenshot_limits_error(super_size: int, max_resolution: int) -> str | None:
    """Bound caller-controlled scalars before resolving an Editor instance."""
    if super_size < 1 or super_size > MAX_SUPER_SIZE:
        return f"screenshot_super_size must be between 1 and {MAX_SUPER_SIZE}."
    if max_resolution < 1 or max_resolution > MAX_RENDER_SIDE:
        return f"max_resolution must be between 1 and {MAX_RENDER_SIDE}."
    return None


def orbit_limits_error(azimuths: int, elevations: Sequence[float]) -> str | None:
    """Limit the Cartesian capture count; viewport pixel budgets stay in Unity."""
    if azimuths < 1 or azimuths > 36:
        return "orbit_angles must be between 1 and 36."
    if len(elevations) < 1 or len(elevations) > MAX_ORBIT_ELEVATIONS:
        return f"orbit_elevations must contain 1 to {MAX_ORBIT_ELEVATIONS} angles."
    if azimuths * len(elevations) > MAX_ORBIT_SHOTS:
        return f"Orbit capture exceeds the {MAX_ORBIT_SHOTS} shot budget."
    try:
        finite_angles = all(
            not isinstance(angle, bool) and math.isfinite(angle) for angle in elevations
        )
    except OverflowError:
        finite_angles = False
    if not finite_angles:
        return "orbit_elevations must contain finite numbers."
    return None
