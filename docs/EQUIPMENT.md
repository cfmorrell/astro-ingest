# Astrophotography & Visual Equipment

Compiled by Claude on 2026-09-23 from (1) Chris's own equipment notes and (2) FITS/XISF headers and folder names across this volume.
- **Plain entries** come from Chris's notes.
- *(header)* entries were found in capture data.
- *(spec)* entries are standard manufacturer specs Claude filled in.
- *(calc)* entries are derived by Claude.
- ❓ marks an open question (see the end of this file).

---
## 1. Imaging cameras

| Camera | Sensor | Size | Resolution | Pixel | ADC | Sensor to face | In use *(header)* | Library folder |
|---|---|---|---|---|---|---|---|---|
| **ZWO ASI2600MM Pro** (mono) | IMX571, APS-C | 23.5 × 15.7 mm | 6248 × 4176 | 3.76 µm | 16-bit | 12.5 mm (17.5 mm incl. 5 mm tilt plate) | 2024-06 → 2025-10; gain 100 / offset 50 (ASIAIR), offset 16 (N.I.N.A.), -10 °C | `ASI2600MM Pro` |
| **ZWO ASI2600MC Duo** (OSC + built-in guide sensor; folder name "ASI2600MC Pro") | IMX571, APS-C | 23.5 × 15.7 mm | 6248 × 4176 | 3.76 µm | 16-bit | 12.5 mm | 2025-04 → present; gain 100 / offset 50, -10 °C, RGGB | `ASI2600MC Pro` |
|  ↳ Duo guide sensor | SC2210 | 7.68 × 4.32 mm | 1920 × 1080 | 4.0 µm *(calc)* | 12-bit | — | guides through the main optics | — |
| **ZWO ASI294MC Pro** (OSC) | IMX294, 4/3" *(spec)* | 19.1 × 13.0 mm *(spec)* | 4144 × 2822 | 4.63 µm | 14-bit *(spec)* | 6.5 mm body; 17.5 mm with the supplied 11 mm adapter *(spec)* | 2020-09 → 2025-10; gain 120 standard | `ASI294MC Pro` |
| **ZWO ASI183MM Pro** (mono), **borrowed from PANE** | IMX183, 1" *(spec)* | 13.2 × 8.8 mm *(spec)* | 5496 × 3672 | 2.4 µm | 12-bit *(spec)* | 6.5 / 17.5 mm *(spec)* | 2022-06 → 2023-11; gain 120 (N.I.N.A.), 111 (ASIAIR) | `ASI183MM Pro (PANE)` |
| **Canon EOS 450D** (EXIF: "EOS DIGITAL REBEL XSi") | APS-C CMOS | 22.2 × 14.8 mm *(spec)* | 4272 × 2848 | 5.2 µm | 14-bit | 44 mm EF flange *(spec)* | 2020-07 → 2020-09; ISO 400/1600; no electronic lens in EXIF | n/a (calibration stays in session) |
| **ZWO Seestar S50** | IMX462 *(spec)* | — | 1920 × 1080 | 2.9 µm | — | integrated, 250 mm f/5 | 2024-07 (Maine); gain 80, IRCUT / LP | n/a |

## 2. Guiding

| Item | Details |
|---|---|
| **William Optics UniGuide 32 mm** | 32 mm aperture, 120 mm FL, f/3.75 |
| **ZWO ASI174MM Mini** | IMX174, 1936 × 1216, 5.86 µm *(spec)*. On UniGuide: **10.1 "/px** *(calc)* |
| **ZWO ASI120MM Mini** | AR0130, 1280 × 960, 3.75 µm *(spec)*. On UniGuide: **6.4 "/px** *(calc)* |
| **ZWO OAG-L** (M68, large) | 17.5 mm. **In use: guides the 2600MM** (with the UniGuide as an alternative) |
| **ZWO OAG** (standard, M48) | 16.5 mm. Not in use; to be sold |
| ASI2600MC Duo built-in guide sensor | Reported in headers as **"ZWO ASI220MM Mini"** (GUIDECAM); SC2210, 1920 × 1080, 4.0 µm. Guides through the imaging scope: 1.44 "/px at 571 mm *(calc)* |

## 3. Telescopes

