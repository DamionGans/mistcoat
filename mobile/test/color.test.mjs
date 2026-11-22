// Colour profiles on phones: reading, converting and re-embedding them.
import { test } from "node:test";
import assert from "node:assert/strict";
import jpeg from "jpeg-js";
import { PNG } from "pngjs";
import {
  DISPLAY_P3, DISPLAY_P3_ICC, SRGB, buildIcc, D65_XY, embedIcc, extractIcc, iccName,
  makeConverter, parseIcc, sniff,
} from "../www/engine/color.js";

const ADOBE = buildIcc("Adobe RGB test", D65_XY, [[0.64, 0.33], [0.21, 0.71], [0.15, 0.06]]);

function tinyJpeg() {
  const data = Buffer.alloc(8 * 8 * 4, 200);
  return new Uint8Array(jpeg.encode({ data, width: 8, height: 8 }, 90).data);
}

function tinyPng() {
  const png = new PNG({ width: 4, height: 4 });
  png.data.fill(128);
  return new Uint8Array(PNG.sync.write(png));
}

test("a built profile parses back to the same colorimetry", () => {
  const p = parseIcc(DISPLAY_P3_ICC);
  assert.ok(p, "parsed");
  assert.equal(iccName(DISPLAY_P3_ICC), "Display P3");
  for (let r = 0; r < 3; r++) {
    for (let c = 0; c < 3; c++) assert.ok(Math.abs(p.toXYZ[r][c] - DISPLAY_P3.toXYZ[r][c]) < 1e-3);
  }
  for (const v of [0, 0.02, 0.2, 0.5, 0.9, 1]) {
    assert.ok(Math.abs(p.decode[0](v) - DISPLAY_P3.decode[0](v)) < 2e-3, `curve at ${v}`);
  }
});

test("converting: identity, clipping a wide red, keeping neutrals", () => {
  const same = makeConverter(SRGB, SRGB)(new Uint8ClampedArray([10, 128, 250, 255]));
  assert.deepEqual([...same], [10, 128, 250, 255]);
  const red = makeConverter(DISPLAY_P3, SRGB)(new Uint8ClampedArray([255, 0, 0, 255]));
  assert.equal(red[0], 255);
  assert.ok(red[1] <= 2 && red[2] <= 2, "P3 red clips to sRGB red");
  const srgbRedInP3 = makeConverter(SRGB, DISPLAY_P3)(new Uint8ClampedArray([255, 0, 0, 255]));
  assert.ok(Math.abs(srgbRedInP3[0] - 234) <= 2 && Math.abs(srgbRedInP3[1] - 51) <= 3, `${srgbRedInP3}`);
  const grey = makeConverter(parseIcc(ADOBE), SRGB)(new Uint8ClampedArray([128, 128, 128, 255]));
  assert.ok(Math.abs(grey[0] - grey[2]) <= 1 && Math.abs(grey[0] - 128) <= 4, `${grey}`);
});

test("JPEG: embed, then extract the same profile", async () => {
  const j = tinyJpeg();
  assert.equal(await extractIcc(j), null);
  const tagged = await embedIcc(j, ADOBE);
  assert.equal(sniff(tagged), "jpeg");
  assert.deepEqual(await extractIcc(tagged), ADOBE);
  assert.ok(jpeg.decode(Buffer.from(tagged)).width === 8, "still a valid JPEG");
  // Re-embedding replaces rather than duplicates.
  const again = await embedIcc(tagged, DISPLAY_P3_ICC);
  assert.deepEqual(await extractIcc(again), DISPLAY_P3_ICC);
});

test("JPEG: a profile larger than one segment is split and rejoined", async () => {
  const big = new Uint8Array(150_000).map((_, i) => i % 251);
  big.set(ADOBE.subarray(0, 40));
  const tagged = await embedIcc(tinyJpeg(), big);
  assert.deepEqual(await extractIcc(tagged), big);
});

test("PNG: iCCP round trip, still a valid PNG", async () => {
  const tagged = await embedIcc(tinyPng(), ADOBE);
  assert.deepEqual(await extractIcc(tagged), ADOBE);
  assert.equal(PNG.sync.read(Buffer.from(tagged)).width, 4);
});
