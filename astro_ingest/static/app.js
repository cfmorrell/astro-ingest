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

const state = {
  activeStep: "connect",  // the flow starts at the beginning and works left to right across the stepper
  health: null,
  devices: null,         // /api/devices: source mode, remembered device, last search
  collapsedDecisions: new Set(),  // decision ids collapsed (this page view)
  passed: new Set(),     // steps finished in this page view: only these get a green check (Chris, 2026-09-27)
  sigmaLive: null,       // σ while the slider is being dragged (flags previewed locally until it's released)
  sessionReady: false,   // the session has been loaded from the server (don't save over it before that)
  sessionSaved: "",      // the last session saved or seen, as JSON
  sessionSeen: 0,        // updated_at of the newest session this window has seen
  watched: new Set(),    // job ids this window is following
  copyPreview: null,     // /api/copy/preview: what approving now would copy
  lastBatch: null,       // the most recent copy batch
  catalogPreview: null,  // /api/catalog/preview: what the Catalog step would write
  catalogRunning: false,
  cleanPreview: null,    // /api/cleanup/preview: what Clean up may delete from the device, by group
  cleanTicked: new Set(),    // files ticked on Clean up (nothing is ticked for you)
  cleanDone: null,           // the last Clean up run's result: its Done panel stays until dismissed
  cleanRunning: false,
  plan: null,
  batches: [],           // recent copy batches (newest first)
  lastStage: null,       // the last stage job's result in this page view
  itemsBySrc: {},
  expandedGroups: {},    // strip id -> Set of "start-end" collapsed ranges the user expanded
  chartsOpen: new Set(), // light group ids whose quality charts are shown
};

// ---------- the session (proposal K): kept on the server, so any window, reload or reconnect returns here ----------

const CLIENT_ID = Math.random().toString(36).slice(2);   // this window, so it can ignore its own saves
let sessionTimer = null;

function sessionData() {
  return {
    step: state.activeStep,
    passed: [...state.passed],
    cleanTicked: [...state.cleanTicked],
    collapsed: [...state.collapsedDecisions],
    cleanDone: state.cleanDone,
  };
}

function applySession(rec) {
  const d = (rec && rec.data) || {};
  state.passed = new Set(d.passed || []);
  state.cleanTicked = new Set(d.cleanTicked || []);
  state.collapsedDecisions = new Set(d.collapsed || []);
  state.cleanDone = d.cleanDone || null;
  if (d.step && STEPS.includes(d.step)) state.activeStep = d.step;
  state.sessionSaved = JSON.stringify(sessionData());
  state.sessionSeen = (rec && rec.updated_at) || 0;
}

function persistSession() {
  // saves only what changed, shortly after it changes
  if (!state.sessionReady) return;
  const json = JSON.stringify(sessionData());
  if (json === state.sessionSaved) return;
  state.sessionSaved = json;
  clearTimeout(sessionTimer);
  sessionTimer = setTimeout(async () => {
    try {
      const r = await api("PUT", "/api/session", { data: JSON.parse(json), client: CLIENT_ID });
      state.sessionSeen = Math.max(state.sessionSeen, r.updated_at);
    } catch (e) { /* the next change tries again */ }
  }, 300);
}

async function followOtherWindows() {
  // another window moved on: follow it (and pick up any job it started)
  try {
    const rec = await api("GET", "/api/session");
    if (rec.updated_at && rec.updated_at > state.sessionSeen && rec.client !== CLIENT_ID) {
      applySession(rec);
      await loadPlan(false);
      if (state.plan) renderDecisions(state.plan);
      if (state.activeStep === "clean" && state.cleanPreview) { renderCleanStep(); renderCleanResult(state.cleanDone); }
    }
    resumeRunningJob(true);
  } catch (e) { /* offline for a moment */ }
}

async function watchDevice() {
  // the pill turns red if the device drops off Wi-Fi mid-way (and back to green when it returns)
  try {
    const h = await api("GET", "/health");
    const changed = !state.health || h.source_online !== state.health.source_online;
    state.health = h;
    document.getElementById("app-banner").style.display = "none";
    if (changed) { renderDevicePill(); renderConnect(); renderStepBar(); }
  } catch (e) {
    const banner = document.getElementById("app-banner");
    banner.textContent = "✕ Can't reach the astro-ingest server. Check that it's running; this page reconnects on its own.";
    banner.style.display = "flex";
  }
}

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
  if (item.staged || item.has_staged_copy || state.plan.source.startsWith("local:")) return `/api/preview?rel=${encodeURIComponent(item.src)}&size=${size}`;
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

async function setFrameChoice(items, choiceFor, from) {
  // choiceFor(item) -> "keep" | "reject" | null (null = back to the recommendation)
  const updates = {};
  items.forEach((i) => { updates[`keep:${i.src}`] = choiceFor(i); });
  const anchor = from && from.closest ? from.closest("[data-anchor]") : null;
  answered(await api("POST", "/api/answers", updates), anchor && anchor.dataset.anchor);
}

