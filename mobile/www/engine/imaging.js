// Signal primitives. A line-for-line port of desktop/src/mistcoat/core/imaging.py.
//
// Images are interleaved RGB Float32Arrays (length w*h*3, values 0..1), the
// same layout numpy uses for an (H, W, 3) array. Single channels are
// Float32Arrays of length w*h.
import { gpuBlur, gpuBlurMany } from "./gpu.js";

export function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

export function clip(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

// ------------------------------------------------------------------ blur

// Both passes are running sums with clamped edges. They are written for
// speed on phones without WebGPU, but every value is produced by the same
// arithmetic in the same order as the straightforward version, so results
// are bit-identical to it.

function boxH(src, dst, w, h, r) {
  const rr = Math.min(r, Math.max(w - 1, 1));
  const width = 2 * rr + 1;
  const last = w - 1;
  for (let y = 0; y < h; y++) {
    const row = y * w;
    let sum = 0;
    for (let k = -rr; k <= rr; k++) sum += src[row + clip(k, 0, last)];
    dst[row] = sum / width;
    // Split into edge and interior runs so the interior needs no clamping.
    const leftEnd = Math.min(rr + 1, w);          // x - rr - 1 < 0 below this
    const rightStart = Math.max(w - rr, leftEnd);  // x + rr > last from here
    let x = 1;
    for (; x < leftEnd; x++) {
      sum += src[row + Math.min(x + rr, last)] - src[row];
      dst[row + x] = sum / width;
    }
    for (; x < rightStart; x++) {
      sum += src[row + x + rr] - src[row + x - rr - 1];
      dst[row + x] = sum / width;
    }
    for (; x < w; x++) {
      sum += src[row + last] - src[row + Math.max(x - rr - 1, 0)];
      dst[row + x] = sum / width;
    }
  }
}

function boxV(src, dst, w, h, r) {
  const rr = Math.min(r, Math.max(h - 1, 1));
  const width = 2 * rr + 1;
  // One running sum per column, advanced a whole row at a time: memory is
  // read in order instead of striding a full row per step.
  const sums = new Float64Array(w);
  for (let k = -rr; k <= rr; k++) {
    const off = clip(k, 0, h - 1) * w;
    for (let x = 0; x < w; x++) sums[x] += src[off + x];
  }
  for (let x = 0; x < w; x++) dst[x] = sums[x] / width;
  for (let y = 1; y < h; y++) {
    const add = Math.min(y + rr, h - 1) * w, sub = Math.max(y - rr - 1, 0) * w, row = y * w;
    for (let x = 0; x < w; x++) {
      sums[x] += src[add + x] - src[sub + x];
      dst[row + x] = sums[x] / width;
    }
  }
}

/** Gaussian approximation: three box passes. Returns a new array. CPU only;
 * the engine itself calls blurFast, which uses the GPU when it can. */
export function blur(src, w, h, sigma) {
  const out = Float32Array.from(src);
  if (sigma <= 0.01) return out;
  const r = Math.max(1, Math.round(sigma * 0.886));
  const tmp = new Float32Array(src.length);
  for (let i = 0; i < 3; i++) {
    boxV(out, tmp, w, h, r);
    boxH(tmp, out, w, h, r);
  }
  return out;
}

/** blur() on the GPU when available (see gpu.js), otherwise on the CPU. */
export async function blurFast(src, w, h, sigma) {
  if (sigma > 0.01) {
    const out = await gpuBlur(src, w, h, Math.max(1, Math.round(sigma * 0.886)));
    if (out) return out;
  }
  return blur(src, w, h, sigma);
}

/** The same array blurred at several sigmas; one GPU round trip when possible. */
export async function blurFastMany(src, w, h, sigmas) {
  if (sigmas.every((s) => s > 0.01)) {
    const out = await gpuBlurMany(src, w, h, sigmas.map((s) => Math.max(1, Math.round(s * 0.886))));
    if (out) return out;
  }
  return sigmas.map((s) => blur(src, w, h, s));
}

// --------------------------------------------------------------- analysis

export function luminance(rgb, w, h) {
  const n = w * h;
  const out = new Float32Array(n);
  for (let i = 0, j = 0; i < n; i++, j += 3) {
    out[i] = 0.2126 * rgb[j] + 0.7152 * rgb[j + 1] + 0.0722 * rgb[j + 2];
  }
  return out;
}

/** Linear-interpolated percentile on a strided sample (bounded cost on big images). */
export function percentile(arr, p, maxSamples = 1_000_000) {
  const stride = Math.max(1, Math.floor(arr.length / maxSamples));
  const n = Math.ceil(arr.length / stride);
  const sample = new Float32Array(n);
  for (let i = 0, k = 0; i < arr.length && k < n; i += stride, k++) sample[k] = arr[i];
  sample.sort();
  const pos = (p / 100) * (n - 1);
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, n - 1);
  return sample[lo] + (sample[hi] - sample[lo]) * (pos - lo);
}

