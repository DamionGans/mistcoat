// GPU acceleration for the blur, the engine's heaviest kernel. Mirrors
// desktop/src/mistcoat/core/accel.py and uses the same shader.
//
// Runs through WebGPU where the browser or WebView offers it (Chrome and
// Android WebView 121+ on Android 12+, Safari / WKWebView on iOS 26+, desktop
// Chrome and Edge) and silently falls back to the CPU everywhere else, after
// any GPU error, or when switched off. Software ("fallback") adapters are
// ignored: they are slower than the plain JavaScript path.

const SHADER = /* wgsl */ `
struct Params { w: u32, h: u32, radius: u32, horizontal: u32 };
@group(0) @binding(0) var<uniform> P: Params;
@group(0) @binding(1) var<storage, read> src: array<f32>;
@group(0) @binding(2) var<storage, read_write> dst: array<f32>;

@compute @workgroup_size(16, 16, 1)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let x = gid.x;
    let y = gid.y;
    if (x >= P.w || y >= P.h) { return; }
    let r = i32(P.radius);
    var acc = 0.0;
    if (P.horizontal == 1u) {
        let row = y * P.w;
        let last = i32(P.w) - 1;
        for (var k = -r; k <= r; k++) {
            acc += src[row + u32(clamp(i32(x) + k, 0, last))];
        }
    } else {
        let last = i32(P.h) - 1;
        for (var k = -r; k <= r; k++) {
            acc += src[u32(clamp(i32(y) + k, 0, last)) * P.w + x];
        }
    }
    dst[y * P.w + x] = acc / f32(2 * r + 1);
}`;

// Below this the upload and read-back cost more than they save.
const MIN_PIXELS = 256 * 256;

let enabled = true;
let gpu = null;            // { device, pipeline, maxBinding, name } once ready
let initPromise = null;
let reason = null;         // why there is no GPU

export function setGpuEnabled(on) { enabled = !!on; }

async function init() {
  try {
    const nav = globalThis.navigator;
    if (!nav?.gpu) { reason = "This browser has no WebGPU"; return null; }
    const adapter = await nav.gpu.requestAdapter({ powerPreference: "high-performance" });
    if (!adapter) { reason = "No compatible graphics processor found"; return null; }
    const info = adapter.info ?? {};
    if (adapter.isFallbackAdapter || info.isFallbackAdapter) {
      reason = "Only a software graphics driver is available";
      return null;
    }
    const maxBinding = adapter.limits.maxStorageBufferBindingSize;
    const device = await adapter.requestDevice({
      requiredLimits: { maxStorageBufferBindingSize: maxBinding, maxBufferSize: adapter.limits.maxBufferSize },
    });
    device.lost.then((e) => { gpu = null; reason = `The graphics processor was lost (${e.message || e.reason})`; });
    const pipeline = device.createComputePipeline({
      layout: "auto",
      compute: { module: device.createShaderModule({ code: SHADER }), entryPoint: "main" },
    });
    const name = [info.vendor, info.architecture || info.device || info.description]
      .filter(Boolean).join(" ") || "GPU";
    return { device, pipeline, maxBinding, name };
  } catch (err) {
    reason = `The graphics processor could not be used (${err.message || err})`;
    return null;
  }
}

async function ensure() {
  if (!initPromise) initPromise = init().then((g) => { gpu = g; return g; });
  await initPromise;
  return gpu;
}

export async function gpuStatus() {
  const g = await ensure();
  return {
    enabled,
    available: !!g,
    name: g?.name ?? null,
    reason: g ? null : reason,
    using: enabled && g ? `GPU (${g.name})` : "CPU",
  };
}

/**
 * Three box passes per axis on the GPU (same as imaging.blur on the CPU).
 * Resolves to a new Float32Array, or null when the GPU cannot be used, in
 * which case the caller falls back to the CPU.
 */
export async function gpuBlur(src, w, h, radius) {
  if (!enabled || w * h < MIN_PIXELS) return null;
  const g = await ensure();
  if (!g || w * h * 4 > g.maxBinding) return null;
  const { device, pipeline } = g;
  const rx = Math.min(radius, Math.max(w - 1, 1)), ry = Math.min(radius, Math.max(h - 1, 1));
  const bytes = w * h * 4;
  const STORAGE = GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC | GPUBufferUsage.COPY_DST;
  const bufs = [];
  try {
    const a = device.createBuffer({ size: bytes, usage: STORAGE });
    const b = device.createBuffer({ size: bytes, usage: STORAGE });
    const read = device.createBuffer({ size: bytes, usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST });
    bufs.push(a, b, read);
    device.queue.writeBuffer(a, 0, src instanceof Float32Array ? src : Float32Array.from(src));
    const uniforms = [[ry, 0], [rx, 1]].map(([r, horizontal]) => {
      const u = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST });
      device.queue.writeBuffer(u, 0, new Uint32Array([w, h, r, horizontal]));
      bufs.push(u);
      return u;
    });
    const layout = pipeline.getBindGroupLayout(0);
    const group = (u, s, d) => device.createBindGroup({ layout, entries: [
      { binding: 0, resource: { buffer: u } },
      { binding: 1, resource: { buffer: s } },
      { binding: 2, resource: { buffer: d } }] });
    const groups = uniforms.map((u) => [group(u, a, b), group(u, b, a)]);

    const enc = device.createCommandEncoder();
    let inA = true;
    // Three vertical passes, then three horizontal - as on the CPU.
    for (const axis of [0, 0, 0, 1, 1, 1]) {
      const pass = enc.beginComputePass();
      pass.setPipeline(pipeline);
      pass.setBindGroup(0, groups[axis][inA ? 0 : 1]);
      pass.dispatchWorkgroups(Math.ceil(w / 16), Math.ceil(h / 16));
      pass.end();
      inA = !inA;
    }
    enc.copyBufferToBuffer(inA ? a : b, 0, read, 0, bytes);
    device.queue.submit([enc.finish()]);
    await read.mapAsync(GPUMapMode.READ);
    const out = new Float32Array(read.getMappedRange().slice(0));
    read.unmap();
    return out;
  } catch (err) {
    gpu = null;
    reason = `The graphics processor stopped responding (${err.message || err})`;
    return null;
  } finally {
    for (const buf of bufs) buf.destroy();
  }
}