function answered(plan, anchorId) {
  // after any answer: the plan (keeping the clicked block in place), then what Copy & verify would copy
  keepInPlace(anchorId, () => applyPlan(plan, true));
  api("GET", "/api/copy/preview").then((pv) => { state.copyPreview = pv; if (!state.copyRunning) renderCopy(); }).catch(() => {});
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

function isNewFrame(item) {
  // a frame this run would bring to the NAS (or is asking about); the rest of a strip is context already on the NAS
  return item.action !== "already-ingested";
}

function frameCard(item, items, index, opts = {}) {
  // Border (astro-stacker's meaning): red = flagged, recommended not to ingest; green = scored and fine;
  // grey = not scored (calibration frames, or before scoring); amber = flagged but you chose to keep it.
  // Proposal L: frames already on the NAS are context (dimmed, dashed, "on the NAS"; previews read from the NAS);
  // quality borders and Keep anyway / Reject appear only on new frames, and only in sessions (`opts.plain` off).
  const nas = !isNewFrame(item);
  const focus = opts.focus && opts.focus.has(item.src);
  const scored = !opts.plain && !nas && item.kind === "Light" && item.quality;
  const cls = nas ? "nas" : focus ? "focus" : opts.plain ? "new" : !scored ? "unscored" : isKept(item) ? "kept" : isFlagged(item) ? "flagged" : "";
  const card = el("div", { class: `frame-card ${cls}` }, []);
  const url = isFrame(item) ? previewUrl(item, THUMB) : null;
  if (url) {
    const img = el("img", { src: url, alt: basename(item.src), onclick: () => openLightboxFor(items.filter(isFrame), items.filter(isFrame).indexOf(item)) }, []);
    // a preview that can't be rendered (e.g. a truncated NAS copy) falls back to the device's own thumbnail
    img.addEventListener("error", () => { if (item.thumb && !img.src.includes("/api/thumb")) img.src = `/api/thumb?rel=${encodeURIComponent(item.thumb)}`; });
    card.appendChild(img);
  }
  const tag = nas ? "on the NAS" : item.retire && item.retire.length ? "replaces damaged copy"
    : scored && isFlagged(item) ? (item.action === "rejected" ? "flagged" : "flagged · kept")
    : item.action === "append" ? "new · append" : item.action === "copy" ? "new" : actionLabel(item);
  const meta = el("div", { class: "frame-meta" }, [
    el("div", { class: "frame-time", title: item.src }, [isFrame(item) ? frameLabel(item.src) : basename(item.src)]),
    el("div", { class: `frame-name frame-tag${nas ? " nas" : ""}`, title: item.reason || item.src }, [tag]),
  ]);
  if (scored && ["copy", "append", "rejected"].includes(item.action)) {
    const going = item.action !== "rejected";
    meta.appendChild(el("div", { class: "frame-actions" }, [
      el("button", {
        class: "small",
        onclick: (e) => { e.stopPropagation(); setFrameChoice([item], (i) => wantIngest(i, !going), e.target); },
      }, [going ? "Reject" : "Keep anyway"]),
    ]));
  }
  card.appendChild(meta);
  return card;
}

function computeVisibleItems(stripId, items) {
  // Proposal L: a strip with frames already on the NAS opens on what's new (plus 2 on either side); the rest
  // collapses into "⋯ N on the NAS". Otherwise astro-stacker's rule: large groups show flagged frames +/- 2.
  const context = items.some((it) => !isNewFrame(it));
  if (!context && items.length <= LARGE_GROUP_THRESHOLD) return items.map((it, i) => ({ type: "frame", item: it, index: i }));
  const expanded = state.expandedGroups[stripId] || new Set();
  const show = new Set();
  const near = (i) => { for (let d = -2; d <= 2; d++) if (i + d >= 0 && i + d < items.length) show.add(i + d); };
  if (context) items.forEach((it, i) => { if (isNewFrame(it)) near(i); });
  else {
    const notable = items.some((it) => isFlagged(it) || it.action === "append" || it.action === "rejected");
    items.forEach((it, i) => {
      if (isFlagged(it) || it.action === "append" || it.action === "rejected" || it.action === "needs-decision" && !notable) near(i);
    });
  }
  if (!show.size) for (let i = 0; i < Math.min(8, items.length); i++) show.add(i);
  const out = [];
  let i = 0;
  while (i < items.length) {
    if (show.has(i)) { out.push({ type: "frame", item: items[i], index: i }); i++; continue; }
    let j = i;
    while (j < items.length && !show.has(j)) j++;
    const key = `${i}-${j}`;
    if (expanded.has(key)) for (let k = i; k < j; k++) out.push({ type: "frame", item: items[k], index: k });
    else out.push({ type: "ellipsis", count: j - i, key, nas: items.slice(i, j).every((it) => !isNewFrame(it)) });
    i = j;
  }
  return out;
}

function frameStrip(stripId, items, opts = {}) {
  if (!items.length) return null;
  const strip = el("div", { class: "frame-strip" }, []);
  computeVisibleItems(stripId, items).forEach((v) => {
    if (v.type === "frame") strip.appendChild(frameCard(v.item, items, v.index, opts));
    else {
      strip.appendChild(el("div", {
        class: "frame-ellipsis",
        title: "show them",
        onclick: (e) => {
          (state.expandedGroups[stripId] = state.expandedGroups[stripId] || new Set()).add(v.key);
          const anchor = e.target.closest("[data-anchor]");
          keepInPlace(anchor && anchor.dataset.anchor, () => renderReview());
        },
      }, [`⋯ ${v.count} ${v.nas ? "on the NAS" : "more"}`]));
    }
  });
  return strip;
}

function keepInPlace(anchorId, rerender) {
  // re-render without the page jumping: the block you clicked in stays where it was on screen
  const find = () => (anchorId ? document.querySelector(`[data-anchor="${CSS.escape(anchorId)}"]`) : null);
  const before = find();
  const top = before ? before.getBoundingClientRect().top : null;
  const y = window.scrollY;
  rerender();
  const after = find();
  if (after && top !== null) window.scrollBy(0, after.getBoundingClientRect().top - top);
  else window.scrollTo(window.scrollX, y);
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
  return state.passed.has("catalog") || state.batches.some((b) => (b.status === "done" || b.status === "failed") && !b.catalogued_at);
}

function stepStatus(step) {
  // A step gets a green check only once it has been finished in this page view (Next pressed, or its job done),
  // and what it did still holds: a reload starts with no checks except a connected device.
  if (STEP_PHASE[step]) return { available: false, complete: false };
  const planned = !!state.plan;
  const passed = state.passed.has(step);
  switch (step) {
    case "connect": return { available: true, complete: !!(state.health && state.health.source_online) };
    case "scan": return { available: planned || !!(state.health && state.health.source_online), complete: passed && planned };
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

function stepDone(step) {
  // has this step's own action been done (so Next is the thing to do now)?
  const p = state.plan;
  switch (step) {
    case "connect": return !!(state.health && state.health.source_online);
    case "scan": return !!p && selectedItems().length > 0;
    case "stage": return !!p && selectedItems().length > 0 && selectedItems().every((i) => i.staged);
    case "review": return !!p && p.summary.decisions_open === 0 && !state.qualityRunning && !needsScoring();
    case "copy": return state.passed.has("copy") || (!!state.copyPreview && !state.copyPreview.copies && catalogAvailable());
    case "catalog": return !!state.catalogPreview && !state.catalogPreview.summary.writes && !state.catalogPreview.summary.batches.length;
    default: return false;
  }
}

function renderStepBar() {
  // Proposal A: the step's status on the left; Next (or Clean up's Delete) always in the same place
  persistSession();
  const running = state.stageRunning || state.copyRunning || state.catalogRunning || state.cleanRunning;
  const so = document.getElementById("start-over-btn");
  so.disabled = !!running;
  so.title = running ? "Wait for the running job to finish" : "Clear this run and go back to Connect";
  const step = state.activeStep;
  const status = document.getElementById("stepbar-status");
  const why = document.getElementById("stepbar-why");
  const next = document.getElementById("stepbar-next");
  const del = document.getElementById("stepbar-delete");
  const p = state.plan;
  const b = (t) => el("b", {}, [t]);
  status.innerHTML = "";
  why.textContent = "";
  next.style.display = step === "clean" ? "none" : "inline-block";
  del.style.display = step === "clean" ? "inline-block" : "none";
  const i = STEPS.indexOf(step);
  if (step !== "clean") next.textContent = `Next: ${STEP_LABELS[STEPS[i + 1]]} →`;
  let blocked = null;   // why Next can't be used yet
  const parts = [];
  if (step === "connect") {
    const h = state.health;
    const dv = state.devices;
    const name = dv && dv.remembered ? (dv.remembered.nickname || dv.remembered.label) : null;
    parts.push(h && h.source_online ? ["Connected to ", b(dv && dv.source_mode === "local" ? "a local folder" : name || "the ASIAIR")] : ["Not connected"]);
    if (!(h && h.source_online)) blocked = "Connect to a device first";
  } else if (step === "scan" && p) {
    const sel = selectedItems();
    const toRead = sel.filter((x) => !x.staged).reduce((a, x) => a + x.size, 0);
    parts.push([b(`${sel.length} frame${sel.length === 1 ? "" : "s"}`), ` selected · ${gb(sel.reduce((a, x) => a + x.size, 0))}${toRead ? ` · ${roughTime(toRead)} over Wi-Fi` : ""}`]);
  } else if (step === "stage" && p) {
    const sel = selectedItems();
    const staged = sel.filter((x) => x.staged).length;
    const live = state.stageRunning ? state.stageLive : null;
    if (live && live.phase === "scoring") parts.push([b(`${live.staged !== undefined ? live.staged : sel.length} frames staged`), ` · scoring light frames ${live.scored || 0} / ${live.to_score || "…"}`]);
    else if (live && live.files_total !== undefined) parts.push([b(`${staged + live.files_done} of ${staged + live.files_total}`), " frames staged"]);
    else parts.push(sel.length && staged === sel.length ? [b("Everything staged"), ` · ${sel.length} frame${sel.length === 1 ? "" : "s"}`] : [b(`${staged} of ${sel.length}`), " frames staged"]);
    if (state.stageRunning) blocked = live && live.phase === "scoring" ? "Scoring light frames for Review…" : "Staging…";
  } else if (step === "review" && p) {
    const s = p.summary;
    parts.push([b(`${s.copy_files} file${s.copy_files === 1 ? "" : "s"}`), ` to copy · ${gb(s.copy_bytes)}`, s.decisions_open ? ` · ${s.decisions_open} decision${s.decisions_open === 1 ? "" : "s"} open` : ""]);
    if (state.qualityRunning) blocked = "Scoring light frames…";
  } else if (step === "copy" && state.copyPreview) {
    const pv = state.copyPreview;
    parts.push(pv.copies ? [b(`${pv.copies} file${pv.copies === 1 ? "" : "s"}`), ` ready to copy · ${gb(pv.bytes)}`] : [b("Nothing left to copy")]);
    if (state.copyRunning) blocked = "Copying…";
    else if (!catalogAvailable()) blocked = "Copy something first";
  } else if (step === "catalog" && state.catalogPreview) {
    const s = state.catalogPreview.summary;
    parts.push(s.writes ? [b(`${s.writes} catalog update${s.writes === 1 ? "" : "s"}`), " to write"] : [b("Catalog up to date")]);
    if (state.catalogRunning) blocked = "Writing…";
  } else if (step === "clean" && state.cleanPreview) {
    const sel = cleanSelected();
    const k = selectionCounts(sel);
    const files = k.files;
    const bytes = sel.reduce((a, x) => a + x.size, 0);
    parts.push([b(`${files} file${files === 1 ? "" : "s"}`), ` ticked${files ? `: ${fileBreakdown(k.frames, k.thumbs, k.mac, k.other)}` : ""} · ${gb(bytes)}`]);
    const c = state.cleanPreview;
    del.textContent = c.unfinished ? `Resume clean-up ${c.unfinished}` : files ? `Delete ${files} file${files === 1 ? "" : "s"} from ${deviceName(c)}` : "Delete…";
    del.classList.toggle("armed", !!files || !!c.unfinished);
    del.disabled = state.cleanRunning || !c.device_delete_allowed || (!files && !c.unfinished);
    if (!c.device_delete_allowed) why.textContent = "Deleting is switched off (ALLOW_DEVICE_DELETE)";
    else if (state.cleanRunning) why.textContent = "Working…";
  }
  (parts[0] || []).forEach((x) => status.appendChild(typeof x === "string" ? document.createTextNode(x) : x));
  if (step !== "clean") {
    next.disabled = !!blocked;
    why.textContent = blocked && (!/…$/.test(blocked) || /^Scoring/.test(blocked)) ? blocked : "";
    if (step === "review" && state.qualityRunning) why.textContent = "Scoring light frames… (see the top of Review)";
    next.classList.toggle("ready", !blocked && stepDone(step));
  }
}

function goTo(step, from) {
  if (from) state.passed.add(from);
  state.activeStep = step;
  showActiveStep();
}

function renderStepper() {
  renderStepBar();
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
  if (state.activeStep === "review" && !state.qualityRunning && !state.stageRunning && needsScoring()) runQuality();
  if (state.activeStep === "clean" && !state.cleanRunning) loadCleanStep();
}

// ---------- connect / scan ----------

function describeDevice(d) {
  if (!d) return "none";
  const where = `${d.label} at ${d.host}`;
  return d.nickname ? `${d.nickname} (${where})` : where;
}

function renderConnect() {
  // Proposal C / C2: recent devices (Connect, Rename, Forget), a search of the home network, and an Advanced section
  const body = document.getElementById("connect-body");
  const msg = document.getElementById("connect-msg");
  body.innerHTML = "";
  msg.textContent = state.connectMsg ? state.connectMsg.text : "";
  msg.style.color = state.connectMsg && state.connectMsg.ok ? "var(--success)" : state.connectMsg ? "var(--warn)" : "";
  const dv = state.devices;
  const h = state.health;
  if (!dv) return;
  if (dv.source_mode === "local") {
    body.appendChild(el("div", {}, ["Reading from a local folder (ASIAIR_ROOT): ", el("span", { class: "session-path" }, [dv.local_root])]));
    return;
  }
  const busy = !!state.connectBusy;
  const sel = dv.remembered;
  const online = !!(h && h.source_online);
  if (state.connectBusy) body.appendChild(el("div", { class: "hint", style: "margin:0 0 10px;" }, [state.connectBusy]));

  if (dv.recent.length) {
    body.appendChild(el("div", { class: "clean-section-h", style: "margin-top:0;" }, [el("span", { class: "t" }, ["Recent devices"]), el("span", { class: "hint", style: "margin:0;" }, ["every device you've connected to, most recent first"])]));
    dv.recent.forEach((r, n) => {
      const isSel = sel && sel.host === r.host;
      const name = el("input", { type: "text", value: r.nickname || "", placeholder: "your name for it", "aria-label": "Device name", style: "width:200px;" }, []);
      const last = r.last_connected ? new Date(r.last_connected * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
      body.appendChild(el("div", { class: `night-block device-row${isSel ? " current" : ""}` }, [
        el("div", { style: "display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:space-between;" }, [
          el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
            el("span", { class: "device-name" }, [r.nickname || r.label]),
            isSel ? el("span", { class: `badge ${online ? "accent" : "danger"}` }, [online ? "currently selected" : "not answering"]) : null,
            (dv.found || []).some((d) => d.host === r.host) ? el("span", { class: "badge ok" }, ["found in this search"]) : null,
          ]),
          el("span", { class: "session-path" }, [`${r.host}${last ? ` · last connected ${last}` : ""}`]),
        ]),
        el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px;" }, [
          isSel && online ? el("span", { class: "badge ok" }, ["connected"])
            : el("button", { class: `small${!online && n === 0 ? " primary" : ""}`, disabled: busy ? "" : null, onclick: () => connectTo(r.host, null, r.nickname || r.label) }, ["Connect"]),
          name,
          el("button", { class: "small", disabled: busy ? "" : null, onclick: () => renameDevice(r.host, name.value) }, ["Rename"]),
          el("button", { class: "small", disabled: busy ? "" : null, onclick: () => forgetDevice(r) }, ["Forget"]),
        ]),
      ]));
    });
  }

  // search the home network (or ask which one it is)
  const net = dv.network;
  const box = el("div", { class: "night-block", style: "margin-top:12px;" }, []);
  if (net) {
    const where = { browser: `From this browser's address (${net.detail}).`, server: "From the server's own network.",
      history: `From a device you connected to before (${net.detail}).`, setting: "Set by ASIAIR_SUBNET." }[net.source] || "";
    box.appendChild(el("div", { style: "display:flex; gap:10px; align-items:center; flex-wrap:wrap;" }, [
      el("button", { class: dv.recent.length ? "" : "primary", disabled: busy ? "" : null, onclick: () => findDevices(null) }, ["Search the network"]),
      el("span", { class: "session-path" }, [net.subnet]),
    ]));
    box.appendChild(el("div", { class: "hint", style: "margin:6px 0 0;" }, [`${where} Tries every address, a few seconds. For a new device, or one whose address changed. Searching disconnects and starts a new session.`]));
  } else {
    const input = el("input", { type: "text", placeholder: "192.168.1.0/24", "aria-label": "Home network", style: "width:200px;" }, []);
    box.appendChild(el("div", { class: "card-title", style: "margin-bottom:6px;" }, ["Which network is your ASIAIR on?"]));
    box.appendChild(el("p", { class: "hint", style: "margin:0 0 8px;" }, ["Enter your home network: the first three numbers of your computer's IP address, then .0/24. For example, if your Mac is 192.168.1.3, enter 192.168.1.0/24. (On a Mac: System Settings → Wi-Fi → Details → TCP/IP.)"]));
    box.appendChild(el("div", { style: "display:flex; gap:8px;" }, [input, el("button", { class: "primary small", disabled: busy ? "" : null, onclick: () => findDevices(input.value) }, ["Search this network"])]));
  }
  body.appendChild(box);

  // what the last search found: a Connect button on every device, even when there's only one
  (dv.found || []).filter((d) => !d.known).forEach((d) => {   // devices seen before are in Recent devices
    const known = d.known;
    const name = el("input", { type: "text", value: known ? (known.nickname || "") : "", placeholder: "name it, e.g. ASIAIR Color", "aria-label": "Name for this device", style: "width:200px;" }, []);
    body.appendChild(el("div", { class: "night-block device-row" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:space-between;" }, [
        el("div", { style: "display:flex; gap:8px; align-items:center;" }, [
          el("span", { class: "device-name" }, [known ? (known.nickname || d.label) : d.label]),
          el("span", { class: `badge ${known ? "" : "ok"}` }, [known ? "seen before" : "new"]),
        ]),
        el("span", { class: "session-path" }, [`${d.host} · share “${d.share}”`]),
      ]),
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px;" }, [
        name, el("button", { class: "small primary", disabled: busy ? "" : null, onclick: () => selectFound(d.host, name.value, known ? known.nickname || d.label : d.label) }, ["Connect"]),
        el("span", { class: "hint", style: "margin:0;" }, [`${d.label} · folders: ${d.folders.filter((f) => !f.startsWith(".")).join(", ")}`]),
      ]),
    ]));
  });

  // Advanced (collapsed): one address, or a different network
  const ip = el("input", { type: "text", placeholder: "192.168.1.43", "aria-label": "Device address", style: "width:200px;" }, []);
  const subnet = el("input", { type: "text", placeholder: "192.168.50.0/24", "aria-label": "Network to search", style: "width:200px;" }, []);
  body.appendChild(el("details", { style: "margin-top:12px;" }, [
    el("summary", { class: "toggle-adv", style: "margin:0;" }, ["Advanced"]),
    el("div", { style: "display:grid; gap:8px; margin-top:10px;" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [el("span", { class: "hint", style: "margin:0; width:170px;" }, ["Connect to an address"]), ip,
        el("button", { class: "small", disabled: busy ? "" : null, onclick: () => connectTo(ip.value, null, ip.value) }, ["Connect"])]),
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [el("span", { class: "hint", style: "margin:0; width:170px;" }, ["Search a different network"]), subnet,
        el("button", { class: "small", disabled: busy ? "" : null, onclick: () => findDevices(subnet.value) }, ["Search"])]),
    ]),
  ]));
}

