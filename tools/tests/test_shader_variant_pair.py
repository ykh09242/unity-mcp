"""Check that a plausible JSON observation cannot cover up different rendered pixels."""

from pathlib import Path
import sys

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_shader_variant_pair import verify_frame


@pytest.mark.parametrize("mismatch", ["none", "json", "region", "background"])
def test_compare_independent_region_and_background(tmp_path, mismatch):
    blue = [0, 0, 255, 255]
    green = [0, 255, 0, 255]
    red = [255, 0, 0, 255]
    with Image.new("RGBA", (64, 64), tuple(blue)) as frame:
        frame.paste(tuple(green), (28, 28, 36, 36))
        if mismatch == "region":
            frame.putpixel((35, 35), tuple(red))
        if mismatch == "background":
            frame.putpixel((0, 63), tuple(red))
        frame.save(tmp_path / "frame.png")
    report = {"rgba": green * 64, "background": blue}
    if mismatch == "json":
        report["rgba"][:4] = red
    if mismatch == "none":
        verify_frame(tmp_path, report, green)
    else:
        with pytest.raises(ValueError):
            verify_frame(tmp_path, report, green)
