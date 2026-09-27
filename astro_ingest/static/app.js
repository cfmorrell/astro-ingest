/* astro-ingest frontend — plain JS, no build step, no framework (same approach and helpers as astro-stacker).
 * Talks to the FastAPI backend in astro_ingest/api.py. All state is derived from the server's plan, so a
 * reload never gets out of sync with the source. Frame review (cards, lightbox, metric strips, flagged ±2
 * collapsing) follows astro-stacker's static/app.js at commit f31cbcb.
 */

const STEPS = ["connect", "scan", "stage", "review", "copy", "catalog", "clean"];
const STEP_LABELS = { connect: "Connect", scan: "Scan Images", stage: "Stage", review: "Review", copy: "Copy & verify", catalog: "Catalog", clean: "Clean up" };
const STEP_PHASE = {};  // steps not built yet: shown, disabled, tagged with their phase
const LARGE_GROUP_THRESHOLD = 20;  // beyond this, collapse to flagged frames +/- 2 neighbours (as astro-stacker)
const SMALL_GROUP_PEERS = 10;      // fewer frames than this to compare against: scoring is less reliable (M42 04-11)
const THUMB = 320;
const FULL = 1600;

const ACTION_LABELS = {
  "copy": "copy",
  "append": "append",
  "already-ingested": "already on NAS",
  "needs-decision": "needs decision",
  "skip": "left on ASIAIR",
  "calib-without-lights": "no lights found",
  "over-cap": "over 10-frame cap",
  "not-kept": "not kept",
  "rejected": "rejected (quality)",
  "excluded": "left out on Select",
  "unrecognized": "unrecognized",
  "orphan-thumb": "orphan thumbnail",
  "ignored": "ignored folder",
};
const CLEANUP_LABELS = {
  "after-verify": "Deleted from the ASIAIR once a checksum proves the NAS copy, when ticked on Clean up",
  "callout": "Offered for deletion, each one called out",
  "blocked": "Kept until they're filed or released for deletion",
  "pending": "Waiting on a decision",
  "never": "Never touched",
};

const state = {
  activeStep: "connect",  // the flow starts at the beginning and works left to right across the stepper
  health: null,
  devices: null,         // /api/devices: source mode, remembered device, last search
  collapsedDecisions: new Set(),  // decision ids collapsed (this page view)
  passed: new Set(),     // steps finished in this page view: only these get a green check (Chris, 2026-09-27)
  sigmaLive: null,       // σ while the slider is being dragged (flags previewed locally until it's released)
  copyPreview: null,     // /api/copy/preview: what approving now would copy
  lastBatch: null,       // the most recent copy batch
  catalogPreview: null,  // /api/catalog/preview: what the Catalog step would write
  catalogRunning: false,
  cleanPreview: null,    // /api/cleanup/preview: what Clean up may delete from the device, by group
  cleanUnticked: new Set(),  // files in default-ticked groups that Chris unticked
  cleanTicked: new Set(),    // callout files Chris ticked
  cleanRunning: false,
  plan: null,
  batches: [],           // recent copy batches (newest first)
  lastStage: null,       // the last stage job's result in this page view
  itemsBySrc: {},
  expandedGroups: {},    // strip id -> Set of "start-end" collapsed ranges the user expanded
  chartsOpen: new Set(), // light group ids whose quality charts are shown
};

// ---------- tiny fetch helpers (same shapes as astro-stacker) ----------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isJson && data && data.detail ? JSON.stringify(data.detail) : String(data);
    throw new Error(`${res.status}: ${detail}`);
  }
  return data;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const child of children || []) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function setStepBadge(badgeId, kind, text) {
  const badge = document.getElementById(badgeId);
  if (!badge) return;
  badge.textContent = text;
  badge.className = `badge${kind ? ` ${kind}` : ""}`;
}

function formatCaptured(iso) {
  // As astro-stacker: FITS DATE-OBS is UTC without a trailing "Z"; add it so the browser converts to local time.
  if (!iso) return { date: "—", time: "—" };
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (isNaN(d.getTime())) return { date: "—", time: "—" };
  const date = d.toLocaleDateString(undefined, { year: "2-digit", month: "2-digit", day: "2-digit" });
  const time = d.toLocaleTimeString(undefined, { hour12: false });
  return { date, time };
}

function gb(bytes) {
  if (!bytes) return "0 B";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

function basename(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function stat(num, label, kind) {
  return el("div", { class: `stat${kind ? ` ${kind}` : ""}` }, [
    el("div", { class: "num" }, [String(num)]),
    el("div", { class: "lbl" }, [label]),
  ]);
}

function toggleList(label, rows) {
  // A "show N files" link that expands a monospace list (collapsed by default: lists get long)
  const list = el("div", { class: "file-list", style: "display:none;" }, rows);
  const link = el("span", { class: "toggle-adv" }, [label]);
  link.addEventListener("click", () => {
    const open = list.style.display === "none";
    list.style.display = open ? "block" : "none";
    link.textContent = open ? label.replace(/^show\b/, "hide") : label;
  });
  return el("div", {}, [link, list]);
}

function actionLabel(item) {
  return item.retire && item.retire.length ? "replace damaged copy" : (ACTION_LABELS[item.action] || item.action);
}

function itemRow(item, withReason) {
  return el("div", { title: item.reason || "" }, [
    el("span", { class: "act" }, [actionLabel(item)]),
    item.src,
    withReason && item.reason ? el("span", { class: "reason" }, [`  (${item.reason})`]) : null,
  ]);
}

function frameLabel(src) {
  // "…_20260915-052048_185deg_-10.0C_0103.fit" -> "09-15 05:20 · #0103" (the unique part of an ASIAIR name)
  const m = basename(src).match(/_(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}_.*?_(\d{4})\.fit$/);
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]} · #${m[6]}` : basename(src);
}

function isFrame(item) {
  return ["Light", "Flat", "Dark", "Bias", "DarkFlat"].includes(item.kind) && item.action !== "unrecognized";
}

function previewUrl(item, size) {
  // Rendered from the FITS only when it's cheap: staged, or already on the NAS. Otherwise (left out on Select, not
  // staged yet) the device's own small thumbnail; a whole frame is never read over Wi-Fi just to show it.
  if (item.staged || state.plan.source.startsWith("local:")) return `/api/preview?rel=${encodeURIComponent(item.src)}&size=${size}`;
  if (item.ingested_at && item.ingested_at.length && !(item.retire && item.retire.length)) return `/api/preview?nas=${encodeURIComponent(item.ingested_at[0])}&size=${size}`;
  return item.thumb ? `/api/thumb?rel=${encodeURIComponent(item.thumb)}` : null;
}

// ---------- frame quality (flags come from the server at the chosen sensitivity) ----------

function isFlagged(item) {
  const q = item.quality;
  if (!q) return false;
  if (state.sigmaLive === null) return !!q.flagged;
  // while the σ slider moves: the server's rule (quality.flag_anomalies), applied locally
  return q.stats.star_count === 0 || Object.values(q.anomaly_z || {}).some((z) => z >= state.sigmaLive);
}

function currentSigma() {
  return state.sigmaLive !== null ? state.sigmaLive : (state.plan ? state.plan.sigma : 4);
}

function isKept(item) {
  // flagged, but you chose to ingest it anyway (frames already on the NAS were never a choice)
  return isFlagged(item) && (item.action === "copy" || item.action === "append");
}

function frameStatsLine(item) {
  const q = item.quality;
  if (!q) return "Not scored yet";
  const s = q.stats;
  const z = q.anomaly_z || {};
  const sigma = currentSigma();
  const part = (label, key, value) => `${label}: ${value}${(z[key] || 0) >= sigma ? ` ⚠ ${z[key].toFixed(1)}σ` : ""}`;
  return [
    part("Stars", "star_count", s.star_count),
    part("FWHM", "fwhm", s.fwhm !== null ? s.fwhm.toFixed(2) : "—"),
    part("Eccentricity", "roundness", s.roundness !== null ? s.roundness.toFixed(3) : "—"),
    part("SNR", "snr", s.snr !== null ? s.snr.toFixed(0) : "—"),
    part("Background", "background", Math.round(s.background)),
    part("Noise", "background_std", s.background_std !== null && s.background_std !== undefined ? s.background_std.toFixed(1) : "—"),
  ].join(", ") + `  ·  vs ${q.peers - 1} other frames`;
}

async function setFrameChoice(items, choiceFor) {
  // choiceFor(item) -> "keep" | "reject" | null (null = back to the recommendation)
  const updates = {};
  items.forEach((i) => { updates[`keep:${i.src}`] = choiceFor(i); });
  applyPlan(await api("POST", "/api/answers", updates));
}

function wantIngest(item, ingest) {
  // Toggle to ingest / not ingest, expressed relative to the recommendation so defaults stay defaults
  if (isFlagged(item)) return ingest ? "keep" : null;
  return ingest ? null : "reject";
}

// ---------- lightbox (astro-stacker's: zoom on click, arrows through the group, "don't ingest" row) ----------

let lightboxZoomed = false;
let lightboxNav = null;  // { srcs: [...], index }

function openLightbox(url, title, { stats, indicator } = {}) {
  const img = document.getElementById("lightbox-img");
  const scroll = document.getElementById("lightbox-scroll");
  img.src = url;
  lightboxZoomed = false;
  scroll.classList.remove("zoomed");
  scroll.scrollTop = 0;
  scroll.scrollLeft = 0;
  const captionEl = document.getElementById("lightbox-caption");
  captionEl.className = `lightbox-caption${indicator ? ` ${indicator}` : ""}`;
  captionEl.innerHTML = "";
  captionEl.appendChild(el("div", { class: "lightbox-caption-title" }, [title]));
  if (stats) captionEl.appendChild(el("div", { class: "lightbox-caption-stats" }, [stats]));
  document.getElementById("lightbox").classList.add("open");
}

function renderLightboxFrame() {
  if (!lightboxNav) return;
  const item = state.itemsBySrc[lightboxNav.srcs[lightboxNav.index]];
  if (!item) return;
  const q = item.quality;
  const { date, time } = formatCaptured(q && q.stats.captured_at);
  const isLight = item.kind === "Light";
  openLightbox(previewUrl(item, FULL) || "", `${basename(item.src)}${q ? ` — ${date} ${time}` : ""}`, {
    stats: isLight ? `${frameStatsLine(item)}${item.reason ? `  ·  ${item.reason}` : ""}` : `${actionLabel(item)}${item.reason ? ` · ${item.reason}` : ""}`,
    indicator: isLight && q ? (isFlagged(item) ? "flagged" : "ok") : null,
  });
  const canChoose = isLight && ["copy", "append", "rejected"].includes(item.action);
  document.getElementById("lightbox-exclude-row").style.display = canChoose ? "flex" : "none";
  document.getElementById("lightbox-exclude").checked = item.action === "rejected";
  document.getElementById("lightbox-prev").classList.toggle("hidden", lightboxNav.index <= 0);
  document.getElementById("lightbox-next").classList.toggle("hidden", lightboxNav.index >= lightboxNav.srcs.length - 1);
}