async function afterConnect(res, what) {
  state.devices = res;
  state.connectBusy = `Connected to ${what}. Scanning it…`;
  renderConnect();
  await loadHealth();
  renderStepBar();          // Next works now; Scan Images shows the scan until it's done
  renderStepper();
  await loadPlan(false);
  state.connectBusy = null;
  state.connectMsg = { ok: true, text: `✓ Connected to ${what}.` };
  renderConnect();
}

async function connectTo(host, nickname, what) {
  state.connectBusy = `Connecting to ${what}…`;
  state.connectMsg = null;
  renderConnect();
  try {
    await afterConnect(await api("POST", "/api/devices/connect", { host, nickname }), what);
  } catch (e) {
    state.connectBusy = null;
    state.connectMsg = { ok: false, text: String(e.message || e).replace(/^\d+: "?|"$/g, "") };
    renderConnect();
  }
}

async function selectFound(host, nickname, what) {
  state.connectBusy = `Connecting to ${nickname || what}…`;
  state.connectMsg = null;
  renderConnect();
  try {
    await afterConnect(await api("POST", "/api/devices/select", { host, nickname: nickname || null }), nickname || what);
  } catch (e) {
    state.connectBusy = null;
    state.connectMsg = { ok: false, text: String(e.message || e).replace(/^\d+: "?|"$/g, "") };
    renderConnect();
  }
}

