// The five perturbation passes. A port of desktop/src/mistcoat/core/passes/*.py - same
// constants, same masking, same order. The random streams differ from numpy's
// (different generator), so outputs match the desktop app in character and
// measured cost, not bit for bit.

import {
  blurFast, blurFastMany, clamp01, clip, dct8, idct8, JPEG_Q50_LUMA, localActivity, luminance,
  percentile, rgbToYcc, std, warp, yccToRgb,
} from "./imaging.js";

// ------------------------------------------------------------------- rng

/** sfc32: small, fast, good statistical quality, seedable. */
export function makeRng(seed) {
  let a = 0x9e3779b9, b = 0x243f6a88, c = 0xb7e15162, d = seed >>> 0;
  const next = () => {
    a >>>= 0; b >>>= 0; c >>>= 0; d >>>= 0;
    const t = (a + b) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    d = (d + 1) | 0;
    const r = (t + d) | 0;
    c = (c + r) | 0;
    return (r >>> 0) / 4294967296;
  };
  for (let i = 0; i < 16; i++) next();
  let spare = null;
  return {
    random: next,
    /** Standard normal via Box-Muller. */
    normal() {
      if (spare !== null) { const s = spare; spare = null; return s; }
      let u = 0;
      while (u <= 1e-12) u = next();
      const v = next();
      const m = Math.sqrt(-2 * Math.log(u));
      spare = m * Math.sin(2 * Math.PI * v);
      return m * Math.cos(2 * Math.PI * v);
    },
    /** n standard normals: the exact sequence repeated normal() calls give,
     * with the generator inlined - this is the hot loop of the noise passes
     * (tens of millions of draws per photo), several times faster. */
    normals(n) {
      const out = new Float32Array(n);
      let i = 0;
      if (spare !== null && n > 0) { out[i++] = spare; spare = null; }
      let A = a >>> 0, B = b >>> 0, C = c >>> 0, D = d >>> 0;
      const TWO_PI = 2 * Math.PI;
      while (i < n) {
        let u = 0, v = 0;
        do {                                   // same rejection as normal()
          const t = (A + B) | 0;
          A = (B ^ (B >>> 9)) >>> 0;
          B = (C + (C << 3)) >>> 0;
          C = ((C << 21) | (C >>> 11)) >>> 0;
          D = (D + 1) >>> 0;
          const r = (t + D) | 0;
          C = (C + r) >>> 0;
          u = (r >>> 0) / 4294967296;
        } while (u <= 1e-12);
        {
          const t = (A + B) | 0;
          A = (B ^ (B >>> 9)) >>> 0;
          B = (C + (C << 3)) >>> 0;
          C = ((C << 21) | (C >>> 11)) >>> 0;
          D = (D + 1) >>> 0;
          const r = (t + D) | 0;
          C = (C + r) >>> 0;
          v = (r >>> 0) / 4294967296;
        }
        const m = Math.sqrt(-2 * Math.log(u));
        const ang = TWO_PI * v;
        out[i++] = m * Math.cos(ang);
        if (i < n) out[i++] = m * Math.sin(ang);
        else spare = m * Math.sin(ang);
      }
      a = A; b = B; c = C; d = D;
      return out;
    },
  };
}

// --------------------------------------------------------------- context

export class PassContext {
  constructor(rgb, w, h, seed = 0) {
    this.rgb = rgb; this.w = w; this.h = h; this.seed = seed;
    this._activity = null;
    this._lum = null;
  }
  /** Compute the (blur-heavy) activity map up front, on the GPU if possible. */
  async prepare() {
    if (!this._activity) this._activity = await localActivity(this.rgb, this.w, this.h, 2.0);
    return this;
  }
  get activity() {
    if (!this._activity) throw new Error("PassContext.prepare() must be awaited first");
    return this._activity;
  }
  get lum() {
    if (!this._lum) this._lum = luminance(this.rgb, this.w, this.h);
    return this._lum;
  }
  rng(salt) {
    // Same mixing as the Python engine, kept in 32 bits.
    return makeRng((Math.imul(this.seed, 1000003) + salt) >>> 0);
  }
  /** Where perturbation can hide: textured areas, mid tones. (H*W) 0..1. */
  visibilityMask(floor = 0.25, gamma = 0.75) {
    const act = this.activity, lum = this.lum;
    const out = new Float32Array(act.length);
    for (let i = 0; i < act.length; i++) {
      const m = floor + (1 - floor) * Math.pow(act[i], gamma);
      const head = clamp01(Math.min(lum[i], 1 - lum[i]) / 0.09);
      out[i] = m * (0.55 + 0.45 * head);
    }
    return out;
  }
}