function openLightboxFor(items, index) {
  lightboxNav = { srcs: items.map((i) => i.src), index };
  renderLightboxFrame();
}

function closeLightbox() {
  document.getElementById("lightbox").classList.remove("open");
  lightboxNav = null;
}

document.getElementById("lightbox").addEventListener("click", closeLightbox);
document.getElementById("lightbox-prev").addEventListener("click", (e) => {
  e.stopPropagation();
  if (lightboxNav && lightboxNav.index > 0) { lightboxNav.index -= 1; renderLightboxFrame(); }
});
document.getElementById("lightbox-next").addEventListener("click", (e) => {
  e.stopPropagation();
  if (lightboxNav && lightboxNav.index < lightboxNav.srcs.length - 1) { lightboxNav.index += 1; renderLightboxFrame(); }
});
document.addEventListener("keydown", (e) => {
  if (!document.getElementById("lightbox").classList.contains("open")) return;
  if (e.key === "ArrowLeft") document.getElementById("lightbox-prev").click();
  else if (e.key === "ArrowRight") document.getElementById("lightbox-next").click();
  else if (e.key === "Escape") closeLightbox();
});
document.getElementById("lightbox-exclude-row").addEventListener("click", (e) => e.stopPropagation());
document.getElementById("lightbox-exclude").addEventListener("change", async (e) => {
  if (!lightboxNav) return;
  const item = state.itemsBySrc[lightboxNav.srcs[lightboxNav.index]];
  await setFrameChoice([item], (i) => wantIngest(i, !e.target.checked));
  renderLightboxFrame();
});
document.getElementById("lightbox-scroll").addEventListener("click", (e) => {
  // As astro-stacker: toggle zoom, centred on where you clicked, instead of closing
  e.stopPropagation();
  const scroll = e.currentTarget;
  if (!lightboxZoomed) {
    const rect = scroll.getBoundingClientRect();
    const fracX = (e.clientX - rect.left) / rect.width;
    const fracY = (e.clientY - rect.top) / rect.height;
    lightboxZoomed = true;
    scroll.classList.add("zoomed");
    requestAnimationFrame(() => {
      scroll.scrollLeft = fracX * scroll.scrollWidth - rect.width / 2;
      scroll.scrollTop = fracY * scroll.scrollHeight - rect.height / 2;
    });
  } else {
    lightboxZoomed = false;
    scroll.classList.remove("zoomed");
  }
});

// ---------- frame cards and strips ----------

function frameCard(item, items, index) {
  // Border (astro-stacker's meaning): red = flagged, recommended not to ingest; green = scored and fine;
  // grey = not scored (calibration frames, or before scoring); amber = flagged but you chose to keep it.
  const scored = item.kind === "Light" && item.quality;
  const cls = !scored ? "unscored" : isKept(item) ? "kept" : isFlagged(item) ? "flagged" : "";
  const card = el("div", { class: `frame-card ${cls}` }, []);
  const url = isFrame(item) ? previewUrl(item, THUMB) : null;
  if (url) {
    const img = el("img", { src: url, alt: basename(item.src), onclick: () => openLightboxFor(items.filter(isFrame), items.filter(isFrame).indexOf(item)) }, []);
    // a preview that can't be rendered (e.g. a truncated NAS copy) falls back to the device's own thumbnail
    img.addEventListener("error", () => { if (item.thumb && !img.src.includes("/api/thumb")) img.src = `/api/thumb?rel=${encodeURIComponent(item.thumb)}`; });
    card.appendChild(img);
  }
  const meta = el("div", { class: "frame-meta" }, [
    el("div", { class: "frame-time", title: item.src }, [isFrame(item) ? frameLabel(item.src) : basename(item.src)]),
    el("div", { class: "frame-name", title: item.reason || item.src }, [actionLabel(item)]),
  ]);
  if (scored && isFlagged(item) && ["copy", "append", "rejected"].includes(item.action)) {
    const kept = item.action !== "rejected";
    meta.appendChild(el("div", { class: "frame-actions" }, [
      el("span", { class: `badge ${kept ? "warn" : "danger"}` }, [kept ? "kept" : "flagged"]),
      el("button", {
        class: "small ghost",
        onclick: (e) => { e.stopPropagation(); setFrameChoice([item], () => (kept ? null : "keep")); },
      }, [kept ? "reject" : "keep anyway"]),
    ]));
  }
  card.appendChild(meta);
  return card;
}

function computeVisibleItems(stripId, items) {
  // astro-stacker's: large groups show flagged frames +/- 2 neighbours, the rest collapse into "⋯ N more".
  // astro-ingest also always shows frames that will actually move (copy/append) or need attention.
  if (items.length <= LARGE_GROUP_THRESHOLD) return items.map((it, i) => ({ type: "frame", item: it, index: i }));
  const expanded = state.expandedGroups[stripId] || new Set();
  const show = new Set();
  const notable = items.some((it) => isFlagged(it) || it.action === "append" || it.action === "rejected");
  items.forEach((it, i) => {
    if (isFlagged(it) || it.action === "append" || it.action === "rejected" || it.action === "needs-decision" && !notable) {
      for (let d = -2; d <= 2; d++) if (i + d >= 0 && i + d < items.length) show.add(i + d);
    }
  });
  if (!show.size) for (let i = 0; i < Math.min(8, items.length); i++) show.add(i);
  const out = [];
  let i = 0;
  while (i < items.length) {
    if (show.has(i)) { out.push({ type: "frame", item: items[i], index: i }); i++; continue; }
    let j = i;
    while (j < items.length && !show.has(j)) j++;
    const key = `${i}-${j}`;
    if (expanded.has(key)) for (let k = i; k < j; k++) out.push({ type: "frame", item: items[k], index: k });
    else out.push({ type: "ellipsis", count: j - i, key });
    i = j;
  }
  return out;
}

function frameStrip(stripId, items) {
  if (!items.length) return null;
  const strip = el("div", { class: "frame-strip" }, []);
  computeVisibleItems(stripId, items).forEach((v) => {
    if (v.type === "frame") strip.appendChild(frameCard(v.item, items, v.index));
    else {
      strip.appendChild(el("div", {
        class: "frame-ellipsis",
        onclick: () => {
          (state.expandedGroups[stripId] = state.expandedGroups[stripId] || new Set()).add(v.key);
          renderReview();
        },
      }, [`⋯ ${v.count} more`]));
    }
  });
  return strip;
}

function metricStrip(label, items, key) {
  // astro-stacker's metric strip: bars scaled to the metric's own range, flagged bars red
  const scored = items.filter((i) => i.quality);
  if (!scored.length) return el("div", { class: "metric-strip" }, [el("div", { class: "metric-label" }, [label]), el("div", { class: "hint" }, ["not scored"])]);
  const values = scored.map((i) => (typeof i.quality.stats[key] === "number" ? i.quality.stats[key] : 0));
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const sigma = currentSigma();
  const bars = el("div", { class: "metric-bars" }, scored.map((it, n) => {
    const h = Math.max(3, ((values[n] - lo) / span) * 68 + 4);
    const flagged = (it.quality.anomaly_z[key] || 0) >= sigma;
    const { time } = formatCaptured(it.quality.stats.captured_at);
    return el("div", { class: `bar${flagged ? " flagged" : ""}`, style: `height:${h}px;`, title: `${basename(it.src)} (${time}): ${key}=${values[n]}` }, []);
  }));
  const first = formatCaptured(scored[0].quality.stats.captured_at).time;
  const last = formatCaptured(scored[scored.length - 1].quality.stats.captured_at).time;
  return el("div", { class: "metric-strip" }, [
    el("div", { class: "metric-label" }, [label]),
    el("div", { class: "metric-strip-body" }, [
      el("div", { class: "metric-axis" }, [el("span", {}, [hi.toFixed(2)]), el("span", {}, [lo.toFixed(2)])]),
      el("div", { class: "metric-bars-wrap" }, [bars, el("div", { class: "metric-xaxis" }, [el("span", {}, [first]), el("span", {}, [last])])]),
    ]),
  ]);
}

function qualityToolbar(group, items) {
  const scored = items.filter((i) => i.quality);
  if (!scored.length) return el("div", { class: "hint", style: "margin-top:6px;" }, ["Light frames not scored yet: use “Score light frames” above."]);
  const flagged = items.filter(isFlagged).filter((i) => ["copy", "append", "rejected"].includes(i.action));
  const kept = flagged.filter(isKept);
  const peers = scored[0].quality.peers;
  const bits = [el("span", { class: "hint", style: "margin:0;" }, [`${scored.length} scored against ${peers} frames · ${flagged.length} flagged${kept.length ? ` · ${kept.length} kept anyway` : ""}`])];
  if (peers < SMALL_GROUP_PEERS) {
    bits.push(el("span", { class: "badge warn", title: "Frames are judged against the rest of their group; with this few to compare against, poor frames can slip through and good ones can be flagged. Check them by eye." }, [`small group: only ${peers} frames to compare, scoring is less reliable`]));
  }
  if (kept.length) bits.push(el("button", { class: "small", onclick: () => setFrameChoice(kept, () => null) }, ["✓ Accept recommendations"]));
  if (flagged.length && kept.length < flagged.length) bits.push(el("button", { class: "small ghost", onclick: () => setFrameChoice(flagged, () => "keep") }, ["Keep all flagged"]));
  const open = state.chartsOpen.has(group.id);
  bits.push(el("span", { class: "toggle-adv", style: "margin:0;", onclick: () => { open ? state.chartsOpen.delete(group.id) : state.chartsOpen.add(group.id); renderReview(); } }, [open ? "hide quality charts" : "quality charts"]));
  const wrap = el("div", {}, [el("div", { class: "quality-toolbar" }, bits)]);
  if (open) {
    wrap.appendChild(el("div", { class: "metric-grid" }, [
      metricStrip("star count", items, "star_count"),
      metricStrip("FWHM", items, "fwhm"),
      metricStrip("eccentricity", items, "roundness"),
      metricStrip("SNR", items, "snr"),
      metricStrip("sky background", items, "background"),
      metricStrip("background noise", items, "background_std"),
    ]));
  }
  return wrap;
}