async function renameDevice(host, nickname) {
  try {
    const res = await api("POST", "/api/devices/rename", { host, nickname });
    state.devices = res;
    state.connectMsg = { ok: true, text: `✓ ${res.message}` };
    renderDevicePill();
    renderConnect();
    renderStepBar();
    if (state.cleanPreview) loadCleanStep();
  } catch (e) {
    state.connectMsg = { ok: false, text: String(e.message || e) };
    renderConnect();
  }
}

async function forgetDevice(r) {
  const name = r.nickname || r.label;
  if (!confirm(`Forget ${name} (${r.host})? It's removed from Recent devices${state.devices.remembered && state.devices.remembered.host === r.host ? " and disconnected" : ""}. Nothing on the device or the NAS changes; frames it had staged stay in staging until Start over.`)) return;
  const res = await api("POST", "/api/devices/forget", { host: r.host });
  state.devices = res;
  state.connectMsg = { ok: true, text: `✓ Forgot ${name}.` };
  if (!res.remembered) { state.plan = null; }
  await loadHealth();
  renderStepper();
}

async function findDevices(subnet) {
  // A search starts a new session: the app disconnects, then lists every device it finds
  state.connectBusy = "Searching the network…";
  state.connectMsg = null;
  state.plan = null;
  state.passed.clear();
  renderDevicePill(true);
  renderConnect();
  renderStepper();
  try {
    const res = await api("POST", "/api/devices/find", subnet ? { subnet } : {});
    state.devices = res;
    state.connectMsg = { ok: !!res.found.length, text: `${res.message}${res.seconds !== undefined ? ` (${res.scanned} addresses in ${res.seconds} s)` : ""}` };
  } catch (e) {
    state.connectMsg = { ok: false, text: String(e.message || e).replace(/^\d+: "?|"$/g, "") };
    state.devices = await api("GET", "/api/devices");
  }
  state.connectBusy = null;
  await loadHealth();
  renderStepper();
}

const CAPTURE_FOLDERS = ["Autorun", "Plan"];  // the only folders ingested; everything else is listed for reference

