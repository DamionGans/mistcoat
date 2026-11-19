// Tests for the JavaScript engine (node --test). Mirrors tests/test_core.py,
// plus a cross-engine check against the desktop engine's measured output.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import jpeg from "jpeg-js";
import { PNG } from "pngjs";

import { blur, dct8, fromRGBA, idct8, rgbToYcc, yccToRgb, resizeArea } from "../www/engine/imaging.js";
import { buildXmp, crc32, injectJpegXmp, injectPngXmp } from "../www/engine/metadata.js";
import { ALL_PASSES } from "../www/engine/passes.js";
import { deltaCeiling, protect } from "../www/engine/pipeline.js";
import { PassContext } from "../www/engine/passes.js";
import { applyPreset, defaultSettings, PRESETS } from "../www/engine/presets.js";
import { psnr } from "../www/engine/quality.js";

// ------------------------------------------------------------- node codec

function jpegRoundtrip(rgb, w, h, q) {
  const data = Buffer.alloc(w * h * 4);
  for (let i = 0, j = 0; i < w * h; i++, j += 4) {
    data[j] = rgb[i * 3] * 255 + 0.5; data[j + 1] = rgb[i * 3 + 1] * 255 + 0.5;
    data[j + 2] = rgb[i * 3 + 2] * 255 + 0.5; data[j + 3] = 255;
  }
  const enc = jpeg.encode({ data, width: w, height: h }, q);
  const dec = jpeg.decode(enc.data, { useTArray: true });
  return fromRGBA(dec.data, w, h).rgb;
}

function rescaleRoundtrip(rgb, w, h, f, q) {
  const sw = Math.max(1, Math.round(w * f)), sh = Math.max(1, Math.round(h * f));
  const small = jpegRoundtrip(resizeArea(rgb, w, h, sw, sh), sw, sh, q);
  // Nearest-neighbour back up is enough for a test codec.
  const out = new Float32Array(w * h * 3);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = Math.min(sw - 1, Math.floor(x * f)), sy = Math.min(sh - 1, Math.floor(y * f));
    for (let c = 0; c < 3; c++) out[(y * w + x) * 3 + c] = small[(sy * sw + sx) * 3 + c];
  }
  return out;
}

const codec = {
  training: [
    { name: "JPEG q72", fn: async (x, w, h) => jpegRoundtrip(x, w, h, 72) },
    { name: "Resize 80%", fn: async (x, w, h) => rescaleRoundtrip(x, w, h, 0.8, 75) },
  ],
  heldOut: [
    { name: "JPEG q85", fn: async (x, w, h) => jpegRoundtrip(x, w, h, 85) },
    { name: "JPEG q60", fn: async (x, w, h) => jpegRoundtrip(x, w, h, 60) },
    { name: "WebP (unavailable)", fn: async () => null },
  ],
};

// ------------------------------------------------------------------ fixtures

function makeArt(h = 160, w = 200) {
  const rgb = new Float32Array(w * h * 3);
  let seed = 3;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296) - 0.5;
  const noise = new Float32Array(w * h).map(() => rnd());
  const tex = blur(noise, w, h, 3.0);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = y * w + x, j = i * 3;
    let r = 0.3 + 0.4 * (x / w), g = 0.5, b = 0.7 - 0.3 * (y / h);
    r += tex[i] * 0.2; g += tex[i] * 0.2; b += tex[i] * 0.2;
    if (y > 20 && y < 60 && x > 20 && x < 80) { r = 0.9; g = 0.2; b = 0.2; }
    if (y > 110) { r *= 0.25; g *= 0.25; b *= 0.25; }
    rgb[j] = Math.min(1, Math.max(0, r)); rgb[j + 1] = Math.min(1, Math.max(0, g));
    rgb[j + 2] = Math.min(1, Math.max(0, b));
  }
  return { rgb, w, h };
}

const balanced = () => ({ ...applyPreset(defaultSettings(), "balanced"), harden: false });
const meanAbs = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - b[i]), 0) / a.length;
const maxAbs = (a, b) => a.reduce((s, v, i) => Math.max(s, Math.abs(v - b[i])), 0);

