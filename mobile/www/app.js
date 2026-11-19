// Mistcoat UI controller. The engine runs in worker.js; this file only manages
// state, the comparison viewer and saving.
import { ALL_PASSES } from "./engine/passes.js";
import {
  applyPreset, DEFAULT_PRESET, defaultSettings, matchingPreset, PRESETS, PRESETS_BY_KEY,
} from "./engine/presets.js";

const $ = (id) => document.getElementById(id);
const PREVIEW_DEBOUNCE_MS = 350;
const STORE_KEY = "mistcoat.settings.v1";

// ----------------------------------------------------------------- state

const state = {
  files: [],          // { file, url, name }
  index: -1,
  settings: loadSettings(),
  maxPixels: 8_000_000,
  split: 0.5,
  loadedToken: 0,
  previewToken: 0,
  busy: false,
  outputs: [],        // { name, bytes, mime }
};

function loadSettings() {
  const base = applyPreset(defaultSettings(), DEFAULT_PRESET);
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    if (saved) return { ...base, ...saved, enabled: { ...base.enabled, ...saved.enabled } };
  } catch { /* private mode or blocked storage: defaults are fine */ }
  return base;
}

function saveSettings() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state.settings)); } catch { /* ignore */ }
}

// ---------------------------------------------------------------- worker

const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
let nextId = 1;
const pending = new Map();

worker.onmessage = (e) => {
  const msg = e.data;
  const p = pending.get(msg.id);
  if (!p) return;
  if (msg.type === "progress") { p.onProgress?.(msg.stage, msg.frac); return; }
  pending.delete(msg.id);
  if (msg.type === "error") p.reject(new Error(msg.message));
  else p.resolve(msg);
};

function call(payload, onProgress) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, ...payload });
  });
}

// ----------------------------------------------------------------- files

function addFiles(list) {
  const imgs = [...list].filter((f) => f.type.startsWith("image/") || /\.(png|jpe?g|webp|heic|heif|tiff?|bmp|gif)$/i.test(f.name));
  if (!imgs.length) return;
  for (const file of imgs) state.files.push({ file, url: URL.createObjectURL(file), name: file.name });
  $("empty").hidden = true;
  $("work").hidden = false;
  renderThumbs();
  if (state.index < 0) select(0);
  updateGoLabel();
}

function removeFile(i) {
  URL.revokeObjectURL(state.files[i].url);
  state.files.splice(i, 1);
  if (!state.files.length) {
    state.index = -1;
    $("work").hidden = true;
    $("empty").hidden = false;
    return;
  }
  const next = Math.min(state.index, state.files.length - 1);
  state.index = -1;
  renderThumbs();
  select(next);
  updateGoLabel();
}

function renderThumbs() {
  const box = $("thumbs");
  box.replaceChildren(...state.files.map((f, i) => {
    const b = document.createElement("button");
    b.className = "thumb";
    b.type = "button";
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", String(i === state.index));
    b.setAttribute("aria-label", f.name);
    const img = document.createElement("img");
    img.src = f.url;
    img.alt = "";
    img.loading = "lazy";
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "×";
    x.setAttribute("aria-label", `Remove ${f.name}`);
    x.addEventListener("click", (ev) => { ev.stopPropagation(); removeFile(i); });
    b.append(img, x);
    b.addEventListener("click", () => select(i));
    return b;
  }));
  box.hidden = state.files.length < 2;
}

async function select(i) {
  if (i === state.index) return;
  state.index = i;
  renderThumbs();
  const token = ++state.loadedToken;
  setBusy(true);
  try {
    const r = await call({ type: "load", file: state.files[i].file, maxPixels: state.maxPixels });
    if (token !== state.loadedToken) return;
    $("imgnote").hidden = !r.note;
    $("imgnote").textContent = r.note;
    schedulePreview(0);
  } catch (err) {
    if (token === state.loadedToken) showError(`Could not open ${state.files[i].name}: ${err.message}`);
    setBusy(false);
  }
}

// --------------------------------------------------------------- preview

let previewTimer = 0;
function schedulePreview(delay = PREVIEW_DEBOUNCE_MS) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(renderPreview, delay);
}

async function renderPreview() {
  if (state.index < 0) return;
  const token = ++state.previewToken;
  setBusy(true);
  try {
    const r = await call({ type: "preview", settings: state.settings });
    if (token !== state.previewToken) return; // superseded by a newer change
    drawCanvas($("before"), r.before, r.w, r.h);
    drawCanvas($("after"), r.after, r.w, r.h);
    applySplit();
    showMetrics(r.metrics);
  } catch (err) {
    if (token === state.previewToken) showError(err.message);
  } finally {
    if (token === state.previewToken) setBusy(false);
  }
}

function drawCanvas(c, rgba, w, h) {
  c.width = w;
  c.height = h;
  c.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(rgba.buffer ?? rgba), w, h), 0, 0);
}

function setBusy(on) { $("busy").hidden = !on; }