function renderScan() {
  // Proposal D: the capture folders (with how many of their new frames are selected), then the rest, dimmed
  const p = state.plan;
  const body = document.getElementById("scan-body");
  body.innerHTML = "";
  if (!p) {
    const dv = state.devices;
    const name = dv && dv.remembered ? (dv.remembered.nickname || dv.remembered.label) : "the device";
    if (state.scanning) body.appendChild(el("div", { class: "hint" }, [`Scanning ${name}: listing every file and reading frame headers in Autorun/ and Plan/… (about a minute over Wi-Fi)`]));
    return;
  }
  document.getElementById("scan-status-badge").style.display = "none";
  const when = new Date(p.scanned_at);
  document.getElementById("scanned-at").textContent = isNaN(when.getTime()) ? "" : `Scanned ${when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;
  const byTop = {};
  const stageable = p.stageable_actions || [];
  p.items.forEach((i) => {
    const top = i.src.includes("/") ? i.src.slice(0, i.src.indexOf("/")) : "(share root)";
    const t = byTop[top] = byTop[top] || { files: 0, bytes: 0, frames: 0, fresh: 0, selected: 0 };
    t.files += 1 + (i.thumb ? 1 : 0);
    t.bytes += i.size + (i.thumb_size || 0);
    if (isFrame(i)) t.frames += 1;
    if (stageable.includes(i.action) || i.action === "excluded") t.fresh += 1;
    if (stageable.includes(i.action)) t.selected += 1;
  });
  const tile = (top, quiet) => {
    const t = byTop[top];
    const what = top === "(share root)" ? "files at the top of the share" : `${top} ${t.frames ? "frames" : "files"}`;
    const kids = [el("div", { class: "num" }, [String(t.frames || t.files)]), el("div", { class: "lbl" }, [`${what} · ${gb(t.bytes)}`])];
    if (!quiet) kids.push(el("div", { class: "sel" }, [t.fresh ? `${t.selected} of ${t.fresh} new selected` : "nothing new"]));
    return el("div", { class: `stat${quiet ? " quiet" : ""}` }, kids);
  };
  const capture = CAPTURE_FOLDERS.filter((t) => byTop[t]);
  const other = Object.keys(byTop).filter((t) => !CAPTURE_FOLDERS.includes(t)).sort();
  body.appendChild(el("div", { class: "folder-groups" }, [
    el("div", { class: "folder-group" }, [el("div", { class: "folder-group-h" }, ["Capture folders · ingested"]), el("div", { class: "stat-row" }, capture.map((t) => tile(t, false)))]),
    other.length ? el("div", { class: "folder-group" }, [el("div", { class: "folder-group-h" }, ["Other folders · not ingested, never touched"]), el("div", { class: "stat-row" }, other.map((t) => tile(t, true)))]) : null,
  ]));
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
    answered(await api("POST", "/api/answers", updates));
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
  state.stageLive = live || null;
  const scoring = !!(live && live.phase === "scoring");
  if (scoring) {
    // part 2 of the stage job: score the light frames (with their sessions' frames already on the NAS) for Review
    remaining = ["✓", `${live.staged !== undefined ? live.staged : selected.length} frames staged`, "ok"];
    frames = [live.to_score ? `${live.scored} / ${live.to_score}` : "…", "light frames scored for Review"];
    data = ["—", "frames already on the NAS are scored alongside"]; speed = ["—", "speed"];
  } else if (live && live.files_total !== undefined) {
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
  if (scoring && live.to_score) {
    const pct = Math.round((live.scored / live.to_score) * 100);
    prog.querySelector(".progress-fill").style.width = `${pct}%`;
    prog.querySelector(".pct").textContent = `${pct}%`;
  }
  if (!live) {
    const pct = selected.length ? Math.round(((allBytes - bytes) / (allBytes || 1)) * 100) : 0;
    prog.querySelector(".progress-fill").style.width = `${pct}%`;
    prog.querySelector(".pct").textContent = `${pct}%`;
    prog.querySelector(".msg").textContent = !selected.length ? "" : toRead.length ? "ready to stage" : "all selected frames are staged";
  }
  const btn = document.getElementById("stage-run-btn");
  btn.textContent = scoring ? "Scoring light frames…" : live ? "Staging…" : toRead.length ? `Stage ${toRead.length} frame${toRead.length === 1 ? "" : "s"}` : "Everything selected is staged";
  btn.classList.toggle("primary", !!(live || toRead.length));
  if (!state.stageRunning) btn.disabled = toRead.length === 0;
  setStepBadge("stage-status-badge", live ? "accent" : toRead.length ? "" : "ok", scoring ? "scoring…" : live ? "staging…" : toRead.length ? "not staged" : (selected.length ? "staged" : "nothing selected"));
  renderStepBar();
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
  }, (snap) => renderStage(snap.stats && (snap.stats.phase === "scoring" || snap.stats.files_total !== undefined) ? snap.stats : null));
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
  btn.classList.toggle("primary", !!(live || pv.copies || pv.unfinished_batch));
  renderStepBar();
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
    btn.classList.toggle("primary", !!(s.writes || s.batches.length));
  }
  renderStepBar();
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

// Clean up (proposal E): groups sorted by what can be done with them; nothing is ticked for you
const CLEAN_SECTIONS = [
  ["Ready to delete", "a checksum proves the NAS copy is identical", ["verified"]],
  ["Needs a check first", "on the NAS, copied there before astro-ingest", ["to-verify"]],
  ["Not on the NAS", "deleting these loses them", ["released", "rejected", "left-out", "over-cap", "not-kept", "other", "orphan-thumb"]],
  ["Stays on the device", "", ["differs", "not-copied", "blocked", "waiting", "never"]],
];
const QUICK_S_PER_FRAME = 0.15;   // measured 0.12 s per frame on the real ASIAIR over Wi-Fi (2026-09-27)

function cleanSelected() {
  const c = state.cleanPreview;
  if (!c) return [];
  const out = [];
  c.groups.filter((g) => g.selectable).forEach((g) => g.items.forEach((i) => { if (state.cleanTicked.has(i.rel)) out.push(i); }));
  return out;
}

function setCleanTick(rel, on) {
  if (on) state.cleanTicked.add(rel); else state.cleanTicked.delete(rel);
}

async function loadCleanStep() {
  setStepBadge("clean-status-badge", "", "loading…");
  try {
    state.cleanPreview = await api("GET", "/api/cleanup/preview");
  } catch (e) {
    setStepBadge("clean-status-badge", "danger", "error");
    const box = document.getElementById("clean-result");
    box.innerHTML = "";
    box.appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
    return;
  }
  renderCleanStep();
  renderCleanResult(state.cleanDone);
  renderStepper();
}

function deviceName(c) {
  return c.device ? `${c.device.nickname || c.device.label || "the ASIAIR"} (${c.device.host})` : c.source.replace(/^local:/, "");
}

function renderCleanStep() {
  const c = state.cleanPreview;
  if (!c) return;
  document.getElementById("clean-title").textContent = `Clean up ${deviceName(c)}`;
  const sel = cleanSelected();
  setStepBadge("clean-status-badge", state.cleanRunning ? "accent" : !c.device_delete_allowed ? "warn" : "",
    state.cleanRunning ? "working…" : !c.device_delete_allowed ? "deleting is switched off" : sel.length ? `${selectionCounts(sel).files} files ticked` : "nothing ticked");
  document.getElementById("clean-recommended-btn").disabled = state.cleanRunning ||
    !c.groups.some((g) => g.recommended && g.selectable && g.items.some((i) => !state.cleanTicked.has(i.rel)));
  const guard = document.getElementById("clean-guard");
  guard.innerHTML = "";
  if (!c.device_delete_allowed) guard.appendChild(el("div", { class: "session-mismatch-warning" }, [
    "⚠ Deleting from the device is switched off (ALLOW_DEVICE_DELETE is not 1). Everything else on this page works; nothing can be deleted until it's switched on."]));

  const byId = Object.fromEntries(c.groups.map((g) => [g.id, g]));
  const list = document.getElementById("clean-groups");
  list.innerHTML = "";
  CLEAN_SECTIONS.forEach(([title, note, ids]) => {
    const groups = ids.map((id) => byId[id]).filter(Boolean);
    if (!groups.length) return;
    list.appendChild(el("div", { class: "clean-section-h" }, [el("span", { class: "t" }, [title]), note ? el("span", { class: "hint", style: "margin:0;" }, [note]) : null]));
    groups.forEach((g) => list.appendChild(cleanGroup(c, g)));
  });
  if (!c.groups.length) list.appendChild(el("div", { class: "empty-hint" }, ["Nothing on the device to clean up."]));
  renderStepBar();
}

function fileBreakdown(frames, thumbs, mac, other) {
  // "109 frames + 109 thumbnails + 2 other files": what a file count is made of
  const n = (k, w) => (k ? `${k} ${w}${k === 1 ? "" : "s"}` : null);
  return [n(frames, "frame"), n(thumbs, "thumbnail"), n(mac, "Mac metadata file"), n(other, "other file")].filter(Boolean).join(" + ");
}

function selectionCounts(items) {
  // what the ticked files are: frames (each with its thumbnail and any Mac metadata), thumbnails, Mac metadata, other
  const k = { frames: 0, thumbs: 0, mac: 0, other: 0, files: 0 };
  items.forEach((i) => {
    const name = basename(i.rel);
    if (i.kind === "frame") { k.frames += 1; if (i.thumb) k.thumbs += 1; }
    else if (name === ".DS_Store" || name.startsWith("._")) k.mac += 1;
    else if (name.endsWith("_thn.jpg")) k.thumbs += 1;
    else k.other += 1;
    k.mac += i.companions ? i.companions.length : 0;
  });
  k.files = k.frames + k.thumbs + k.mac + k.other;
  return k;
}

function cleanGroup(c, g) {
  const ticked = g.items.filter((i) => state.cleanTicked.has(i.rel));
  const all = selectionCounts(g.items);
  const count = g.id === "never" ? `${g.files} file${g.files === 1 ? "" : "s"}` : fileBreakdown(all.frames, all.thumbs, all.mac, all.other);
  const top = el("div", { class: "top" }, [
    g.selectable ? el("input", {
      type: "checkbox", "aria-label": `tick every file in ${g.label}`,
      checked: ticked.length === g.items.length && g.items.length ? "" : null,
      disabled: state.cleanRunning ? "" : null,
      onchange: (e) => { g.items.forEach((i) => setCleanTick(i.rel, e.target.checked)); renderCleanStep(); },
    }, []) : null,
    el("span", { class: "t" }, [g.label]),
    g.recommended ? el("span", { class: "badge ok" }, ["recommended"]) : null,
    el("span", { class: "num" }, [`${count} · ${gb(g.bytes)}`]),
    g.selectable && ticked.length && ticked.length < g.items.length ? el("span", { class: "hint", style: "margin:0;" }, [`${selectionCounts(ticked).files} of ${g.files} files ticked`]) : null,
  ]);
  const body = [top];
  if (g.note && !/^not on the NAS/.test(g.note)) body.push(el("div", { class: "hint", style: "margin:4px 0 0;" }, [g.note]));   // the section already says it
  if (g.id === "to-verify") body.push(checkControls(c, g));
  if (g.items.length) {
    body.push(toggleList(`show ${g.items.length} item${g.items.length === 1 ? "" : "s"}`, g.items.map((i) => el("label", { title: i.how || "" }, [
      g.selectable ? el("input", { type: "checkbox", checked: state.cleanTicked.has(i.rel) ? "" : null, disabled: state.cleanRunning ? "" : null,
        onchange: (e) => { setCleanTick(i.rel, e.target.checked); renderCleanStep(); } }, []) : null,
      el("span", { class: "session-path" }, [i.rel + (i.thumb ? "  + thumbnail" : "") + (i.companions && i.companions.length ? "  + Mac metadata" : "")]),
      i.nas.length ? el("span", { class: "hint", style: "margin:0;" }, [`NAS: ${i.nas[0]}`]) : (i.how ? el("span", { class: "hint", style: "margin:0;" }, [i.how]) : null),
    ]))));
  }
  return el("div", { class: "clean-group" }, body);
}

function checkControls(c, g) {
  // "Needs a check first": the Check buttons sit next to what they apply to (proposal E, G)
  const n = g.items.length;
  const rate = (state.plan && state.plan.rate_mb_s) || 10;
  const quick = clock(n * QUICK_S_PER_FRAME);
  const thorough = clock(c.to_verify.bytes / 1e6 / rate);
  const box = el("div", { style: "display:grid; gap:6px; margin:8px 0 4px;" }, []);
  box.appendChild(el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [
    el("button", { disabled: state.cleanRunning ? "" : null, onclick: () => runCleanVerify("quick") }, [`Check ${n} frame${n === 1 ? "" : "s"} · about ${quick}`]),
    el("button", { class: "small", disabled: state.cleanRunning ? "" : null, onclick: () => runCleanVerify("thorough") }, [`Thorough · reads every byte, about ${thorough}`]),
  ]));
  box.appendChild(el("div", { class: "hint", style: "margin:0;" }, [
    "The check compares each frame's size, FITS header and 8 slices spread through the image with its NAS copy. Thorough reads every byte over Wi-Fi. Checked frames move up to Ready to delete."]));
  const live = el("div", { id: "clean-check-live" }, []);
  box.appendChild(live);
  return box;
}

const CLEAN_PHASES = [
  ["list", "Listing the device", (st) => `Listed the device: ${st.listed ?? "every"} file${st.listed === 1 ? "" : "s"}`],
  ["check", "Checking every ticked file and its NAS copy", (st) => `Checked ${st.checked ?? st.phase_total ?? ""} file${st.checked === 1 ? "" : "s"}`],
  ["delete", null, (st) => `Deleted ${st.deleted ?? ""} file${st.deleted === 1 ? "" : "s"}`],
  ["confirm", "Listing the device again to confirm", () => "Listed the device again"],
];

function renderCleanActivity(snap) {
  // Delete progress as its four stages, each with its own count (proposal E); a check shows its own bar
  const box = document.getElementById("clean-activity");
  const st = (snap && snap.stats) || {};
  const pct = snap ? Math.min(100, Math.max(0, snap.percent_complete || 0)) : 0;
  if (snap && snap.kind === "verify") {
    box.innerHTML = "";
    const live = document.getElementById("clean-check-live");
    if (!live) return;
    live.innerHTML = "";
    live.appendChild(el("div", { class: "progress-bar" }, [el("div", { class: "progress-fill", style: `width:${pct.toFixed(0)}%` }, [])]));
    live.appendChild(el("div", { class: "hint", style: "margin:4px 0 0;" }, [
      st.files_total !== undefined ? `Checked ${st.files_done} of ${st.files_total}${st.eta_s !== null && st.eta_s !== undefined ? ` · about ${clock(st.eta_s)} left` : ""}` : "Starting…"]));
    return;
  }
  box.innerHTML = "";
  if (!snap) return;
  const order = CLEAN_PHASES.map((x) => x[0]);
  const at = order.indexOf(st.phase || "list");
  const dev = deviceName(state.cleanPreview);
  box.appendChild(el("div", { class: "phases" }, CLEAN_PHASES.map(([id, doing, did], n) => {
    const cls = n < at ? "done" : n === at ? "now" : "todo";
    const label = n < at ? did(st) : (doing || `Deleting from ${dev}`);
    const count = n === at && st.phase_total ? `${st.phase_done} / ${st.phase_total}` : "";
    return el("div", { class: `phase ${cls}` }, [el("span", { class: "i" }, [cls === "done" ? "✓" : cls === "now" ? "●" : "○"]), el("span", {}, [label]), el("span", { class: "c" }, [count])]);
  })));
  box.appendChild(el("div", { class: "progress-bar" }, [el("div", { class: "progress-fill", style: `width:${(st.phase_total ? (st.phase_done / st.phase_total) * 100 : pct).toFixed(0)}%` }, [])]));
}

function renderCleanResult(res) {
  // The Done panel stays up until dismissed (proposal E)
  const box = document.getElementById("clean-result");
  box.innerHTML = "";
  if (!res) return;
  const dev = state.cleanPreview ? deviceName(state.cleanPreview) : "the device";
  const ok = el("button", { class: "small", onclick: () => { state.cleanDone = null; renderCleanResult(null); persistSession(); } }, ["OK"]);
  if (res.stopped) {
    box.appendChild(el("div", { class: "done-panel stopped" }, [el("div", { class: "h" }, ["Nothing deleted"]), el("div", {}, [res.stopped]), el("div", {}, [ok])]));
    return;
  }
  if (res.verified !== undefined) {
    const bad = res.mismatch || res.nas_missing || res.read_errors;
    box.appendChild(el("div", { class: `done-panel${bad ? " problem" : ""}` }, [
      el("div", { class: "h" }, [`${bad ? "Checked, with differences:" : "✓ Checked"} ${res.verified} frame${res.verified === 1 ? "" : "s"} match their NAS copies`]),
      el("div", {}, [`${res.method === "thorough" ? "Thorough check (every byte)" : "Quick check"} · read ${gb(res.bytes)} in ${clock(res.seconds)}` +
        `${res.mismatch ? ` · ${res.mismatch} differ from the NAS copy (not offered for deletion)` : ""}${res.nas_missing ? ` · ${res.nas_missing} NAS cop${res.nas_missing === 1 ? "y" : "ies"} not found` : ""}` +
        `${res.read_errors ? ` · ${res.read_errors} couldn't be read` : ""}. Matching frames are now under Ready to delete.`]),
      el("div", {}, [ok]),
    ]));
    return;
  }
  const bad = res.failed.length || res.unexpected_missing.length || res.still_there.length;
  const panel = el("div", { class: `done-panel${bad ? " problem" : ""}` }, [
    el("div", { class: "h" }, [bad ? `Deleted ${res.deleted} file${res.deleted === 1 ? "" : "s"} from ${dev}, with problems` : `✓ Deleted ${res.deleted} file${res.deleted === 1 ? "" : "s"} (${gb(res.bytes_freed)}) from ${dev}`]),
    el("div", {}, [
      `${res.pruned.length ? `${res.pruned.length} empty folder${res.pruned.length === 1 ? "" : "s"} removed (${res.pruned.join(", ")}) · ` : ""}` +
      `${res.already_gone ? `${res.already_gone} already gone · ` : ""}${res.skipped.length ? `${res.skipped.length} kept (a check didn't pass) · ` : ""}` +
      `finished ${new Date().toLocaleTimeString(undefined, { timeStyle: "short" })} in ${clock(res.seconds)}. ` +
      (bad ? "The device listing afterwards doesn't match what was deleted: see below." : "The device was listed again afterwards: exactly these files are gone and nothing else changed."),
    ]),
  ]);
  const rows = (label, items) => items.length && panel.appendChild(el("div", {}, [`${label}:`, ...items.map((r) => el("div", { class: "session-path" }, [typeof r === "string" ? r : `${r.rel} — ${r.detail}`]))]));
  rows("Failed", res.failed);
  rows("Missing but not deleted by this run", res.unexpected_missing);
  rows("Still on the device after deleting", res.still_there);
  rows("Kept (a check didn't pass)", res.skipped);
  panel.appendChild(el("div", { style: "display:flex; gap:12px; align-items:center;" }, ["Log: ", logLink(res.log), ok,
    el("button", { class: "small", onclick: openStartOver }, ["Start over"])]));
  box.appendChild(panel);
}

async function watchCleanJob(jobId, kind) {
  state.cleanRunning = true;
  state.cleanDone = null;
  renderCleanResult(null);
  renderCleanStep();
  const holder = el("div", { style: "display:none;" }, [el("div", { class: "progress-fill" }, []), el("span", { class: "pct" }, []), el("span", { class: "msg" }, [])]);
  await pollJob(jobId, holder, async (snap) => {
    state.cleanRunning = false;
    renderCleanActivity(null);
    state.cleanTicked.clear();   // after a run nothing stays ticked (proposal E)
    if (snap.status === "succeeded") {
      state.cleanDone = snap.result;
      if (snap.result.cleanup) state.passed.add("clean");
    } else {
      state.cleanDone = { stopped: snap.error || "failed" };
    }
    await loadPlan(false);
    await loadCleanStep();
    renderCleanResult(state.cleanDone);
  }, (snap) => renderCleanActivity(Object.assign({ kind }, snap)));
}

async function runCleanVerify(method) {
  try {
    const { job_id } = await api("POST", "/api/cleanup/verify", { method });
    await watchCleanJob(job_id, "verify");
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
    sel.forEach((i) => { const g = c.groups.find((x) => x.items.includes(i)); per[g.label] = (per[g.label] || 0) + (i.files || 1 + (i.thumb ? 1 : 0)); });
    const files = Object.values(per).reduce((a, b) => a + b, 0);
    what = `Delete ${files} file${files === 1 ? "" : "s"} (${gb(sel.reduce((a, i) => a + i.size, 0))}) from ${deviceName(c)}?\n\n` +
      Object.entries(per).map(([k, n]) => `  ${n}  ${k}`).join("\n") + "\n\nThis can't be undone.";
  }
  if (!confirm(what)) return;
  try {
    const { job_id } = await api("POST", "/api/cleanup/run", { selected: sel.map((i) => i.rel) });
    await watchCleanJob(job_id, "cleanup");
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
}

async function answerDecision(d, value) {
  try {
    answered(await api("POST", "/api/answers", { [d.id]: value }), `dec-${d.id}`);
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
  const chips = el("div", { class: "seg", role: "radiogroup" }, d.options.map((o) => {
    const chosenName = isChosen(o) && isNew(d.resolved) ? d.resolved.slice(4) : "";
    const label = o.label + (isNew(o.value) && (chosenName || o.value.length > 4) ? ` (${chosenName || o.value.slice(4)})` : "");
    return el("button", {
      type: "button", role: "radio", "aria-checked": isChosen(o) ? "true" : "false",
      class: isChosen(o) ? "on" : "",
      onclick: (e) => {
        e.stopPropagation();
        if (isNew(o.value)) {
          // a new target needs its folder name: ask for it inline
          nameRow.style.display = "flex";
          nameRow.querySelector("input").focus();
          return;
        }
        if (o.value !== d.resolved || d.answer === null) answerDecision(d, o.value);
      },
    }, [isChosen(o) ? `✓ ${label}` : label]);
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
  const groupsById = Object.fromEntries(p.groups.map((g) => [g.id, g]));
  p.decisions.forEach((d) => {   // keep the planner's order: answering a decision doesn't move it
    const cls = d.answer !== null ? "answered" : d.resolved === null ? "" : "defaulted";
    const collapsed = state.collapsedDecisions.has(d.id);
    const files = d.items.map((src) => state.itemsBySrc[src]).filter(Boolean);
    const toggle = () => {
      if (collapsed) state.collapsedDecisions.delete(d.id); else state.collapsedDecisions.add(d.id);
      renderDecisions(state.plan);
      persistSession();
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
    // an append or a replacement shows its frames among their neighbours already on the NAS (proposal L)
    const g = groupsById[d.group];
    const withContext = (d.kind === "append" || d.kind === "name-clash") && g;
    const stripItems = withContext ? g.items.map((src) => state.itemsBySrc[src]).filter(Boolean) : files;
    const sess = g && g.kind === "lights" ? p.sessions.find((x) => x.groups.includes(g.id) && (!x.exists || x.lights || x.flats || x.rejected)) : null;
    const hasNas = stripItems.some((i) => !isNewFrame(i));
    const note = [];
    if (hasNas) note.push(`The ${files.length} new frame${files.length === 1 ? "" : "s"}, with 2 on either side that are already on the NAS (dimmed; their previews are read from the NAS).`);
    if (sess) note.push(el("span", {}, ["Quality is reviewed with the session: ", el("a", {
      href: "#", onclick: (e) => { e.preventDefault(); const t = document.querySelector(`[data-anchor="sess-${CSS.escape(sess.rel)}"]`); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); },
    }, [`${sess.rel} ↓`])]));
    const body = collapsed
      ? [el("div", { class: "decision-question collapsed" }, [d.question])]
      : [
        el("div", { class: "decision-question" }, [d.question]),
        decisionChips(d),
        frameStrip(`decision-${d.id}`, stripItems, { plain: true, focus: new Set(d.kind === "name-clash" ? d.items : []) }),
        note.length ? el("div", { class: "decision-context" }, ["ⓘ ", ...note.flatMap((n, k) => (k ? [" ", n] : [n]))]) : null,
        files.length ? toggleList(`show ${files.length} file${files.length === 1 ? "" : "s"}`, files.map((i) => itemRow(i))) : null,
      ];
    list.appendChild(el("div", { class: `decision ${cls}`, "data-anchor": `dec-${d.id}` }, [header, ...body]));
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
    const block = el("div", { class: "night-block", "data-anchor": `sess-${sess.rel}` }, []);
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

// ---------- jobs (astro-stacker's pollJob shape) ----------

async function pollJob(jobId, progressEl, onDone, onTick) {
  state.watched.add(jobId);
  try {
    await pollJobInner(jobId, progressEl, onDone, onTick);
  } finally {
    state.watched.delete(jobId);
  }
}

async function pollJobInner(jobId, progressEl, onDone, onTick) {
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
  // Scoring runs on Review (Chris, 2026-09-27), with its own bar at the top; Next waits for it
  const btn = document.getElementById("quality-run-btn");
  const bar = document.getElementById("quality-progress");
  const label = document.getElementById("quality-progress-label");
  btn.disabled = true;
  state.qualityRunning = true;
  label.textContent = "Scoring light frames for Review…";
  renderStepBar();
  await pollJob(jobId, bar, async (snap) => {
    btn.disabled = false;
    state.qualityRunning = false;
    bar.style.display = "none";
    await loadPlan(false);
    document.getElementById("quality-status").textContent = snap.status === "succeeded"
      ? `Scored ${snap.result.scored} frame(s) in ${clock(snap.result.seconds)}${snap.result.failed ? `, ${snap.result.failed} failed` : ""}.`
      : `Scoring failed: ${snap.error}`;
    renderStepBar();
  }, (snap) => {
    const st = snap.stats || {};
    label.textContent = st.to_score
      ? `Scoring light frames for Review: ${st.scored} / ${st.to_score} (frames already on the NAS in these sessions are scored alongside, to compare against)`
      : "Scoring light frames for Review…";
    renderStepBar();
  });
}

function needsScoring() {
  // staged light frames this run still has no score for
  const p = state.plan;
  return !!p && p.items.some((i) => i.kind === "Light" && !i.quality && (i.staged || i.has_staged_copy) &&
    ["copy", "append", "rejected", "needs-decision"].includes(i.action));
}

async function runQuality() {
  try {
    const { job_id } = await api("POST", "/api/quality/run");
    if (!job_id) { document.getElementById("quality-status").textContent = "Every staged light frame is scored."; return; }
    await watchQualityJob(job_id);
  } catch (e) {
    document.getElementById("quality-status").textContent = String(e.message || e);
  }
}

async function resumeRunningJob(quiet) {
  // Picks up a job started earlier or from another window (astro-stacker's active-jobs idea). `quiet`: don't
  // switch steps (used by the background poll).
  try {
    const jobsNow = (await api("GET", "/jobs")).jobs.filter((j) => j.status === "running" && !state.watched.has(j.id));
    const go = (step) => { if (!quiet) { state.activeStep = step; showActiveStep(); } };
    const stage = jobsNow.find((j) => j.kind === "stage");
    if (stage) { go("stage"); await watchStageJob(stage.id); }
    const copying = jobsNow.find((j) => j.kind === "copy");
    if (copying) { go("copy"); await watchCopyJob(copying.id); }
    const cleaning = jobsNow.find((j) => j.kind === "cleanup" || j.kind === "verify");
    if (cleaning) { go("clean"); await watchCleanJob(cleaning.id, cleaning.kind); }
    const cataloguing = jobsNow.find((j) => j.kind === "catalog");
    if (cataloguing) { go("catalog"); await watchCatalogJob(cataloguing.id); }
    const running = jobsNow.find((j) => j.kind === "quality");
    if (running) await watchQualityJob(running.id);
  } catch (e) { /* ignore */ }
}

// ---------- start over (proposal J) ----------

let startOverPreview = null;

function renderStartOverBody() {
  const pv = startOverPreview;
  const body = document.getElementById("so-body");
  body.innerHTML = "";
  if (!pv) { body.appendChild(el("div", { class: "hint" }, ["Loading…"])); return; }
  const keepAnswers = document.getElementById("so-keep-answers").checked;
  const go = document.getElementById("so-go");
  if (pv.busy || pv.blocked) {
    body.appendChild(el("div", { class: "session-mismatch-warning" }, [`⚠ Not now: ${pv.busy ? `a ${pv.busy} run is in progress; wait for it to finish` : pv.blocked}.`]));
    go.disabled = true;
    return;
  }
  go.disabled = false;
  const dev = pv.device ? (pv.device.nickname || pv.device.label) : null;
  const cleared = [
    pv.staged.files ? `${pv.staged.files} staged frame${pv.staged.files === 1 ? "" : "s"} not yet copied (${gb(pv.staged.bytes)}), still on the ASIAIR; staging them again reads them over Wi-Fi${pv.staged.restage_s ? ` (about ${clock(pv.staged.restage_s)})` : ""}` : "Nothing staged to clear",
    `Previews and quality scores of the device's frames${pv.scores ? ` (${pv.scores} scored)` : ""}`,
    `What was left out on Select (${pv.left_out} frame${pv.left_out === 1 ? "" : "s"}) and kept or rejected on Review (${pv.kept_or_rejected} frame${pv.kept_or_rejected === 1 ? "" : "s"})`,
    keepAnswers ? null : `Decision answers (${pv.decision_answers}): target names, replace, keep or release`,
    `Progress through the steps${dev ? `, and the connection to ${dev}` : ""}`,
  ].filter(Boolean);
  const st = pv.staged || {};
  const kept = [
    st.kept_gone ? `${st.kept_gone} staged frame${st.kept_gone === 1 ? "" : "s"} no longer on the ASIAIR: the staged copy may be the last one` : null,
    st.kept_unchecked ? `${st.kept_unchecked} staged frame${st.kept_unchecked === 1 ? "" : "s"} from ${(st.unreachable || []).join(", ") || "a device"}, which couldn't be reached to check` : null,
    "Everything on the NAS, its catalog, and the logs",
    keepAnswers ? `Decision answers (${pv.decision_answers})` : null,
    "Recent devices, Verify results, and the sensitivity (σ)",
    "Nothing is deleted from the ASIAIR",
  ].filter(Boolean);
  body.appendChild(el("h4", { class: "cleared" }, ["Cleared"]));
  body.appendChild(el("ul", {}, cleared.map((t) => el("li", {}, [t]))));
  body.appendChild(el("h4", { class: "kept" }, ["Kept"]));
  body.appendChild(el("ul", {}, kept.map((t) => el("li", {}, [t]))));
}