// ---------- stepper ----------

function catalogAvailable() {
  // Catalog follows a copy: reachable once a copy batch is waiting to be catalogued, or was catalogued in this view
  return state.passed.has("catalog") || state.batches.some((b) => b.status === "done" && !b.catalogued_at);
}

function stepStatus(step) {
  // A step gets a green check only once it has been finished in this page view (Next pressed, or its job done),
  // and what it did still holds: a reload starts with no checks except a connected device.
  if (STEP_PHASE[step]) return { available: false, complete: false };
  const planned = !!state.plan;
  const passed = state.passed.has(step);
  switch (step) {
    case "connect": return { available: true, complete: !!(state.health && state.health.source_online) };
    case "scan": return { available: true, complete: passed && planned };
    case "stage": return { available: planned, complete: passed && planned && selectedItems().every((i) => i.staged) };
    case "review": return { available: planned, complete: passed && planned && state.plan.summary.decisions_open === 0 };
    case "copy": return { available: planned, complete: passed && !!(state.copyPreview && state.copyPreview.copies === 0) };
    case "catalog": {
      const c = state.catalogPreview;
      return { available: catalogAvailable(), complete: passed && !!(c && !c.summary.batches.length && !c.summary.writes) };
    }
    case "clean": return { available: planned, complete: passed && !cleanSelected().length };
    default: return { available: false, complete: false };
  }
}

function goTo(step, from) {
  if (from) state.passed.add(from);
  state.activeStep = step;
  showActiveStep();
}

function renderStepper() {
  const stepper = document.getElementById("stepper");
  stepper.innerHTML = "";
  STEPS.forEach((step, i) => {
    const { available, complete } = stepStatus(step);
    const classes = ["step"];
    if (step === state.activeStep) classes.push("active");
    if (complete) classes.push("complete");
    if (!available) classes.push("disabled");
    stepper.appendChild(el("div", {
      class: classes.join(" "),
      title: STEP_PHASE[step] ? `arrives in phase ${STEP_PHASE[step]}` : null,
      onclick: available ? () => { state.activeStep = step; showActiveStep(); } : null,
    }, [
      el("div", { class: "dot" }, [complete ? "" : String(i + 1)]),
      el("span", {}, [STEP_LABELS[step]]),
      STEP_PHASE[step] ? el("span", { class: "phase-tag" }, [`(phase ${STEP_PHASE[step]})`]) : null,
    ]));
    if (i < STEPS.length - 1) stepper.appendChild(el("div", { class: "connector" }, []));
  });
}

function showActiveStep() {
  document.querySelectorAll(".step-panel").forEach((panel) => {
    panel.classList.toggle("visible", panel.dataset.step === state.activeStep);
  });
  renderStepper();
  if (state.activeStep === "catalog" && !state.catalogRunning) loadCatalog();
  if (state.activeStep === "clean" && !state.cleanRunning) loadCleanStep();
}

// ---------- connect / scan ----------

function describeDevice(d) {
  if (!d) return "none";
  const where = `${d.label} at ${d.host}`;
  return d.nickname ? `${d.nickname} (${where})` : where;
}

function renderConnect() {
  const h = state.health;
  const body = document.getElementById("connect-body");
  body.innerHTML = "";
  if (!h) return;
  setStepBadge("connect-status-badge", h.source_online ? "ok" : "danger", h.source_online ? "online" : "offline");
  const dv = state.devices;
  if (dv && dv.source_mode === "local") {
    body.appendChild(el("div", {}, ["Reading from a local folder (ASIAIR_ROOT): ", el("span", { class: "session-path" }, [dv.local_root])]));
    body.appendChild(el("div", { class: "hint" }, [`Your capture device: ${describeDevice(dv.remembered)}. It is used whenever ASIAIR_ROOT is not set.`]));
  } else {
    body.appendChild(el("div", {}, ["Reading from ", el("b", {}, [dv ? describeDevice(dv.remembered) : "…"]), " over the network: ", el("span", { class: "session-path" }, [h.source])]));
  }
}

function renderFindResult(res) {
  const box = document.getElementById("find-result");
  box.innerHTML = "";
  if (!res) return;
  const status = res.choice ? res.choice.status : null;
  box.appendChild(el("div", { class: status === "ask" ? "session-mismatch-warning" : "hint", style: "margin-bottom:8px;" }, [
    res.choice ? res.choice.message : "",
    res.seconds !== undefined ? `  (searched ${res.scanned} address${res.scanned === 1 ? "" : "es"} on ${res.subnet} in ${res.seconds}s)` : "",
  ]));
  Object.entries(res.errors || {}).forEach(([host, err]) => box.appendChild(el("div", { class: "hint" }, [`${host}: couldn't identify (${err})`])));
  res.found.forEach((d) => {
    const nameInput = el("input", { type: "text", placeholder: "your name for it, e.g. ASIAIR Color", value: d.remembered && res.remembered ? (res.remembered.nickname || "") : "" }, []);
    box.appendChild(el("div", { class: "night-block" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [
        el("span", { class: `badge ${d.remembered ? "ok" : "accent"}` }, [d.remembered ? "currently selected" : d.label]),
        el("span", { class: "session-path" }, [d.host]),
        el("span", { class: "hint", style: "margin:0;" }, [d.name ? `network name ${d.name}` : ""]),
      ]),
      el("div", { class: "session-meta" }, [`${d.label} · share “${d.share}” · folders: ${d.folders.filter((f) => !f.startsWith(".")).join(", ")}`]),
      el("div", { style: "display:flex; gap:6px; align-items:center;" }, [
        nameInput,
        el("button", {
          class: d.remembered ? "ghost small" : "small",
          onclick: async () => {
            state.devices = await api("POST", "/api/devices/select", { host: d.host, nickname: nameInput.value });
            await loadHealth();
            renderFindResult(Object.assign({}, res, state.devices, { choice: { status: "remembered", message: `Using ${describeDevice(state.devices.remembered)}.` }, found: state.devices.found }));
            await loadPlan(false);
          },
        }, [d.remembered ? "Save name" : "Use this device"]),
      ]),
    ]));
  });
}

