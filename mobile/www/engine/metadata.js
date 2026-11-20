// Opt-out metadata. Mirrors desktop/src/mistcoat/core/output.py: the IPTC/PLUS
// "Data Mining" property, written into PNG (iTXt) and JPEG (APP1) byte streams.
//
// Like the desktop app, this does NOT announce "this image was protected by
// Mistcoat" - that would help anyone building a filter to exclude protected
// images from a dataset.

export const PLUS_PROHIBIT_AI = "http://ns.useplus.org/ldf/vocab/DMI-PROHIBITED-AIMLTRAINING";

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function buildXmp({ writeOptout = true, credit = "", copyright = "" } = {}) {
  const parts = [];
  if (writeOptout) {
    const terms = "This image may not be used to train, fine-tune or develop any machine " +
      "learning or generative AI model, or as input to any such model, without the " +
      "express written permission of the copyright holder.";
    parts.push(
      `   <plus:DataMining>${PLUS_PROHIBIT_AI}</plus:DataMining>`,
      "   <xmpRights:Marked>True</xmpRights:Marked>",
      `   <xmpRights:UsageTerms><rdf:Alt><rdf:li xml:lang='x-default'>${esc(terms)}</rdf:li></rdf:Alt></xmpRights:UsageTerms>`,
      "   <mistcoat:Robots>noai, noimageai, noml</mistcoat:Robots>");
  }
  if (copyright) {
    parts.push(`   <dc:rights><rdf:Alt><rdf:li xml:lang='x-default'>${esc(copyright)}</rdf:li></rdf:Alt></dc:rights>`);
  }
  if (credit) {
    parts.push(`   <dc:creator><rdf:Seq><rdf:li>${esc(credit)}</rdf:li></rdf:Seq></dc:creator>`,
      `   <photoshop:Credit>${esc(credit)}</photoshop:Credit>`);
  }
  return [
    "<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>",
    "<x:xmpmeta xmlns:x='adobe:ns:meta/'>",
    " <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>",
    "  <rdf:Description rdf:about=''",
    "    xmlns:dc='http://purl.org/dc/elements/1.1/'",
    "    xmlns:xmpRights='http://ns.adobe.com/xap/1.0/rights/'",
    "    xmlns:photoshop='http://ns.adobe.com/photoshop/1.0/'",
    "    xmlns:plus='http://ns.useplus.org/ldf/xmp/1.0/'",
    "    xmlns:mistcoat='https://mistcoat.app/ns/1.0/'>",
    ...parts,
    "  </rdf:Description>",
    " </rdf:RDF>",
    "</x:xmpmeta>",
    "<?xpacket end='w'?>",
  ].join("\n");
}

// ------------------------------------------------------------------ PNG

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** Insert an uncompressed iTXt XMP chunk right after IHDR. */
export function injectPngXmp(png, xmp) {
  const enc = new TextEncoder();
  const keyword = enc.encode("XML:com.adobe.xmp");
  const text = enc.encode(xmp);
  // keyword \0 compressionFlag compressionMethod lang\0 translated\0 text
  const data = new Uint8Array(keyword.length + 5 + text.length);
  data.set(keyword, 0);
  let o = keyword.length;
  data[o++] = 0; data[o++] = 0; data[o++] = 0; data[o++] = 0; data[o++] = 0;
  data.set(text, o);

  const type = enc.encode("iTXt");
  const chunk = new Uint8Array(12 + data.length);
  const dv = new DataView(chunk.buffer);
  dv.setUint32(0, data.length);
  chunk.set(type, 4);
  chunk.set(data, 8);
  const crcInput = new Uint8Array(4 + data.length);
  crcInput.set(type, 0);
  crcInput.set(data, 4);
  dv.setUint32(8 + data.length, crc32(crcInput));

  // PNG signature (8) + IHDR chunk (4 len + 4 type + 13 data + 4 crc = 25).
  const ihdrEnd = 8 + 25;
  const out = new Uint8Array(png.length + chunk.length);
  out.set(png.subarray(0, ihdrEnd), 0);
  out.set(chunk, ihdrEnd);
  out.set(png.subarray(ihdrEnd), ihdrEnd + chunk.length);
  return out;
}

// ----------------------------------------------------------------- JPEG

/** Insert an APP1 XMP segment after SOI (and after APP0/JFIF if present). */
export function injectJpegXmp(jpeg, xmp) {
  const enc = new TextEncoder();
  const ns = enc.encode("http://ns.adobe.com/xap/1.0/\0");
  const body = enc.encode(xmp);
  const len = 2 + ns.length + body.length;
  if (len > 0xffff) throw new Error("XMP packet too large for one JPEG segment");
  const seg = new Uint8Array(2 + len);
  seg[0] = 0xff; seg[1] = 0xe1; seg[2] = len >> 8; seg[3] = len & 0xff;
  seg.set(ns, 4);
  seg.set(body, 4 + ns.length);

  let at = 2; // after SOI
  if (jpeg[2] === 0xff && jpeg[3] === 0xe0) at = 4 + ((jpeg[4] << 8) | jpeg[5]);
  const out = new Uint8Array(jpeg.length + seg.length);
  out.set(jpeg.subarray(0, at), 0);
  out.set(seg, at);
  out.set(jpeg.subarray(at), at + seg.length);
  return out;
}