/** 0..1 map of local detail. High where texture can hide perturbation. */
export async function localActivity(rgb, w, h, sigma = 2.0) {
  const lum = luminance(rgb, w, h);
  const sq = new Float32Array(lum.length);
  for (let i = 0; i < lum.length; i++) sq[i] = lum[i] * lum[i];
  const mean = await blurFast(lum, w, h, sigma);
  const meanSq = await blurFast(sq, w, h, sigma);
  const std = new Float32Array(lum.length);
  for (let i = 0; i < lum.length; i++) {
    std[i] = Math.sqrt(Math.max(meanSq[i] - mean[i] * mean[i], 0));
  }
  let hi = percentile(std, 98);
  if (hi < 1e-6) hi = 1e-6;
  for (let i = 0; i < std.length; i++) std[i] = clamp01(std[i] / hi);
  return std;
}

export function std(arr) {
  let s = 0, s2 = 0;
  for (let i = 0; i < arr.length; i++) { s += arr[i]; s2 += arr[i] * arr[i]; }
  const m = s / arr.length;
  return Math.sqrt(Math.max(s2 / arr.length - m * m, 0));
}

// ----------------------------------------------------------------- colour

// Same matrices as the Python engine (BT.601 full-range, Cb/Cr centred on 0).
export function rgbToYcc(rgb) {
  const out = new Float32Array(rgb.length);
  for (let j = 0; j < rgb.length; j += 3) {
    const r = rgb[j], g = rgb[j + 1], b = rgb[j + 2];
    out[j] = 0.299 * r + 0.587 * g + 0.114 * b;
    out[j + 1] = -0.168736 * r - 0.331264 * g + 0.5 * b;
    out[j + 2] = 0.5 * r - 0.418688 * g - 0.081312 * b;
  }
  return out;
}

export function yccToRgb(ycc) {
  const out = new Float32Array(ycc.length);
  for (let j = 0; j < ycc.length; j += 3) {
    const y = ycc[j], cb = ycc[j + 1], cr = ycc[j + 2];
    out[j] = y + 1.402 * cr;
    out[j + 1] = y - 0.344136 * cb - 0.714136 * cr;
    out[j + 2] = y + 1.772 * cb;
  }
  return out;
}

// -------------------------------------------------------------------- DCT

const N = 8;
export const DCT = (() => {
  const m = new Float32Array(64);
  for (let k = 0; k < N; k++) {
    for (let i = 0; i < N; i++) {
      let v = Math.cos((Math.PI * (2 * i + 1) * k) / (2 * N)) * Math.sqrt(2 / N);
      if (k === 0) v /= Math.SQRT2;
      m[k * 8 + i] = v;
    }
  }
  return m;
})();

const tmp64 = new Float32Array(64);

/** In-place forward 2-D DCT of one 8x8 block: B <- D B D^T. */
export function dct8(block) {
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      let s = 0;
      for (let k = 0; k < 8; k++) s += DCT[i * 8 + k] * block[k * 8 + j];
      tmp64[i * 8 + j] = s;
    }
  }
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      let s = 0;
      for (let k = 0; k < 8; k++) s += tmp64[i * 8 + k] * DCT[j * 8 + k];
      block[i * 8 + j] = s;
    }
  }
}

/** In-place inverse 2-D DCT: B <- D^T B D. */
export function idct8(block) {
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      let s = 0;
      for (let k = 0; k < 8; k++) s += DCT[k * 8 + i] * block[k * 8 + j];
      tmp64[i * 8 + j] = s;
    }
  }
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      let s = 0;
      for (let k = 0; k < 8; k++) s += tmp64[i * 8 + k] * DCT[k * 8 + j];
      block[i * 8 + j] = s;
    }
  }
}

