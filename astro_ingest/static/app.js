/* astro-ingest frontend — plain JS, no build step, no framework (same approach and helpers as astro-stacker).
 * Talks to the FastAPI backend in astro_ingest/api.py. All state is derived from the server's plan, so a
 * reload never gets out of sync with the source.
 */

const STEPS = ["connect", "scan", "review", "copy", "file", "clean"];
const STEP_LABELS = { connect: "Connect", scan: "Scan", review: "Review", copy: "Copy & verify", file: "File", clean: "Clean up" };
const STEP_PHASE = { copy: 4, file: 5, clean: 6 };  // steps not built yet: shown, disabled, tagged with their phase

const ACTION_LABELS = {
  "copy": "copy",
  "append": "append",
  "already-ingested": "already on NAS",
  "needs-decision": "needs decision",
  "skip": "left on ASIAIR",
  "calib-without-lights": "no lights found",
  "over-cap": "over 10-frame cap",
  "not-kept": "not kept",
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

function gb(bytes) {
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

// ---------- lightbox (stacker markup, simplified: no frame navigation yet) ----------

function openLightbox(url, title) {
  const img = document.getElementById("lightbox-img");
  const scroll = document.getElementById("lightbox-scroll");
  img.src = url;
  scroll.classList.remove("zoomed");
  document.getElementById("lightbox-caption").textContent = title;
  document.getElementById("lightbox").classList.add("open");
}

document.getElementById("lightbox").addEventListener("click", (e) => {
  if (e.target.id === "lightbox") e.currentTarget.classList.remove("open");
});
document.getElementById("lightbox-scroll").addEventListener("click", (e) => {
  e.currentTarget.classList.toggle("zoomed");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.getElementById("lightbox").classList.remove("open");
});

function thumbStrip(items, max) {
  const withThumbs = items.filter((i) => i.thumb);
  if (!withThumbs.length) return null;
  const strip = el("div", { class: "frame-strip" }, []);
  withThumbs.slice(0, max).forEach((i) => {
    const url = `/api/thumb?rel=${encodeURIComponent(i.thumb)}`;
    strip.appendChild(el("div", { class: "frame-card" }, [
      el("img", { src: url, loading: "lazy", alt: basename(i.src), onclick: () => openLightbox(url, basename(i.src)) }, []),
      el("div", { class: "frame-meta" }, [
        el("div", { class: "frame-time", title: i.src }, [frameLabel(i.src)]),
        el("div", { class: "frame-name", title: i.src }, [actionLabel(i)]),
      ]),
    ]));
  });
  if (withThumbs.length > max) {
    strip.appendChild(el("div", { class: "frame-ellipsis" }, [`+${withThumbs.length - max} more`]));
  }
  return strip;
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

// ---------- connect ----------

function renderConnect() {
  const h = state.health;
  const body = document.getElementById("connect-body");
  body.innerHTML = "";
  if (!h) return;
  setStepBadge("connect-status-badge", h.source_online ? "ok" : "danger", h.source_online ? "online" : "offline");
  body.appendChild(el("div", { class: "session-path" }, [h.source]));
}

// ---------- scan ----------

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
  Object.keys(byTop).sort().forEach((top) => {
    row.appendChild(stat(byTop[top].files, `${top} · ${gb(byTop[top].bytes)}`));
  });
  body.appendChild(row);
}

// ---------- review ----------

function renderReview() {
  const p = state.plan;
  if (!p) return;
  const s = p.summary;
  setStepBadge("review-status-badge", s.decisions_open ? "warn" : "ok",
    s.decisions_open ? `${s.decisions_open} decision${s.decisions_open === 1 ? "" : "s"} open` : "ready");

  const summary = document.getElementById("review-summary");
  summary.innerHTML = "";
  summary.appendChild(el("div", { class: "stat-row" }, [
    stat(s.copy_files, `files to copy · ${gb(s.copy_bytes)}`, "ok"),
    stat(s.sessions_new, "new sessions"),
    stat(p.sessions.filter((x) => x.exists && (x.lights || x.flats)).length, "sessions to append to"),
    stat(s.actions["already-ingested"] || 0, "already on the NAS"),
    stat(s.decisions_open, "decisions open", s.decisions_open ? "warn" : "ok"),
  ]));

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
  const itemsBySrc = Object.fromEntries(p.items.map((i) => [i.src, i]));
  // open first, then defaulted, then answered
  const rank = (d) => (d.answer !== null ? 2 : d.resolved === null ? 0 : 1);
  [...p.decisions].sort((a, b) => rank(a) - rank(b)).forEach((d) => {
    const cls = d.answer !== null ? "answered" : d.resolved === null ? "" : "defaulted";
    // A new-target answer carries the folder Chris chose ("new:NeedleGalaxy-NGC4565"), which differs from the
    // proposal in the option ("new:NGC4565"): match on the "new:" prefix and show the chosen name.
    const isChosen = (o) => o.value === d.resolved ||
      (o.value.startsWith("new:") && d.resolved !== null && d.resolved.startsWith("new:"));
    const newName = (o) => (isChosen(o) && d.resolved.startsWith("new:") ? d.resolved : o.value).slice(4);
    const chips = el("div", { class: "checklist" }, d.options.map((o) => el("label", {
      class: `chip${isChosen(o) ? " checked" : ""}`,
    }, [
      el("input", Object.assign({ type: "radio", name: d.id, disabled: "disabled" }, isChosen(o) ? { checked: "checked" } : {}), []),
      o.label + (o.value.startsWith("new:") && newName(o) ? ` (${newName(o)})` : ""),
    ])));
    const files = d.items.map((src) => itemsBySrc[src]).filter(Boolean);
    const open = d.resolved === null;
    list.appendChild(el("div", { class: `decision ${cls}${open ? "" : " compact"}` }, [
      el("div", {}, [
        el("span", { class: `badge ${d.resolved === null ? "warn" : "accent"}` }, [d.kind]),
        " ",
        el("span", { class: "hint" }, [d.answer !== null ? "answered" : d.resolved === null ? "needs your answer" : `default: ${d.resolved}`]),
      ]),
      el("div", { class: "decision-question" }, [d.question]),
      chips,
      open ? thumbStrip(files, 6) : null,
      files.length ? toggleList(`show ${files.length} file${files.length === 1 ? "" : "s"}`, files.map((i) => itemRow(i))) : null,
    ]));
  });
}

function renderSessions(p) {
  const list = document.getElementById("sessions-list");
  list.innerHTML = "";
  const active = p.sessions.filter((x) => !x.exists || x.lights || x.flats);
  const quiet = p.sessions.filter((x) => x.exists && !x.lights && !x.flats);
  setStepBadge("sessions-badge", "", `${active.length} receiving frames`);
  if (!active.length) list.appendChild(el("div", { class: "empty-hint" }, ["Nothing new to copy into any session."]));
  active.forEach((sess) => {
    const prefix = `${sess.rel}/`;
    const items = p.items.filter((i) => i.dsts.some((d) => d.startsWith(prefix)));
    const badge = sess.new_target ? ["accent", "new target"] : sess.exists ? ["", "on the NAS: append"] : ["ok", "new session"];
    list.appendChild(el("div", { class: "night-block" }, [
      el("div", { style: "display:flex; gap:8px; align-items:center; flex-wrap:wrap;" }, [
        el("span", { class: `badge ${badge[0]}` }, [badge[1]]),
        el("span", { class: "session-path" }, [sess.rel]),
      ]),
      el("div", { class: "session-meta" }, [
        `+${sess.lights} lights${sess.replaced ? ` (${sess.replaced} replacing damaged copies)` : ""} · +${sess.flats} flats · ${gb(sess.bytes)}`,
        sess.siblings.length ? ` · sibling nights: ${sess.siblings.join(", ")}` : "",
      ]),
      ...sess.warnings.map((w) => el("div", { class: "session-mismatch-warning" }, [`⚠ ${w}`])),
      thumbStrip(items.filter((i) => i.kind === "Light"), 8),
      toggleList(`show ${items.length} file${items.length === 1 ? "" : "s"} and destinations`,
        items.map((i) => el("div", { title: i.reason || "" }, [el("span", { class: "act" }, [actionLabel(i)]), `${basename(i.src)}  →  ${i.dsts.filter((d) => d.startsWith(prefix)).join(", ")}`]))),
    ]));
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
      toggleList("show files", byFolder[folder].map((i) => itemRow(i))),
    ]));
  });
}

function renderCleanup(p) {
  const box = document.getElementById("cleanup-preview");
  box.innerHTML = "";
  const order = ["after-verify", "callout", "blocked", "pending", "never"];
  order.forEach((key) => {
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

// ---------- load ----------

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
    state.plan = await api("GET", "/api/plan");
  } catch (e) {
    state.plan = null;
    setStepBadge("review-status-badge", "danger", "error");
    document.getElementById("review-summary").innerHTML = "";
    document.getElementById("review-summary").appendChild(el("div", { class: "error-banner" }, ["✕ ", String(e.message || e)]));
  }
  renderScan();
  renderReview();
  if (!state.plan) state.activeStep = "connect";
  showActiveStep();
}

document.getElementById("rescan-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  await loadPlan(true);
  e.target.disabled = false;
});
document.getElementById("scan-next-btn").addEventListener("click", () => { state.activeStep = "review"; showActiveStep(); });
document.getElementById("brand-link").addEventListener("click", (e) => { e.preventDefault(); state.activeStep = "review"; showActiveStep(); });

(async function init() {
  showActiveStep();
  await loadHealth();
  await loadPlan(false);
})();