async function openStartOver() {
  startOverPreview = null;
  document.getElementById("so-keep-answers").checked = false;   // answers are cleared by default (Chris)
  document.getElementById("start-over-modal").style.display = "flex";
  renderStartOverBody();
  try {
    startOverPreview = await api("GET", "/api/start-over");
  } catch (e) {
    startOverPreview = { blocked: String(e.message || e), staged: {} };
  }
  renderStartOverBody();
  document.getElementById("so-cancel").focus();
}

function closeStartOver() {
  document.getElementById("start-over-modal").style.display = "none";
}

async function doStartOver() {
  const go = document.getElementById("so-go");
  go.disabled = true;
  go.textContent = "Starting over…";
  try {
    await api("POST", "/api/start-over", { forget_answers: !document.getElementById("so-keep-answers").checked });
  } catch (e) {
    startOverPreview = Object.assign({}, startOverPreview, { blocked: String(e.message || e) });
    renderStartOverBody();
    go.textContent = "Start over";
    return;
  }
  go.textContent = "Start over";
  closeStartOver();
  // back to a fresh Connect: nothing finished, nothing ticked, no device
  Object.assign(state, { plan: null, copyPreview: null, catalogPreview: null, cleanPreview: null, cleanDone: null,
    lastStage: null, itemsBySrc: {}, activeStep: "connect", connectBusy: null,
    connectMsg: { ok: true, text: "✓ Started over. Connect to a device to begin." } });
  state.passed.clear();
  state.cleanTicked.clear();
  state.collapsedDecisions.clear();
  ["stage-result", "copy-result", "catalog-result", "clean-result", "clean-activity", "review-summary"].forEach((id) => { document.getElementById(id).innerHTML = ""; });
  state.sessionSaved = JSON.stringify(sessionData());
  state.sessionSeen = Date.now() / 1000;
  await loadHealth();
  showActiveStep();
}

