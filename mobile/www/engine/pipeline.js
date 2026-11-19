// The protection pipeline. Mirrors mistcoat/core/pipeline.py - read that file
// for the reasoning behind each step; comments here are kept short.
import { clamp01, resizeLongest } from "./imaging.js";
import { ALL_PASSES, PassContext } from "./passes.js";
import { passStrength } from "./presets.js";
import { measure } from "./quality.js";

const noop = () => {};

/** Per-pixel ceiling on any channel's change: ~3/255 at 0%, ~11/255 at 100%. */
export function deltaCeiling(ctx, strength) {
  const peak = 0.012 + 0.030 * Math.max(0, Math.min(1, strength));
  const mask = ctx.visibilityMask(0.25);
  const out = new Float32Array(mask.length);
  for (let i = 0; i < mask.length; i++) out[i] = peak * (0.35 + 0.65 * mask[i]);
  return out;
}

export function limit(original, current, ceiling) {
  const out = new Float32Array(original.length);
  for (let i = 0, p = 0; p < ceiling.length; p++) {
    const c = ceiling[p];
    for (let k = 0; k < 3; k++, i++) {
      let d = current[i] - original[i];
      if (d > c) d = c; else if (d < -c) d = -c;
      out[i] = clamp01(original[i] + d);
    }
  }
  return out;
}

function rms(a) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * a[i];
  return Math.sqrt(s / a.length);
}

/**
 * Survivable projection against an ensemble of training transforms: keep the
 * part of the perturbation that survives all of them, rescale to the intended
 * energy, repeat; return the best-scoring candidate (never worse than input).
 */
async function harden(original, prot, w, h, settings, ceiling, codec, base, progress, span) {
  const transforms = codec.training.map((t) => t.fn);
  const n = original.length;
  const reference = new Float32Array(n);
  for (let i = 0; i < n; i++) reference[i] = prot[i] - base[i];
  const targetRms = rms(reference);
  if (targetRms < 1e-9) return prot;
  const rNorm = targetRms * Math.sqrt(n);

  const baseDecoded = [];
  const usable = [];
  for (const fn of transforms) {
    const bd = await fn(base, w, h);
    if (bd) { baseDecoded.push(bd); usable.push(fn); }
  }
  if (!usable.length) return prot;

  const encodeAll = async (img) => Promise.all(usable.map((fn) => fn(img, w, h)));
  const score = (cand, decoded) => {
    let dd = 0;
    for (let i = 0; i < n; i++) { const d = cand[i] - base[i]; dd += d * d; }
    const dNorm = Math.sqrt(dd);
    if (dNorm < 1e-9) return 0;
    let total = 0;
    decoded.forEach((dec, t) => {
      const bd = baseDecoded[t];
      let dot = 0;
      for (let i = 0; i < n; i++) dot += (dec[i] - bd[i]) * (cand[i] - base[i]);
      total += dot / (dNorm * rNorm);
    });
    return Math.max(0, Math.min(1, total / decoded.length));
  };

  let current = prot;
  let decoded = await encodeAll(current);
  let best = current, bestScore = score(current, decoded);
  const [lo, hi] = span;
  const iters = Math.max(1, settings.hardenIterations);
  for (let it = 0; it < iters; it++) {
    progress("Hardening against re-compression", lo + ((hi - lo) * it) / iters);
    const surviving = new Float32Array(n);
    decoded.forEach((dec, t) => {
      const bd = baseDecoded[t];
      for (let i = 0; i < n; i++) surviving[i] += (dec[i] - bd[i]) / decoded.length;
    });
    const scale = targetRms / Math.max(rms(surviving), 1e-9);
    const cand = new Float32Array(n);
    for (let i = 0; i < n; i++) cand[i] = base[i] + surviving[i] * scale;
    current = limit(original, cand, ceiling);
    decoded = await encodeAll(current);
    const s = score(current, decoded);
    if (s > bestScore) { best = current; bestScore = s; }
  }
  return best;
}

/**
 * Protect an image. ``codec`` supplies {training, heldOut} transform lists;
 * pass null to skip hardening and durability measurement.
 */
export async function protect(rgb, w, h, settings, { codec = null, progress = noop, doMeasure = true } = {}) {
  const t0 = Date.now();
  const original = rgb;
  const ctx = new PassContext(original, w, h, settings.seed);
  const active = ALL_PASSES.filter((p) => passStrength(settings, p.name) > 0);
  if (!active.length) {
    return { rgb: Float32Array.from(original), metrics: null, applied: [], seconds: 0,
      note: "No passes enabled - output is identical to the input." };
  }

  const ceiling = deltaCeiling(ctx, settings.strength);
  let current = Float32Array.from(original);
  let geometricDone = null;
  for (let i = 0; i < active.length; i++) {
    const p = active[i];
    progress(`Applying ${p.label.toLowerCase()}`, (0.55 * i) / active.length);
    current = p.apply(ctx, current, passStrength(settings, p.name));
    if (p.geometric) {
      current = limit(original, current, ceiling);
      geometricDone = Float32Array.from(current);
    }
  }
  current = limit(original, current, ceiling);

  let preHarden = null;
  if (settings.harden && codec) {
    preHarden = current;
    current = await harden(original, current, w, h, settings, ceiling, codec,
      geometricDone ?? original, progress, [0.55, 0.85]);
  }

  let metrics = null;
  if (doMeasure) {
    progress("Measuring quality and durability", 0.88);
    metrics = await measure(original, current, w, h, codec,
      { survivalBase: geometricDone, intended: preHarden });
  }
  progress("Done", 1);
  return { rgb: current, metrics, applied: active.map((p) => p.name),
    seconds: (Date.now() - t0) / 1000, note: "" };
}

/** Fast downscaled preview: no hardening, as on desktop. */
export async function preview(rgb, w, h, settings, codec, longest = 1100) {
  const small = resizeLongest(rgb, w, h, longest);
  const result = await protect(small.rgb, small.w, small.h, { ...settings, harden: false },
    { codec, doMeasure: true });
  return { ...result, original: small.rgb, w: small.w, h: small.h };
}
