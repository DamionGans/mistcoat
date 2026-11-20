// Background worker: decodes images, runs the engine, encodes the result.
// Everything happens on the device - no image ever leaves it.
import { fromRGBA, resizeLongest, toRGBA } from "./engine/imaging.js";
import { buildXmp, injectJpegXmp, injectPngXmp } from "./engine/metadata.js";
import { preview, protect } from "./engine/pipeline.js";
import { gpuStatus, setGpuEnabled } from "./engine/gpu.js";

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

async function loadFile(file, maxPixels) {
  const bmp = await createImageBitmap(file, {
    imageOrientation: "from-image", colorSpaceConversion: "default", premultiplyAlpha: "none",
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
  const [, ctx] = canvas(w, h);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  const { rgb, alpha } = fromRGBA(ctx.getImageData(0, 0, w, h).data, w, h);
  return { rgb, alpha, w, h, ow, oh, note };
}

// ------------------------------------------------------------------- export

async function encodeOutput(rgb, alpha, w, h, settings) {
  const jpeg = settings.outputFormat === "jpeg";
  let pixels = rgb, a = alpha;
  if (jpeg && alpha) {
    // JPEG has no alpha: composite onto white rather than letting it go black.
    pixels = new Float32Array(rgb.length);
    for (let i = 0; i < w * h; i++) {
      const t = alpha[i] / 255;
      for (let c = 0; c < 3; c++) pixels[i * 3 + c] = rgb[i * 3 + c] * t + (1 - t);
    }
    a = null;
  }
  const [c, ctx] = canvas(w, h);
  ctx.putImageData(new ImageData(toRGBA(pixels, w, h, a), w, h), 0, 0);
  const type = jpeg ? "image/jpeg" : "image/png";
  const blob = await c.convertToBlob({ type, quality: settings.jpegQuality });
  let bytes = new Uint8Array(await blob.arrayBuffer());
  if (settings.writeOptout || settings.credit || settings.copyright) {
    const xmp = buildXmp({ writeOptout: settings.writeOptout, credit: settings.credit,
      copyright: settings.copyright });
    bytes = jpeg ? injectJpegXmp(bytes, xmp) : injectPngXmp(bytes, xmp);
  }
  return { bytes, type, ext: jpeg ? "jpg" : "png" };
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
      current = { ...img, small: resizeLongest(img.rgb, img.w, img.h, 1100) };
      reply({ type: "loaded", w: img.w, h: img.h, ow: img.ow, oh: img.oh, note: img.note });
    } else if (msg.type === "preview") {
      if (!current) throw new Error("No image loaded");
      const s = current.small;
      const r = await preview(s.rgb, s.w, s.h, msg.settings, codec, 1100);
      const before = toRGBA(r.original, r.w, r.h);
      const after = toRGBA(r.rgb, r.w, r.h);
      reply({ type: "preview", w: r.w, h: r.h, before, after, metrics: r.metrics },
        [before.buffer, after.buffer]);
    } else if (msg.type === "export") {
      const img = await loadFile(msg.file, msg.maxPixels);
      const progress = (stage, frac) =>
        self.postMessage({ id: msg.id, type: "progress", stage, frac });
      const r = await protect(img.rgb, img.w, img.h, msg.settings, { codec, progress });
      const out = await encodeOutput(r.rgb, img.alpha, img.w, img.h, msg.settings);
      reply({ type: "exported", bytes: out.bytes, mime: out.type, ext: out.ext,
        metrics: r.metrics, seconds: r.seconds, w: img.w, h: img.h, note: img.note },
      [out.bytes.buffer]);
    }
  } catch (err) {
    reply({ type: "error", message: String(err && err.message || err) });
  }
};