/** Noise with energy between two blur scales (difference of gaussians). */
export async function bandNoise(w, h, rng, inner, outer) {
  const n = rng.normals(w * h);
  const [lo, hi] = await blurFastMany(n, w, h, [inner, outer]);
  for (let i = 0; i < lo.length; i++) lo[i] -= hi[i];
  const s = std(lo);
  if (s > 1e-8) for (let i = 0; i < lo.length; i++) lo[i] /= s;
  return lo;
}

async function normField(w, h, rng, sigma) {
  const f = await blurFast(rng.normals(w * h), w, h, sigma);
  const s = std(f);
  if (s > 1e-8) for (let i = 0; i < f.length; i++) f[i] /= s;
  return f;
}

function clipRgb(rgb) {
  for (let i = 0; i < rgb.length; i++) rgb[i] = clamp01(rgb[i]);
  return rgb;
}

// ----------------------------------------------------------------- passes

export const TextureWarp = {
  name: "texture_warp",
  label: "Stroke disruption",
  blurb: "A smooth sub-pixel displacement field. Scrambles the local stroke and edge " +
    "statistics a model reads as your style, while leaving composition, colour and " +
    "line untouched. Cannot be denoised away.",
  targets: "style mimicry; LoRA and fine-tune training on your hand",
  visibility: "subtle",
  defaultOn: true,
  geometric: true,
  salt: 37,
  async apply(ctx, rgb, strength) {
    if (strength <= 0) return rgb;
    const { w, h } = ctx;
    const rng = ctx.rng(this.salt);
    const base = 0.34 + 0.22 * Math.min(Math.max(h, w) / 3000, 1.5);
    const amp = base * strength;
    const octaves = [[3.0, 1.0], [9.0, 0.6]];
    const norm = octaves.reduce((s, o) => s + o[1], 0);
    const dx = new Float32Array(w * h), dy = new Float32Array(w * h);
    for (const [sigma, weight] of octaves) {
      const fx = await normField(w, h, rng, sigma), fy = await normField(w, h, rng, sigma);
      for (let i = 0; i < dx.length; i++) {
        dx[i] += fx[i] * (weight / norm);
        dy[i] += fy[i] * (weight / norm);
      }
    }
    const act = ctx.activity;
    for (let i = 0; i < dx.length; i++) {
      let gate = act[i] * (1 - 0.85 * clamp01((act[i] - 0.55) / 0.45));
      gate = 0.15 + 1.35 * gate;
      dx[i] *= amp * gate;
      dy[i] *= amp * gate;
    }
    return warp(rgb, w, h, dx, dy);
  },
};