async function findDevices(full) {
  const btns = [document.getElementById("find-btn"), document.getElementById("find-all-btn")];
  btns.forEach((b) => { b.disabled = true; });
  document.getElementById("find-result").innerHTML = "";
  document.getElementById("find-result").appendChild(el("div", { class: "hint" }, ["Searching…"]));
  try {
    const res = await api("POST", "/api/devices/find", { full });
    state.devices = res;
    renderFindResult(res);
    await loadHealth();
  } catch (e) {
    document.getElementById("find-result").innerHTML = "";
    document.getElementById("find-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
  btns.forEach((b) => { b.disabled = false; });
}

function renderScan() {
  const p = state.plan;
  const body = document.getElementById("scan-body");
  body.innerHTML = "";
  if (!p) return;
  document.getElementById("scan-status-badge").style.display = "none";
  const when = new Date(p.scanned_at);
  document.getElementById("scanned-at").textContent = isNaN(when.getTime()) ? "" : `Scanned ${when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;
  const byTop = {};
  p.items.forEach((i) => {
    const top = i.src.includes("/") ? i.src.slice(0, i.src.indexOf("/")) : "(share root)";
    byTop[top] = byTop[top] || { files: 0, bytes: 0 };
    byTop[top].files += 1 + (i.thumb ? 1 : 0);
    byTop[top].bytes += i.size + (i.thumb_size || 0);
  });
  const row = el("div", { class: "stat-row" }, []);
  Object.keys(byTop).sort().forEach((top) => row.appendChild(stat(byTop[top].files, `${top} · ${gb(byTop[top].bytes)}`)));
  body.appendChild(row);
}

// ---------- scan, part 2: choose which frames to read from the device ----------

function captureStamp(src) {
  // "…_20260915-052048_…" -> "20260915-052048" (sorts chronologically; ASIAIR names carry local time)
  const m = basename(src).match(/_(\d{8}-\d{6})_/);
  return m ? m[1] : basename(src);
}

function stampLabel(stamp) {
  const m = stamp.match(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/);
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : stamp;
}

function isSelectable(item) {
  return (state.plan.stageable_actions || []).includes(item.action) || item.action === "excluded";
}

function selectedItems() {
  return state.plan ? state.plan.items.filter((i) => (state.plan.stageable_actions || []).includes(i.action)) : [];
}

function roughTime(bytes) {
  const minutes = bytes / 1e6 / state.plan.rate_mb_s / 60;
  if (bytes === 0) return "nothing to read";
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return `about ${Math.max(1, Math.round(minutes / 5) * 5 || Math.round(minutes))} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round((minutes - h * 60) / 5) * 5;
  return `about ${h} h${m ? ` ${m} min` : ""}`;
}

function setsForSelect(p) {
  const sets = [];
  p.groups.forEach((g) => {
    const items = g.items.map((src) => state.itemsBySrc[src]).filter((i) => i && isSelectable(i));
    if (!items.length) return;
    items.sort((a, b) => captureStamp(a.src).localeCompare(captureStamp(b.src)));
    const where = g.dst_folders.length ? g.dst_folders[0].replace(/\/(lights|flats|darkflats)(-[^/]+)?$/, "") : null;
    const what = g.kind === "lights" ? `${g.object || "lights"} lights` : g.kind;
    sets.push({ id: g.id, night: g.night || "", title: where || `${what} · night of ${g.night} (target to be decided)`, kind: g.kind, what, items });
  });
  return sets.sort((a, b) => (a.night + a.title).localeCompare(b.night + b.title));
}

const pendingExclusions = {};
let exclusionTimer = null;

function toggleExclusions(items, exclude) {
  // Optimistic: flip locally at once (the estimate updates immediately), send a batch to the server shortly after
  items.forEach((i) => {
    i.action = exclude ? "excluded" : "copy";
    pendingExclusions[`exclude:${i.src}`] = exclude ? "1" : null;
  });
  renderSelect();
  clearTimeout(exclusionTimer);
  exclusionTimer = setTimeout(async () => {
    const updates = Object.assign({}, pendingExclusions);
    Object.keys(pendingExclusions).forEach((k) => delete pendingExclusions[k]);
    applyPlan(await api("POST", "/api/answers", updates));
  }, 600);
}

function renderSelect() {
  const p = state.plan;
  if (!p) return;
  const selected = selectedItems();
  const toRead = selected.filter((i) => !i.staged);
  const bytes = toRead.reduce((a, i) => a + i.size, 0);
  const excluded = p.items.filter((i) => i.action === "excluded");
  const summary = document.getElementById("select-summary");
  summary.innerHTML = "";
  summary.appendChild(el("div", { class: "stat-row" }, [
    stat(selected.length, "frames selected"),
    stat(gb(selected.reduce((a, i) => a + i.size, 0)), "selected"),
    stat(roughTime(bytes), `to read over Wi-Fi at ~${p.rate_mb_s} MB/s (${p.rate_kind === "measured" ? "measured" : "typical Wi-Fi"})`, "ok"),
    stat(excluded.length, "left out", excluded.length ? "warn" : ""),
  ]));
  if (selected.length && toRead.length < selected.length) {
    summary.appendChild(el("div", { class: "hint" }, [`${selected.length - toRead.length} of these are already staged and won't be read again.`]));
  }

  const box = document.getElementById("select-sets");
  box.innerHTML = "";
  setsForSelect(p).forEach((set) => {
    const inSet = set.items.filter((i) => i.action !== "excluded");
    const setBytes = inSet.reduce((a, i) => a + i.size, 0);
    const allOut = inSet.length === 0;
    const grid = el("div", { class: "thumb-grid" }, set.items.map((item) => {
      const tile = el("div", {
        class: `thumb-tile${item.action === "excluded" ? " excluded" : ""}`,
        title: `${basename(item.src)} — ${stampLabel(captureStamp(item.src))}${item.action === "excluded" ? " (left out: click to include)" : " (click to leave out)"}`,
        onclick: () => toggleExclusions([item], item.action !== "excluded"),
      }, item.thumb ? [el("img", { src: `/api/thumb?rel=${encodeURIComponent(item.thumb)}`, alt: "", loading: "lazy" }, [])] : []);
      return tile;
    }));
    box.appendChild(el("div", { class: "card" }, [
      el("div", { class: "set-header" }, [
        el("span", { class: `badge ${set.kind === "lights" ? "accent" : ""}` }, [set.what]),
        el("span", { class: "session-path" }, [set.title]),
        el("span", { class: "hint", style: "margin:0;" }, [`${inSet.length} of ${set.items.length} selected · ${gb(setBytes)}`]),
        el("button", {
          class: "small ghost", style: "margin-left:auto;",
          onclick: () => toggleExclusions(set.items, !allOut),
        }, [allOut ? "Include set" : "Leave out set"]),
      ]),
      grid,
    ]));
  });
  if (!box.children.length) box.appendChild(el("div", { class: "card empty-hint" }, ["Nothing on the device needs reading: everything is already on the NAS."]));
}

// ---------- stage ----------

function clock(seconds) {
  // "12 min", "1 h 05 min", "under a minute"
  if (seconds === null || seconds === undefined) return "…";
  const m = Math.round(seconds / 60);
  if (m < 1) return "under a minute";
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

function renderStage(live) {
  // `live`: the running stage job's stats (files_done/total, bytes_done/total, mb_s, eta_s), if one is running
  const p = state.plan;
  if (!p) return;
  const selected = selectedItems();
  const toRead = selected.filter((i) => !i.staged);
  const bytes = toRead.reduce((a, i) => a + i.size, 0);
  const allBytes = selected.reduce((a, i) => a + i.size, 0);
  const box = document.getElementById("stage-summary");
  box.innerHTML = "";
  let remaining, frames, data, speed;
  if (live && live.files_total !== undefined) {
    remaining = [live.eta_s !== null ? clock(live.eta_s) : "estimating…", "time remaining", "ok"];
    frames = [`${live.files_done} / ${live.files_total}`, "frames staged"];
    data = [`${gb(live.bytes_done)} / ${gb(live.bytes_total)}`, "data staged"];
    speed = [live.mb_s ? `${live.mb_s} MB/s` : "…", "speed"];
  } else if (!selected.length) {
    remaining = ["—", "nothing selected: choose frames on the Scan step"];
    frames = ["0", "frames"]; data = ["0 B", "data"]; speed = ["—", "speed"];
  } else if (!toRead.length) {
    const took = state.lastStage ? state.lastStage.seconds : (p.rate_seconds || null);
    remaining = ["done", took ? `everything staged in ${clock(took)}` : "everything staged", "ok"];
    frames = [`${selected.length} / ${selected.length}`, "frames staged"];
    data = [gb(allBytes), "data staged"]; speed = [p.rate_kind === "measured" ? `${p.rate_mb_s} MB/s` : "—", "last measured speed"];
  } else {
    remaining = [roughTime(bytes).replace("about ", "~"), `estimated at ~${p.rate_mb_s} MB/s (${p.rate_kind === "measured" ? "measured" : "typical Wi-Fi"})`, "ok"];
    frames = [`${selected.length - toRead.length} / ${selected.length}`, "frames staged"];
    data = [`${gb(bytes)}`, "to stage"]; speed = [p.rate_kind === "measured" ? `${p.rate_mb_s} MB/s` : "—", "last measured speed"];
  }
  box.appendChild(el("div", { class: "stat-row" }, [remaining, frames, data, speed].map(([n, l, k]) => stat(n, l, k))));

  // the progress bar is always shown: idle, running, or complete
  const prog = document.getElementById("stage-progress");
  if (!live) {
    const pct = selected.length ? Math.round(((allBytes - bytes) / (allBytes || 1)) * 100) : 0;
    prog.querySelector(".progress-fill").style.width = `${pct}%`;
    prog.querySelector(".pct").textContent = `${pct}%`;
    prog.querySelector(".msg").textContent = !selected.length ? "" : toRead.length ? "ready to stage" : "all selected frames are staged";
  }
  const btn = document.getElementById("stage-run-btn");
  btn.textContent = live ? "Staging…" : toRead.length ? `Stage ${toRead.length} frame${toRead.length === 1 ? "" : "s"}` : "Everything selected is staged";
  if (!state.stageRunning) btn.disabled = toRead.length === 0;
  setStepBadge("stage-status-badge", live ? "accent" : toRead.length ? "" : "ok", live ? "staging…" : toRead.length ? "not staged" : (selected.length ? "staged" : "nothing selected"));
  document.getElementById("stage-next-btn").style.display = !live && selected.length && !toRead.length ? "inline-block" : "none";
}

async function watchStageJob(jobId) {
  const btn = document.getElementById("stage-run-btn");
  state.stageRunning = true;
  btn.disabled = true;
  await pollJob(jobId, document.getElementById("stage-progress"), async (snap) => {
    state.stageRunning = false;
    const res = document.getElementById("stage-result");
    res.innerHTML = "";
    if (snap.status === "succeeded") {
      state.lastStage = snap.result;   // the done box says how long it took
      state.passed.add("stage");
    } else {
      res.appendChild(el("div", { class: "error-banner" }, ["✕ ", snap.error || "staging failed"]));
    }
    await loadPlan(false);
  }, (snap) => renderStage(snap.stats && snap.stats.phase !== "scoring" ? snap.stats : null));
}

async function runStage() {
  try {
    const { job_id } = await api("POST", "/api/stage/run");
    await watchStageJob(job_id);
  } catch (e) {
    document.getElementById("stage-result").innerHTML = "";
    document.getElementById("stage-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
}

// ---------- copy & verify ----------

function renderCopy(live) {
  const pv = state.copyPreview;
  if (!pv) return;
  const box = document.getElementById("copy-summary");
  box.innerHTML = "";
  let boxes;
  if (live && live.files_total !== undefined) {
    boxes = [[live.eta_s !== null ? clock(live.eta_s) : "estimating…", "time remaining", "ok"],
      [`${live.files_done} / ${live.files_total}`, "files copied"],
      [`${gb(live.bytes_done)} / ${gb(live.bytes_total)}`, "data copied"],
      [live.mb_s ? `${live.mb_s} MB/s` : "…", "speed"]];
  } else if (pv.unfinished_batch) {
    boxes = [["resume", `batch ${pv.unfinished_batch} didn't finish`, "warn"], ["—", "files"], ["—", "data"], ["—", "speed"]];
  } else {
    const last = state.lastBatch && state.lastBatch.summary && state.lastBatch.summary.result;
    boxes = [[pv.copies ? `~${clock(pv.bytes / 1e6 / 45)}` : "done", pv.copies ? "estimated at disk speed (~45 MB/s)" : "nothing left to copy", "ok"],
      [String(pv.copies), "files to copy"], [gb(pv.bytes), "to copy"],
      [last && last.seconds ? `${(last.bytes / 1e6 / last.seconds).toFixed(1)} MB/s` : "—", "last copy speed"]];
  }
  box.appendChild(el("div", { class: "stat-row" }, boxes.map(([n, l, k]) => stat(n, l, k))));
  const prog = document.getElementById("copy-progress");
  if (!live) {
    const done = !pv.copies && state.lastBatch && state.lastBatch.status === "done";
    prog.querySelector(".progress-fill").style.width = done ? "100%" : "0%";
    prog.querySelector(".pct").textContent = done ? "100%" : "0%";
    prog.querySelector(".msg").textContent = pv.copies ? "waiting for your approval" : (done ? "last batch copied and verified" : "");
  }
  const btn = document.getElementById("copy-run-btn");
  btn.textContent = live ? "Copying…" : pv.unfinished_batch ? "Resume copy" : pv.copies ? `Approve & copy ${pv.copies} file${pv.copies === 1 ? "" : "s"}` : "Nothing to copy";
  if (!state.copyRunning) btn.disabled = !pv.copies && !pv.unfinished_batch;
  const next = document.getElementById("copy-next-btn");
  next.disabled = !catalogAvailable();
  next.title = next.disabled ? "Copy something first: Catalog works on what was copied" : "";
  setStepBadge("copy-status-badge", live ? "accent" : pv.copies ? "" : "ok", live ? "copying…" : pv.unfinished_batch ? "interrupted" : pv.copies ? "awaiting approval" : "up to date");

  const dests = document.getElementById("copy-dests");
  dests.innerHTML = "";
  setStepBadge("copy-dests-badge", "", `${pv.destinations.length} destination${pv.destinations.length === 1 ? "" : "s"}`);
  if (!pv.destinations.length) dests.appendChild(el("div", { class: "empty-hint" }, ["Nothing is ready to copy."]));
  pv.destinations.forEach((d) => dests.appendChild(el("div", { class: "night-block", style: "display:flex; gap:10px; align-items:center;" }, [
    el("span", { class: "badge accent" }, [`${d.files} file${d.files === 1 ? "" : "s"}`]),
    el("span", { class: "session-path" }, [d.folder]),
    el("span", { class: "hint", style: "margin:0 0 0 auto;" }, [gb(d.bytes)]),
  ])));
  if (pv.retires) dests.appendChild(el("div", { class: "session-mismatch-warning" }, [`⚠ ${pv.retires} damaged NAS cop${pv.retires === 1 ? "y is" : "ies are"} moved to _to_delete/ first`]));

  const ni = document.getElementById("copy-notincluded");
  ni.innerHTML = "";
  document.getElementById("copy-notincluded-card").style.display = pv.not_included.length ? "block" : "none";
  pv.not_included.forEach((r) => ni.appendChild(el("div", { class: "night-block" }, [
    el("div", {}, [el("span", { class: "badge warn" }, [`${r.count}`]), " ", r.reason]),
    toggleList(`show ${r.count} file${r.count === 1 ? "" : "s"}`, r.items.map((src) => el("div", {}, [src]))),
  ])));
}

function logLink(path) {
  // STATE_DIR/logs/<name> is served at /api/logs/<name>; job logs live one folder down
  const name = basename(path);
  return el("a", { href: `/api/logs/${path.includes("/logs/jobs/") ? "jobs/" : ""}${encodeURIComponent(name)}`, target: "_blank", rel: "noopener" }, [name]);
}

function renderCopyResult(res) {
  const box = document.getElementById("copy-result");
  box.innerHTML = "";
  if (!res) return;
  box.appendChild(el("div", { class: "hint" }, [
    `Batch ${res.batch}: ${res.copied} copied and verified, ${res.already_there} already there, ${res.clashes.length} clash${res.clashes.length === 1 ? "" : "es"}, ${res.failed.length} failed, in ${clock(res.seconds)}; ${gb(res.staging_cleared_bytes)} cleared from staging. Log: `, logLink(res.log),
  ]));
  res.count_problems.forEach((c) => box.appendChild(el("div", { class: "error-banner" }, [`✕ file count mismatch: ${c}`])));
  const list = (label, rows, cls) => rows.length && box.appendChild(el("div", { class: cls }, [`${label}:`, ...rows.map((r) => el("div", { class: "session-path" }, [`${r.dst} — ${r.detail}`]))]));
  list("Failed", res.failed, "error-banner");
  list("Not overwritten (a different file is already there)", res.clashes, "session-mismatch-warning");
  list("Damaged NAS copies", res.retired, "hint");
}

async function watchCopyJob(jobId) {
  const btn = document.getElementById("copy-run-btn");
  state.copyRunning = true;
  btn.disabled = true;
  await pollJob(jobId, document.getElementById("copy-progress"), async (snap) => {
    state.copyRunning = false;
    if (snap.status === "succeeded") { renderCopyResult(snap.result); state.passed.add("copy"); }
    else {
      document.getElementById("copy-result").innerHTML = "";
      document.getElementById("copy-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", snap.error || "copy failed"]));
    }
    await loadPlan(false);
  }, (snap) => renderCopy(snap.stats));
}

async function runCopy() {
  const pv = state.copyPreview;
  const what = pv.unfinished_batch ? `Resume the interrupted batch ${pv.unfinished_batch}?`
    : `Copy ${pv.copies} file${pv.copies === 1 ? "" : "s"} (${gb(pv.bytes)}) onto the NAS into ${pv.destinations.length} folder${pv.destinations.length === 1 ? "" : "s"}${pv.retires ? `, retiring ${pv.retires} damaged cop${pv.retires === 1 ? "y" : "ies"} to _to_delete/ first` : ""}? Nothing is overwritten, and nothing is deleted from the ASIAIR.`;
  if (!confirm(what)) return;
  try {
    const { job_id } = await api("POST", "/api/copy/run");
    await watchCopyJob(job_id);
  } catch (e) {
    document.getElementById("copy-result").innerHTML = "";
    document.getElementById("copy-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
}

// ---------- catalog ----------

const CHANGE_GROUPS = [
  ["project-info", "PROJECT_INFO.txt"], ["targets-csv", "targets.csv: new targets"],
  ["notes", "Sibling nights (.project_notes.txt)"], ["flats-note", "Borrowed flats (.flats_are_copies)"],
  ["links", "Index links (ByMessierNumber, ByNGCNumber, ByICNumber, ByDate)"], ["index-md", "ZZ_TARGET_INDEX.md"],
];

function diffBlock(text) {
  return el("pre", { class: "session-path", style: "white-space:pre-wrap; margin:6px 0; font-size:12px;" }, [text]);
}

async function loadCatalog() {
  setStepBadge("catalog-status-badge", "", "reading the NAS…");
  try {
    state.catalogPreview = await api("GET", "/api/catalog/preview");
  } catch (e) {
    setStepBadge("catalog-status-badge", "danger", "error");
    const box = document.getElementById("catalog-summary");
    box.innerHTML = "";
    box.appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
    return;
  }
  renderCatalog();
  renderStepper();
}

function renderCatalog() {
  const pv = state.catalogPreview;
  if (!pv) return;
  const s = pv.summary;
  const pi = s.project_info;
  const box = document.getElementById("catalog-summary");
  box.innerHTML = "";
  box.appendChild(el("div", { class: "stat-row" }, [
    stat(String(s.batches.length), `copy batch${s.batches.length === 1 ? "" : "es"} not catalogued yet`, s.batches.length ? "ok" : ""),
    stat(String(pi.create + pi.update), `PROJECT_INFO to write · ${pi.unchanged} unchanged`),
    stat(String(s.new_targets.length), "new targets"),
    stat(`+${s.links_added} / −${s.links_removed}`, "index links"),
  ]));
  const btn = document.getElementById("catalog-run-btn");
  if (!state.catalogRunning) {
    btn.disabled = !s.writes && !s.batches.length;
    btn.textContent = s.writes ? `Write ${s.writes} catalog update${s.writes === 1 ? "" : "s"}` : s.batches.length ? "Mark as catalogued" : "Nothing to write";
  }
  setStepBadge("catalog-status-badge", s.writes ? "" : "ok", s.writes ? "awaiting approval" : "up to date");
  if (!state.catalogRunning) {
    const prog = document.getElementById("catalog-progress");
    prog.querySelector(".progress-fill").style.width = s.writes ? "0%" : "100%";
    prog.querySelector(".pct").textContent = s.writes ? "0%" : "100%";
    prog.querySelector(".msg").textContent = s.writes ? "waiting for your approval" : "catalog is up to date";
  }

  const list = document.getElementById("catalog-changes");
  list.innerHTML = "";
  const todo = pv.changes.filter((c) => c.status !== "unchanged");
  setStepBadge("catalog-changes-badge", "", `${todo.length} change${todo.length === 1 ? "" : "s"}`);
  if (!todo.length) list.appendChild(el("div", { class: "empty-hint" }, ["Nothing to write: the catalog matches what's on the NAS."]));
  CHANGE_GROUPS.forEach(([kind, label]) => {
    const rows = todo.filter((c) => (kind === "links" ? c.kind.startsWith("link-") : c.kind === kind));
    if (!rows.length) return;
    const block = el("div", { class: "night-block" }, [el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
      el("span", { class: "badge accent" }, [String(rows.length)]), el("strong", {}, [label])])]);
    if (kind === "links") {
      block.appendChild(toggleList(`show ${rows.length} link${rows.length === 1 ? "" : "s"}`, rows.map((c) =>
        el("div", { class: "session-path" }, [c.kind === "link-add" ? `+ ${c.path} → ${c.target}` : `− ${c.path}  (${c.why})`]))));
    } else {
      rows.forEach((c) => block.appendChild(el("div", {}, [
        el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [
          el("span", { class: `badge ${c.status === "create" ? "ok" : ""}` }, [c.status]),
          el("span", { class: "session-path" }, [c.path]),
          c.why ? el("span", { class: "hint", style: "margin:0;" }, [c.why]) : null,
        ]),
        c.diff ? toggleList(c.status === "create" ? "show contents" : "show changes", [diffBlock(c.diff)]) : null,
      ])));
    }
    list.appendChild(block);
  });

  const gaps = document.getElementById("catalog-gaps");
  gaps.innerHTML = "";
  document.getElementById("catalog-gaps-card").style.display = pv.gaps.length ? "block" : "none";
  pv.gaps.forEach((g) => gaps.appendChild(el("div", { class: "session-mismatch-warning" }, [
    `⚠ ${g.kind} ${g.camera || "?"}${g.kind === "Dark" ? ` ${g.exposure}s` : ""} gain ${g.gain} offset ${g.offset}: ${g.problem} — ${g.session}`])));
  const log = document.getElementById("catalog-log");
  log.innerHTML = "";
  document.getElementById("catalog-log-card").style.display = pv.log_lines.length ? "block" : "none";
  if (pv.log_lines.length) log.appendChild(diffBlock(pv.log_lines.join("\n")));
}

function renderCatalogResult(res) {
  const box = document.getElementById("catalog-result");
  box.innerHTML = "";
  if (!res) return;
  const parts = Object.entries(res.by_kind).map(([k, n]) => `${n} ${k}`).join(", ");
  box.appendChild(el("div", { class: "hint" }, [
    `Wrote ${res.writes} update${res.writes === 1 ? "" : "s"}${parts ? ` (${parts})` : ""}; ${res.batches.length} batch${res.batches.length === 1 ? "" : "es"} marked catalogued${res.decision_log_drafts ? `; ${res.decision_log_drafts} decision-log draft line${res.decision_log_drafts === 1 ? "" : "s"} saved` : ""}. Log: `, logLink(res.log),
  ]));
  const skipped = Object.entries(res.by_kind).filter(([k]) => / (clash|skipped)$/.test(k));
  if (skipped.length) box.appendChild(el("div", { class: "session-mismatch-warning" }, [`⚠ not written (something already there): ${skipped.map(([k, n]) => `${n} ${k}`).join(", ")}`]));
}

async function watchCatalogJob(jobId) {
  const btn = document.getElementById("catalog-run-btn");
  state.catalogRunning = true;
  btn.disabled = true;
  btn.textContent = "Writing…";
  await pollJob(jobId, document.getElementById("catalog-progress"), async (snap) => {
    state.catalogRunning = false;
    if (snap.status === "succeeded") { renderCatalogResult(snap.result); state.passed.add("catalog"); }
    else {
      document.getElementById("catalog-result").innerHTML = "";
      document.getElementById("catalog-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", snap.error || "catalog failed"]));
    }
    await loadCatalog();
  });
}

