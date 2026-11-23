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
    if (saved) {
      // Version 2 made "same as the original" the default output format.
      if ((saved.settingsVersion ?? 1) < 2) { saved.outputFormat = "source"; saved.settingsVersion = 2; }
      return { ...base, ...saved, enabled: { ...base.enabled, ...saved.enabled } };
    }
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
    const r = await call({ type: "load", file: state.files[i].file, maxPixels: state.maxPixels,
      display: DISPLAY_SPACE });
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

// The preview is drawn in Display P3 on screens that show it, so wide-gamut
// art is not clipped; the worker converts each image's own profile to this.
const DISPLAY_SPACE = (() => {
  try {
    if (!matchMedia("(color-gamut: p3)").matches) return "srgb";
    const ctx = document.createElement("canvas").getContext("2d", { colorSpace: "display-p3" });
    return ctx?.getContextAttributes?.().colorSpace === "display-p3" ? "display-p3" : "srgb";
  } catch { return "srgb"; }
})();

function drawCanvas(c, rgba, w, h) {
  c.width = w;
  c.height = h;
  const data = new Uint8ClampedArray(rgba.buffer ?? rgba);
  const opts = { colorSpace: DISPLAY_SPACE };
  c.getContext("2d", opts).putImageData(new ImageData(data, w, h, opts), 0, 0);
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
    const r = $("stage").getBoundingClientRect();
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
    $("zoom").textContent = cmd(z ? "Fit to screen" : "View 1:1");
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
  $("gpu").addEventListener("change", (ev) => {
    state.settings.useGpu = ev.target.checked;
    saveSettings();
    refreshGpuStatus();
  });
  refreshGpuStatus();
  $("credit").addEventListener("input", (ev) => { state.settings.credit = ev.target.value; saveSettings(); });
  $("copyright").addEventListener("input", (ev) => { state.settings.copyright = ev.target.value; saveSettings(); });
  $("format").addEventListener("change", (ev) => { state.settings.outputFormat = ev.target.value; saveSettings(); });

  // Visible watermark. Each change re-renders the preview only while it is on.
  const wmChanged = () => changed(state.settings.watermark);
  $("wm").addEventListener("change", (ev) => { state.settings.watermark = ev.target.checked; changed(); });
  $("wm-text").addEventListener("input", (ev) => { state.settings.watermarkText = ev.target.value; wmChanged(); });
  $("wm-layout").addEventListener("change", (ev) => { state.settings.watermarkLayout = ev.target.value; wmChanged(); });
  $("wm-resistance").addEventListener("change", (ev) => { state.settings.watermarkResistance = ev.target.value; wmChanged(); });
  $("wm-opacity").addEventListener("input", (ev) => { state.settings.watermarkOpacity = Number(ev.target.value) / 100; wmChanged(); });
  $("maxpx").addEventListener("change", (ev) => {
    state.maxPixels = Number(ev.target.value);
    const i = state.index;
    state.index = -1;
    if (i >= 0) select(i);
  });
  syncControls();
}

// Which processor does the work. Probing happens in the worker, where the
// engine runs; the answer only changes if the GPU is lost.
async function refreshGpuStatus() {
  try {
    const { status } = await call({ type: "gpu-status", settings: state.settings });
    const el = $("gpu-status");
    if (status.available) {
      el.textContent = status.enabled ? `Faster processing on ${status.name}.`
        : "Off: using the processor.";
    } else {
      el.textContent = `${status.reason}; using the processor.`;
      $("gpu").disabled = true;
    }
  } catch { /* status is informational only */ }
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
  $("gpu").checked = s.useGpu !== false;
  $("credit").value = s.credit;
  $("copyright").value = s.copyright;
  $("format").value = s.outputFormat;
  $("wm").checked = !!s.watermark;
  $("wm-options").disabled = !s.watermark;
  if (document.activeElement !== $("wm-text")) $("wm-text").value = s.watermarkText ?? "SAMPLE";
  $("wm-layout").value = s.watermarkLayout ?? "tiled";
  $("wm-resistance").value = s.watermarkResistance ?? "strong";
  $("wm-hint").textContent = `${WM_HINTS[s.watermarkResistance] ?? WM_HINTS.strong} ` +
    "No visible watermark is impossible to remove.";
  $("wm-opacity").value = Math.round((s.watermarkOpacity ?? 0.5) * 100);
  $("wm-opacity-out").textContent = `${Math.round((s.watermarkOpacity ?? 0.5) * 100)}%`;
}