document.getElementById("start-over-btn").addEventListener("click", openStartOver);
document.getElementById("so-cancel").addEventListener("click", closeStartOver);
document.getElementById("so-go").addEventListener("click", doStartOver);
document.getElementById("so-keep-answers").addEventListener("change", renderStartOverBody);
document.getElementById("start-over-modal").addEventListener("click", (e) => { if (e.target.id === "start-over-modal") closeStartOver(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("start-over-modal").style.display !== "none") closeStartOver();
});

// ---------- load ----------

function applyPlan(plan, keepScroll) {
  const y = window.scrollY;
  state.plan = plan;
  state.itemsBySrc = Object.fromEntries(plan.items.map((i) => [i.src, i]));
  renderScan();
  renderSelect();
  renderStage();
  renderReview();
  renderStepper();
  if (!keepScroll) requestAnimationFrame(() => window.scrollTo(window.scrollX, y));
}

async function loadHealth() {
  const banner = document.getElementById("app-banner");
  try {
    state.devices = await api("GET", "/api/devices");
    state.health = await api("GET", "/health");
    banner.style.display = "none";
    if (state.health.version) document.getElementById("app-version").textContent = `v${state.health.version}`;
  } catch (e) {
    // the app itself isn't answering: say so across the top (you only see this when something is wrong)
    banner.textContent = "✕ Can't reach the astro-ingest server. Check that it's running, then reload this page.";
    banner.style.display = "flex";
  }
  renderDevicePill();
  renderConnect();
}