async function runCatalog() {
  const s = state.catalogPreview.summary;
  const what = s.writes ? `Write ${s.writes} catalog update${s.writes === 1 ? "" : "s"} on the NAS? Nothing is deleted: a replaced targets.csv goes to _to_delete/.`
    : "Nothing to write. Mark the copy batches as catalogued?";
  if (!confirm(what)) return;
  try {
    const { job_id } = await api("POST", "/api/catalog/run");
    await watchCatalogJob(job_id);
  } catch (e) {
    document.getElementById("catalog-result").innerHTML = "";
    document.getElementById("catalog-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
}

// ---------- clean up ----------

function cleanSelected() {
  const c = state.cleanPreview;
  if (!c) return [];
  const out = [];
  c.groups.filter((g) => g.selectable).forEach((g) => g.items.forEach((i) => {
    if (g.ticked ? !state.cleanUnticked.has(i.rel) : state.cleanTicked.has(i.rel)) out.push(i);
  }));
  return out;
}

function setCleanTick(group, rel, on) {
  if (group.ticked) { if (on) state.cleanUnticked.delete(rel); else state.cleanUnticked.add(rel); }
  else if (on) state.cleanTicked.add(rel); else state.cleanTicked.delete(rel);
}

async function loadCleanStep() {
  setStepBadge("clean-status-badge", "", "loading…");
  try {
    state.cleanPreview = await api("GET", "/api/cleanup/preview");
  } catch (e) {
    setStepBadge("clean-status-badge", "danger", "error");
    const box = document.getElementById("clean-summary");
    box.innerHTML = "";
    box.appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
    return;
  }
  renderCleanStep();
  renderStepper();
}

function deviceName(c) {
  return c.device ? `${c.device.nickname || c.device.label || "the ASIAIR"} (${c.device.host})` : c.source.replace(/^local:/, "");
}

function renderCleanStep(live) {
  const c = state.cleanPreview;
  if (!c) return;
  const sel = cleanSelected();
  const selBytes = sel.reduce((a, i) => a + i.size, 0);
  const selFiles = sel.reduce((a, i) => a + 1 + (i.thumb ? 1 : 0), 0);
  const tv = c.to_verify;
  const rate = (state.plan && state.plan.rate_mb_s) || 10;
  const box = document.getElementById("clean-summary");
  box.innerHTML = "";
  const boxes = live && live.files_total !== undefined
    ? [[live.eta_s !== null && live.eta_s !== undefined ? clock(live.eta_s) : "estimating…", "time remaining", "ok"],
      [`${live.files_done} / ${live.files_total}`, "frames verified"], [`${gb(live.bytes_done)} / ${gb(live.bytes_total)}`, "read from the device"],
      [live.mb_s ? `${live.mb_s} MB/s` : "…", "speed"]]
    : [[String(selFiles), `files ticked · ${gb(selBytes)} to free`, selFiles ? "ok" : ""],
      [String(tv.files), `frames to verify · ${gb(tv.bytes)}`],
      [tv.files ? `~${clock(tv.bytes / 1e6 / rate)}` : "—", `to verify over Wi-Fi at ~${rate} MB/s`],
      [String(c.uncatalogued_batches), "copy batches not catalogued yet"]];
  box.appendChild(el("div", { class: "stat-row" }, boxes.map(([n, l, k]) => stat(n, l, k))));

  const vbtn = document.getElementById("clean-verify-btn");
  const rbtn = document.getElementById("clean-run-btn");
  if (!state.cleanRunning) {
    vbtn.disabled = !tv.files;
    vbtn.textContent = tv.files ? `Verify ${tv.files} frame${tv.files === 1 ? "" : "s"} already on the NAS (${gb(tv.bytes)})` : "Nothing to verify";
    rbtn.disabled = !c.device_delete_allowed || (!selFiles && !c.unfinished);
    rbtn.textContent = c.unfinished ? `Resume clean-up ${c.unfinished}` : `Delete ${selFiles} file${selFiles === 1 ? "" : "s"} (${gb(selBytes)}) from ${deviceName(c)}`;
  }
  setStepBadge("clean-status-badge", live ? "accent" : c.device_delete_allowed ? (selFiles ? "" : "ok") : "warn",
    live ? "working…" : !c.device_delete_allowed ? "deleting is switched off" : selFiles ? "awaiting approval" : "nothing ticked");
  const guard = document.getElementById("clean-guard");
  guard.innerHTML = "";
  if (!c.device_delete_allowed) guard.appendChild(el("div", { class: "session-mismatch-warning" }, [
    "⚠ Deleting from the device is switched off (ALLOW_DEVICE_DELETE is not 1). Everything else on this page works; nothing can be deleted until it's switched on."]));
  if (c.uncatalogued_batches) guard.appendChild(el("div", { class: "hint" }, [
    `${c.uncatalogued_batches} copy batch${c.uncatalogued_batches === 1 ? " hasn't" : "es haven't"} been catalogued yet (Catalog step). That doesn't block clean-up.`]));

  const list = document.getElementById("clean-groups");
  list.innerHTML = "";
  c.groups.forEach((g) => {
    const inGroup = g.items.filter((i) => (g.ticked ? !state.cleanUnticked.has(i.rel) : state.cleanTicked.has(i.rel)));
    const header = el("div", { class: "card-header" }, [
      el("div", { style: "display:flex; gap:10px; align-items:center; flex-wrap:wrap;" }, [
        g.selectable ? el("input", {
          type: "checkbox", checked: inGroup.length === g.items.length && g.items.length ? "" : null,
          title: "tick or untick every file in this group",
          onchange: (e) => { g.items.forEach((i) => setCleanTick(g, i.rel, e.target.checked)); renderCleanStep(); },
        }, []) : null,
        el("div", { class: "card-title" }, [g.label]),
        g.recommended ? el("span", { class: "badge ok" }, ["recommended"]) : null,
        el("span", { class: `badge ${g.selectable ? (g.recommended ? "ok" : "warn") : ""}` }, [`${g.files} file${g.files === 1 ? "" : "s"} · ${gb(g.bytes)}`]),
        g.selectable ? el("span", { class: "hint", style: "margin:0;" }, [`${inGroup.length} of ${g.items.length} ticked`]) : null,
      ]),
    ]);
    const body = [header];
    if (g.note) body.push(el("p", { class: "hint", style: "margin-top:0;" }, [g.note]));
    if (g.items.length) {
      body.push(toggleList(`show ${g.items.length} item${g.items.length === 1 ? "" : "s"}`, g.items.map((i) => el("label", {
        style: "display:flex; gap:8px; align-items:baseline;", title: i.how || "",
      }, [
        g.selectable ? el("input", { type: "checkbox", checked: inGroup.includes(i) ? "" : null,
          onchange: (e) => { setCleanTick(g, i.rel, e.target.checked); renderCleanStep(); } }, []) : null,
        el("span", { class: "session-path" }, [i.rel + (i.thumb ? "  + thumbnail" : "")]),
        i.nas.length ? el("span", { class: "hint", style: "margin:0;" }, [`NAS: ${i.nas[0]}`]) : (i.how ? el("span", { class: "hint", style: "margin:0;" }, [i.how]) : null),
      ]))));
    }
    list.appendChild(el("div", { class: "card" }, body));
  });
}

function renderCleanResult(res) {
  const box = document.getElementById("clean-result");
  box.innerHTML = "";
  if (!res) return;
  if (res.stopped) { box.appendChild(el("div", { class: "session-mismatch-warning" }, [`⚠ ${res.stopped}`])); return; }
  if (res.verified !== undefined) {
    box.appendChild(el("div", { class: "hint" }, [`Verified ${res.verified} frame${res.verified === 1 ? "" : "s"}` +
      `${res.mismatch ? `, ${res.mismatch} differ from the NAS copy` : ""}${res.nas_missing ? `, ${res.nas_missing} NAS cop${res.nas_missing === 1 ? "y" : "ies"} not found` : ""}; read ${gb(res.bytes)} in ${clock(res.seconds)}.`]));
    return;
  }
  box.appendChild(el("div", { class: "hint" }, [
    `Clean-up ${res.cleanup}: ${res.deleted} file${res.deleted === 1 ? "" : "s"} deleted (${gb(res.bytes_freed)} freed)${res.already_gone ? `, ${res.already_gone} already gone` : ""}, ${res.skipped.length} skipped, ${res.failed.length} failed${res.pruned.length ? `, ${res.pruned.length} empty folder${res.pruned.length === 1 ? "" : "s"} removed` : ""}. The device was listed again afterwards: ${res.unexpected_missing.length || res.still_there.length ? "see below" : "exactly the deleted files are gone"}. Log: `, logLink(res.log)]));
  const rows = (label, items, cls) => items.length && box.appendChild(el("div", { class: cls }, [`${label}:`, ...items.map((r) => el("div", { class: "session-path" }, [typeof r === "string" ? r : `${r.rel} — ${r.detail}`]))]));
  rows("Failed", res.failed, "error-banner");
  rows("Missing but not deleted by this run", res.unexpected_missing, "error-banner");
  rows("Still on the device after deleting", res.still_there, "error-banner");
  rows("Kept (a gate didn't pass)", res.skipped, "session-mismatch-warning");
}

async function watchCleanJob(jobId) {
  state.cleanRunning = true;
  document.getElementById("clean-verify-btn").disabled = true;
  document.getElementById("clean-run-btn").disabled = true;
  await pollJob(jobId, document.getElementById("clean-progress"), async (snap) => {
    state.cleanRunning = false;
    if (snap.status === "succeeded") { renderCleanResult(snap.result); if (snap.result.cleanup) state.passed.add("clean"); }
    else {
      document.getElementById("clean-result").innerHTML = "";
      document.getElementById("clean-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", snap.error || "failed"]));
    }
    state.cleanUnticked.clear();
    state.cleanTicked.clear();
    await loadPlan(false);
    await loadCleanStep();
  }, (snap) => renderCleanStep(snap.stats));
}