function showMetrics(m) {
  if (!m) return;
  const vis = $("m-vis"), dur = $("m-dur");
  vis.textContent = m.visibilityLabel;
  vis.className = "v " + (m.psnr >= 36 ? "good" : "warn");
  dur.textContent = `${Math.round(m.survival * 100)}%`;
  dur.title = m.survivalLabel;
  dur.className = "v " + (m.survival >= 0.5 ? "good" : "warn");
  $("m-psnr").textContent = `${m.psnr.toFixed(1)} dB`;
  $("m-peak").textContent = `${Math.round(m.maxDelta)}/255`;
}

// ---------------------------------------------------------------- viewer

function applySplit() {
  $("after").style.clipPath = `inset(0 0 0 ${state.split * 100}%)`;
  $("handle").style.left = `${state.split * 100}%`;
}

function setupViewer() {
  const v = $("viewer");
  let dragging = false;
  const move = (ev) => {
    if (v.classList.contains("zoomed")) return;
    const r = v.getBoundingClientRect();
    state.split = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
    applySplit();
  };
  v.addEventListener("pointerdown", (ev) => {
    if (v.classList.contains("zoomed")) return;
    dragging = true;
    v.setPointerCapture(ev.pointerId);
    move(ev);
  });
  v.addEventListener("pointermove", (ev) => { if (dragging) move(ev); });
  v.addEventListener("pointerup", () => { dragging = false; });
  v.addEventListener("pointercancel", () => { dragging = false; });

  const hold = $("hold");
  const on = (ev) => { ev.preventDefault(); v.classList.add("peek"); };
  const off = () => v.classList.remove("peek");
  hold.addEventListener("pointerdown", on);
  for (const t of ["pointerup", "pointerleave", "pointercancel"]) hold.addEventListener(t, off);
  hold.addEventListener("contextmenu", (ev) => ev.preventDefault());
  document.addEventListener("keydown", (ev) => {
    if (ev.code === "Space" && ev.target === document.body) { ev.preventDefault(); v.classList.add("peek"); }
  });
  document.addEventListener("keyup", (ev) => { if (ev.code === "Space") off(); });

  $("zoom").addEventListener("click", () => {
    const z = v.classList.toggle("zoomed");
    $("zoom").setAttribute("aria-pressed", String(z));
    $("zoom").textContent = z ? "Fit to screen" : "View 1:1";
    // In 1:1 the wipe is replaced by "hold to compare" - easier on a phone.
    $("after").style.clipPath = z ? "none" : "";
    if (!z) applySplit();
  });
}

// -------------------------------------------------------------- controls

function setupControls() {
  const seg = $("presets");
  seg.replaceChildren(...PRESETS.map((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = p.label;
    b.dataset.key = p.key;
    b.setAttribute("role", "radio");
    b.addEventListener("click", () => {
      state.settings = applyPreset(state.settings, p.key);
      changed();
    });
    return b;
  }));

  $("passes").replaceChildren(...ALL_PASSES.map((p) => {
    const l = document.createElement("label");
    l.className = "check";
    l.innerHTML = `<input type="checkbox" data-pass="${p.name}"><span>${p.label}<small class="muted">${p.targets}</small></span>`;
    l.title = p.blurb;
    l.querySelector("input").addEventListener("change", (ev) => {
      state.settings.enabled[p.name] = ev.target.checked;
      changed();
    });
    return l;
  }));

  $("strength").addEventListener("input", (ev) => {
    state.settings.strength = Number(ev.target.value) / 100;
    changed();
  });
  $("harden").addEventListener("change", (ev) => { state.settings.harden = ev.target.checked; changed(false); });
  $("optout").addEventListener("change", (ev) => { state.settings.writeOptout = ev.target.checked; saveSettings(); });
  $("credit").addEventListener("input", (ev) => { state.settings.credit = ev.target.value; saveSettings(); });
  $("copyright").addEventListener("input", (ev) => { state.settings.copyright = ev.target.value; saveSettings(); });
  $("format").addEventListener("change", (ev) => { state.settings.outputFormat = ev.target.value; saveSettings(); });
  $("maxpx").addEventListener("change", (ev) => {
    state.maxPixels = Number(ev.target.value);
    const i = state.index;
    state.index = -1;
    if (i >= 0) select(i);
  });
  syncControls();
}

function changed(refresh = true) {
  saveSettings();
  syncControls();
  if (refresh) schedulePreview();
}

function syncControls() {
  const s = state.settings;
  const match = matchingPreset(s);
  for (const b of $("presets").children) b.setAttribute("aria-checked", String(match?.key === b.dataset.key));
  $("preset-blurb").textContent = match ? match.blurb :
    "Custom settings. Check the figures above before saving a batch.";
  $("strength").value = Math.round(s.strength * 100);
  $("strength-out").textContent = `${Math.round(s.strength * 100)}%`;
  for (const cb of $("passes").querySelectorAll("input")) cb.checked = !!s.enabled[cb.dataset.pass];
  $("harden").checked = s.harden;
  $("optout").checked = s.writeOptout;
  $("credit").value = s.credit;
  $("copyright").value = s.copyright;
  $("format").value = s.outputFormat;
}