const WM_HINTS = {
  plain: "An even grid of identical marks. Neatest, but watermark-removal tools handle this kind easily.",
  strong: "Every mark differs slightly in position, angle, size and opacity, with a soft textured " +
    "edge, so removal tools have no single shape to learn.",
  maximum: "As Strong, plus a second row of smaller marks, and the lettering is woven into the " +
    "picture's texture. Hardest to remove cleanly, but busier to look at.",
};

function updateGoLabel() {
  const n = state.files.length;
  $("go").textContent = cmd(n > 1 ? `Protect and save ${n} images` : "Protect and save");
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
      const acts = document.createElement("span");
      acts.className = "acts";
      const save = document.createElement("button");
      save.className = "btn";
      save.type = "button";
      save.textContent = cmd("Save…");
      save.addEventListener("click", () => saveOutputs([out], { ask: true }));
      acts.append(save);
      if (canShareFiles()) {
        const share = document.createElement("button");
        share.className = "btn ghost";
        share.type = "button";
        share.textContent = cmd("Share");
        share.addEventListener("click", () => shareOutputs([out]));
        acts.append(share);
      }
      li.append(acts);
    } catch (err) {
      li.querySelector(".meta").textContent = `Failed: ${err.message}`;
      li.querySelector(".meta").classList.add("err");
    }
  }
  setProgress(1, `Done. ${state.outputs.length} of ${n} protected. Keep your originals; share only these copies.`);
  $("save-all").hidden = state.outputs.length < 2;
  $("go").disabled = false;
  state.busy = false;
  // Save straight away where the user said to; with "ask", only open the
  // picker by itself for a single image (a batch waits for "Save all").
  if (state.outputs.length && (state.settings.saveTo !== "ask" || state.outputs.length === 1)) {
    saveOutputs(state.outputs);
  }
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

// Where protected copies go. Native apps use the system pickers through the
// app's MistcoatFiles plugin (see android/.../MistcoatFilesPlugin.java and
// ios/App/App/MistcoatFiles.swift); browsers use the File System Access API
// where it exists, otherwise an ordinary download.

const filesPlugin = () => window.Capacitor?.Plugins?.MistcoatFiles;
const platformName = () => window.mistcoatPlatform?.name ?? "web";
let webFolder = null;      // a FileSystemDirectoryHandle, for this session only

/** Which "save to" choices this device can offer. */
function saveCapabilities() {
  if (native()) {
    return { gallery: true, folder: platformName() === "android" };
  }
  return { gallery: false, folder: "showDirectoryPicker" in window };
}

async function writeToCache(outs) {
  const { Filesystem } = window.Capacitor.Plugins;
  const files = [];
  for (const o of outs) {
    const r = await Filesystem.writeFile({ path: o.name, data: toBase64(o.bytes), directory: "CACHE" });
    files.push({ path: r.uri, name: o.name, mime: o.mime });
  }
  return files;
}

function showSaveNote(text, isError = false) {
  const el = $("save-note");
  el.hidden = !text;
  el.textContent = text || "";
  el.classList.toggle("err", isError);
}

/**
 * Save ``outs``. ``ask`` forces a location picker regardless of the setting
 * (the per-image "Save…" button).
 */