async function runCleanVerify() {
  const tv = state.cleanPreview.to_verify;
  if (!confirm(`Read ${tv.files} frame${tv.files === 1 ? "" : "s"} (${gb(tv.bytes)}) from the device once and compare each with its NAS copy? Nothing is deleted or changed.`)) return;
  try {
    const { job_id } = await api("POST", "/api/cleanup/verify");
    await watchCleanJob(job_id);
  } catch (e) {
    document.getElementById("clean-result").innerHTML = "";
    document.getElementById("clean-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
}

async function runCleanDelete() {
  const c = state.cleanPreview;
  const sel = cleanSelected();
  let what;
  if (c.unfinished) what = `Resume the interrupted clean-up ${c.unfinished} on ${deviceName(c)}?`;
  else {
    const per = {};
    sel.forEach((i) => { const g = c.groups.find((x) => x.items.includes(i)); per[g.label] = (per[g.label] || 0) + 1 + (i.thumb ? 1 : 0); });
    const files = Object.values(per).reduce((a, b) => a + b, 0);
    what = `Delete ${files} file${files === 1 ? "" : "s"} (${gb(sel.reduce((a, i) => a + i.size, 0))}) from ${deviceName(c)}?\n\n` +
      Object.entries(per).map(([k, n]) => `  ${n}  ${k}`).join("\n") + "\n\nThis can't be undone.";
  }
  if (!confirm(what)) return;
  try {
    const { job_id } = await api("POST", "/api/cleanup/run", { selected: sel.map((i) => i.rel) });
    await watchCleanJob(job_id);
  } catch (e) {
    document.getElementById("clean-result").innerHTML = "";
    document.getElementById("clean-result").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
}

// ---------- review ----------

function decisionFrames(p) {
  // every frame some decision is about (a frame can't be filed or released until its decision has an answer)
  const srcs = new Set();
  p.decisions.forEach((d) => d.items.forEach((src) => srcs.add(src)));
  return [...srcs].map((src) => state.itemsBySrc[src]).filter((i) => i && isFrame(i));
}

function renderReview() {
  const p = state.plan;
  if (!p) return;
  const s = p.summary;
  setStepBadge("review-status-badge", s.decisions_open ? "warn" : "ok",
    s.decisions_open ? `${s.decisions_open} decision${s.decisions_open === 1 ? "" : "s"} open` : "ready");

  const scored = p.items.filter((i) => i.kind === "Light" && i.quality).length;
  const rejected = s.actions.rejected || 0;
  const summary = document.getElementById("review-summary");
  summary.innerHTML = "";
  summary.appendChild(el("div", { class: "stat-row" }, [
    stat(s.copy_files, `files to copy · ${gb(s.copy_bytes)}`, "ok"),
    stat(s.sessions_new, "new sessions"),
    stat(p.sessions.filter((x) => x.exists && (x.lights || x.flats)).length, "sessions to append to"),
    stat(s.actions["already-ingested"] || 0, "already on the NAS"),
    stat(rejected, "rejected for quality", rejected ? "warn" : ""),
    stat(decisionFrames(p).length, `frame${decisionFrames(p).length === 1 ? "" : "s"} in decisions`),
    stat(s.decisions_open, "decisions open", s.decisions_open ? "warn" : "ok"),
  ]));
  const sigmaInput = document.getElementById("quality-sigma");
  if (state.sigmaLive === null) sigmaInput.value = p.sigma;
  document.getElementById("quality-sigma-value").textContent = `σ ${Number(currentSigma()).toFixed(1)}`;
  document.getElementById("quality-status").textContent = scored
    ? `${scored} light frame${scored === 1 ? "" : "s"} scored · default σ ${p.sigma_default}`
    : "No frames scored yet.";

  renderDecisions(p);
  renderSessions(p);
  renderLibrary(p);
  renderCleanup(p);
}

async function answerDecision(d, value) {
  try {
    applyPlan(await api("POST", "/api/answers", { [d.id]: value }));
  } catch (e) {
    alert(`Couldn't save that answer: ${e.message || e}`);
  }
}

function answerText(d, value) {
  // what an answer does, in the words of its button ("release for deletion", not "release" or "test")
  if (value && value.startsWith("new:")) return `new target ${value.slice(4)}`;
  const o = d.options.find((x) => x.value === value);
  if (!o) return value;
  return o.label.charAt(0).toLowerCase() + o.label.slice(1);
}

function decisionChips(d) {
  // A new-target answer carries the folder Chris chose ("new:NeedleGalaxy-NGC4565"), which differs from the
  // proposal in the option ("new:NGC4565"): match on the "new:" prefix and show the chosen name.
  const isNew = (v) => v !== null && v.startsWith("new:");
  const isChosen = (o) => o.value === d.resolved || (isNew(o.value) && isNew(d.resolved));
  const wrap = el("div", {}, []);
  const nameRow = el("div", { class: "field-row", style: "display:none; align-items:center; margin:-4px 0 10px;" }, []);
  const chips = el("div", { class: "checklist" }, d.options.map((o) => {
    const chosenName = isChosen(o) && isNew(d.resolved) ? d.resolved.slice(4) : "";
    const label = o.label + (isNew(o.value) && (chosenName || o.value.length > 4) ? ` (${chosenName || o.value.slice(4)})` : "");
    return el("label", {
      class: `chip${isChosen(o) ? " checked" : ""}`,
      onclick: (e) => {
        e.preventDefault();
        if (isNew(o.value)) {
          // a new target needs its folder name: ask for it inline
          nameRow.style.display = "flex";
          nameRow.querySelector("input").focus();
          return;
        }
        if (o.value !== d.resolved || d.answer === null) answerDecision(d, o.value);
      },
    }, [
      el("input", Object.assign({ type: "radio", name: d.id }, isChosen(o) ? { checked: "checked" } : {}), []),
      label,
    ]);
  }));
  const newOpt = d.options.find((o) => isNew(o.value));
  if (newOpt) {
    const current = isNew(d.resolved) ? d.resolved.slice(4) : newOpt.value.slice(4);
    const input = el("input", { type: "text", value: current, placeholder: "e.g. NeedleGalaxy-NGC4565", style: "width:280px;" }, []);
    const save = () => answerDecision(d, `new:${input.value.trim()}`);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    nameRow.appendChild(el("div", { class: "field", style: "margin:0; flex:0 0 auto;" }, [
      el("label", {}, ["Target folder name (CamelCase name, then catalog: no spaces or special characters)"]),
      el("div", { style: "display:flex; gap:6px;" }, [input, el("button", { class: "small primary", onclick: save }, ["Save"]),
        el("button", { class: "small ghost", onclick: () => { nameRow.style.display = "none"; } }, ["Cancel"])]),
    ]));
  }
  wrap.appendChild(chips);
  wrap.appendChild(nameRow);
  return wrap;
}

function renderDecisions(p) {
  const card = document.getElementById("decisions-card");
  const list = document.getElementById("decisions-list");
  list.innerHTML = "";
  card.style.display = p.decisions.length ? "block" : "none";
  const open = p.decisions.filter((d) => d.resolved === null).length;
  setStepBadge("decisions-badge", open ? "warn" : "ok", open ? `${open} open` : "all have answers or defaults");
  p.decisions.forEach((d) => {   // keep the planner's order: answering a decision doesn't move it
    const cls = d.answer !== null ? "answered" : d.resolved === null ? "" : "defaulted";
    const collapsed = state.collapsedDecisions.has(d.id);
    const files = d.items.map((src) => state.itemsBySrc[src]).filter(Boolean);
    const toggle = () => {
      if (collapsed) state.collapsedDecisions.delete(d.id); else state.collapsedDecisions.add(d.id);
      renderDecisions(state.plan);
    };
    const header = el("div", { class: "decision-header", onclick: toggle, title: collapsed ? "expand" : "collapse" }, [
      el("span", { class: "decision-chevron" }, [collapsed ? "▸" : "▾"]),
      el("span", { class: `badge ${d.resolved === null ? "warn" : "accent"}` }, [d.title || d.kind]),
      el("span", { class: "hint", style: "margin:0;" }, [d.answer !== null ? `answered: ${answerText(d, d.answer)}` : d.resolved === null ? "needs an answer" : `default: ${answerText(d, d.resolved)}`]),
      d.answer !== null ? el("span", {
        class: "toggle-adv", style: "margin:0 0 0 auto;",
        onclick: (e) => { e.stopPropagation(); answerDecision(d, null); },
        title: "forget this answer (back to the default, or open)",
      }, ["reset"]) : null,
    ]);
    const body = collapsed
      ? [el("div", { class: "decision-question collapsed" }, [d.question])]
      : [
        el("div", { class: "decision-question" }, [d.question]),
        decisionChips(d),
        frameStrip(`decision-${d.id}`, files),
        files.length ? toggleList(`show ${files.length} file${files.length === 1 ? "" : "s"}`, files.map((i) => itemRow(i))) : null,
      ];
    list.appendChild(el("div", { class: `decision ${cls}` }, [header, ...body]));
  });
}

function renderSessions(p) {
  const list = document.getElementById("sessions-list");
  list.innerHTML = "";
  const groupsById = Object.fromEntries(p.groups.map((g) => [g.id, g]));
  const active = p.sessions.filter((x) => !x.exists || x.lights || x.flats || x.rejected);
  const quiet = p.sessions.filter((x) => !active.includes(x));
  setStepBadge("sessions-badge", "", `${active.length} receiving frames`);
  if (!active.length) list.appendChild(el("div", { class: "empty-hint" }, ["Nothing new to copy into any session."]));
  active.forEach((sess) => {
    const prefix = `${sess.rel}/`;
    const block = el("div", { class: "night-block" }, []);
    const badge = sess.new_target ? ["accent", "new target"] : sess.exists ? ["", "on the NAS: append"] : ["ok", "new session"];
    block.appendChild(el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [
      el("span", { class: `badge ${badge[0]}` }, [badge[1]]),
      el("span", { class: "session-path" }, [sess.rel]),
    ]));
    block.appendChild(el("div", { class: "session-meta" }, [
      `+${sess.lights} lights${sess.replaced ? ` (${sess.replaced} replacing damaged copies)` : ""}`,
      sess.rejected ? ` · ${sess.rejected} rejected for quality` : "",
      ` · +${sess.flats} flats · ${gb(sess.bytes)}`,
      sess.siblings.length ? ` · sibling nights: ${sess.siblings.join(", ")}` : "",
    ]));
    sess.warnings.forEach((w) => block.appendChild(el("div", { class: "session-mismatch-warning" }, [`⚠ ${w}`])));
    sess.groups.map((id) => groupsById[id]).filter(Boolean).forEach((g) => {
      const items = g.items.map((src) => state.itemsBySrc[src]).filter(Boolean);
      if (g.kind === "lights") {
        block.appendChild(qualityToolbar(g, items));
        block.appendChild(frameStrip(g.id, items));
      } else {
        const going = items.filter((i) => i.dsts.some((d) => d.startsWith(prefix)));
        if (going.length) {
          block.appendChild(el("div", { class: "hint", style: "margin-top:8px;" }, [`${g.kind} · ${going.length} frames`]));
          block.appendChild(frameStrip(`${g.id}-${sess.rel}`, going));
        }
      }
    });
    const items = p.items.filter((i) => i.dsts.some((d) => d.startsWith(prefix)));
    block.appendChild(toggleList(`show ${items.length} file${items.length === 1 ? "" : "s"} and destinations`,
      items.map((i) => el("div", { title: i.reason || "" }, [el("span", { class: "act" }, [actionLabel(i)]), `${basename(i.src)}  →  ${i.dsts.filter((d) => d.startsWith(prefix)).join(", ")}`]))));
    list.appendChild(block);
  });
  if (quiet.length) {
    list.appendChild(el("p", { class: "hint" }, [`${quiet.length} more session(s) already on the NAS have nothing new to copy.`]));
    list.appendChild(toggleList("show them", quiet.map((q) => el("div", {}, [q.rel]))));
  }
}

function renderLibrary(p) {
  const byFolder = {};
  p.items.forEach((i) => {
    i.dsts.filter((d) => d.startsWith("001-") || d.startsWith("002-")).forEach((d) => {
      const folder = d.slice(0, d.lastIndexOf("/"));
      (byFolder[folder] = byFolder[folder] || []).push(i);
    });
  });
  const card = document.getElementById("library-card");
  const list = document.getElementById("library-list");
  list.innerHTML = "";
  const folders = Object.keys(byFolder).sort();
  card.style.display = folders.length ? "block" : "none";
  folders.forEach((folder) => {
    list.appendChild(el("div", { class: "night-block" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
        el("span", { class: "badge ok" }, ["new batch"]), el("span", { class: "session-path" }, [folder]),
      ]),
      el("div", { class: "session-meta" }, [`${byFolder[folder].length} frames · ${gb(byFolder[folder].reduce((a, i) => a + i.size, 0))}`]),
      frameStrip(`lib-${folder}`, byFolder[folder]),
      toggleList("show files", byFolder[folder].map((i) => itemRow(i))),
    ]));
  });
}

