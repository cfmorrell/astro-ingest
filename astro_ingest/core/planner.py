"""The ingest plan: for every file on the capture source, what happens to it and where it goes.

Pure and deterministic: build_plan(scan, index, targets, answers) always gives the same Plan for the same
inputs, so Chris's answers to decisions are applied simply by planning again with them. Nothing is written here.

Vocabulary
- item: one source file (a frame with its thumbnail, an orphan thumbnail, or an unrecognized file)
- group: frames that travel together: a light group (night + object + camera + scope + filter), a flat set,
  a dark-flat set, or a library batch of darks/bias shot together
- session: a destination session folder on the NAS (new or already there)
- decision: something Chris must (or may) answer; each has a stable id
"""

from __future__ import annotations

import datetime as dt
import hashlib
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from astro_ingest.core import asiair, rules
from astro_ingest.core.fits import first, num
from astro_ingest.core.nas import NasIndex, Session
from astro_ingest.core.scan import Scan, SourceFrame
from astro_ingest.core.targets import Target, catalog_id, match_object
from astro_ingest.sources.base import SourceEntry

# Actions
COPY = "copy"                          # new frame, copied to dsts
APPEND = "append"                      # new frame for a session already on the NAS (§7.5); decision, default append
ALREADY = "already-ingested"           # same name and size already on the NAS
PENDING = "needs-decision"             # waits for Chris
SKIP = "skip"                          # Chris chose to leave it on the ASIAIR
NO_LIGHTS = "calib-without-lights"     # flats/dark flats whose lights can't be found; blocked from cleanup
OVER_CAP = "over-cap"                  # beyond the 10-frame calibration cap; offered for deletion, called out
NOT_KEPT = "not-kept"                  # not kept by the rules (2600 dark flats, test frames); called out
UNRECOGNIZED = "unrecognized"          # other tools' files, ._*, .DS_Store, unparsed .fit in Autorun/Plan
ORPHAN_THUMB = "orphan-thumb"          # _thn.jpg without its .fit
IGNORED = "ignored"                    # Live, Preview, … : never touched
REJECTED = "rejected"                  # light frame flagged as poor quality vs its group; not ingested unless kept
EXCLUDED = "excluded"                  # Chris opted out on the Select step: never read; offered for deletion

# Cleanup preview (what Phase 6 may do with the source file)
CLEANUP = {COPY: "after-verify", APPEND: "after-verify", ALREADY: "after-verify", PENDING: "pending", SKIP: "never",
           NO_LIGHTS: "blocked", OVER_CAP: "callout", NOT_KEPT: "callout", UNRECOGNIZED: "callout",
           ORPHAN_THUMB: "callout", IGNORED: "never", REJECTED: "callout", EXCLUDED: "callout"}

TINY_GROUP = 3                          # light groups this small are asked about (Chris, 2026-09-25)
BATCH_GAP = dt.timedelta(hours=1)       # a new calibration batch starts after a gap longer than this
ANGLE_TOLERANCE = 3                     # degrees, compared mod 180 (a meridian flip reports +180)
SUSPECT_ANGLE = 79                      # the ASIAIR's value when it hasn't plate-solved a rotation


@dataclass
class PlanItem:
    src: str
    size: int
    action: str
    thumb: str | None = None
    thumb_size: int = 0
    kind: str | None = None
    dsts: list[str] = field(default_factory=list)       # destination file paths, relative to the share root
    retire: list[str] = field(default_factory=list)     # NAS files to move to _to_delete/ first (bad copies)
    ingested_at: list[str] = field(default_factory=list)  # where it already is on the NAS
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    group: str | None = None
    decision: str | None = None
    quality: dict | None = None  # frame-quality scoring for a light: stats, anomaly_z, flagged, peers

    @property
    def cleanup(self) -> str:
        return CLEANUP[self.action]


@dataclass
class Group:
    id: str
    kind: str                  # lights | flats | darkflats | darks | bias
    night: dt.date | None
    camera: str | None         # camera token
    scope: str | None          # scope token ("" for a known scope without a token, e.g. the Seestar)
    exposure_s: float | None
    filter: str | None
    items: list[str] = field(default_factory=list)
    object: str | None = None
    angles: list[int] = field(default_factory=list)
    dst_folders: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _LightLink:
    """Where a light group went: a session, or a decision it is waiting on (flats follow their lights)."""
    group: Group
    session: str | None = None
    decision: str | None = None
    excluded: bool = False     # every frame still to ingest was excluded by Chris