export const JPEG_Q50_LUMA = Float32Array.from([
  16, 11, 10, 16, 24, 40, 51, 61,
  12, 12, 14, 19, 26, 58, 60, 55,
  14, 13, 16, 24, 40, 57, 69, 56,
  14, 17, 22, 29, 51, 87, 80, 62,
  18, 22, 37, 56, 68, 109, 103, 77,
  24, 35, 55, 64, 81, 104, 113, 92,
  49, 64, 78, 87, 103, 121, 120, 101,
  72, 92, 95, 98, 112, 100, 103, 99,
]);

// ------------------------------------------------------------------- warp

/** Bilinear resample by a per-pixel displacement field (pixels). */
export function warp(rgb, w, h, dx, dy) {
  const out = new Float32Array(rgb.length);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      const sx = clip(x + dx[i], 0, w - 1);
      const sy = clip(y + dy[i], 0, h - 1);
      const x0 = Math.floor(sx), y0 = Math.floor(sy);
      const x1 = Math.min(x0 + 1, w - 1), y1 = Math.min(y0 + 1, h - 1);
      const fx = sx - x0, fy = sy - y0;
      const a = (y0 * w + x0) * 3, b = (y0 * w + x1) * 3;
      const c = (y1 * w + x0) * 3, d = (y1 * w + x1) * 3;
      const o = i * 3;
      for (let ch = 0; ch < 3; ch++) {
        const top = rgb[a + ch] * (1 - fx) + rgb[b + ch] * fx;
        const bot = rgb[c + ch] * (1 - fx) + rgb[d + ch] * fx;
        out[o + ch] = top * (1 - fy) + bot * fy;
      }
    }
  }
  return out;
}

// ------------------------------------------------------------- conversion

export function fromRGBA(data, w, h) {
  const n = w * h;
  const rgb = new Float32Array(n * 3);
  let hasAlpha = false;
  const alpha = new Uint8ClampedArray(n);
  for (let i = 0, j = 0, k = 0; i < n; i++, j += 4, k += 3) {
    rgb[k] = data[j] / 255;
    rgb[k + 1] = data[j + 1] / 255;
    rgb[k + 2] = data[j + 2] / 255;
    alpha[i] = data[j + 3];
    if (data[j + 3] !== 255) hasAlpha = true;
  }
  return { rgb, alpha: hasAlpha ? alpha : null };
}

export function toRGBA(rgb, w, h, alpha = null) {
  const n = w * h;
  const out = new Uint8ClampedArray(n * 4);
  for (let i = 0, j = 0, k = 0; i < n; i++, j += 4, k += 3) {
    out[j] = rgb[k] * 255 + 0.5;
    out[j + 1] = rgb[k + 1] * 255 + 0.5;
    out[j + 2] = rgb[k + 2] * 255 + 0.5;
    out[j + 3] = alpha ? alpha[i] : 255;
  }
  return out;
}

/** Quantise to 8 bits and back - what saving a file does to the pixels. */
export function quantise8(rgb) {
  const out = new Float32Array(rgb.length);
  for (let i = 0; i < rgb.length; i++) out[i] = Math.round(clamp01(rgb[i]) * 255) / 255;
  return out;
}

/** Area-average downscale so the longest side is at most ``longest``. */
export function resizeLongest(rgb, w, h, longest) {
  if (Math.max(w, h) <= longest) return { rgb, w, h };
  const scale = longest / Math.max(w, h);
  const nw = Math.max(1, Math.round(w * scale));
  const nh = Math.max(1, Math.round(h * scale));
  return { rgb: resizeArea(rgb, w, h, nw, nh), w: nw, h: nh };
}

export function resizeArea(rgb, w, h, nw, nh) {
  const out = new Float32Array(nw * nh * 3);
  const sx = w / nw, sy = h / nh;
  for (let y = 0; y < nh; y++) {
    const y0 = Math.floor(y * sy);
    const y1 = Math.min(h, Math.max(y0 + 1, Math.floor((y + 1) * sy)));
    for (let x = 0; x < nw; x++) {
      const x0 = Math.floor(x * sx);
      const x1 = Math.min(w, Math.max(x0 + 1, Math.floor((x + 1) * sx)));
      let r = 0, g = 0, b = 0, n = 0;
      for (let yy = y0; yy < y1; yy++) {
        for (let xx = x0; xx < x1; xx++) {
          const j = (yy * w + xx) * 3;
          r += rgb[j]; g += rgb[j + 1]; b += rgb[j + 2]; n++;
        }
      }
      const o = (y * nw + x) * 3;
      out[o] = r / n; out[o + 1] = g / n; out[o + 2] = b / n;
    }
  }
  return out;
}