function renderCleanup(p) {
  const box = document.getElementById("cleanup-preview");
  box.innerHTML = "";
  ["after-verify", "callout", "blocked", "pending", "never"].forEach((key) => {
    const c = p.summary.cleanup[key];
    if (!c) return;
    const items = p.items.filter((i) => i.cleanup === key);
    const block = el("div", { class: "night-block" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
        el("span", { class: `badge ${key === "after-verify" ? "ok" : key === "blocked" ? "danger" : key === "never" ? "" : "warn"}` }, [`${c.files} files · ${gb(c.bytes)}`]),
        el("span", {}, [CLEANUP_LABELS[key]]),
      ]),
    ]);
    // Anything that isn't a plain verified frame is listed by name (Chris: call out everything unusual)
    if (key !== "after-verify") block.appendChild(toggleList(`show ${items.length} item${items.length === 1 ? "" : "s"}`, items.map((i) => itemRow(i, key === "callout" || key === "blocked"))));
    box.appendChild(block);
  });
  box.appendChild(el("p", { class: "hint" }, ["Every frame's _thn.jpg thumbnail goes with it; file counts include them."]));
}

// ---------- jobs (astro-stacker's pollJob shape) ----------

async function pollJob(jobId, progressEl, onDone, onTick) {
  const fill = progressEl.querySelector(".progress-fill");
  const pct = progressEl.querySelector(".pct");
  const msg = progressEl.querySelector(".msg");
  progressEl.style.display = "block";
  while (true) {
    let snap;
    try { snap = await api("GET", `/jobs/${jobId}`); } catch (e) { await onDone({ status: "failed", error: String(e) }); return; }
    const p = snap.percent_complete || 0;
    fill.style.width = `${Math.min(100, Math.max(0, p)).toFixed(0)}%`;
    pct.textContent = `${p.toFixed(0)}%`;
    msg.textContent = snap.current_line || "";
    if (onTick) onTick(snap);
    if (snap.status === "succeeded" || snap.status === "failed") { await onDone(snap); return; }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function watchQualityJob(jobId) {
  const btn = document.getElementById("quality-run-btn");
  btn.disabled = true;
  await pollJob(jobId, document.getElementById("quality-progress"), async (snap) => {
    btn.disabled = false;
    await loadPlan(false);
    document.getElementById("quality-status").textContent = snap.status === "succeeded"
      ? `Scored ${snap.result.scored} frame(s) in ${snap.result.seconds}s (${snap.result.skipped_cached} already scored${snap.result.failed ? `, ${snap.result.failed} failed` : ""}).`
      : `Scoring failed: ${snap.error}`;
  });
}

async function runQuality() {
  try {
    const { job_id } = await api("POST", "/api/quality/run");
    await watchQualityJob(job_id);
  } catch (e) {
    document.getElementById("quality-status").textContent = String(e.message || e);
  }
}

async function resumeRunningJob() {
  // Picks up a scoring job started earlier or from another tab (astro-stacker's active-jobs idea)
  try {
    const jobsNow = (await api("GET", "/jobs")).jobs;
    const stage = jobsNow.find((j) => j.kind === "stage" && j.status === "running");
    if (stage) { state.activeStep = "stage"; showActiveStep(); await watchStageJob(stage.id); }
    const copying = jobsNow.find((j) => j.kind === "copy" && j.status === "running");
    if (copying) { state.activeStep = "copy"; showActiveStep(); await watchCopyJob(copying.id); }
    const cleaning = jobsNow.find((j) => (j.kind === "cleanup" || j.kind === "verify") && j.status === "running");
    if (cleaning) { state.activeStep = "clean"; showActiveStep(); await watchCleanJob(cleaning.id); }
    const cataloguing = jobsNow.find((j) => j.kind === "catalog" && j.status === "running");
    if (cataloguing) { state.activeStep = "catalog"; showActiveStep(); await watchCatalogJob(cataloguing.id); }
    const running = jobsNow.find((j) => j.kind === "quality" && j.status === "running");
    if (running) await watchQualityJob(running.id);
  } catch (e) { /* ignore */ }
}

// ---------- load ----------

function applyPlan(plan) {
  const y = window.scrollY;
  state.plan = plan;
  state.itemsBySrc = Object.fromEntries(plan.items.map((i) => [i.src, i]));
  renderScan();
  renderSelect();
  renderStage();
  renderReview();
  renderStepper();
  requestAnimationFrame(() => window.scrollTo(window.scrollX, y));
}

async function loadHealth() {
  try {
    state.devices = state.devices || await api("GET", "/api/devices");
    state.health = await api("GET", "/health");
    setStepBadge("health-badge", state.health.status === "ok" ? "ok" : "danger", state.health.status === "ok" ? "online" : "error");
    if (state.health.version) document.getElementById("app-version").textContent = `v${state.health.version}`;
    const src = document.getElementById("source-badge");
    const dv = state.devices;
    const who = dv && dv.source_mode === "local" ? "local folder" : (dv && dv.remembered ? (dv.remembered.nickname || dv.remembered.label) : "ASIAIR");
    src.textContent = `${who}: ${state.health.source_online ? "online" : "offline"}`;
    src.className = `badge ${state.health.source_online ? "ok" : "warn"}`;
    src.title = state.health.source;
  } catch (e) {
    setStepBadge("health-badge", "danger", "offline");
  }
  renderConnect();
}

async function loadPlan(refresh) {
  setStepBadge("review-status-badge", "", refresh ? "scanning…" : "loading…");
  try {
    if (refresh) await api("POST", "/api/scan");
    const [plan, preview, batches] = await Promise.all([api("GET", "/api/plan"), api("GET", "/api/copy/preview"), api("GET", "/api/batches")]);
    state.copyPreview = preview;
    state.batches = batches.batches;
    state.lastBatch = batches.batches[0] || null;
    applyPlan(plan);
    if (!state.copyRunning) renderCopy();
  } catch (e) {
    state.plan = null;
    setStepBadge("review-status-badge", "danger", "error");
    document.getElementById("review-summary").innerHTML = "";
    document.getElementById("review-summary").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
    state.activeStep = "connect";
  }
  showActiveStep();
}

document.getElementById("rescan-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  await loadPlan(true);
  e.target.disabled = false;
});
document.getElementById("connect-next-btn").addEventListener("click", () => goTo("scan", "connect"));
document.getElementById("select-next-btn").addEventListener("click", () => goTo("stage", "scan"));
document.getElementById("stage-next-btn").addEventListener("click", () => goTo("review", "stage"));
document.getElementById("stage-run-btn").addEventListener("click", runStage);
document.getElementById("copy-run-btn").addEventListener("click", runCopy);
document.getElementById("catalog-run-btn").addEventListener("click", runCatalog);
document.getElementById("catalog-next-btn").addEventListener("click", () => goTo("clean", "catalog"));
document.getElementById("clean-verify-btn").addEventListener("click", runCleanVerify);
document.getElementById("clean-run-btn").addEventListener("click", runCleanDelete);
document.getElementById("review-next-btn").addEventListener("click", () => goTo("copy", "review"));
document.getElementById("copy-next-btn").addEventListener("click", () => goTo("catalog", "copy"));
document.getElementById("brand-link").addEventListener("click", (e) => { e.preventDefault(); state.activeStep = "connect"; showActiveStep(); });
document.getElementById("quality-run-btn").addEventListener("click", runQuality);
document.getElementById("find-btn").addEventListener("click", () => findDevices(false));
document.getElementById("find-all-btn").addEventListener("click", () => findDevices(true));
// σ slider: flags and charts follow while dragging (computed locally); the server applies it on release
document.getElementById("quality-sigma").addEventListener("input", (e) => {
  state.sigmaLive = Number(e.target.value);
  renderReview();
});
document.getElementById("quality-sigma").addEventListener("change", async (e) => {
  const plan = await api("POST", "/api/answers", { "quality-sigma": String(e.target.value) });
  state.sigmaLive = null;
  applyPlan(plan);
});

(async function init() {
  showActiveStep();
  await loadHealth();
  await loadPlan(false);
  resumeRunningJob();
})();