async function saveOutputs(outs, { ask = false } = {}) {
  const mode = ask ? "ask" : state.settings.saveTo;
  const n = outs.length, what = n === 1 ? outs[0].name : `${n} images`;
  try {
    let where;
    if (native()) {
      const plugin = filesPlugin();
      if (!plugin) throw new Error("Saving is not available in this build");
      const files = await writeToCache(outs);
      if (mode === "gallery") {
        where = (await plugin.saveToGallery({ files })).location;
      } else if (mode === "folder" && state.settings.saveFolder) {
        try {
          where = (await plugin.saveToFolder({ folder: state.settings.saveFolder.id, files })).location;
        } catch (err) {
          if (err.code !== "NO_ACCESS") throw err;
          state.settings.saveFolder = null; saveSettings(); syncSaveControls();
          throw new Error("Access to the chosen folder was lost. Choose it again under Advanced.");
        }
      } else if (n === 1) {
        where = (await plugin.saveAs({ file: files[0] })).location;   // name + place
      } else if (platformName() === "android") {
        const picked = await plugin.pickFolder();                      // one folder for all
        where = (await plugin.saveToFolder({ folder: picked.folder, files })).location;
      } else {
        where = (await plugin.saveToFolder({ files })).location;       // iOS export picker
      }
    } else {
      where = await saveInBrowser(outs, mode);
    }
    if (where) showSaveNote(`Saved ${what} to ${where}.`);
  } catch (err) {
    if (err?.code === "CANCELLED" || err?.name === "AbortError") return;
    showSaveNote(`Could not save: ${err.message || err}`, true);
  }
}

async function saveInBrowser(outs, mode) {
  if (mode === "folder" && webFolder) {
    try {
      for (const o of outs) await writeHandle(await uniqueFile(webFolder, o.name), o);
      return webFolder.name;
    } catch (err) {
      if (err.name !== "NotAllowedError") throw err;
      webFolder = null; syncSaveControls();
    }
  }
  if (outs.length === 1 && "showSaveFilePicker" in window) {
    const o = outs[0];
    const ext = o.name.split(".").pop();
    const handle = await showSaveFilePicker({
      suggestedName: o.name,
      types: [{ description: "Image", accept: { [o.mime]: [`.${ext}`] } }],
    });
    await writeHandle(handle, o);
    return handle.name;
  }
  if (outs.length > 1 && "showDirectoryPicker" in window) {
    const dir = await showDirectoryPicker({ mode: "readwrite", startIn: "pictures" });
    for (const o of outs) await writeHandle(await uniqueFile(dir, o.name), o);
    return dir.name;
  }
  // Plain download: the browser's own "save as" or downloads folder.
  for (const o of outs) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new File([o.bytes], o.name, { type: o.mime }));
    a.download = o.name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
  }
  return "your downloads";
}

async function writeHandle(handle, o) {
  const w = await handle.createWritable();
  await w.write(o.bytes);
  await w.close();
}

/** A file in ``dir`` that does not overwrite anything: name, name (2), ... */
async function uniqueFile(dir, name) {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : "";
  for (let i = 1; i < 1000; i++) {
    const candidate = i === 1 ? name : `${stem} (${i})${ext}`;
    try { await dir.getFileHandle(candidate); } catch { return dir.getFileHandle(candidate, { create: true }); }
  }
  throw new Error("Too many files with that name");
}

async function shareOutputs(outs) {
  try {
    if (native()) {
      const files = await writeToCache(outs);
      await window.Capacitor.Plugins.Share.share({ title: "Protected artwork", files: files.map((f) => f.path) });
      return;
    }
    await navigator.share({ files: outs.map((o) => new File([o.bytes], o.name, { type: o.mime })), title: "Protected artwork" });
  } catch (err) {
    if (err?.name !== "AbortError" && !/cancel/i.test(String(err?.message))) showSaveNote(`Could not share: ${err.message || err}`, true);
  }
}