export const GhostContours = {
  name: "decoy",
  label: "Ghost contours",
  blurb: "Overlays faint synthetic edges and regions at object scale, so a model's shape " +
    "layers see structure that is not in the artwork. The only pass here a careful " +
    "viewer may notice - off by default.",
  targets: "shape and object recognition; automatic captioning and tagging",
  visibility: "visible",
  defaultOn: false,
  geometric: false,
  salt: 71,
  async apply(ctx, rgb, strength) {
    if (strength <= 0) return rgb;
    const { w, h } = ctx;
    const rng = ctx.rng(this.salt);
    const span = Math.max(h, w);
    const field = new Float32Array(w * h);
    for (const [frac, weight, freq] of [[0.045, 1.0, 7.0], [0.018, 0.55, 15.0]]) {
      const sigma = Math.max(1.5, span * frac);
      const n = await normField(w, h, rng, sigma);
      const bands = new Float32Array(n.length);
      for (let i = 0; i < n.length; i++) bands[i] = Math.sin(n[i] * freq);
      const soft = await blurFast(bands, w, h, Math.max(1.5, sigma * 0.08));
      for (let i = 0; i < n.length; i++) field[i] += (bands[i] - soft[i]) * weight;
    }
    const s = std(field);
    const act = ctx.activity, lum = luminance(rgb, w, h);
    const amp = 0.028 * strength;
    const out = Float32Array.from(rgb);
    for (let i = 0, j = 0; i < field.length; i++, j += 3) {
      const f = s > 1e-8 ? field[i] / s : field[i];
      let gate = clamp01(act[i] * 2.2) * (1 - clamp01((act[i] - 0.75) / 0.25));
      gate = 0.25 + 0.75 * gate;
      const head = clamp01(Math.min(lum[i], 1 - lum[i]) / 0.15);
      const d = f * gate * head * amp;
      out[j] += d; out[j + 1] += d; out[j + 2] += d;
    }
    return clipRgb(out);
  },
};

// Mid-band DCT mask and survival weights - computed once, as in Python.
const SURVIVAL = (() => {
  const s = new Float32Array(64);
  let max = 0;
  for (let u = 0; u < 8; u++) {
    for (let v = 0; v < 8; v++) {
      const r = u + v;
      const val = r >= 2 && r <= 6 ? 1 / Math.sqrt(JPEG_Q50_LUMA[u * 8 + v]) : 0;
      s[u * 8 + v] = val;
      if (val > max) max = val;
    }
  }
  for (let i = 0; i < 64; i++) s[i] /= max;
  return s;
})();

export const DctPoison = {
  name: "dct_poison",
  label: "Compression-resistant pattern",
  blurb: "Writes the perturbation into the 8x8 DCT coefficients that JPEG and WebP " +
    "preserve, sized against the quantisation step. This is what keeps the protection " +
    "alive after a site re-compresses the upload.",
  targets: "survival through re-encoding, resizing and screenshotting",
  visibility: "invisible",
  defaultOn: true,
  geometric: false,
  salt: 23,
  async apply(ctx, rgb, strength) {
    if (strength <= 0) return rgb;
    const { w, h } = ctx;
    const rng = ctx.rng(this.salt);
    const ycc = rgbToYcc(rgb);
    const nbx = Math.ceil(w / 8), nby = Math.ceil(h / 8);
    const nb = nbx * nby;
    const coeffs = new Float32Array(nb * 64);
    const ac = new Float32Array(nb);
    const block = new Float32Array(64);

    // Forward DCT of every block (edge-replicated padding), recording AC energy.
    for (let by = 0; by < nby; by++) {
      for (let bx = 0; bx < nbx; bx++) {
        for (let y = 0; y < 8; y++) {
          const sy = Math.min(by * 8 + y, h - 1);
          for (let x = 0; x < 8; x++) {
            const sx = Math.min(bx * 8 + x, w - 1);
            block[y * 8 + x] = ycc[(sy * w + sx) * 3] * 255;
          }
        }
        dct8(block);
        const b = by * nbx + bx;
        let sum = 0;
        for (let k = 1; k < 64; k++) sum += Math.abs(block[k]);
        ac[b] = sum;
        coeffs.set(block, b * 64);
      }
    }
    const p95 = Math.max(percentile(ac, 95), 1e-6);
    const amplitude = 0.16 * strength;
    const yNew = new Float32Array(w * h);

    for (let b = 0; b < nb; b++) {
      const gain = 0.45 + 0.55 * clamp01(ac[b] / p95);
      for (let k = 0; k < 64; k++) {
        const sign = rng.random() < 0.5 ? -1 : 1;
        block[k] = coeffs[b * 64 + k] + sign * SURVIVAL[k] * JPEG_Q50_LUMA[k] * amplitude * gain;
      }
      idct8(block);
      const by = Math.floor(b / nbx), bx = b % nbx;
      for (let y = 0; y < 8; y++) {
        const py = by * 8 + y;
        if (py >= h) break;
        for (let x = 0; x < 8; x++) {
          const px = bx * 8 + x;
          if (px >= w) break;
          yNew[py * w + px] = block[y * 8 + x] / 255;
        }
      }
    }

    const mask = ctx.visibilityMask(0.35);
    for (let i = 0, j = 0; i < yNew.length; i++, j += 3) {
      ycc[j] += (yNew[i] - ycc[j]) * mask[i];
    }
    return clipRgb(yccToRgb(ycc));
  },
};

