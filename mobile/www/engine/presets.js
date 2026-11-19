// Settings and presets. Mirrors mistcoat/core/presets.py.
import { ALL_PASSES } from "./passes.js";

export function defaultSettings() {
  return {
    strength: 0.6,
    enabled: Object.fromEntries(ALL_PASSES.map((p) => [p.name, p.defaultOn])),
    weights: Object.fromEntries(ALL_PASSES.map((p) => [p.name, 1.0])),
    harden: true,
    hardenIterations: 2,
    seed: 20260101,
    writeOptout: true,
    credit: "",
    copyright: "",
    outputFormat: "png", // "png" | "jpeg"
    jpegQuality: 0.95,
  };
}

export function passStrength(settings, name) {
  if (!settings.enabled[name]) return 0;
  return Math.max(0, Math.min(1, settings.strength * (settings.weights[name] ?? 1)));
}

const SIGNAL = ["texture_warp", "dct_poison", "chroma", "hf_noise"];

export const PRESETS = [
  {
    key: "light", label: "Light", strength: 0.32,
    enabled: ["dct_poison", "chroma", "hf_noise"],
    blurb: "Imperceptible at 100%. Safe for print proofs and client previews. Modest " +
      "protection: best treated as a deterrent, not a wall.",
  },
  {
    key: "balanced", label: "Balanced", strength: 0.6, enabled: SIGNAL,
    blurb: "The default. Invisible in normal viewing, including full screen. Disrupts " +
      "texture, colour and style learning, and survives being re-compressed by a " +
      "gallery or social platform.",
  },
  {
    key: "strong", label: "Strong", strength: 0.85, enabled: SIGNAL,
    blurb: "A faint texture may be visible if someone pixel-peeps a flat area. " +
      "Recommended for anything posted publicly where mimicry is the actual worry.",
  },
  {
    key: "maximum", label: "Maximum", strength: 1.0, enabled: [...SIGNAL, "decoy"],
    blurb: "Everything on, including ghost contours. Visibly altered up close. For work " +
      "you are willing to degrade to keep out of a dataset.",
  },
];

export const PRESETS_BY_KEY = Object.fromEntries(PRESETS.map((p) => [p.key, p]));
export const DEFAULT_PRESET = "balanced";

export function applyPreset(settings, key) {
  const p = PRESETS_BY_KEY[key];
  return {
    ...settings,
    strength: p.strength,
    enabled: Object.fromEntries(ALL_PASSES.map((x) => [x.name, p.enabled.includes(x.name)])),
    harden: true,
  };
}

export function matchingPreset(settings) {
  return PRESETS.find((p) =>
    Math.abs(p.strength - settings.strength) < 0.005 &&
    ALL_PASSES.every((x) => !!settings.enabled[x.name] === p.enabled.includes(x.name)) &&
    settings.harden) ?? null;
}
