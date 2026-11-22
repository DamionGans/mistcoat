// Background worker: decodes images, runs the engine, encodes the result.
// Everything happens on the device - no image ever leaves it.
import { fromRGBA, resizeLongest, toRGBA } from "./engine/imaging.js";
import { buildXmp, injectJpegXmp, injectPngXmp } from "./engine/metadata.js";
import { preview, protect } from "./engine/pipeline.js";
import { gpuStatus, setGpuEnabled } from "./engine/gpu.js";
import {
  DISPLAY_P3, DISPLAY_P3_ICC, SRGB, embedIcc, extractIcc, iccName, makeConverter, parseIcc, sniff,
} from "./engine/color.js";

// ----------------------------------------------------------- browser codec

function canvas(w, h) {
  const c = new OffscreenCanvas(w, h);
  return [c, c.getContext("2d", { willReadFrequently: true })];
}

async function decodeBlob(blob, w, h) {
  const bmp = await createImageBitmap(blob, { colorSpaceConversion: "none", premultiplyAlpha: "none" });
  const [, ctx] = canvas(w, h);
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  return fromRGBA(ctx.getImageData(0, 0, w, h).data, w, h).rgb;
}

async function roundtrip(rgb, w, h, type, quality) {
  const [c, ctx] = canvas(w, h);
  ctx.putImageData(new ImageData(toRGBA(rgb, w, h), w, h), 0, 0);
  const blob = await c.convertToBlob({ type, quality });
  // Browsers silently fall back to PNG for formats they cannot encode
  // (Safari has no WebP encoder) - treat that as "unavailable", not as a
  // perfect, lossless re-encode.
  if (blob.type !== type) return null;
  return decodeBlob(blob, w, h);
}

async function rescale(rgb, w, h, factor, quality) {
  const sw = Math.max(1, Math.round(w * factor)), sh = Math.max(1, Math.round(h * factor));
  const [src, sctx] = canvas(w, h);
  sctx.putImageData(new ImageData(toRGBA(rgb, w, h), w, h), 0, 0);
  const [small, smctx] = canvas(sw, sh);
  smctx.imageSmoothingQuality = "high";
  smctx.drawImage(src, 0, 0, sw, sh);
  const blob = await small.convertToBlob({ type: "image/jpeg", quality });
  const bmp = await createImageBitmap(blob);
  const [, bctx] = canvas(w, h);
  bctx.imageSmoothingQuality = "high";
  bctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  return fromRGBA(bctx.getImageData(0, 0, w, h).data, w, h).rgb;
}

// Same split as desktop: harden against one set, measure against another.
const codec = {
  training: [
    { name: "JPEG q72", fn: (x, w, h) => roundtrip(x, w, h, "image/jpeg", 0.72) },
    { name: "WebP q82", fn: (x, w, h) => roundtrip(x, w, h, "image/webp", 0.82) },
    { name: "Resize 80% + JPEG", fn: (x, w, h) => rescale(x, w, h, 0.8, 0.75) },
  ],
  heldOut: [
    { name: "JPEG q85", fn: (x, w, h) => roundtrip(x, w, h, "image/jpeg", 0.85) },
    { name: "JPEG q60", fn: (x, w, h) => roundtrip(x, w, h, "image/jpeg", 0.6) },
    { name: "WebP q75", fn: (x, w, h) => roundtrip(x, w, h, "image/webp", 0.75) },
    { name: "Resize 75% + JPEG", fn: (x, w, h) => rescale(x, w, h, 0.75, 0.8) },
  ],
};

// ------------------------------------------------------------------ loading

let p3Ok = null;
/** Whether this browser can hold Display P3 values in a canvas. */
function p3Canvas() {
  if (p3Ok === null) {
    try {
      const ctx = new OffscreenCanvas(1, 1).getContext("2d", { colorSpace: "display-p3" });
      p3Ok = ctx?.getContextAttributes?.().colorSpace === "display-p3";
    } catch { p3Ok = false; }
  }
  return p3Ok;
}

/** Decide how to keep an image's colours (see engine/color.js). */
async function colourPlan(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const kind = sniff(bytes);
  const icc = kind ? await extractIcc(bytes) : null;
  const parsed = icc ? parseIcc(icc) : null;
  if (kind && (!icc || parsed)) {
    // Untagged (sRGB) or a matrix profile: keep the file's own values.
    return { kind, raw: true, profile: parsed || SRGB, icc: parsed ? icc : null,
      name: parsed ? parsed.name : "sRGB", note: "" };
  }
  const p3 = p3Canvas();
  const name = icc ? (iccName(icc) || "its colour profile") : null;
  return {
    kind, raw: false, profile: p3 ? DISPLAY_P3 : SRGB, icc: p3 ? DISPLAY_P3_ICC : null,
    name: p3 ? "Display P3" : "sRGB",
    // HEIC and friends carry Display P3 already: nothing worth saying.
    note: name ? `Colours are converted from ${name} to ${p3 ? "Display P3" : "sRGB"}, ` +
      "because phones can't keep that profile. Use the desktop app to keep it exactly." : "",
  };
}

