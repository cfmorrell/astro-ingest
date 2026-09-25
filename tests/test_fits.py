import io

import pytest
from fitsgen import ASIAIR_LIGHT, write_fits

from astro_ingest.core.fits import HeaderError, first, header, num, read_header


def test_reads_asiair_style_header(tmp_path):
    h = header(write_fits(tmp_path / "a.fit", {**ASIAIR_LIGHT, "OBJECT": "NGC 7000", "DATE-OBS": "2026-09-24T01:17:32.792625"}))
    assert h["OBJECT"] == "NGC 7000"
    assert h["BZERO"] == "32768"
    assert num(h["FOCALLEN"]) == 369
    assert h["DATE-OBS"] == "2026-09-24T01:17:32.792625"


def test_quote_escaping_and_first_wins(tmp_path):
    path = write_fits(tmp_path / "a.fit", {"OBJECT": "Bode's Galaxy"})
    assert header(path)["OBJECT"] == "Bode's Galaxy"
    assert header(path)["NAXIS"] == "2"  # mandatory card, not overridden


def test_truncated_file_raises():
    with pytest.raises(HeaderError):
        read_header(io.BytesIO(b"SIMPLE  =                    T" + b" " * 50), "x.fit")


def test_not_fits_stops_without_reading_everything():
    with pytest.raises(HeaderError):
        read_header(io.BytesIO(b" " * 2880 * 300), "x.fit")


def test_xisf_keywords():
    xml = ('XISF0100....<xisf><Image><FITSKeyword name="EXPTIME" value="300." comment=""/>'
           '<FITSKeyword name="OBJECT" value="\'M 31\'" comment=""/></Image></xisf>')
    h = read_header(io.BytesIO(xml.encode()), "light.xisf")
    assert h == {"EXPTIME": "300.", "OBJECT": "M 31"}


def test_helpers():
    assert num("300.") == 300.0
    assert num("abc") is None and num(None) is None
    assert first({"EXPOSURE": "120", "EXPTIME": ""}, "EXPTIME", "EXPOSURE") == "120"
    assert first({}, "EXPTIME") is None
