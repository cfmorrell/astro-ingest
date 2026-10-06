"""A throwaway world for the OWASP ZAP scan (.github/workflows/zap-scan.yml): generated, tiny, disposable.

    python dev/zap/make_fixture.py <dir>

Creates <dir>/asiair (a fake ASIAIR folder: Soul Nebula lights and flats with thumbnails, a Mac metadata file),
<dir>/astro (a fake Astronomy share: targets.csv only) and empty <dir>/{state,staging,cache}. The scan attacks every
endpoint, including the ones that write to the share and delete from the device, so it must only ever run against
data like this: never against the real share or a real ASIAIR.
"""

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from fitsgen import asiair_frame, star_field  # noqa: E402


def main(root: Path) -> None:
    air, astro = root / "asiair", root / "astro"
    for d in (air, astro / "Z95-ClaudeReferences", root / "state", root / "staging", root / "cache"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "docs" / "targets.csv", astro / "Z95-ClaudeReferences" / "targets.csv")
    for i, t in enumerate(["20260923-211420", "20260923-221420", "20260923-231420", "20260924-001420"]):
        asiair_frame(air, "Plan/Light/SoulNebula", "Light", t, obj="SoulNebula", angle=3, seq=i + 1,
                     data=star_field(seed=i))
    for i in range(2):
        asiair_frame(air, "Autorun/Flat", "Flat", f"20260924-06330{i}", exposure_s=6.1, angle=3, seq=i + 1,
                     data=star_field(seed=40 + i, stars=0))
    (air / "Plan" / ".DS_Store").write_bytes(b"\0" * 64)
    print(f"ZAP fixture in {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