// --------------------------------------------------------------- primitives

test("DCT round trip is lossless", () => {
  const b = new Float32Array(64).map((_, i) => Math.sin(i) * 100);
  const orig = Float32Array.from(b);
  dct8(b); idct8(b);
  for (let i = 0; i < 64; i++) assert.ok(Math.abs(b[i] - orig[i]) < 1e-3);
});

test("YCbCr round trip", () => {
  const a = new Float32Array(30).map((_, i) => (i * 0.37) % 1);
  const back = yccToRgb(rgbToYcc(a));
  for (let i = 0; i < a.length; i++) assert.ok(Math.abs(back[i] - a[i]) < 1e-5);
});

test("blur preserves the mean", () => {
  const a = new Float32Array(1600).map((_, i) => (i * 7919 % 1000) / 1000);
  const m = (x) => x.reduce((s, v) => s + v, 0) / x.length;
  assert.ok(Math.abs(m(blur(a, 40, 40, 3)) - m(a)) < 0.01);
});

// ----------------------------------------------------------------- behaviour

test("output differs from input and stays in range", async () => {
  const { rgb, w, h } = makeArt();
  const r = await protect(rgb, w, h, { ...balanced(), strength: 1 }, { doMeasure: false });
  assert.ok(meanAbs(r.rgb, rgb) > 0.0005, "protection was a no-op");
  assert.ok(r.rgb.every((v) => Number.isFinite(v) && v >= 0 && v <= 1));
});

test("deterministic for the same seed; seed changes the output", async () => {
  const { rgb, w, h } = makeArt();
  const a = (await protect(rgb, w, h, balanced(), { doMeasure: false })).rgb;
  const b = (await protect(rgb, w, h, balanced(), { doMeasure: false })).rgb;
  const c = (await protect(rgb, w, h, { ...balanced(), seed: 7 }, { doMeasure: false })).rgb;
  assert.deepEqual(a, b);
  assert.notDeepEqual(a, c);
});

test("no passes means no change", async () => {
  const { rgb, w, h } = makeArt();
  const off = { ...balanced(), enabled: Object.fromEntries(ALL_PASSES.map((p) => [p.name, false])) };
  const r = await protect(rgb, w, h, off, { doMeasure: false });
  assert.deepEqual(r.rgb, rgb);
});

test("strength is monotonic", async () => {
  const { rgb, w, h } = makeArt();
  const d = [];
  for (const s of [0.1, 0.35, 0.6, 0.85, 1.0]) {
    d.push(meanAbs((await protect(rgb, w, h, { ...balanced(), strength: s }, { doMeasure: false })).rgb, rgb));
  }
  assert.deepEqual(d, [...d].sort((x, y) => x - y), `not monotonic: ${d}`);
});

test("fidelity ceiling is enforced, hardening included", async () => {
  const { rgb, w, h } = makeArt();
  for (const s of [0.1, 0.5, 1.0]) {
    const settings = { ...balanced(), strength: s, harden: true,
      enabled: Object.fromEntries(ALL_PASSES.map((p) => [p.name, true])) };
    const r = await protect(rgb, w, h, settings, { codec, doMeasure: false });
    const allowed = 0.012 + 0.030 * s;
    assert.ok(maxAbs(r.rgb, rgb) <= allowed + 1e-5, `strength ${s} exceeded ceiling`);
  }
});

test("presets order by visible change", async () => {
  const { rgb, w, h } = makeArt();
  const p = [];
  for (const preset of PRESETS) {
    const s = { ...applyPreset(defaultSettings(), preset.key), harden: false };
    p.push(psnr(rgb, (await protect(rgb, w, h, s, { doMeasure: false })).rgb));
  }
  assert.deepEqual(p, [...p].sort((a, b) => b - a), `out of order: ${p}`);
});

