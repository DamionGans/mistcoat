// Colour profiles. Mirrors the idea of desktop/src/mistcoat/core/color.py.
//
// Browsers decode images to sRGB (or the screen's space) by default, which
// clips wide-gamut art and loses its profile. Instead, images whose profile is
// a matrix/TRC RGB profile (sRGB, Display P3, Adobe RGB, ProPhoto, most camera
// and scanner profiles) are decoded WITHOUT conversion, protected in their own
// values, and saved with the same profile embedded. The preview converts those
// values to the screen's space here, in JavaScript.
//
// Anything else (CMYK, greyscale or table-based profiles, HEIC whose profile
// we do not parse) is converted by the browser into Display P3 where the
// device supports it, and saved with a Display P3 profile, so nothing is
// clipped to sRGB.

// --------------------------------------------------------------- matrices

const BRADFORD = [[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]];
const D50 = [0.9642, 1.0, 0.8249];
export const D65_XY = [0.3127, 0.3290];

const mul = (a, b) => a.map((r) => [0, 1, 2].map((j) => r[0] * b[0][j] + r[1] * b[1][j] + r[2] * b[2][j]));
const apply = (m, v) => m.map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]);

function inv(m) {
  const [[a, b, c], [d, e, f], [g, h, i]] = m;
  const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [
    [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
    [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
    [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
  ];
}

const xyz = ([x, y]) => [x / y, 1, (1 - x - y) / y];

/** RGB -> XYZ (D50-adapted, as ICC profiles store it) for primaries and white. */
export function primariesToXYZ(white, primaries) {
  const w = xyz(white);
  const p = [0, 1, 2].map((r) => primaries.map((pr) => xyz(pr)[r]));   // columns = primaries
  const s = apply(inv(p), w);
  const m = p.map((r) => r.map((v, j) => v * s[j]));
  const cs = apply(BRADFORD, w), cd = apply(BRADFORD, D50);
  const diag = [[cd[0] / cs[0], 0, 0], [0, cd[1] / cs[1], 0], [0, 0, cd[2] / cs[2]]];
  return mul(mul(mul(inv(BRADFORD), diag), BRADFORD), m);
}

// --------------------------------------------------------------- curves

const srgbDecode = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
const srgbEncode = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);

/** A profile as this module uses it: decode curves (encoded -> linear) and
 *  the matrix to D50 XYZ. */
function makeProfile(name, toXYZ, decode) {
  return { name, toXYZ, decode: [decode, decode, decode] };
}

export const SRGB = makeProfile("sRGB",
  primariesToXYZ(D65_XY, [[0.64, 0.33], [0.30, 0.60], [0.15, 0.06]]), srgbDecode);
export const DISPLAY_P3 = makeProfile("Display P3",
  primariesToXYZ(D65_XY, [[0.680, 0.320], [0.265, 0.690], [0.150, 0.060]]), srgbDecode);

// --------------------------------------------------------------- reading ICC

const tag4 = (b, o) => String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]);
const s15 = (dv, o) => dv.getInt32(o) / 65536;

export function iccSpace(icc) {
  return icc && icc.length >= 20 ? tag4(icc, 16).trim() : null;
}

export function iccName(icc) {
  if (!icc) return null;
  try {
    const dv = new DataView(icc.buffer, icc.byteOffset, icc.byteLength);
    const tags = readTags(icc, dv);
    const t = tags.desc;
    if (!t) return null;
    const type = tag4(icc, t.offset);
    if (type === "desc") {
      const n = dv.getUint32(t.offset + 8);
      return new TextDecoder("latin1").decode(icc.subarray(t.offset + 12, t.offset + 12 + n)).replace(/\0+$/, "").trim();
    }
    if (type === "mluc") {
      const len = dv.getUint32(t.offset + 20), off = dv.getUint32(t.offset + 24);
      const bytes = icc.subarray(t.offset + off, t.offset + off + len);
      let s = "";
      for (let i = 0; i + 1 < bytes.length; i += 2) s += String.fromCharCode((bytes[i] << 8) | bytes[i + 1]);
      return s.trim();
    }
  } catch { /* unreadable: no name */ }
  return null;
}