| Folder token | Telescope | Aperture | Native FL / ratio | As used (FL / ratio) | Back focus / thread | Image circle | Notes |
|---|---|---|---|---|---|---|---|
| `SV503` | **SVBony SV503 102 ED** | 102 mm | 714 mm, f/7 | **571 mm (measured in ASIAIR), ≈ f/5.6** with the **SVBony M54×1 field flattener/reducer** (≈ 0.8×) *(calc)* | **55 mm**, 48 mm male (with flattener); 101.9 mm native | ❓ | Tube OD 121 mm; 5.5 kg with rings; 2" slip. Focuser starts at **10639**. Headers show 568–570 mm. ⚠ This file's first version wrongly said "80 mm". |
| `Z61` (older folders: `WO61`) | **William Optics ZenithStar 61** | 61 mm | 360 mm, f/5.9 | **289 mm (f/4.7)** with the **Astro-Tech AT60ED 0.8× reducer/field flattener** (2020–2023, 2024-06-01; replaced by the Flat61A for its adjustability); **360–369 mm** with the **WO Adjustable Flat61 (FLAT61A)** (2024-06-13 →) *(header)* | Flat61A: **67.7 mm** back focus, **M48 × 0.75**, **12.9 mm adjustable** outward travel, 2-element *(spec, High Point)*. So a 55 mm camera train is accommodated by running the adjuster out about 12.7 mm *(calc)* | Scope 45 mm; Flat61A 43 mm | Most-used scope |
| `RC6` | **iOptron Photron RC6** | 150 mm | 1370 mm, f/9 | 1370 mm (2021–22); 1382–1388 mm (2025–26) *(header)*. **No flattener or reducer used** | native (RC back focus varies with mirror spacing) | — | The ~1% longer FL in 2025–26 is consistent with a slightly longer back-focus distance, since RC focal length grows as the camera sits farther out |
| `FMA135` | **Askar FMA135** (30 mm triplet APO, built-in 3-element flattener) | 30 mm | 135 mm, f/4.5 | 137 mm *(header)* | **55 mm**, **M42** interface; helical focuser *(spec, High Point)* | APS-C supported (circle not published) | 0.6 lb. Accepts 1.25" filters and eyepieces via the included adapters. Used 2025-10 with 2600MC and 294MC |
| (none) | **Celestron 6" Newtonian** (no longer owned). Probably the 450D's scope in mid-2020, until the Z61 arrived | 150 mm | 750 mm, f/5 (current model of that scope) | | | | EXIF focal length 0 = no electronic lens. The Z61 arrived around 2020-08/09 |
| — | **Apertura AD10** (visual Dobsonian) | 254 mm | **1250 mm, f/4.9** (confirmed) | | | | See §8 |

## 4. Mounts

| Mount | Period *(header)* | Notes |
|---|---|---|
| Celestron CGEM | 2020 → early 2021 | N.I.N.A. TELESCOP = "CGEM AND WO Z61" |
| **iOptron CEM60** | **by 2021-09** (Chris's M31 capture notes: "CEM60, Z61, L-Pro, EAF, 294MCPro, OAG") → 2023 (ASIAIR) | Header reads "iOptron CEM25/CEM60" (ASIAIR's generic driver name); Chris confirms CEM60 |
| ZWO AM5 | 2024-06 → present | On ASIAIR the TELESCOP header holds the mount name, not the telescope |

## 5. Image scale and field of view *(calc)*

Image scale = 206.265 × pixel (µm) / FL (mm). FOV = 57.296 × sensor dimension / FL.

| Camera \\ Optic | SV503 + flattener (571 mm) | Z61 native/flattener (360 mm) | Z61 + 0.8× (289 mm) | RC6 (1370 mm) | FMA135 (135 mm) |
|---|---|---|---|---|---|
| **ASI2600MM / MC** (3.76 µm, 23.5 × 15.7) | 1.36 "/px · 2.36° × 1.58° | 2.15 "/px · 3.74° × 2.50° | 2.68 "/px · 4.66° × 3.11° | 0.57 "/px · 0.98° × 0.66° | 5.74 "/px · 9.97° × 6.66° |
| **ASI294MC** (4.63 µm, 19.1 × 13.0) | 1.67 "/px · 1.92° × 1.30° | 2.65 "/px · 3.04° × 2.07° | 3.30 "/px · 3.79° × 2.58° | 0.70 "/px · 0.80° × 0.54° | 7.07 "/px · 8.11° × 5.52° |
| **ASI183MM** (2.4 µm, 13.2 × 8.8) | 0.87 "/px · 1.32° × 0.88° | 1.38 "/px · 2.10° × 1.40° | 1.71 "/px · 2.62° × 1.75° | 0.36 "/px · 0.55° × 0.37° | 3.67 "/px · 5.60° × 3.73° |

- Sensor diagonals: APS-C 2600 = 28.3 mm; 294MC = 23.1 mm; 183MM = 15.9 mm. All fit inside the Z61's 45 mm image circle.
- Guide-to-image ratio, e.g. SV503 + 2600 (1.36"/px) guided by 174MM on UniGuide (10.1"/px): about 7.4:1. That's within the usual ≤ 8–10:1 rule of thumb, so it's fine with sub-pixel centroiding.

## 6. Back focus: current imaging trains