function renderDevicePill(searching) {
  // One indicator (proposal B): the device you're connected to, with a coloured dot
  const pill = document.getElementById("device-pill");
  const dot = pill.querySelector(".dot");
  const text = pill.querySelector(".pill-text");
  const h = state.health;
  const dv = state.devices;
  pill.querySelectorAll(".ip").forEach((n) => n.remove());
  let cls = "", label = "Not connected", ip = null;
  if (searching) { cls = "warn"; label = "Searching the network…"; }
  else if (dv && dv.source_mode === "local") {
    cls = h && h.source_online ? "ok" : "danger";
    label = `Local folder ${dv.local_root ? basename(dv.local_root) : ""}`;
  } else if (dv && dv.remembered) {
    const name = dv.remembered.nickname || dv.remembered.label || "ASIAIR";
    ip = dv.remembered.host;
    if (h && h.source_online) { cls = "ok"; label = name; }
    else if (h) { cls = "danger"; label = `${name} isn't answering`; }
    else { label = name; }
  }
  dot.className = `dot${cls ? ` ${cls}` : ""}`;
  text.textContent = label;
  if (ip) pill.appendChild(el("span", { class: "ip" }, [ip]));
  pill.title = h ? h.source : "";
}

async function loadPlan(refresh) {
  setStepBadge("review-status-badge", "", refresh ? "scanning…" : "loading…");
  state.scanning = true;
  renderScan();
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
  state.scanning = false;
  showActiveStep();
}

document.getElementById("rescan-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  await loadPlan(true);
  e.target.disabled = false;
});
document.getElementById("stepbar-next").addEventListener("click", () => {
  const i = STEPS.indexOf(state.activeStep);
  if (i < STEPS.length - 1) goTo(STEPS[i + 1], state.activeStep);
});
document.getElementById("stepbar-delete").addEventListener("click", () => runCleanDelete());
document.getElementById("clean-recommended-btn").addEventListener("click", () => {
  (state.cleanPreview ? state.cleanPreview.groups : []).filter((g) => g.recommended && g.selectable)
    .forEach((g) => g.items.forEach((i) => state.cleanTicked.add(i.rel)));
  renderCleanStep();
});
document.getElementById("stage-run-btn").addEventListener("click", runStage);
document.getElementById("copy-run-btn").addEventListener("click", runCopy);
document.getElementById("catalog-run-btn").addEventListener("click", runCatalog);
document.getElementById("brand-link").addEventListener("click", (e) => { e.preventDefault(); state.activeStep = "connect"; showActiveStep(); });
document.getElementById("quality-run-btn").addEventListener("click", runQuality);
// σ slider: flags and charts follow while dragging (computed locally); the server applies it on release
document.getElementById("quality-sigma").addEventListener("input", (e) => {
  state.sigmaLive = Number(e.target.value);
  renderReview();
});
document.getElementById("quality-sigma").addEventListener("change", async (e) => {
  const plan = await api("POST", "/api/answers", { "quality-sigma": String(e.target.value) });
  state.sigmaLive = null;
  answered(plan);
});

(async function init() {
  showActiveStep();
  await loadHealth();
  try { applySession(await api("GET", "/api/session")); } catch (e) { /* no session yet */ }
  const want = state.activeStep;
  await loadPlan(false);
  // back to where this session was, if that step can be shown now; otherwise Connect
  state.activeStep = stepStatus(want).available ? want : "connect";
  state.sessionReady = true;
  showActiveStep();
  resumeRunningJob();
  setInterval(followOtherWindows, 4000);
  setInterval(watchDevice, 15000);
})();