function readTags(icc, dv) {
  const n = dv.getUint32(128);
  const tags = {};
  for (let i = 0; i < n && 132 + 12 * i + 12 <= icc.length; i++) {
    const o = 132 + 12 * i;
    tags[tag4(icc, o)] = { offset: dv.getUint32(o + 4), size: dv.getUint32(o + 8) };
  }
  return tags;
}

function readCurve(icc, dv, t) {
  const type = tag4(icc, t.offset);
  if (type === "curv") {
    const n = dv.getUint32(t.offset + 8);
    if (n === 0) return (v) => v;
    if (n === 1) {
      const g = dv.getUint16(t.offset + 12) / 256;
      return (v) => v ** g;
    }
    const table = new Float64Array(n);
    for (let i = 0; i < n; i++) table[i] = dv.getUint16(t.offset + 12 + 2 * i) / 65535;
    return (v) => {
      const x = Math.min(Math.max(v, 0), 1) * (n - 1), i = Math.floor(x), f = x - i;
      return i >= n - 1 ? table[n - 1] : table[i] * (1 - f) + table[i + 1] * f;
    };
  }
  if (type === "para") {
    const fn = dv.getUint16(t.offset + 8);
    const p = [0, 1, 2, 3, 4, 5, 6].map((k) => (t.offset + 12 + 4 * k + 4 <= icc.length ? s15(dv, t.offset + 12 + 4 * k) : 0));
    const [g, a, b, c, d, e, f] = p;
    switch (fn) {
      case 0: return (x) => x ** g;
      case 1: return (x) => (x >= -b / a ? (a * x + b) ** g : 0);
      case 2: return (x) => (x >= -b / a ? (a * x + b) ** g + c : c);
      case 3: return (x) => (x >= d ? (a * x + b) ** g : c * x);
      case 4: return (x) => (x >= d ? (a * x + b) ** g + e : c * x + f);
      default: return null;
    }
  }
  return null;
}

/** A matrix/TRC RGB profile -> { name, toXYZ, decode }, or null when the
 *  profile is something else (CMYK, grey, table-based). */
export function parseIcc(icc) {
  try {
    if (!icc || icc.length < 132 || iccSpace(icc) !== "RGB" || tag4(icc, 20) !== "XYZ ") return null;
    const dv = new DataView(icc.buffer, icc.byteOffset, icc.byteLength);
    const tags = readTags(icc, dv);
    const need = ["rXYZ", "gXYZ", "bXYZ", "rTRC", "gTRC", "bTRC"];
    if (!need.every((k) => tags[k])) return null;
    const col = (k) => [0, 1, 2].map((i) => s15(dv, tags[k].offset + 8 + 4 * i));
    const r = col("rXYZ"), g = col("gXYZ"), b = col("bXYZ");
    const toXYZ = [[r[0], g[0], b[0]], [r[1], g[1], b[1]], [r[2], g[2], b[2]]];
    const decode = ["rTRC", "gTRC", "bTRC"].map((k) => readCurve(icc, dv, tags[k]));
    if (decode.some((f) => !f)) return null;
    return { name: iccName(icc) || "embedded profile", toXYZ, decode };
  } catch {
    return null;
  }
}

// --------------------------------------------------------------- converting

/** A function converting RGBA bytes (in place) from one profile to another:
 *  relative colorimetric, clipped, as the desktop preview does. */