Early "quick-reference" combos in Chris's notes predate the current equipment and are **retired** (they used a camera, OAG and M42 rotator no longer in play).

### Component thicknesses
| Component | Thickness | Threads / notes |
|---|---|---|
| ASI2600 camera (sensor to face) | 12.5 mm | |
| ZWO tilt plate | 5 mm | |
| ZWO 7 × 36 mm EFW | 20 mm | |
| ZWO OAG-L (M68) | 17.5 mm | in the 2600MM train |
| ZWO OAG (M48) | 16.5 mm | not in use; to be sold |
| ZWO M54 filter drawer (Gen2) | 20 mm | on the 2600MC |
| ZWO M48 filter drawer | 21 mm *(earlier note)* | on the 294MC |
| ZWO CAA (camera angle adjuster) | 16.5 mm | **M54 both sides** |
| M54 → M48 adapter | 2 mm | |
| Manual rotator | 13.5 mm | M42 both sides. **Not currently used** |

### Train 1: ASI2600MM Pro (mono), guided through the OAG-L
camera 12.5 + tilt plate 5 + EFW 20 + OAG-L 17.5 = **55.0 mm**, ends M48 female.
- ✔ Exactly matches the **SV503 flattener (55 mm)**.
- On the **Z61 + Flat61A** (67.7 mm, 12.9 mm adjustable) it's within the adjuster's range.
- Filters in the EFW add about 0.7 mm of optical path for ~2 mm thick glass. Refocusing absorbs that.

### Train 2: ASI2600MC Duo (OSC)
camera 12.5 + tilt plate 5 + M54 filter drawer 20 + CAA 16.5 + M54→M48 adapter 2 = **56.0 mm**, ends M48 female.
- ✔ About 1 mm long mechanically. A drawer filter adds ~0.7 mm of optical path, so the net is ≈ +0.3 mm, within tolerance.
- Both ASI2600 cameras ship with the 5 mm tilt plate.

### Design principle (Chris)
**Every camera kit is set to 55 mm ± 1 mm, ending in M48**, so any kit connects directly to any optic:
- SV503 flattener: 55 mm, M48
- Z61 + Flat61A: adjustable, M48
- FMA135: 55 mm; its M42 is brought out to M48 by a dedicated adapter
- RC6: native, focus by the focuser

### ASI294MC Pro (legacy OSC)
Sensor 6.5 mm behind the face; 17.5 mm with ZWO's supplied 11 mm adapter *(spec)*. Used the **M48 filter drawer** with L-eNhance / L-Pro trays. In 2022 it used the **EFW now on the 2600MM** for its Ha / OIII / SII frames.

## 7. Filters

### 36 mm, in the ZWO 7-position EFW (mono trains)
| Slot | Filter |
|---|---|
| L | Astronomik L-2 UV-IR block |
| R, G, B | Baader CMOS-optimized RGB |
| H, O, S | Baader 6.5 nm Hα, OIII, SII |
| (spare, not installed) | Baader UHC-S / L-Booster, 36 mm. Was the "LP" filter in 2022–23 headers |

### Filter drawer trays
| Filter | V/P | Where it lives |
|---|---|---|
| Optolong **L-eXtreme** (7 nm Hα/OIII dual-band) | P | **2600MC** M54 drawer tray |
| **Baader CMOS UV/IR-cut L** | V/P | **2600MC** M54 drawer tray (header code **`UVIR`** on the 294MC in 2023) |
| Optolong **L-eNhance** (tri-band) | P | **294MC** M48 drawer tray. Used 2020–21 (DSS stack names say "LEnhance") |
| Optolong **L-Pro** | V/P | **294MC** M48 drawer tray |
| Optolong **Clear** | — | header code **`Clr`** (294MC 2023) |

V/P = visual / photo (confirmed).

### Visual filters (in the visual kit)
- Optolong **Variable Polarizer** (V, Moon)
- Optolong **UHC Nebula** (V)

### Header filter codes seen *(header)*
- Mono: L, R, G, B, H, O, S.
- 294MC: `Clr` = Optolong Clear; `UVIR` = Baader UV/IR-cut; `LP` = the **Baader UHC-S / L-Booster (36 mm)** in the EFW, used as a light-pollution "luminance" filter (per Chris; 2022 M13, FishHead, Ghost; 2023 M51); `Ha` / `OIII` / `SII` (2022 FishHead, Ghost) = Baader 6.5 nm through the 36 mm EFW now on the 2600MM.
- Seestar: IRCUT, LP (built-in).

## 8. Visual equipment (on the AD10, FL ≈ 1250 mm)

