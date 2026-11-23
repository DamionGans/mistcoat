// Visible watermark. Mirrors desktop/src/mistcoat/core/watermark.py - read that file
// for the reasoning; comments here are kept short.
//
// Levels: "plain" (even grid), "strong" (every mark moved, turned, resized and
// faded a little, with a soft textured edge), "maximum" (strong, plus a second
// row of small marks and the lettering woven into the texture). The protection
// runs afterwards, over the watermarked picture.
import { blur } from "./imaging.js";
import { makeRng } from "./passes.js";

export const LAYOUTS = ["tiled", "centre", "corner"];
export const RESISTANCE = ["plain", "strong", "maximum"];

const FONT = 'system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

/** Draws text with an OffscreenCanvas (browser or worker). */
export const canvasGlyphs = {
  ratio(text) {
    const ctx = new OffscreenCanvas(1, 1).getContext("2d");
    ctx.font = `bold 100px ${FONT}`;
    return Math.max(1, ctx.measureText(text).width) / 100;
  },
  /** Letters (fill) and outline (ring) as 0..1 coverage, rotated by angle (degrees, anticlockwise). */
  stamp(text, size, angle) {
    const stroke = Math.max(1, Math.round(size * 0.06));
    const probe = new OffscreenCanvas(1, 1).getContext("2d");
    probe.font = `bold ${size}px ${FONT}`;
    const m = probe.measureText(text);
    const tw = m.width + 2 * stroke;
    const th = (m.actualBoundingBoxAscent ?? size * 0.8) + (m.actualBoundingBoxDescent ?? size * 0.2) + 2 * stroke;
    const a = (angle * Math.PI) / 180;
    const w = Math.ceil(Math.abs(tw * Math.cos(a)) + Math.abs(th * Math.sin(a))) + 4;
    const h = Math.ceil(Math.abs(tw * Math.sin(a)) + Math.abs(th * Math.cos(a))) + 4;
    const draw = (withStroke) => {
      const ctx = new OffscreenCanvas(w, h).getContext("2d", { willReadFrequently: true });
      ctx.translate(w / 2, h / 2);
      ctx.rotate(-a);
      ctx.font = `bold ${size}px ${FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = ctx.strokeStyle = "#fff";
      ctx.lineJoin = "round";
      if (withStroke) { ctx.lineWidth = 2 * stroke; ctx.strokeText(text, 0, 0); }
      ctx.fillText(text, 0, 0);
      const d = ctx.getImageData(0, 0, w, h).data;
      const out = new Float32Array(w * h);
      for (let i = 0; i < out.length; i++) out[i] = d[i * 4 + 3] / 255;
      return out;
    };
    const outer = draw(true), fill = draw(false);
    const ring = new Float32Array(w * h);
    for (let i = 0; i < ring.length; i++) ring[i] = Math.max(0, outer[i] - fill[i]);
    return { fill, ring, w, h };
  },
};

function crc32(str) {
  let c, crc = 0xffffffff;
  for (const b of new TextEncoder().encode(str)) {
    c = (crc ^ b) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function layoutMarks(ratio, w, h, layout, resistance, rng) {
  const short = Math.min(w, h);
  const jitter = resistance !== "plain";
  const u = (lo, hi) => lo + (hi - lo) * rng.random();
  if (layout === "corner") {
    let size = Math.max(10, 0.05 * short);
    size = Math.min(size, (0.45 * w) / ratio);
    const margin = 0.025 * short, tw = ratio * size;
    return [{ x: w - margin - tw / 2, y: h - margin - size / 2,
      size: size * (jitter ? u(0.94, 1.06) : 1), angle: jitter ? u(-2, 2) : 0, opacity: 1 }];
  }
  if (layout === "centre") {
    const angle = Math.min(40, Math.max(15, (Math.atan2(h, w) * 180) / Math.PI));
    const a = (angle * Math.PI) / 180;
    const size = Math.min((0.85 * w) / (ratio * Math.cos(a) + Math.sin(a)),
      (0.85 * h) / (ratio * Math.sin(a) + Math.cos(a)));
    if (!jitter) return [{ x: w / 2, y: h / 2, size, angle, opacity: 1 }];
    return [{ x: w / 2 + u(-0.03, 0.03) * w, y: h / 2 + u(-0.03, 0.03) * h,
      size: size * u(0.95, 1), angle: angle + u(-3, 3), opacity: 1 }];
  }
  let size = Math.max(9, 0.06 * short);
  size = Math.min(size, (0.7 * Math.max(w, h)) / ratio);
  const tw = ratio * size, dx = tw * 1.5 + size, dy = size * 3.2;
  const rows = Math.ceil(h / dy) + 2, cols = Math.ceil(w / dx) + 2;
  const layers = [[0, 1, 1]];
  if (resistance === "maximum") layers.push([0.5, 0.5, 0.6]);
  const marks = [];
  for (const [shift, scale, opacity] of layers) {
    for (let r = 0; r < rows; r++) {
      const y = (r - 0.5 + shift) * dy;
      const offset = (dx / 2) * (r % 2) + (shift * dx) / 2;
      for (let c = 0; c < cols; c++) {
        const m = { x: (c - 0.5) * dx + offset, y, size: size * scale, angle: 30, opacity };
        if (jitter) {
          m.x += u(-0.12, 0.12) * dx;
          m.y += u(-0.15, 0.15) * dy;
          m.angle += u(-7, 7);
          m.size *= u(0.88, 1.12);
          m.opacity *= u(0.75, 1);
        }
        marks.push(m);
      }
    }
  }
  return marks;
}

function pasteMax(dst, w, h, src, sw, sh, cx, cy, scale) {
  const x0 = Math.round(cx - sw / 2), y0 = Math.round(cy - sh / 2);
  const xa = Math.max(0, x0), ya = Math.max(0, y0);
  const xb = Math.min(w, x0 + sw), yb = Math.min(h, y0 + sh);
  for (let y = ya; y < yb; y++) {
    const drow = y * w, srow = (y - y0) * sw - x0;
    for (let x = xa; x < xb; x++) {
      const v = src[srow + x] * scale;
      if (v > dst[drow + x]) dst[drow + x] = v;
    }
  }
}

/** Soft random field in 0..1, the same pattern at any resolution. */
function smoothNoise(w, h, rng, cells) {
  const short = Math.min(w, h);
  const gw = Math.ceil((w / short) * cells) + 1, gh = Math.ceil((h / short) * cells) + 1;
  const grid = new Float32Array(gw * gh);
  for (let i = 0; i < grid.length; i++) grid[i] = rng.random();
  const out = new Float32Array(w * h);
  const sx = (gw - 1) / Math.max(1, w - 1), sy = (gh - 1) / Math.max(1, h - 1);
  const ease = (t) => t * t * (3 - 2 * t);
  for (let y = 0; y < h; y++) {
    const gy = y * sy, y0 = Math.min(gh - 2, Math.floor(gy)), ty = ease(gy - y0);
    for (let x = 0; x < w; x++) {
      const gx = x * sx, x0 = Math.min(gw - 2, Math.floor(gx)), tx = ease(gx - x0);
      const a = grid[y0 * gw + x0], b = grid[y0 * gw + x0 + 1];
      const c = grid[(y0 + 1) * gw + x0], d = grid[(y0 + 1) * gw + x0 + 1];
      out[y * w + x] = (a + (b - a) * tx) * (1 - ty) + (c + (d - c) * tx) * ty;
    }
  }
  return out;
}

/**
 * Draw the watermark into ``rgb`` (Float32Array, RGB 0..1). ``alpha`` is a
 * Uint8ClampedArray or null. Returns {rgb, alpha}; transparent areas under the
 * mark become opaque enough to show it.
 */
export function applyWatermark(rgb, alpha, w, h, {
  text = "SAMPLE", layout = "tiled", resistance = "strong", opacity = 0.5, seed = 0,
} = {}, glyphs = canvasGlyphs) {
  text = String(text ?? "").split(/\s+/).filter(Boolean).join(" ");
  opacity = Math.max(0, Math.min(1, Number(opacity) || 0));
  if (!text || opacity <= 0) return { rgb, alpha };
  if (!LAYOUTS.includes(layout)) layout = "tiled";
  if (!RESISTANCE.includes(resistance)) resistance = "strong";

  const aspect = Math.round((w / h) * 1000);
  const key = ((seed >>> 0) ^ crc32(text) ^ Math.imul(aspect, 0x9e3779b1)) >>> 0;
  const rng = makeRng(key);
  const noiseRng = makeRng((key ^ 0x5bd1e995) >>> 0);

  const n = w * h;
  const marks = layoutMarks(glyphs.ratio(text), w, h, layout, resistance, rng);
  let fill = new Float32Array(n), ring = new Float32Array(n);
  const cache = new Map();
  for (const m of marks) {
    const size = Math.max(6, Math.round(m.size));
    const k = `${size}|${m.angle.toFixed(1)}`;
    let g = cache.get(k);
    if (!g) { g = glyphs.stamp(text, size, m.angle); cache.set(k, g); }
    pasteMax(fill, w, h, g.fill, g.w, g.h, m.x, m.y, m.opacity);
    pasteMax(ring, w, h, g.ring, g.w, g.h, m.x, m.y, m.opacity);
  }

  const size = marks[0].size;
  if (resistance !== "plain") {
    const field = smoothNoise(w, h, noiseRng, 24);
    const soft = Math.max(0.6, size * 0.012);
    fill = blur(fill, w, h, soft);
    ring = blur(ring, w, h, soft);
    for (let i = 0; i < n; i++) {
      const f = 0.55 + 0.45 * field[i];
      const grain = 1 + 0.18 * noiseRng.normal();
      fill[i] = Math.min(1, Math.max(0, fill[i] * f * grain));
      ring[i] = Math.min(1, ring[i] * f);
    }
  }

  const out = Float32Array.from(rgb);
  const strength = layout === "corner" ? 1.5 : 1;
  if (resistance === "maximum") {
    const k = Math.min(1, opacity * 2);
    const sigma = Math.max(0.8, size * 0.03);
    for (let c = 0; c < 3; c++) {
      const plane = new Float32Array(n);
      for (let i = 0; i < n; i++) plane[i] = out[i * 3 + c];
      const smooth = blur(plane, w, h, sigma);
      for (let i = 0; i < n; i++) out[i * 3 + c] -= 1.6 * k * fill[i] * (plane[i] - smooth[i]);
    }
    const shift = Math.max(1, Math.round(size * 0.04));
    const sf = blur(fill, w, h, sigma);
    for (let y = 0; y < h; y++) {
      const ys = (y - shift + h) % h;
      for (let x = 0; x < w; x++) {
        const xs = (x - shift + w) % w;
        const bevel = 0.35 * k * (sf[y * w + x] - sf[ys * w + xs]);
        const i = (y * w + x) * 3;
        for (let c = 0; c < 3; c++) out[i + c] = Math.min(1, Math.max(0, out[i + c] + bevel));
      }
    }
  }

  // A dark outline, then a light fill: readable on light and dark pictures.
  const newAlpha = alpha ? new Uint8ClampedArray(n) : null;
  for (let i = 0; i < n; i++) {
    const cr = Math.min(1, ring[i] * 0.6 * opacity * strength);
    const cf = Math.min(1, fill[i] * 0.8 * opacity * strength);
    let a = alpha ? alpha[i] / 255 : 1;
    for (const [colour, cov] of [[0, cr], [1, cf]]) {
      if (cov <= 0) continue;
      const na = a * (1 - cov) + cov;
      for (let c = 0; c < 3; c++) {
        const j = i * 3 + c;
        out[j] = (out[j] * a * (1 - cov) + colour * cov) / Math.max(na, 1e-6);
      }
      a = na;
    }
    for (let c = 0; c < 3; c++) out[i * 3 + c] = Math.min(1, Math.max(0, out[i * 3 + c]));
    if (newAlpha) newAlpha[i] = Math.round(a * 255);
  }
  return { rgb: out, alpha: newAlpha };
}

/** The watermark options held in the app's settings, or null when it is off. */
export function watermarkOptions(settings) {
  if (!settings?.watermark) return null;
  return { text: settings.watermarkText, layout: settings.watermarkLayout,
    resistance: settings.watermarkResistance, opacity: settings.watermarkOpacity,
    seed: settings.seed };
}
