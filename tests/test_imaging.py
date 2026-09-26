"""Previews (ported from astro-stacker): every stretch mode renders, and the thumbnail fast path matches."""

import io

import numpy as np
import pytest
from fitsgen import star_field, write_fits
from PIL import Image

from astro_ingest.core import imaging


def mosaic(h=240, w=320):
    """An RGGB mosaic of a star field: R and B sites carry a colour cast so debayering has something to do."""
    sky = star_field(width=w, height=h, stars=60, seed=5)
    m = sky.copy()
    m[0::2, 0::2] *= 1.3   # R
    m[1::2, 1::2] *= 0.7   # B
    return np.clip(m, 0, 65535)


@pytest.mark.parametrize("stretch", ["none", "linked", "unlinked", "calibration", "noise"])
def test_every_stretch_renders(tmp_path, stretch):
    path = write_fits(tmp_path / "f.fit", {"BAYERPAT": "RGGB"}, data=mosaic())
    png = imaging.render_preview_png(open(path, "rb"), max_size=160, stretch=stretch, debayer=True)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and max(img.size) <= 160


def test_unknown_stretch_is_refused(tmp_path):
    path = write_fits(tmp_path / "f.fit", data=mosaic())
    with pytest.raises(ValueError):
        imaging.render_preview_png(path, stretch="bogus")


def test_superpixel_thumbnail_matches_full_debayer():
    data = mosaic(480, 640).astype(np.float32)
    header = {"BAYERPAT": "RGGB"}
    fast = np.asarray(Image.open(io.BytesIO(imaging.render_array(data, header, max_size=80, stretch="unlinked",
                                                                 debayer=True)))).astype(int)
    # the bilinear path, forced by asking for a size that doesn't trigger the fast path, then downsized the same way
    full = imaging._debayer_bilinear(data, "RGGB")
    full = imaging._block_average(full, 8)
    ref = np.stack([imaging._apply_stretch(full[..., c], "linked") for c in range(3)], axis=-1)
    ref = (ref * 255).astype(np.uint8).astype(int)
    assert fast.shape == ref.shape
    assert np.abs(fast - ref).mean() < 4.0


def test_stretch_for_kind():
    assert imaging.stretch_for_kind("Light") == "unlinked"
    assert imaging.stretch_for_kind("Flat") == "calibration"
    assert imaging.stretch_for_kind("Dark") == imaging.stretch_for_kind("Bias") == "noise"
