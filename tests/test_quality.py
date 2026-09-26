"""Frame-quality scoring (ported from astro-stacker) on synthetic star fields."""

import io

import numpy as np
from fitsgen import star_field, write_fits

from astro_ingest.core import quality

BIN = 2  # the synthetic frames are tiny; stacker's default 4x binning would leave too few pixels per star


def stats_for(tmp_path, name, data):
    path = write_fits(tmp_path / name, {"DATE-OBS": "2026-09-24T01:17:32"}, data=data)
    with open(path, "rb") as fh:  # through a stream, as frames arrive from a Source
        return quality.analyze_frame(io.BytesIO(fh.read()), name, bin_factor=BIN)


def test_good_frame_stats(tmp_path):
    s = stats_for(tmp_path, "good.fit", star_field(seed=1))
    assert s.star_count >= 20
    assert 2.0 < s.fwhm < 5.0
    assert s.snr and s.snr > 10
    assert abs(s.background - 500) < 50
    assert s.captured_at == "2026-09-24T01:17:32"


def test_bad_frames_are_flagged_against_their_group(tmp_path):
    frames = [stats_for(tmp_path, f"good{i}.fit", star_field(seed=i)) for i in range(8)]
    bloated = stats_for(tmp_path, "bloated.fit", star_field(seed=20, fwhm=7.0, peak=900))
    twilight = stats_for(tmp_path, "twilight.fit", star_field(seed=21, background=6000, noise=40, peak=600))
    empty = stats_for(tmp_path, "zero.fit", np.full((120, 160), 0.0))
    group = frames + [bloated, twilight, empty]
    quality.flag_anomalies(group, z_threshold=quality.INGEST_ANOMALY_Z_THRESHOLD)
    flagged = {s.filename for s in group if s.flagged}
    assert {"bloated.fit", "twilight.fit", "zero.fit"} <= flagged
    assert not any(name.startswith("good") for name in flagged)
    assert empty.star_count == 0
    # (synthetic "good" frames share an identical background, so that metric has zero spread and stacker's robust
    # z-score leaves it at 0; the twilight frame is still caught, by a wide margin, on the other metrics)
    assert max(twilight.anomaly_z.values()) > 10


def test_too_few_frames_are_never_flagged(tmp_path):
    frames = [stats_for(tmp_path, "a.fit", star_field(seed=1)),
              stats_for(tmp_path, "b.fit", star_field(seed=2, background=6000))]
    quality.flag_anomalies(frames)
    assert not any(f.flagged for f in frames)


def test_round_trip_through_dict(tmp_path):
    s = stats_for(tmp_path, "good.fit", star_field(seed=3))
    from dataclasses import asdict
    assert quality.FrameStats.from_dict(asdict(s)) == s
