/* astro-ingest frontend — plain JS, no build step, no framework (same approach and helpers as astro-stacker).
 * Talks to the FastAPI backend in astro_ingest/api.py. All state is derived from the server's plan, so a
 * reload never gets out of sync with the source. Frame review (cards, lightbox, metric strips, flagged ±2
 * collapsing) follows astro-stacker's static/app.js at commit f31cbcb.
 */

const STEPS = ["connect", "scan", "review", "copy", "file", "clean"];
const STEP_LABELS = { connect: "Connect", scan: "Scan", review: "Review", copy: "Copy & verify", file: "File", clean: "Clean up" };
const STEP_PHASE = { copy: 4, file: 5, clean: 6 };  // steps not built yet: shown, disabled, tagged with their phase
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
  "unrecognized": "unrecognized",
  "orphan-thumb": "orphan thumbnail",
  "ignored": "ignored folder",
};
const CLEANUP_LABELS = {
  "after-verify": "Deleted from the ASIAIR after checksum-verified copies and your approval",
  "callout": "Offered for deletion, each one called out",
  "blocked": "Blocked until you decide",
  "pending": "Waiting on a decision",
  "never": "Never touched",
};

const state = {
  activeStep: "review",
  health: null,
  plan: null,
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
  link.addEventListener("click", () => { list.style.display = list.style.display === "none" ? "block" : "none"; });
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
  return `/api/preview?rel=${encodeURIComponent(item.src)}&size=${size}`;
}

// ---------- frame quality (flags come from the server at the chosen sensitivity) ----------

function isFlagged(item) {
  return !!(item.quality && item.quality.flagged);
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
  const sigma = state.plan ? state.plan.sigma : 4;
  const part = (label, key, value) => `${label}: ${value}${(z[key] || 0) >= sigma ? ` ⚠ ${z[key].toFixed(1)}σ` : ""}`;
  return [
    part("Stars", "star_count", s.star_count),
    part("FWHM", "fwhm", s.fwhm !== null ? s.fwhm.toFixed(2) : "—"),
    part("Eccentricity", "roundness", s.roundness !== null ? s.roundness.toFixed(3) : "—"),
    part("SNR", "snr", s.snr !== null ? s.snr.toFixed(0) : "—"),
    part("Background", "background", Math.round(s.background)),
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
  openLightbox(previewUrl(item, FULL), `${basename(item.src)}${q ? ` — ${date} ${time}` : ""}`, {
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
  if (isFrame(item)) {
    card.appendChild(el("img", { src: previewUrl(item, THUMB), alt: basename(item.src), onclick: () => openLightboxFor(items.filter(isFrame), items.filter(isFrame).indexOf(item)) }, []));
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
  const sigma = state.plan.sigma;
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
    ]));
  }
  return wrap;
}

// ---------- stepper ----------

function stepStatus(step) {
  if (STEP_PHASE[step]) return { available: false, complete: false };
  const planned = !!state.plan;
  switch (step) {
    case "connect": return { available: true, complete: !!(state.health && state.health.source_online) };
    case "scan": return { available: true, complete: planned };
    case "review": return { available: planned, complete: planned && state.plan.summary.decisions_open === 0 };
    default: return { available: false, complete: false };
  }
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
}

// ---------- connect / scan ----------

function renderConnect() {
  const h = state.health;
  const body = document.getElementById("connect-body");
  body.innerHTML = "";
  if (!h) return;
  setStepBadge("connect-status-badge", h.source_online ? "ok" : "danger", h.source_online ? "online" : "offline");
  body.appendChild(el("div", { class: "session-path" }, [h.source]));
}

function renderScan() {
  const p = state.plan;
  const body = document.getElementById("scan-body");
  body.innerHTML = "";
  if (!p) return;
  setStepBadge("scan-status-badge", "ok", `scanned ${p.scanned_at.replace("T", " ")}`);
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

// ---------- review ----------

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
    stat(s.decisions_open, "decisions open", s.decisions_open ? "warn" : "ok"),
  ]));
  const sigmaInput = document.getElementById("quality-sigma");
  if (document.activeElement !== sigmaInput) sigmaInput.value = p.sigma;
  document.getElementById("quality-status").textContent = scored
    ? `${scored} light frame${scored === 1 ? "" : "s"} scored · default σ ${p.sigma_default}`
    : "No frames scored yet.";

  renderDecisions(p);
  renderSessions(p);
  renderLibrary(p);
  renderCleanup(p);
}