export function makeConverter(src, dst) {
  const m = mul(inv(dst.toXYZ), src.toXYZ);
  const lin = src.decode.map((f) => {
    const t = new Float32Array(256);
    for (let i = 0; i < 256; i++) t[i] = f(i / 255);
    return t;
  });
  // Encode via a 4096-step table of the destination's inverse curve. Both
  // destinations we draw to (sRGB and Display P3) use the sRGB curve.
  const N = 4096;
  const enc = new Uint8ClampedArray(N + 1);
  for (let i = 0; i <= N; i++) enc[i] = Math.round(srgbEncode(i / N) * 255);
  const [m0, m1, m2] = m;
  return (rgba) => {
    for (let i = 0; i < rgba.length; i += 4) {
      const r = lin[0][rgba[i]], g = lin[1][rgba[i + 1]], b = lin[2][rgba[i + 2]];
      const R = m0[0] * r + m0[1] * g + m0[2] * b;
      const G = m1[0] * r + m1[1] * g + m1[2] * b;
      const B = m2[0] * r + m2[1] * g + m2[2] * b;
      rgba[i] = enc[Math.round(Math.min(Math.max(R, 0), 1) * N)];
      rgba[i + 1] = enc[Math.round(Math.min(Math.max(G, 0), 1) * N)];
      rgba[i + 2] = enc[Math.round(Math.min(Math.max(B, 0), 1) * N)];
    }
    return rgba;
  };
}

// --------------------------------------------------------------- writing ICC

function s15bytes(v) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setInt32(0, Math.round(v * 65536));
  return b;
}

function cat(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

const ascii = (s) => new TextEncoder().encode(s);

/** An ICC v2 matrix/TRC profile with the sRGB tone curve (the curve Display
 *  P3 uses), as a 1024-entry table so every v2 reader understands it. */
export function buildIcc(name, white, primaries) {
  const m = primariesToXYZ(white, primaries);
  const xyzTag = (v) => cat([ascii("XYZ "), new Uint8Array(4), ...v.map(s15bytes)]);
  const n = 1024;
  const curve = new Uint8Array(12 + 2 * n);
  const cdv = new DataView(curve.buffer);
  curve.set(ascii("curv"));
  cdv.setUint32(8, n);
  for (let i = 0; i < n; i++) cdv.setUint16(12 + 2 * i, Math.round(srgbDecode(i / (n - 1)) * 65535));
  const descText = ascii(name + "\0");
  const desc = cat([ascii("desc"), new Uint8Array(4), (() => { const b = new Uint8Array(4); new DataView(b.buffer).setUint32(0, descText.length); return b; })(),
    descText, new Uint8Array(8), new Uint8Array(70)]);
  const w = xyz(white);
  const tags = [
    ["desc", desc], ["cprt", cat([ascii("text"), new Uint8Array(4), ascii("No copyright, use freely\0")])],
    ["wtpt", xyzTag(w)],
    ["rXYZ", xyzTag([m[0][0], m[1][0], m[2][0]])], ["gXYZ", xyzTag([m[0][1], m[1][1], m[2][1]])],
    ["bXYZ", xyzTag([m[0][2], m[1][2], m[2][2]])],
    ["rTRC", curve], ["gTRC", curve], ["bTRC", curve],
  ];
  const pad = (b) => cat([b, new Uint8Array((4 - (b.length % 4)) % 4)]);
  let offset = 128 + 4 + 12 * tags.length;
  const table = [], data = [];
  let curveAt = null;
  for (const [sig, body] of tags) {
    if (body === curve && curveAt) { table.push([sig, ...curveAt]); continue; }
    const b = pad(body);
    table.push([sig, offset, b.length]);
    if (body === curve) curveAt = [offset, b.length];
    data.push(b);
    offset += b.length;
  }
  const head = new Uint8Array(128);
  const hdv = new DataView(head.buffer);
  hdv.setUint32(0, offset);
  hdv.setUint32(8, 0x02100000);
  head.set(ascii("mntrRGB XYZ "), 12);
  head.set(ascii("acsp"), 36);
  D50.forEach((v, i) => head.set(s15bytes(v), 68 + 4 * i));
  const tt = new Uint8Array(4 + 12 * table.length);
  const tdv = new DataView(tt.buffer);
  tdv.setUint32(0, table.length);
  table.forEach(([sig, o, len], i) => { tt.set(ascii(sig), 4 + 12 * i); tdv.setUint32(8 + 12 * i, o); tdv.setUint32(12 + 12 * i, len); });
  return cat([head, tt, ...data]);
}

export const DISPLAY_P3_ICC = buildIcc("Display P3", D65_XY, [[0.680, 0.320], [0.265, 0.690], [0.150, 0.060]]);

// --------------------------------------------------------------- containers

export function sniff(b) {
  if (b[0] === 0xff && b[1] === 0xd8) return "jpeg";
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return "png";
  if (tag4(b, 0) === "RIFF" && tag4(b, 8) === "WEBP") return "webp";
  return null;
}

async function inflate(bytes) {
  const s = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(s).arrayBuffer());
}