async function chooseFolder() {
  try {
    if (native()) {
      const picked = await filesPlugin().pickFolder();
      state.settings.saveFolder = { id: picked.folder, name: picked.name };
    } else {
      webFolder = await showDirectoryPicker({ mode: "readwrite", startIn: "pictures" });
      state.settings.saveFolder = { id: "web", name: webFolder.name };
    }
    state.settings.saveTo = "folder";
    saveSettings();
  } catch (err) {
    if (err?.code !== "CANCELLED" && err?.name !== "AbortError") showSaveNote(`Could not use that folder: ${err.message || err}`, true);
  }
  syncSaveControls();
}

function syncSaveControls() {
  const caps = saveCapabilities();
  const sel = $("save-to");
  for (const opt of sel.options) {
    if (opt.hasAttribute("data-native")) {
      opt.hidden = opt.disabled = !caps.gallery;
      opt.textContent = platformName() === "android" ? "Gallery (Pictures/Mistcoat)" : "Photos";
    }
    if (opt.hasAttribute("data-folder")) opt.hidden = opt.disabled = !caps.folder;
  }
  let mode = state.settings.saveTo;
  if ((mode === "gallery" && !caps.gallery) || (mode === "folder" && !caps.folder)) mode = "ask";
  // A browser folder is only remembered for the session.
  if (!native() && mode === "folder" && !webFolder) state.settings.saveFolder = null;
  sel.value = mode;
  const folder = mode === "folder";
  $("folder-row").hidden = !folder;
  $("folder-name").textContent = state.settings.saveFolder?.name
    ? `Folder: ${state.settings.saveFolder.name}` : "No folder chosen yet";
  $("save-all").textContent = cmd(mode === "gallery"
    ? (platformName() === "android" ? "Save all to Gallery" : "Save all to Photos")
    : folder && state.settings.saveFolder ? `Save all to ${state.settings.saveFolder.name}` : "Save all…");
}

// --------------------------------------------------------------------- misc

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showError(message) {
  $("imgnote").hidden = false;
  $("imgnote").textContent = message;
}

// ------------------------------------------------------------ platform

// Button wording in the platform's convention (title case on iOS).
function cmd(text) {
  return window.mistcoatPlatform ? window.mistcoatPlatform.command(text) : text;
}

function setupPlatformChrome() {
  // Static button labels in the HTML: rewrite their text, keep their inputs.
  for (const el of document.querySelectorAll(".btn, .viewer-tools button")) {
    for (const node of el.childNodes) {
      if (node.nodeType === Node.TEXT_NODE && node.nodeValue.trim()) {
        node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), cmd(node.nodeValue.trim()));
      }
    }
  }
  // Browser / status bar colour matches the page background, as native
  // apps' bars do, and follows light/dark switches.
  const sync = () => {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    for (const m of document.querySelectorAll('meta[name="theme-color"]')) m.remove();
    const meta = document.createElement("meta");
    meta.name = "theme-color";
    meta.content = bg;
    document.head.append(meta);
  };
  sync();
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", sync);
  const bar = document.querySelector(".top");
  addEventListener("scroll", () => bar.classList.toggle("scrolled", scrollY > 4), { passive: true });
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
  $("save-to").addEventListener("change", (ev) => {
    state.settings.saveTo = ev.target.value;
    saveSettings();
    if (ev.target.value === "folder" && !state.settings.saveFolder) chooseFolder();
    syncSaveControls();
  });
  $("choose-folder").addEventListener("click", chooseFolder);
  $("about").addEventListener("click", () => $("about-dialog").showModal());
}

setupInputs();
setupViewer();
setupControls();
setupPlatformChrome();
syncSaveControls();

if ("serviceWorker" in navigator && !native() && location.protocol !== "file:") {
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline support is optional */ });
}

// Test hook: lets automated checks drive the app without a file picker.
window.__mistcoat = { addFiles, state, call };