function renderDecisions(p) {
  const card = document.getElementById("decisions-card");
  const list = document.getElementById("decisions-list");
  list.innerHTML = "";
  card.style.display = p.decisions.length ? "block" : "none";
  const open = p.decisions.filter((d) => d.resolved === null).length;
  setStepBadge("decisions-badge", open ? "warn" : "ok", open ? `${open} open` : "all have answers or defaults");
  const rank = (d) => (d.answer !== null ? 2 : d.resolved === null ? 0 : 1);  // open first, then defaulted, then answered
  [...p.decisions].sort((a, b) => rank(a) - rank(b)).forEach((d) => {
    const cls = d.answer !== null ? "answered" : d.resolved === null ? "" : "defaulted";
    // A new-target answer carries the folder Chris chose ("new:NeedleGalaxy-NGC4565"), which differs from the
    // proposal in the option ("new:NGC4565"): match on the "new:" prefix and show the chosen name.
    const isChosen = (o) => o.value === d.resolved || (o.value.startsWith("new:") && d.resolved !== null && d.resolved.startsWith("new:"));
    const newName = (o) => (isChosen(o) && d.resolved.startsWith("new:") ? d.resolved : o.value).slice(4);
    const chips = el("div", { class: "checklist" }, d.options.map((o) => el("label", { class: `chip${isChosen(o) ? " checked" : ""}` }, [
      el("input", Object.assign({ type: "radio", name: d.id, disabled: "disabled" }, isChosen(o) ? { checked: "checked" } : {}), []),
      o.label + (o.value.startsWith("new:") && newName(o) ? ` (${newName(o)})` : ""),
    ])));
    const files = d.items.map((src) => state.itemsBySrc[src]).filter(Boolean);
    list.appendChild(el("div", { class: `decision ${cls}` }, [
      el("div", {}, [
        el("span", { class: `badge ${d.resolved === null ? "warn" : "accent"}` }, [d.kind]),
        " ",
        el("span", { class: "hint" }, [d.answer !== null ? "answered" : d.resolved === null ? "needs your answer" : `default: ${d.resolved}`]),
      ]),
      el("div", { class: "decision-question" }, [d.question]),
      chips,
      frameStrip(`decision-${d.id}`, files),
      files.length ? toggleList(`show ${files.length} file${files.length === 1 ? "" : "s"}`, files.map((i) => itemRow(i))) : null,
    ]));
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

async function pollJob(jobId, progressEl, onDone) {
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
    const running = (await api("GET", "/jobs")).jobs.find((j) => j.kind === "quality" && j.status === "running");
    if (running) await watchQualityJob(running.id);
  } catch (e) { /* ignore */ }
}

// ---------- load ----------

function applyPlan(plan) {
  const y = window.scrollY;
  state.plan = plan;
  state.itemsBySrc = Object.fromEntries(plan.items.map((i) => [i.src, i]));
  renderScan();
  renderReview();
  requestAnimationFrame(() => window.scrollTo(window.scrollX, y));
}

async function loadHealth() {
  try {
    state.health = await api("GET", "/health");
    setStepBadge("health-badge", state.health.status === "ok" ? "ok" : "danger", state.health.status === "ok" ? "online" : "error");
    if (state.health.version) document.getElementById("app-version").textContent = `v${state.health.version}`;
    const src = document.getElementById("source-badge");
    src.textContent = state.health.source_online ? "ASIAIR: online" : "ASIAIR: offline";
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
    applyPlan(await api("GET", "/api/plan"));
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
document.getElementById("scan-next-btn").addEventListener("click", () => { state.activeStep = "review"; showActiveStep(); });
document.getElementById("brand-link").addEventListener("click", (e) => { e.preventDefault(); state.activeStep = "review"; showActiveStep(); });
document.getElementById("quality-run-btn").addEventListener("click", runQuality);
document.getElementById("quality-sigma-btn").addEventListener("click", async () => {
  const v = document.getElementById("quality-sigma").value;
  applyPlan(await api("POST", "/api/answers", { "quality-sigma": v }));
});

(async function init() {
  showActiveStep();
  await loadHealth();
  await loadPlan(false);
  resumeRunningJob();
})();
