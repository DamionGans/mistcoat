// Measuring cost and durability. Mirrors desktop/src/mistcoat/core/quality.py.
import { blur, luminance, resizeLongest } from "./imaging.js";

export function psnr(a, b) {
  let mse = 0;
  for (let i = 0; i < a.length; i++) { const d = a[i] - b[i]; mse += d * d; }
  mse /= a.length;
  return mse <= 1e-12 ? 99 : 10 * Math.log10(1 / mse);
}

export function ssim(a, b, w, h) {
  const x = luminance(a, w, h), y = luminance(b, w, h);
  const n = x.length, sigma = 1.5, c1 = 0.01 ** 2, c2 = 0.03 ** 2;
  const xx = new Float32Array(n), yy = new Float32Array(n), xy = new Float32Array(n);
  for (let i = 0; i < n; i++) { xx[i] = x[i] * x[i]; yy[i] = y[i] * y[i]; xy[i] = x[i] * y[i]; }
  const mx = blur(x, w, h, sigma), my = blur(y, w, h, sigma);
  const mxx = blur(xx, w, h, sigma), myy = blur(yy, w, h, sigma), mxy = blur(xy, w, h, sigma);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const vx = Math.max(mxx[i] - mx[i] * mx[i], 0);
    const vy = Math.max(myy[i] - my[i] * my[i], 0);
    const cxy = mxy[i] - mx[i] * my[i];
    const num = (2 * mx[i] * my[i] + c1) * (2 * cxy + c2);
    const den = (mx[i] * mx[i] + my[i] * my[i] + c1) * (vx + vy + c2);
    sum += num / Math.max(den, 1e-12);
  }
  return sum / n;
}

/**
 * durability = <surviving, delta> / (|delta| * |intended - original|)
 *
 * Normalised by the *intended* perturbation, so it cannot be gamed by
 * shrinking the perturbation to whatever compression happens to keep.
 * Returns null when the transform is unavailable on this platform.
 */
export async function survival(original, protectedRgb, w, h, transform, intended = null) {
  const n = original.length;
  let dd = 0, rr = 0;
  for (let i = 0; i < n; i++) {
    const d = protectedRgb[i] - original[i];
    const r = intended ? intended[i] - original[i] : d;
    dd += d * d; rr += r * r;
  }
  const dNorm = Math.sqrt(dd), rNorm = Math.sqrt(rr);
  if (dNorm < 1e-9 || rNorm < 1e-9) return 0;
  const dec = await transform(protectedRgb, w, h);
  const base = await transform(original, w, h);
  if (!dec || !base) return null;
  let dot = 0;
  for (let i = 0; i < n; i++) dot += (dec[i] - base[i]) * (protectedRgb[i] - original[i]);
  return Math.max(0, Math.min(1, dot / (dNorm * rNorm)));
}

export function visibilityLabel(m) {
  if (m.psnr >= 42 && m.maxDelta <= 10) return "Imperceptible";
  if (m.psnr >= 36) return "Invisible in normal viewing";
  if (m.psnr >= 30) return "Faint texture visible up close";
  return "Visibly altered";
}

export function survivalLabel(s) {
  if (s >= 0.75) return "Survives re-compression well";
  if (s >= 0.5) return "Mostly survives re-compression";
  if (s >= 0.3) return "Partly survives re-compression";
  return "Largely stripped by re-compression";
}

/**
 * Compare protected against original. Durability is averaged over the codec's
 * held-out transforms - never the ones hardening optimised against.
 */
export async function measure(original, protectedRgb, w, h, codec, opts = {}) {
  const { survivalBase = null, intended = null, sampleLongest = 1400 } = opts;
  let maxD = 0, sumD = 0;
  for (let i = 0; i < original.length; i++) {
    const d = Math.abs(protectedRgb[i] - original[i]);
    if (d > maxD) maxD = d;
    sumD += d;
  }
  const m = {
    psnr: psnr(original, protectedRgb),
    ssim: ssim(original, protectedRgb, w, h),
    maxDelta: maxD * 255,
    meanDelta: (sumD / original.length) * 255,
    survival: 0,
  };
  if (codec) {
    const a = resizeLongest(survivalBase ?? original, w, h, sampleLongest);
    const b = resizeLongest(protectedRgb, w, h, sampleLongest);
    const r = intended ? resizeLongest(intended, w, h, sampleLongest).rgb : null;
    const scores = [];
    for (const t of codec.heldOut) {
      const s = await survival(a.rgb, b.rgb, a.w, a.h, t.fn, r);
      if (s !== null) scores.push(s);
    }
    m.survival = scores.length ? scores.reduce((x, y) => x + y, 0) / scores.length : 0;
  }
  m.visibilityLabel = visibilityLabel(m);
  m.survivalLabel = survivalLabel(m.survival);
  return m;
}