async function deflate(bytes) {
  const s = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate"));
  return new Uint8Array(await new Response(s).arrayBuffer());
}

function* jpegSegments(b) {
  let o = 2;
  while (o + 4 <= b.length && b[o] === 0xff) {
    const marker = b[o + 1];
    if (marker === 0xda || marker === 0xd9) return;          // image data starts
    const len = (b[o + 2] << 8) | b[o + 3];
    yield { marker, start: o, end: o + 2 + len, data: b.subarray(o + 4, o + 2 + len) };
    o += 2 + len;
  }
}

function* pngChunks(b) {
  let o = 8;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  while (o + 12 <= b.length) {
    const len = dv.getUint32(o);
    const type = tag4(b, o + 4);
    yield { type, start: o, end: o + 12 + len, data: b.subarray(o + 8, o + 8 + len) };
    if (type === "IEND") return;
    o += 12 + len;
  }
}

function* webpChunks(b) {
  let o = 12;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  while (o + 8 <= b.length) {
    const len = dv.getUint32(o + 4, true);
    yield { type: tag4(b, o), start: o, end: o + 8 + len + (len & 1), data: b.subarray(o + 8, o + 8 + len) };
    o += 8 + len + (len & 1);
  }
}

const ICC_MARK = ascii("ICC_PROFILE\0");
const isIccSegment = (s) => s.marker === 0xe2 && ICC_MARK.every((v, i) => s.data[i] === v);

/** The embedded ICC profile of a JPEG, PNG or WebP file, or null. */
export async function extractIcc(bytes) {
  try {
    const kind = sniff(bytes);
    if (kind === "jpeg") {
      const parts = [];
      for (const s of jpegSegments(bytes)) if (isIccSegment(s)) parts.push([s.data[12], s.data.subarray(14)]);
      return parts.length ? cat(parts.sort((a, b) => a[0] - b[0]).map((p) => p[1])) : null;
    }
    if (kind === "png") {
      for (const c of pngChunks(bytes)) {
        if (c.type === "iCCP") {
          const z = c.data.indexOf(0);
          return await inflate(c.data.subarray(z + 2));
        }
        if (c.type === "IDAT") break;
      }
      return null;
    }
    if (kind === "webp") {
      for (const c of webpChunks(bytes)) if (c.type === "ICCP") return new Uint8Array(c.data);
    }
  } catch { /* malformed: treat as untagged */ }
  return null;
}

function pngChunk(type, data) {
  const body = cat([ascii(type), data]);
  const out = new Uint8Array(12 + data.length);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, data.length);
  out.set(body, 4);
  dv.setUint32(8 + data.length, crc32(body));
  return out;
}