test("each pass does something", async () => {
  const { rgb, w, h } = makeArt();
  for (const pass of ALL_PASSES) {
    const s = { ...balanced(), strength: 1,
      enabled: Object.fromEntries(ALL_PASSES.map((q) => [q.name, q.name === pass.name])) };
    const r = await protect(rgb, w, h, s, { doMeasure: false });
    assert.ok(meanAbs(r.rgb, rgb) > 0, `${pass.name} did nothing`);
  }
});

test("hardening never lowers durability; unavailable transforms are skipped", async () => {
  const { rgb, w, h } = makeArt();
  const soft = await protect(rgb, w, h, balanced(), { codec });
  const hard = await protect(rgb, w, h, { ...balanced(), harden: true }, { codec });
  assert.ok(soft.metrics.survival > 0 && soft.metrics.survival <= 1);
  assert.ok(hard.metrics.survival >= soft.metrics.survival - 0.03,
    `${hard.metrics.survival} < ${soft.metrics.survival}`);
});

test("warp alone is not counted as surviving signal", async () => {
  const { rgb, w, h } = makeArt();
  const s = { ...balanced(), strength: 1, harden: true,
    enabled: Object.fromEntries(ALL_PASSES.map((p) => [p.name, p.name === "texture_warp"])) };
  const r = await protect(rgb, w, h, s, { codec });
  assert.ok(r.metrics.survival < 0.05);
});

test("odd sizes and tiny images", async () => {
  for (const [h, w] of [[103, 61], [5, 7]]) {
    const { rgb } = makeArt(h, w);
    const r = await protect(rgb, w, h, { ...balanced(), strength: 1 }, { doMeasure: false });
    assert.equal(r.rgb.length, rgb.length);
    assert.ok(r.rgb.every(Number.isFinite));
  }
});

// ------------------------------------------------------------------ metadata

test("PNG XMP injection produces a valid chunk", () => {
  const png = new PNG({ width: 4, height: 4 });
  png.data.fill(128);
  const bytes = new Uint8Array(PNG.sync.write(png));
  const out = injectPngXmp(bytes, buildXmp({ credit: "A. Painter" }));
  const decoded = PNG.sync.read(Buffer.from(out));   // throws on a bad CRC
  assert.equal(decoded.width, 4);
  const text = Buffer.from(out).toString("latin1");
  assert.ok(text.includes("DMI-PROHIBITED-AIMLTRAINING"));
  assert.ok(text.includes("A. Painter"));
});

test("JPEG XMP injection keeps the file decodable", () => {
  const data = Buffer.alloc(8 * 8 * 4, 200);
  const enc = jpeg.encode({ data, width: 8, height: 8 }, 90).data;
  const out = injectJpegXmp(new Uint8Array(enc), buildXmp({}));
  const dec = jpeg.decode(Buffer.from(out));
  assert.equal(dec.width, 8);
  assert.ok(Buffer.from(out).toString("latin1").includes("DMI-PROHIBITED-AIMLTRAINING"));
});

test("protection marker is never written", () => {
  assert.ok(!buildXmp({}).includes("Protected"));
});

test("crc32 matches the PNG spec check value", () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
});

// --------------------------------------------------------------- cross-engine

test("matches the desktop engine's visible cost on the sample painting", async () => {
  const png = PNG.sync.read(readFileSync(new URL("../../samples/test_painting.png", import.meta.url)));
  const { rgb } = fromRGBA(png.data, png.width, png.height);
  const s = { ...applyPreset(defaultSettings(), "balanced"), harden: false };
  const r = await protect(rgb, png.width, png.height, s, { doMeasure: false });
  const p = psnr(rgb, r.rgb);
  const peak = maxAbs(r.rgb, rgb) * 255;
  // Desktop engine, same preset, no hardening: 39.1 dB, 8/255 peak.
  assert.ok(Math.abs(p - 39.1) < 1.5, `PSNR ${p.toFixed(2)} vs desktop 39.1`);
  assert.ok(peak <= 8.5, `peak ${peak.toFixed(1)}/255 vs desktop 8/255`);
});