async function loadFile(file, maxPixels) {
  const plan = await colourPlan(file);
  const bmp = await createImageBitmap(file, {
    imageOrientation: "from-image", premultiplyAlpha: "none",
    colorSpaceConversion: plan.raw ? "none" : "default",
  });
  const ow = bmp.width, oh = bmp.height;
  let w = ow, h = oh, note = "";
  if (ow * oh > maxPixels) {
    const s = Math.sqrt(maxPixels / (ow * oh));
    w = Math.max(1, Math.floor(ow * s));
    h = Math.max(1, Math.floor(oh * s));
    note = `This image is ${(ow * oh / 1e6).toFixed(0)} MP; it is processed at ` +
      `${w}x${h} (${(w * h / 1e6).toFixed(1)} MP) to fit in a phone's memory. ` +
      "Use the desktop app for full resolution.";
  }
  // Raw values go through an sRGB canvas untouched; converted ones land in
  // the widest space the canvas supports.
  const space = !plan.raw && plan.profile === DISPLAY_P3 ? "display-p3" : "srgb";
  const c = new OffscreenCanvas(w, h);
  const ctx = c.getContext("2d", { willReadFrequently: true, colorSpace: space });
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  const { rgb, alpha } = fromRGBA(ctx.getImageData(0, 0, w, h, { colorSpace: space }).data, w, h);
  return { rgb, alpha, w, h, ow, oh, note: [note, plan.note].filter(Boolean).join(" "), plan };
}

// ------------------------------------------------------------------- export

/** "Same as original" picks the source's format where a phone can write it. */
function outputType(setting, file, kind) {
  if (setting === "jpeg" || setting === "png" || setting === "webp") return setting;
  if (kind) return kind;                                    // jpeg, png or webp
  if (/hei[cf]|avif/i.test(file.type) || /\.(hei[cf]|avif)$/i.test(file.name)) return "jpeg";
  return "png";                                             // TIFF, BMP, GIF: lossless
}

async function encodeOutput(rgb, alpha, w, h, settings, file, plan) {
  let fmt = outputType(settings.outputFormat, file, plan.kind);
  let pixels = rgb, a = alpha;
  if (fmt === "jpeg" && alpha) {
    // JPEG has no alpha: composite onto white rather than letting it go black.
    pixels = new Float32Array(rgb.length);
    for (let i = 0; i < w * h; i++) {
      const t = alpha[i] / 255;
      for (let c = 0; c < 3; c++) pixels[i * 3 + c] = rgb[i * 3 + c] * t + (1 - t);
    }
    a = null;
  }
  // The values are written as they are; the profile embedded below says
  // what they mean.
  const [c, ctx] = canvas(w, h);
  ctx.putImageData(new ImageData(toRGBA(pixels, w, h, a), w, h), 0, 0);
  let blob = await c.convertToBlob({ type: `image/${fmt}`, quality: settings.jpegQuality });
  if (blob.type !== `image/${fmt}`) {                       // no WebP encoder (Safari)
    fmt = "png";
    blob = await c.convertToBlob({ type: "image/png" });
  }
  let bytes = new Uint8Array(await blob.arrayBuffer());
  const xmp = settings.writeOptout || settings.credit || settings.copyright
    ? buildXmp({ writeOptout: settings.writeOptout, credit: settings.credit, copyright: settings.copyright })
    : "";
  if (xmp && fmt === "jpeg") bytes = injectJpegXmp(bytes, xmp);
  if (xmp && fmt === "png") bytes = injectPngXmp(bytes, xmp);
  if (plan.icc || (fmt === "webp" && xmp)) bytes = await embedIcc(bytes, plan.icc, { webpXmp: xmp });
  return { bytes, type: `image/${fmt}`, ext: fmt === "jpeg" ? "jpg" : fmt };
}

/** Before/after RGBA for the screen: converted from the image's profile to
 *  the space the app draws in. */
function forDisplay(plan, display) {
  const target = display === "display-p3" ? DISPLAY_P3 : SRGB;
  return plan.profile === target ? null : makeConverter(plan.profile, target);
}

// ----------------------------------------------------------------- messages

let current = null; // { rgb, w, h, small }

self.onmessage = async (e) => {
  if (e.data?.settings) setGpuEnabled(e.data.settings.useGpu !== false);
  const msg = e.data;
  const reply = (payload, transfer = []) => self.postMessage({ id: msg.id, ...payload }, transfer);
  try {
    if (msg.type === "gpu-status") {
      reply({ type: "gpu-status", status: await gpuStatus() });
    } else if (msg.type === "load") {
      const img = await loadFile(msg.file, msg.maxPixels);
      current = { ...img, small: resizeLongest(img.rgb, img.w, img.h, 1100),
        toScreen: forDisplay(img.plan, msg.display) };
      reply({ type: "loaded", w: img.w, h: img.h, ow: img.ow, oh: img.oh, note: img.note,
        colour: img.plan.name });
    } else if (msg.type === "preview") {
      if (!current) throw new Error("No image loaded");
      const s = current.small;
      const r = await preview(s.rgb, s.w, s.h, msg.settings, codec, 1100);
      const before = toRGBA(r.original, r.w, r.h);
      const after = toRGBA(r.rgb, r.w, r.h);
      if (current.toScreen) { current.toScreen(before); current.toScreen(after); }
      reply({ type: "preview", w: r.w, h: r.h, before, after, metrics: r.metrics },
        [before.buffer, after.buffer]);
    } else if (msg.type === "export") {
      const img = await loadFile(msg.file, msg.maxPixels);
      const progress = (stage, frac) =>
        self.postMessage({ id: msg.id, type: "progress", stage, frac });
      const r = await protect(img.rgb, img.w, img.h, msg.settings, { codec, progress });
      const out = await encodeOutput(r.rgb, img.alpha, img.w, img.h, msg.settings, msg.file, img.plan);
      reply({ type: "exported", bytes: out.bytes, mime: out.type, ext: out.ext,
        metrics: r.metrics, seconds: r.seconds, w: img.w, h: img.h, note: img.note },
      [out.bytes.buffer]);
    }
  } catch (err) {
    reply({ type: "error", message: String(err && err.message || err) });
  }
};