function updateGoLabel() {
  const n = state.files.length;
  $("go").textContent = n > 1 ? `Protect and save ${n} images` : "Protect and save";
}

// ------------------------------------------------------------------ export

function outName(name, ext) {
  const stem = name.replace(/\.[^.]+$/, "");
  return `${stem}_protected.${ext}`;
}

async function runBatch() {
  if (state.busy || !state.files.length) return;
  state.busy = true;
  $("go").disabled = true;
  $("progress").hidden = false;
  $("results").hidden = false;
  $("result-list").replaceChildren();
  state.outputs = [];
  const n = state.files.length;

  for (let i = 0; i < n; i++) {
    const f = state.files[i];
    const li = document.createElement("li");
    li.innerHTML = `<span class="name">${escapeHtml(f.name)}<span class="meta">Working…</span></span>`;
    $("result-list").append(li);
    try {
      const r = await call({ type: "export", file: f.file, settings: state.settings, maxPixels: state.maxPixels },
        (stage, frac) => setProgress((i + frac) / n, `${i + 1}/${n} · ${f.name} · ${stage}`));
      const name = outName(f.name, r.ext);
      const out = { name, bytes: r.bytes, mime: r.mime };
      state.outputs.push(out);
      const m = r.metrics;
      li.querySelector(".name").innerHTML = `${escapeHtml(name)}<span class="meta">` +
        `${r.w}×${r.h} · ${m.psnr.toFixed(1)} dB · ${Math.round(m.survival * 100)}% durable · ${r.seconds.toFixed(1)}s</span>`;
      const save = document.createElement("button");
      save.className = "btn";
      save.type = "button";
      save.textContent = canShareFiles() ? "Share" : "Save";
      save.addEventListener("click", () => saveOutputs([out]));
      li.append(save);
    } catch (err) {
      li.querySelector(".meta").textContent = `Failed: ${err.message}`;
      li.querySelector(".meta").classList.add("err");
    }
  }
  setProgress(1, `Done. ${state.outputs.length} of ${n} protected. Keep your originals; share only these copies.`);
  $("save-all").hidden = state.outputs.length < 2;
  $("go").disabled = false;
  state.busy = false;
  if (state.outputs.length === 1) saveOutputs(state.outputs).catch(() => {});
}

function setProgress(frac, text) {
  $("bar-fill").style.width = `${Math.round(frac * 100)}%`;
  $("progress-text").textContent = text;
}

// ------------------------------------------------------------------- saving

const native = () => !!window.Capacitor?.isNativePlatform?.();
const coarse = () => matchMedia("(pointer: coarse)").matches;

function canShareFiles() {
  if (native()) return true;
  try {
    return coarse() && !!navigator.canShare?.({ files: [new File([new Uint8Array(1)], "x.png", { type: "image/png" })] });
  } catch { return false; }
}

function toBase64(bytes) {
  let s = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) s += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(s);
}

async function saveOutputs(outs) {
  if (native()) {
    // Capacitor: write to the app cache, then hand the files to the system
    // share sheet (Save to Photos / Files / Drive / AirDrop ...).
    const { Filesystem, Share } = window.Capacitor.Plugins;
    const uris = [];
    for (const o of outs) {
      const r = await Filesystem.writeFile({ path: o.name, data: toBase64(o.bytes), directory: "CACHE" });
      uris.push(r.uri);
    }
    await Share.share({ title: "Protected artwork", files: uris });
    return;
  }
  const files = outs.map((o) => new File([o.bytes], o.name, { type: o.mime }));
  if (canShareFiles() && navigator.canShare({ files })) {
    try { await navigator.share({ files, title: "Protected artwork" }); return; }
    catch (err) { if (err.name === "AbortError") return; }
  }
  for (const f of files) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(f);
    a.download = f.name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
  }
}

// --------------------------------------------------------------------- misc

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showError(message) {
  $("imgnote").hidden = false;
  $("imgnote").textContent = message;
}

function setupInputs() {
  for (const id of ["picker", "picker-more"]) {
    $(id).addEventListener("change", (ev) => { addFiles(ev.target.files); ev.target.value = ""; });
  }
  const dz = $("empty");
  for (const t of ["dragenter", "dragover"]) {
    document.addEventListener(t, (ev) => { ev.preventDefault(); dz.classList.add("drag"); });
  }
  for (const t of ["dragleave", "drop"]) document.addEventListener(t, () => dz.classList.remove("drag"));
  document.addEventListener("drop", (ev) => { ev.preventDefault(); addFiles(ev.dataTransfer.files); });
  $("go").addEventListener("click", runBatch);
  $("save-all").addEventListener("click", () => saveOutputs(state.outputs));
  $("about").addEventListener("click", () => $("about-dialog").showModal());
}

setupInputs();
setupViewer();
setupControls();

if ("serviceWorker" in navigator && !native() && location.protocol !== "file:") {
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline support is optional */ });
}

// Test hook: lets automated checks drive the app without a file picker.
window.__mistcoat = { addFiles, state, call };