const CRC = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(b) {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** Replace whatever colour description an encoded file has with ``icc``
 *  (JPEG APP2, PNG iCCP, WebP ICCP), optionally adding XMP to a WebP. */
export async function embedIcc(bytes, icc, { webpXmp = "" } = {}) {
  const kind = sniff(bytes);
  if (kind === "jpeg") {
    if (!icc) return bytes;
    const keep = [bytes.subarray(0, 2)];
    let insertAt = 1, tail = 2;
    let idx = 0;
    for (const s of jpegSegments(bytes)) {
      tail = s.end;
      if (isIccSegment(s)) continue;
      keep.push(bytes.subarray(s.start, s.end));
      idx++;
      if (s.marker === 0xe0 || s.marker === 0xe1) insertAt = idx + 1;   // after JFIF/EXIF/XMP
    }
    const max = 65519, count = Math.ceil(icc.length / max);
    const segs = [];
    for (let i = 0; i < count; i++) {
      const part = icc.subarray(i * max, (i + 1) * max);
      const len = 2 + ICC_MARK.length + 2 + part.length;
      segs.push(cat([new Uint8Array([0xff, 0xe2, len >> 8, len & 0xff]), ICC_MARK,
        new Uint8Array([i + 1, count]), part]));
    }
    keep.splice(insertAt, 0, ...segs);
    return cat([...keep, bytes.subarray(tail)]);
  }
  if (kind === "png") {
    if (!icc) return bytes;
    const parts = [bytes.subarray(0, 8)];
    for (const c of pngChunks(bytes)) {
      if (["iCCP", "sRGB", "gAMA", "cHRM"].includes(c.type)) continue;
      parts.push(bytes.subarray(c.start, c.end));
      if (c.type === "IHDR") {
        parts.push(pngChunk("iCCP", cat([ascii("ICC profile\0\0"), await deflate(icc)])));
      }
    }
    return cat(parts);
  }
  if (kind === "webp") return webpWith(bytes, icc, webpXmp);
  return bytes;
}

function riffChunk(type, data) {
  const head = new Uint8Array(8);
  head.set(ascii(type));
  new DataView(head.buffer).setUint32(4, data.length, true);
  return cat([head, data, new Uint8Array(data.length & 1)]);
}

/** Rebuild a WebP as the extended format with an ICC profile and XMP. */
function webpWith(bytes, icc, xmp) {
  const chunks = [...webpChunks(bytes)];
  let w = 0, h = 0, alpha = false;
  for (const c of chunks) {
    const d = c.data;
    if (c.type === "VP8X") {
      w = 1 + (d[4] | (d[5] << 8) | (d[6] << 16));
      h = 1 + (d[7] | (d[8] << 8) | (d[9] << 16));
      alpha = alpha || !!(d[0] & 0x10);
    } else if (c.type === "VP8 " && !w) {
      w = (d[6] | (d[7] << 8)) & 0x3fff;
      h = (d[8] | (d[9] << 8)) & 0x3fff;
    } else if (c.type === "VP8L" && !w) {
      const bits = d[1] | (d[2] << 8) | (d[3] << 16) | (d[4] << 24);
      w = (bits & 0x3fff) + 1;
      h = ((bits >> 14) & 0x3fff) + 1;
      alpha = alpha || !!((bits >> 28) & 1);
    } else if (c.type === "ALPH") alpha = true;
  }
  const image = chunks.filter((c) => !["VP8X", "ICCP", "XMP ", "EXIF"].includes(c.type));
  const flags = (icc ? 0x20 : 0) | (alpha ? 0x10 : 0) | (xmp ? 0x04 : 0);
  const vp8x = new Uint8Array(10);
  vp8x[0] = flags;
  [w - 1, h - 1].forEach((v, k) => { vp8x[4 + 3 * k] = v & 0xff; vp8x[5 + 3 * k] = (v >> 8) & 0xff; vp8x[6 + 3 * k] = (v >> 16) & 0xff; });
  const body = cat([
    ascii("WEBP"), riffChunk("VP8X", vp8x),
    ...(icc ? [riffChunk("ICCP", icc)] : []),
    ...image.map((c) => bytes.subarray(c.start, c.end)),
    ...(xmp ? [riffChunk("XMP ", ascii(xmp))] : []),
  ]);
  const head = new Uint8Array(8);
  head.set(ascii("RIFF"));
  new DataView(head.buffer).setUint32(4, body.length, true);
  return cat([head, body]);
}
