// The visible watermark on phones. Node has no canvas, so the letters are
// stood in for by solid blocks; the layout, blending and transparency logic
// is the real code.
import { test } from "node:test";
import assert from "node:assert/strict";
import { LAYOUTS, RESISTANCE, applyWatermark, watermarkOptions } from "../www/engine/watermark.js";
import { protect } from "../www/engine/pipeline.js";
import { applyPreset, defaultSettings } from "../www/engine/presets.js";

const blocks = {
  ratio: (text) => 0.6 * text.length,
  stamp(text, size, angle) {
    const w = Math.round(0.6 * text.length * size) + 4, h = Math.round(size) + 4;
    const fill = new Float32Array(w * h), ring = new Float32Array(w * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const edge = x < 2 || y < 2 || x >= w - 2 || y >= h - 2;
        (edge ? ring : fill)[y * w + x] = 1;
      }
    }
    return { fill, ring, w, h, angle };
  },
};

function art(w = 300, h = 200) {
  const rgb = new Float32Array(w * h * 3);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 3;
      rgb[i] = 0.2 + 0.5 * (x / w);
      rgb[i + 1] = 0.4 + 0.3 * (y / h);
      rgb[i + 2] = 0.5 + 0.1 * Math.sin(x * 0.3 + y * 0.2);
    }
  }
  return rgb;
}

function changedMap(a, b, w, h) {
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    let m = 0;
    for (let c = 0; c < 3; c++) m = Math.max(m, Math.abs(a[i * 3 + c] - b[i * 3 + c]));
    out[i] = m > 0.08 ? 1 : 0;
  }
  return out;
}

function share(map, w, x0, y0, x1, y1) {
  let s = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) s += map[y * w + x];
  return s / ((x1 - x0) * (y1 - y0));
}

test("off by default, matching the desktop defaults", () => {
  const s = defaultSettings();
  assert.equal(s.watermark, false);
  assert.equal(watermarkOptions(s), null);
  assert.deepEqual([s.watermarkText, s.watermarkLayout, s.watermarkResistance, s.watermarkOpacity],
    ["SAMPLE", "tiled", "strong", 0.5]);
});

test("every layout and level draws a mark where it should", () => {
  const w = 300, h = 200, img = art(w, h);
  for (const layout of LAYOUTS) {
    for (const resistance of RESISTANCE) {
      const { rgb, alpha } = applyWatermark(img, null, w, h,
        { text: "SAMPLE", layout, resistance, opacity: 0.5, seed: 7 }, blocks);
      assert.equal(alpha, null);
      const map = changedMap(img, rgb, w, h);
      assert.ok(share(map, w, 0, 0, w, h) > 0.001, `${layout}/${resistance} drew something`);
      if (layout === "tiled") {
        for (const [x0, y0] of [[0, 0], [150, 0], [0, 100], [150, 100]]) {
          assert.ok(share(map, w, x0, y0, x0 + 150, y0 + 100) > 0.01, `${resistance} covers every quarter`);
        }
      }
      if (layout === "corner") {
        assert.equal(share(map, w, 0, 0, w, 140), 0, "nothing above the corner");
        assert.equal(share(map, w, 0, 0, 170, h), 0, "nothing left of the corner");
      }
    }
  }
});

test("empty text or zero opacity changes nothing", () => {
  const img = art();
  assert.equal(applyWatermark(img, null, 300, 200, { text: "  " }, blocks).rgb, img);
  assert.equal(applyWatermark(img, null, 300, 200, { opacity: 0 }, blocks).rgb, img);
});

test("transparent areas become visible under the mark", () => {
  const w = 300, h = 200;
  const alpha = new Uint8ClampedArray(w * h);
  for (let y = 80; y < 120; y++) for (let x = 100; x < 200; x++) alpha[y * w + x] = 255;
  const r = applyWatermark(art(w, h), alpha, w, h, { opacity: 0.6, seed: 1 }, blocks);
  assert.ok(r.alpha instanceof Uint8ClampedArray);
  let maxTop = 0;
  for (let i = 0; i < 60 * w; i++) maxTop = Math.max(maxTop, r.alpha[i]);
  assert.ok(maxTop > 50, `mark shows on the transparent area (${maxTop})`);
  assert.equal(r.alpha[100 * w + 150], 255, "opaque stays opaque");
});

test("protect draws the watermark first and returns the new transparency", async () => {
  const w = 120, h = 80, img = art(w, h);
  const settings = { ...applyPreset(defaultSettings(), "light"), watermark: true, watermarkText: "Art sample" };
  const alpha = new Uint8ClampedArray(w * h).fill(255);
  const r = await protect(img, w, h, settings, { doMeasure: true, alpha, glyphs: blocks });
  assert.ok(share(changedMap(img, r.rgb, w, h), w, 0, 0, w, h) > 0.01);
  assert.ok(r.alpha && r.alpha.length === w * h);
  assert.ok(r.metrics.psnr > 30, "figures describe the protection only");
  const off = await protect(img, w, h, applyPreset(defaultSettings(), "light"), { doMeasure: false });
  assert.equal(share(changedMap(img, off.rgb, w, h), w, 0, 0, w, h), 0, "no watermark when off");
});