@dataclass
class PlannedSession:
    target_folder: str
    folder: str
    exists: bool
    new_target: bool = False
    groups: list[str] = field(default_factory=list)
    lights: int = 0            # light frames this plan will copy there
    rejected: int = 0          # light frames flagged for quality and left out (unless Chris keeps them)
    replaced: int = 0          # of those, frames that replace a damaged copy already there
    flats: int = 0             # flat / dark-flat frames this plan will copy there
    bytes: int = 0
    siblings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return f"{self.target_folder}/{self.folder}"


@dataclass
class Decision:
    id: str
    kind: str                  # target | append | tiny-group | clock | scope | name-clash | library-folder | release
    question: str
    options: list[dict]        # [{"value": ..., "label": ...}]
    default: str | None
    items: list[str]
    group: str | None = None
    answer: str | None = None  # Chris's answer, if given

    @property
    def resolved(self) -> str | None:
        return self.answer if self.answer is not None else self.default


@dataclass
class Plan:
    source: str
    items: list[PlanItem]
    groups: list[Group]
    sessions: list[PlannedSession]
    decisions: list[Decision]

    def summary(self) -> dict:
        by_action = Counter(i.action for i in self.items)
        cleanup = defaultdict(lambda: {"files": 0, "bytes": 0})
        for i in self.items:
            c = cleanup[i.cleanup]
            c["files"] += 1 + (1 if i.thumb else 0)
            c["bytes"] += i.size + i.thumb_size
        return {
            "items": len(self.items),
            "actions": dict(sorted(by_action.items())),
            "copy_files": sum(len(i.dsts) for i in self.items if i.action in (COPY, APPEND)),
            "copy_bytes": sum(i.size * len(i.dsts) for i in self.items if i.action in (COPY, APPEND)),
            "sessions_new": sum(1 for s in self.sessions if not s.exists),
            "sessions_existing": sum(1 for s in self.sessions if s.exists),
            "decisions_open": sum(1 for d in self.decisions if d.resolved is None),
            "decisions_total": len(self.decisions),
            "cleanup": dict(sorted(cleanup.items())),
        }

    def to_dict(self) -> dict:
        def conv(o):
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, list):
                return [conv(v) for v in o]
            if isinstance(o, dt.date):
                return o.isoformat()
            return o
        items = []
        for i in self.items:
            d = asdict(i)
            d["cleanup"] = i.cleanup
            items.append(d)
        return conv({
            "source": self.source,
            "summary": self.summary(),
            "items": items,
            "groups": [asdict(g) for g in self.groups],
            "sessions": [{**asdict(s), "rel": s.rel} for s in self.sessions],
            "decisions": [{**asdict(d), "resolved": d.resolved} for d in self.decisions],
        })


def _stable_id(*parts: object) -> str:
    return hashlib.sha1("\x1f".join(map(str, parts)).encode()).hexdigest()[:10]


def _angle_close(a: int, b: int) -> bool:
    d = (a - b) % 180
    return min(d, 180 - d) <= ANGLE_TOLERANCE