export const ChromaPerturb = {
  name: "chroma",
  label: "Colour-channel perturbation",
  blurb: "Exploits the eye's low resolution for colour. Pushes a much larger perturbation " +
    "into the chroma channels than luma would tolerate - nearly free to a human, " +
    "expensive to a model.",
  targets: "colour and palette learning; feature extractors reading raw RGB",
  visibility: "invisible",
  defaultOn: true,
  geometric: false,
  salt: 53,
  async apply(ctx, rgb, strength) {
    if (strength <= 0) return rgb;
    const { w, h } = ctx;
    const rng = ctx.rng(this.salt);
    const ycc = rgbToYcc(rgb);
    const amp = 0.020 * strength;
    const n = w * h;
    const gate = new Float32Array(n);
    for (let i = 0, j = 0; i < n; i++, j += 3) {
      const sat = Math.sqrt(ycc[j + 1] ** 2 + ycc[j + 2] ** 2);
      let g = 0.35 + 0.65 * clamp01(sat / 0.25);
      const head = clamp01(Math.min(ycc[j], 1 - ycc[j]) / 0.1);
      gate[i] = g * (0.4 + 0.6 * head);
    }
    // Band starts at ~3px so 4:2:0 chroma subsampling does not erase it.
    for (const c of [1, 2]) {
      const field = await bandNoise(w, h, rng, 3.2, 10.0);
      for (let i = 0; i < n; i++) ycc[i * 3 + c] += field[i] * amp * gate[i];
    }
    return clipRgb(yccToRgb(ycc));
  },
};

export const HighFrequencyNoise = {
  name: "hf_noise",
  label: "Texture noise",
  blurb: "Structured noise spread across two frequency bands and hidden in existing " +
    "detail. Cheap, and it degrades the fine-texture statistics that image models " +
    "learn from.",
  targets: "texture and detail learning; fine-grained style capture",
  visibility: "invisible",
  defaultOn: true,
  geometric: false,
  salt: 11,
  async apply(ctx, rgb, strength) {
    if (strength <= 0) return rgb;
    const { w, h } = ctx;
    const rng = ctx.rng(this.salt);
    const n = w * h;
    const mask = ctx.visibilityMask(0.3);
    const amp = 0.010 * strength;
    const bands = [[0.8, 2.0, 1.0], [2.2, 5.5, 0.7]];
    const norm = bands.reduce((s, b) => s + b[2], 0);
    const total = new Float32Array(n);
    for (const [inner, outer, weight] of bands) {
      const b = await bandNoise(w, h, rng, inner, outer);
      for (let i = 0; i < n; i++) total[i] += b[i] * (weight / norm);
    }
    const out = Float32Array.from(rgb);
    for (let c = 0; c < 3; c++) {
      const jitter = await bandNoise(w, h, rng, 1.0, 3.0);
      for (let i = 0; i < n; i++) {
        out[i * 3 + c] += (total[i] + jitter[i] * 0.35) * amp * mask[i];
      }
    }
    return clipRgb(out);
  },
};

// Order matters - geometric first, then shape, then additive (see Python).
export const ALL_PASSES = [TextureWarp, GhostContours, DctPoison, ChromaPerturb, HighFrequencyNoise];
export const PASSES_BY_NAME = Object.fromEntries(ALL_PASSES.map((p) => [p.name, p]));

export { clip };