| Eyepiece | Barrel | AFOV | Magnification | True FOV (notes) | True FOV = AFOV / mag *(calc)* | Case |
|---|---|---|---|---|---|---|
| Agena SWA 38 mm | 2" | 70° | 33× | 2.22° | 2.12° | 65 × 120 mm |
| Agena SWA 32 mm | 2" | 70° | 39× | 1.87° | 1.79° | 65 × 120 mm |
| Agena SWA 26 mm | 2" | 70° | 48× | 1.52° | 1.46° | 65 × 80 mm |
| Meade Super Plössl 32 mm | 1.25" | 52° | 39× | 1.39° | 1.33° (1.25" field stop limits it) | 42 × 80 mm |
| Celestron 8–24 mm zoom | 1.25" | 40–60° | 52×–156× | 0.8°–0.4° | 0.77°–0.38° | 52 × 80 mm |
| SVBony 12.5 mm reticle | 1.25" | 40° | 100× | 0.42° | 0.40° | — |

The noted TFOVs run about 4% high. The calc column (1250 mm, AFOV ÷ mag) is the corrected value.

| Barlow | Barrel | Case |
|---|---|---|
| GSO 1.5× / 2× (spacing dependent) | 2" | 85 × 120 mm |
| SVBony 2× | 1.25" | 52 × 80 mm |
| Astromania 3× | 1.25" | 52 × 80 mm |
| SVBony 5× | 1.25" | — |

**Other:** Apertura 1.25" laser collimator.

**Practical ceiling** *(calc)*: about 2× aperture in mm, so roughly **500×** on the AD10. Seeing usually caps it near 200–250×. For example, the 8 mm zoom with the 2× barlow gives 312×, which is a steady-seeing-only combination.

---
## 9. Capture software (by era) *(header)*
- Astro Photography Tool 3.84 (2020-09)
- SharpCap (2020-10)
- N.I.N.A. 1.10 → 2.0 (2020-10 → 2022); N.I.N.A. 3.1.2 (2025-10, RC6 + 2600MM)
- ZWO ASIAIR Plus (2023 → present)
- Seestar app (2024)

## 10. Processing software seen in folders
DeepSkyStacker (2020–21), PixInsight / WBPP / AutoIntegrate (2021–2024), Siril 1.2.3 → 1.4.4 (2024 → present), Photoshop (2020 median stacks).

## 11. Capture sites (SITELAT/SITELONG)
| Coordinates | Location |
|---|---|
| 41.43, -74.036 | Cornwall, NY (home) |
| 41.50, -74.017 | Cornwall area. Coarse coordinates from early N.I.N.A.; home |
| 41.390, -73.954 / 41.382, -73.975 | West Point, NY |
| 41.75, -73.917 | GPS error (Ghost 2022-09-09). This was home |
| 42.090, -73.720 | **MHAA Star Party**: Lake Taghkanic State Park parking lot |
| 44.3875, -68.0155 | Gouldsboro/Schoodic Peninsula area, Maine (2024-07 Seestar trip) |
| (no GPS) | Maine, August 2020 (450D sessions) |

## 12. Open questions ❓
- None outstanding.


## 13. Resolved 2026-09-23
- OAGs: M68 OAG-L 17.5 mm and M48 OAG 16.5 mm. Both rarely used or unused; to be sold.
- Drawers: M54 Gen2 on the 2600MC; M48 on the 294MC. CAA is M54 both sides.
- The M42 rotator isn't in use. The early quick-reference combos are retired.
- Z61 flattener = WO Adjustable Flat61 (FLAT61A). The RC6 is used native. The FMA135 has a built-in flattener, 55 mm, M42.
- "ASI220MM Mini" in the headers = the 2600MC Duo's built-in guide sensor.
- Mount = CEM60. AD10 = Apertura, 1250 mm (confirmed).
- 2600MC train includes the 5 mm tilt plate (56 mm). All camera kits are built to 55 ± 1 mm ending in M48; FMA135 has an M42→M48 adapter.
- OAG-L is in use on the 2600MM. The M48 OAG is to be sold.
- The Z61's earlier reducer was the Astro-Tech AT60ED 0.8×. The Celestron 6" Newtonian was 750 mm.
- The 36 mm wheel holds L (Astronomik L-2), R/G/B (Baader CMOS-optimized) and H/O/S (Baader 6.5 nm). The UHC-S / L-Booster 36 mm is a spare.
- "LP" header filter = Baader UHC-S / L-Booster 36 mm. SV503 flattener is the SVBony M54×1.
- Chris's 2021–22 capture notes (merged into PROJECT_INFO): the CEM60 and a ZWO EAF focuser were in use by Sept 2021; the WO UniGuide 32 mm was new in April 2022 (M82 notes); a homemade flat panel was in use by July 2022.
- SV503 + M54×1 flattener image circle: SVBony doesn't publish one; it's specified to cover APS-C, which is sufficient for both ASI2600 cameras.