class _Planner:
    def __init__(self, scan: Scan, index: NasIndex, targets: list[Target], answers: dict[str, str],
                 quality: dict[str, dict]):
        self.scan, self.index, self.targets = scan, index, targets
        self.answers = answers
        self.quality = quality
        self.items: dict[str, PlanItem] = {}
        self.frames: dict[str, SourceFrame] = {}
        self.groups: list[Group] = []
        self.sessions: dict[str, PlannedSession] = {}
        self.decisions: dict[str, Decision] = {}
        self.light_links: list[_LightLink] = []

    # ------------------------------------------------------------ helpers

    def item(self, frame: SourceFrame, action: str, reason: str = "") -> PlanItem:
        it = PlanItem(frame.rel, frame.entry.size, action, frame.thumb.rel if frame.thumb else None,
                      frame.thumb.size if frame.thumb else 0, frame.kind, reason=reason,
                      warnings=list(frame.warnings))
        self.items[frame.rel] = it
        self.frames[frame.rel] = frame
        return it

    def apply_exclusions(self, items: dict[str, PlanItem]) -> None:
        """Frames Chris opted out of on the Select step (answers "exclude:<src>" = "1") are never read."""
        for rel, it in items.items():
            if it.action in (COPY, PENDING) and self.answers.get(f"exclude:{rel}") == "1":
                it.action, it.dsts, it.retire = EXCLUDED, [], []   # a left-out replacement retires nothing either
                it.reason = "excluded by you on the Select step"

    def entry_item(self, e: SourceEntry, action: str, reason: str) -> PlanItem:
        it = PlanItem(e.rel, e.size, action, reason=reason)
        self.items[e.rel] = it
        return it

    def decide(self, kind: str, key: object, question: str, options: list[tuple[str, str]], default: str | None,
               items: list[str], group: str | None = None) -> Decision:
        did = f"{kind}-{_stable_id(kind, key)}"
        d = Decision(did, kind, question, [{"value": v, "label": lbl} for v, lbl in options], default, list(items),
                     group, self.answers.get(did))
        self.decisions[did] = d
        for rel in items:
            self.items[rel].decision = did
        return d

    def session(self, target_folder: str, folder: str, new_target: bool = False) -> PlannedSession:
        rel = f"{target_folder}/{folder}"
        if rel not in self.sessions:
            exists = self.index.session(target_folder, folder) is not None
            self.sessions[rel] = PlannedSession(target_folder, folder, exists, new_target and not exists)
        return self.sessions[rel]

    def ingested(self, frame: SourceFrame) -> tuple[list[str], list[str]]:
        """(locations with the same size, locations with a different size) of this filename on the NAS."""
        name = frame.rel.rsplit("/", 1)[-1]
        same = sorted(f.rel for f in self.index.find(name) if f.size == frame.entry.size)
        diff = sorted(f.rel for f in self.index.find(name) if f.size != frame.entry.size)
        return same, diff

    def check_ingested(self, frame: SourceFrame, it: PlanItem) -> None:
        """Mark an item already-ingested, or raise a name-clash decision when the NAS copy differs in size."""
        same, diff = self.ingested(frame)
        if same:
            it.action, it.ingested_at, it.reason = ALREADY, same, "already on the NAS"
            return
        if not diff:
            return
        it.ingested_at = diff
        truncated = any(x.size % 2880 for x in self.index.find(frame.rel.rsplit("/", 1)[-1])) and \
            frame.entry.size % 2880 == 0
        if truncated:
            it.warnings.append("the NAS copy looks truncated (not a whole number of FITS blocks)")
        d = self.decide("name-clash", frame.rel,
                        f"{frame.rel.rsplit('/', 1)[-1]} is already on the NAS at {diff[0]} but with a different size "
                        f"({self.index.find(frame.rel.rsplit('/', 1)[-1])[0].size:,} bytes there, {frame.entry.size:,} "
                        f"here){' — the NAS copy looks truncated' if truncated else ''}. Replace it?",
                        [("replace", "Retire the NAS copy to _to_delete/ and copy this one"),
                         ("skip", "Leave on the ASIAIR")],
                        # A damaged NAS copy with a good one here: use the better copy (Chris, 2026-09-25)
                        "replace" if truncated else None, [frame.rel], it.group)
        if d.resolved == "replace":
            it.action, it.dsts, it.retire = COPY, [diff[0]], list(diff)
            it.reason = "replaces a bad copy on the NAS (Chris)"
        elif d.resolved == "skip":
            it.action, it.reason = SKIP, "left on the ASIAIR (Chris)"
        else:
            it.action, it.reason = PENDING, "a different file with this name is already on the NAS"

    def camera(self, frame: SourceFrame) -> rules.Camera | None:
        return rules.camera_from(frame.name.camera if frame.name else None) or \
            rules.camera_from(first(frame.header or {}, "INSTRUME"))

    def scope(self, frames: list[SourceFrame]) -> tuple[rules.Scope | None, float | None]:
        fls = [num(first(f.header or {}, "FOCALLEN")) for f in frames]
        fls = [x for x in fls if x is not None]
        if not fls:
            return None, None
        fl = statistics.median(fls)
        return rules.scope_from_focal_length(fl), fl

    # ------------------------------------------------------------ plan

    def run(self) -> Plan:
        usable: list[SourceFrame] = []
        for f in self.scan.frames:
            if f.category != "handled":
                self.item(f, IGNORED, f"{f.rel.split('/')[0]}/ is not ingested")
            elif f.name is None:
                self.item(f, UNRECOGNIZED, "not an ASIAIR frame name")
            else:
                usable.append(f)
        for e in self.scan.orphan_thumbs:
            self.entry_item(e, ORPHAN_THUMB, "thumbnail whose .fit is gone")
        for e in self.scan.unrecognized:
            self.entry_item(e, UNRECOGNIZED, "not written by the ASIAIR's capture")

        usable = self.clock_decisions(usable)
        lights = [f for f in usable if f.kind == "Light"]
        flats = [f for f in usable if f.kind == "Flat"]
        darks = [f for f in usable if f.kind in ("Dark", "DarkFlat")]
        bias = [f for f in usable if f.kind == "Bias"]

        self.plan_lights(lights)
        darkflats, library_darks = self.split_dark_flats(darks, flats)
        self.plan_flats(flats, "flats")
        self.plan_flats(darkflats, "darkflats")
        self.plan_library(library_darks, "darks")
        self.plan_library(bias, "bias")
        self.link_siblings()

        items = sorted(self.items.values(), key=lambda i: i.src)
        sessions = sorted(self.sessions.values(), key=lambda s: s.rel)
        decisions = sorted(self.decisions.values(), key=lambda d: (d.kind, d.id))
        return Plan(self.scan.source_label, items, self.groups, sessions, decisions)

    # ------------------------------------------------------------ clock

    def clock_decisions(self, frames: list[SourceFrame]) -> list[SourceFrame]:
        """Frames whose filename time disagrees with DATE-OBS wait for Chris (one decision per folder)."""
        bad: dict[str, list[SourceFrame]] = defaultdict(list)
        ok = []
        for f in frames:
            (bad[f.folder] if any("ASIAIR clock" in w for w in f.warnings) else ok).append(f)
        for folder, fs in sorted(bad.items()):
            for f in fs:
                self.item(f, PENDING, "filename time disagrees with DATE-OBS")
            d = self.decide("clock", folder, f"{len(fs)} frame(s) in {folder} have a filename time that disagrees "
                            "with the FITS DATE-OBS (ASIAIR clock or time zone). Use the filename time anyway?",
                            [("use", "Use the filename time"), ("skip", "Leave on the ASIAIR")], None,
                            [f.rel for f in fs])
            if d.resolved == "use":
                for f in fs:
                    del self.items[f.rel]
                ok.extend(fs)
            elif d.resolved == "skip":
                for f in fs:
                    self.items[f.rel].action = SKIP
        return ok

    # ------------------------------------------------------------ lights

    def plan_lights(self, frames: list[SourceFrame]) -> None:
        groups: dict[tuple, list[SourceFrame]] = defaultdict(list)
        for f in frames:
            cam = self.camera(f)
            groups[(f.night, f.name.object or "", cam.token if cam else None, f.name.filter)].append(f)
        for (night, obj, cam_token, filt), fs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1:])):
            # a scope change within one night/object splits the group
            by_scope: dict[str | None, list[SourceFrame]] = defaultdict(list)
            for f in fs:
                s, _ = self.scope([f])
                by_scope[s.token if s else None].append(f)
            for scope_token, sfs in sorted(by_scope.items(), key=lambda kv: str(kv[0])):
                self.plan_light_group(night, obj, cam_token, filt, sorted(sfs, key=lambda f: f.rel))

    def plan_light_group(self, night, obj, cam_token, filt, fs: list[SourceFrame]) -> None:
        scope, fl = self.scope(fs)
        gid = f"lights-{_stable_id(night, obj, cam_token, scope.token if scope else fl, filt)}"
        g = Group(gid, "lights", night, cam_token, scope.token if scope else None, fs[0].name.exposure_s, filt,
                  [f.rel for f in fs], obj,
                  sorted({f.name.angle for f in fs if f.name.angle is not None}))
        self.groups.append(g)
        self.site_warnings(g, fs)
        link = _LightLink(g)
        self.light_links.append(link)

        status = {}
        for f in fs:
            it = self.item(f, COPY)
            it.group = gid
            it.quality = self.quality.get(f.rel)
            self.check_ingested(f, it)
            status[f.rel] = it
        self.apply_exclusions(status)
        if not any(it.action in (COPY, PENDING, ALREADY) for it in status.values()):
            link.excluded = True   # nothing of this group is being ingested: no session, no questions
            return

        # 1. ingest evidence: a session already holding some of these lights
        evidence = Counter()
        for it in status.values():
            for rel in it.ingested_at:
                parts = rel.split("/")
                if len(parts) >= 4 and parts[2].lower().startswith("lights"):
                    evidence[(parts[0], parts[1], parts[2])] += 1
        if evidence:
            (target_folder, folder, sub), _ = max(evidence.items(), key=lambda kv: (kv[1], kv[0]))
            session = self.session(target_folder, folder)
            self.place_lights(link, session, sub, status)
            return

        # 2. target from the object name; ask when unclear
        cam = rules.camera_from(cam_token)
        if cam is None:
            self.pending_group(link, status, "camera", f"Unknown camera for {len(fs)} {obj} lights on {night}.",
                               [("skip", "Leave on the ASIAIR")])
            return
        if scope is None:
            self.pending_group(
                link, status, "scope",
                f"{obj} on {night}: focal length {fl:g} mm doesn't match a known telescope. Which scope was it?"
                if fl is not None else f"{obj} on {night}: no focal length in the headers. Which scope was it?",
                [(s.token or "", s.name) for _, _, s in rules.FOCAL_LENGTH_SCOPES] + [("skip", "Leave on the ASIAIR")])
            return
        target, new_target = self.resolve_target(link, status, obj, night)
        if target is None:
            return
        live = [f for f in fs if status[f.rel].action == COPY]
        if len(live) <= TINY_GROUP:
            d = self.decide("tiny-group", gid, f"Only {len(live)} {obj} light(s) on {night}. File them as a session, "
                            "or treat them as test frames?",
                            [("file", "File as a session"), ("test", "Test frames: offer for deletion")], None,
                            [f.rel for f in fs if status[f.rel].action == COPY], gid)
            if d.resolved is None:
                link.decision = d.id
                for it in status.values():
                    if it.action == COPY:
                        it.action, it.reason = PENDING, "small group: file or test frames?"
                return
            if d.resolved == "test":
                for it in status.values():
                    if it.action == COPY:
                        it.action, it.reason = NOT_KEPT, "test frames (Chris)"
                return
        folder = rules.session_name(night, target.name, cam.token, scope.token)
        session = self.session(target.folder, folder, new_target)
        sub = f"lights-{filt}" if filt else "lights"
        self.place_lights(link, session, sub, status)

    def resolve_target(self, link: _LightLink, status: dict[str, PlanItem], obj: str, night
                       ) -> tuple[Target | None, bool]:
        g = link.group
        m = match_object(obj, self.targets)
        if m.target:
            return m.target, False
        options = [(t.folder, t.folder) for t in m.candidates]
        cid = catalog_id(obj)
        proposal = f"{cid[0]}{cid[1]}" if cid and cid[0] in ("M", "NGC", "IC") else None
        options.append((f"new:{proposal}" if proposal else "new:", "New target (you name it)"))
        options.append(("skip", "Leave on the ASIAIR"))
        question = (f"'{obj}' on {night} could be several targets: {', '.join(t.folder for t in m.candidates)}. Which?"
                    if m.candidates else
                    f"'{obj}' on {night} doesn't match any target in targets.csv. New target, or an existing one?")
        d = self.decide("target", (obj, g.camera, g.scope), question, options, None,
                        [r for r, it in status.items() if it.action == COPY], g.id)
        answer = d.resolved
        if answer is None or answer == "skip":
            if answer is None:
                link.decision = d.id
            for it in status.values():
                if it.action == COPY:
                    it.action = PENDING if answer is None else SKIP
                    it.reason = "target unclear" if answer is None else "left on the ASIAIR (Chris)"
            return None, False
        if answer.startswith("new:"):
            folder = answer[4:] or obj.replace(" ", "")
            # "NeedleGalaxy-NGC4565" -> name "NeedleGalaxy"; a bare "NGC4565" is its own name
            name = folder.split("-")[0]
            return Target(folder, name), True
        chosen = next((t for t in self.targets if t.folder == answer), None)
        return (chosen, False) if chosen else (None, False)

    def pending_group(self, link: _LightLink | Group, status, kind, question, options) -> None:
        g = link.group if isinstance(link, _LightLink) else link
        d = self.decide(kind, g.id, question, options, None, [r for r, it in status.items() if it.action == COPY], g.id)
        if isinstance(link, _LightLink) and d.resolved is None:
            link.decision = d.id
        for it in status.values():
            if it.action == COPY:
                it.action = SKIP if d.resolved == "skip" else PENDING
                it.reason = question

    def place_lights(self, link: _LightLink, session: PlannedSession, sub: str, status: dict[str, PlanItem]) -> None:
        g = link.group
        link.session = session.rel
        dst_folder = f"{session.rel}/{sub}"
        g.dst_folders = [dst_folder]
        session.groups.append(g.id)
        session.warnings.extend(w for w in g.warnings if w not in session.warnings)
        new = [r for r, it in status.items() if it.action == COPY and not it.retire]
        for rel in new:
            status[rel].dsts = [f"{dst_folder}/{rel.rsplit('/', 1)[-1]}"]
        if session.exists and new:
            d = self.decide("append", (session.rel, g.id),
                            f"{len(new)} {g.object} light(s) from {g.night} are not in {session.rel}, which is "
                            "already on the NAS (e.g. subs you once dropped). Append them?",
                            [("append", "Append to the session"), ("skip", "Leave on the ASIAIR")], "append",
                            new, g.id)
            for rel in new:
                it = status[rel]
                if d.resolved == "append":
                    it.action, it.reason = APPEND, f"missing from {session.rel}"
                else:
                    it.action, it.reason, it.dsts = SKIP, "left on the ASIAIR (Chris)", []
        else:
            for rel in new:
                status[rel].reason = f"new session {session.rel}" if not session.exists else ""
        self.apply_quality(status)
        session.rejected += sum(1 for it in status.values() if it.action == REJECTED)
        copied = [it for it in status.values() if it.action in (COPY, APPEND)]
        session.lights += len(copied)
        session.replaced += sum(1 for it in copied if it.retire)
        session.bytes += sum(it.size for it in copied)

    def apply_quality(self, status: dict[str, PlanItem]) -> None:
        """Flagged lights are not ingested unless Chris keeps them (answers "keep:<src>" = "keep"); he can also
        reject any frame by hand ("reject"). Frames without a score are unaffected."""
        for rel, it in status.items():
            if it.action not in (COPY, APPEND):
                continue
            q = self.quality.get(rel)
            choice = self.answers.get(f"keep:{rel}")
            if choice == "reject":
                it.action, it.dsts, it.reason = REJECTED, [], "rejected by Chris"
            elif q and q.get("flagged") and choice != "keep":
                it.action, it.dsts, it.reason = REJECTED, [], _quality_reason(q)

    def site_warnings(self, g: Group, fs: list[SourceFrame]) -> None:
        labels = Counter()
        for f in fs:
            h = f.header or {}
            lat, lon = first(h, "SITELAT"), first(h, "SITELONG")
            if lat is None or lon is None:
                continue
            site = rules.site_for(lat, lon)
            labels[site.label if site else f"unrecognized site ({lat}, {lon})"] += 1
        if len(labels) > 1:
            g.warnings.append("frames disagree on site: " + "; ".join(f"{k} ({v})" for k, v in labels.most_common()))
        elif labels and next(iter(labels)).startswith("unrecognized"):
            g.warnings.append(next(iter(labels)))

    # ------------------------------------------------------------ flats and dark flats

    def split_dark_flats(self, darks: list[SourceFrame], flats: list[SourceFrame]):
        """A Dark whose exposure matches a flat exposure on the same night and camera is a dark flat."""
        flat_exps = defaultdict(set)
        for f in flats:
            cam = self.camera(f)
            flat_exps[(f.night, cam.token if cam else None)].add(round(f.name.exposure_s, 3))
        darkflats, library = [], []
        for f in darks:
            cam = self.camera(f)
            if f.kind == "DarkFlat" or round(f.name.exposure_s, 3) in flat_exps[(f.night, cam.token if cam else None)]:
                darkflats.append(f)
            else:
                library.append(f)
        return darkflats, library

    def batches(self, frames: list[SourceFrame], key) -> list[list[SourceFrame]]:
        """Group by key, then split each group where frames are more than BATCH_GAP apart."""
        grouped = defaultdict(list)
        for f in frames:
            grouped[key(f)].append(f)
        out = []
        for k in sorted(grouped, key=str):
            fs = sorted(grouped[k], key=lambda f: (f.start_local, f.rel))
            batch = [fs[0]]
            for prev, cur in zip(fs, fs[1:]):
                if cur.start_local - prev.start_local > BATCH_GAP:
                    out.append(batch)
                    batch = []
                batch.append(cur)
            out.append(batch)
        return out

    def plan_flats(self, frames: list[SourceFrame], kind: str) -> None:
        def key(f):
            cam = self.camera(f)
            s, _ = self.scope([f])
            return (f.night, cam.token if cam else None, s.token if s else None, round(f.name.exposure_s, 3),
                    f.name.filter)
        for batch in self.batches(frames, key):
            self.plan_flat_set(batch, kind)

    def plan_flat_set(self, fs: list[SourceFrame], kind: str) -> None:
        f0 = fs[0]
        cam = self.camera(f0)
        scope, fl = self.scope(fs)
        angles = sorted({f.name.angle for f in fs if f.name.angle is not None})
        gid = f"{kind}-{_stable_id(f0.night, cam.token if cam else None, fl, f0.name.exposure_s, f0.name.filter, f0.rel)}"
        g = Group(gid, kind, f0.night, cam.token if cam else None, scope.token if scope else None,
                  f0.name.exposure_s, f0.name.filter, [f.rel for f in fs], angles=angles)
        self.groups.append(g)
        items: dict[str, PlanItem] = {}
        for f in fs:
            it = self.item(f, COPY)
            it.group = gid
            self.check_ingested(f, it)
            if it.action == ALREADY:
                it.action = COPY  # flats may still be needed in another session; decided below
            items[f.rel] = it
        self.apply_exclusions(items)

        if kind == "darkflats" and cam is not None and not cam.darkflats:
            for it in items.values():
                it.action, it.dsts = NOT_KEPT, []
                it.reason = f"dark flats aren't kept for the {cam.token} (its flats use the master bias)"
            return

        targets, waiting, lights_excluded = self.flat_targets(g)
        if not targets and not waiting and lights_excluded:
            for it in items.values():
                if it.action == COPY:
                    it.action, it.dsts = EXCLUDED, []
                    it.reason = "its lights were all excluded by you; include them here if you still want these"
            return
        if not targets and waiting:
            d = self.decisions[waiting[0]]
            for rel, it in items.items():
                if it.action == COPY:
                    it.action, it.decision = PENDING, d.id
                    it.reason = f"waits for the decision on its lights ({d.kind})"
                    d.items.append(rel)
            return
        if not targets:
            d = self.decide("release", gid,
                            f"No lights found on the ASIAIR or the NAS for {len(fs)} {kind} from {g.night} "
                            f"({g.exposure_s:g} s{', ' + str(angles[0]) + '°' if angles else ''}). Keep them on the "
                            "ASIAIR, or release them for deletion?",
                            [("keep", "Keep on the ASIAIR"), ("release", "Release for deletion")], "keep",
                            [f.rel for f in fs], gid)
            for it in items.values():
                if d.resolved == "release":
                    it.action, it.reason = NOT_KEPT, "no matching lights; released for deletion (Chris)"
                else:
                    it.action, it.reason = NO_LIGHTS, "no matching lights found on the ASIAIR or the NAS"
            return

        sub = kind + (f"-{g.filter}" if g.filter else "")
        g.dst_folders = [f"{s}/{sub}" for s in targets]
        keep = {f.rel for f in sorted(fs, key=lambda f: (f.start_local, f.rel))[:rules.CALIBRATION_CAP]}
        for rel, it in items.items():
            if it.action in (PENDING, SKIP, EXCLUDED) or it.retire:
                continue
            if rel not in keep:
                it.action, it.reason = OVER_CAP, f"beyond the {rules.CALIBRATION_CAP}-frame calibration cap"
                continue
            name = rel.rsplit("/", 1)[-1]
            missing = [s for s in targets if not any(a.startswith(f"{s}/") for a in it.ingested_at)]
            if not missing:
                it.action, it.reason = ALREADY, "already on the NAS"
                continue
            it.dsts = [f"{s}/{sub}/{name}" for s in missing]
            it.reason = f"{kind} for " + ", ".join(s.split("/")[1] for s in missing)
            for s in missing:
                session = self.session(*s.split("/", 1))
                session.flats += 1
                session.bytes += it.size
                if gid not in session.groups:
                    session.groups.append(gid)
                session.warnings.extend(w for w in g.warnings if w not in session.warnings)

    def flat_targets(self, g: Group) -> tuple[list[str], list[str], bool]:
        """Sessions (planned or on the NAS) these flats belong to: same night, camera, scope and filter.

        Also returns the decisions of matching light groups that have no session yet (e.g. an unknown target):
        the flats wait on those, and whether matching lights exist but were all excluded by Chris.
        """
        candidates: dict[str, list[int]] = {}
        waiting: list[str] = []
        lights_excluded = False
        for link in self.light_links:
            lg = link.group
            if not (lg.night == g.night and lg.camera == g.camera and lg.scope == g.scope and lg.filter == g.filter):
                continue
            if link.session:
                candidates.setdefault(link.session, []).extend(lg.angles)
            elif link.decision and link.decision not in waiting:
                waiting.append(link.decision)
            elif link.excluded:
                lights_excluded = True
        for s in self.index.sessions_on(g.night) if g.night else []:
            p = s.parsed
            if p and p.camera_token == g.camera and (p.scope_token or None) == (g.scope or None):
                candidates.setdefault(s.rel, []).extend(sorted(s.light_angles))
        if not candidates:
            return [], waiting, lights_excluded
        if g.angles and g.angles != [SUSPECT_ANGLE]:
            close = [s for s, angs in candidates.items() if any(_angle_close(a, b) for a in g.angles for b in angs)]
            if close:
                return sorted(close), waiting, lights_excluded
            g.warnings.append(f"flat angle {g.angles} doesn't match the lights; matched by night, camera and scope")
        elif g.angles == [SUSPECT_ANGLE] and any(angs and not any(_angle_close(SUSPECT_ANGLE, a) for a in angs)
                                                  for angs in candidates.values()):
            g.warnings.append("flats report 79° (the ASIAIR's unsolved default); matched by night, camera and scope")
        return sorted(candidates), waiting, lights_excluded

    # ------------------------------------------------------------ library darks and bias

    def plan_library(self, frames: list[SourceFrame], kind: str) -> None:
        def key(f):
            cam = self.camera(f)
            return (cam.token if cam else None, round(f.name.exposure_s, 3), f.name.gain,
                    first(f.header or {}, "OFFSET"))
        for batch in self.batches(frames, key):
            self.plan_batch(batch, kind)

    def plan_batch(self, fs: list[SourceFrame], kind: str) -> None:
        f0 = fs[0]
        cam = self.camera(f0)
        temps = [num(first(f.header or {}, "CCD-TEMP")) for f in fs]
        temps = [t if t is not None else f.name.temp_c for t, f in zip(temps, fs)]
        mean_temp = sum(temps) / len(temps)
        gid = f"{kind}-{_stable_id(cam.token if cam else None, f0.name.exposure_s, f0.rel)}"
        g = Group(gid, kind, f0.start_local.date(), cam.token if cam else None, None, f0.name.exposure_s, None,
                  [f.rel for f in fs])
        self.groups.append(g)
        items = {}
        for f in fs:
            it = self.item(f, COPY)
            it.group = gid
            self.check_ingested(f, it)
            items[f.rel] = it
        self.apply_exclusions(items)
        if cam is None or not cam.cooled:
            self.pending_group(g, items, "camera", f"{kind} batch from {g.night}: unknown camera, no library to file it in.",
                               [("skip", "Leave on the ASIAIR")])
            return
        folder = (rules.bias_dir(cam, g.night, mean_temp) if kind == "bias"
                  else rules.dark_dir(cam, f0.name.exposure_s, g.night, mean_temp))
        g.dst_folders = [folder]
        new = [r for r, it in items.items() if it.action == COPY and not it.retire]
        if not new:
            return
        others = [n for n, fl in self.index.files.items() for x in fl if x.rel.startswith(folder + "/")]
        if others:
            d = self.decide("library-folder", folder,
                            f"{folder} is already on the NAS with {len(others)} other frame(s). A second batch shot "
                            "the same day? It is never merged or overwritten.",
                            [("skip", "Leave on the ASIAIR")], None, new, gid)
            for r in new:
                items[r].action = SKIP if d.resolved == "skip" else PENDING
                items[r].reason = f"{folder} already holds other frames"
            return
        already = sum(1 for it in items.values() if it.action == ALREADY)
        room = max(rules.CALIBRATION_CAP - already, 0)
        for i, r in enumerate(sorted(new, key=lambda r: (self.frames[r].start_local, r))):
            it = items[r]
            if i < room:
                it.dsts = [f"{folder}/{r.rsplit('/', 1)[-1]}"]
                it.reason = f"new library batch {folder}"
            else:
                it.action, it.reason = OVER_CAP, f"beyond the {rules.CALIBRATION_CAP}-frame calibration cap"

    # ------------------------------------------------------------ siblings

    def link_siblings(self) -> None:
        by_setup = defaultdict(list)
        for s in self.sessions.values():
            p = rules.parse_session_name(s.folder)
            if p:
                by_setup[(s.target_folder, p.camera_token, p.scope_token)].append(s)
        for group in by_setup.values():
            for s in group:
                s.siblings = sorted(o.folder for o in group if o is not s)
        flat_sessions = {f.rsplit("/", 1)[0] for g in self.groups if g.kind in ("flats", "darkflats")
                         for f in g.dst_folders}
        for link in self.light_links:
            s = self.sessions.get(link.session) if link.session else None
            if s is None or (s.exists and s.lights == 0):   # nothing new going there: no need to nag
                continue
            if link.session not in flat_sessions and not self._nas_has_flats(link.session):
                msg = "no flats found for these lights"
                if msg not in s.warnings:
                    s.warnings.append(msg)

    def _nas_has_flats(self, srel: str) -> bool:
        t, f = srel.split("/", 1)
        s: Session | None = self.index.session(t, f)
        return bool(s and s.has_flats)


def _quality_reason(q: dict) -> str:
    stats = q.get("stats") or {}
    if stats.get("star_count") == 0:
        return f"poor quality: no stars detected (vs {q.get('peers', 0)} frames in its group)"
    metric, z = max((q.get("anomaly_z") or {"?": 0}).items(), key=lambda kv: kv[1])
    label = {"star_count": "star count", "fwhm": "FWHM", "roundness": "eccentricity", "snr": "SNR",
             "background": "sky background", "background_std": "background noise"}.get(metric, metric)
    return f"poor quality: {label} is {z:.1f}σ off the other {q.get('peers', 0) - 1} frames in its group"


def build_plan(scan: Scan, index: NasIndex, targets: list[Target], answers: dict[str, str] | None = None,
               quality: dict[str, dict] | None = None) -> Plan:
    """`quality`: {source rel: frame-quality scoring} from analysis.score_groups(); optional."""
    return _Planner(scan, index, targets, answers or {}, quality or {}).run()
