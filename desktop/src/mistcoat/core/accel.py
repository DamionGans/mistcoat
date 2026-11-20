"""Hardware acceleration for the engine's heaviest kernel: the blur.

Blurs (the visibility mask, the band-limited noise fields, the SSIM quality
check) are most of the engine's arithmetic. This module runs them

* on the GPU through WebGPU (wgpu: Direct3D 12 / Vulkan on Windows, Metal on
  macOS, Vulkan on Linux), 10-35x faster than the CPU on a typical card; or
* on all CPU cores, when there is no usable GPU or it is switched off.

Both blurs agree to within float rounding (~1e-7). The protected image
before hardening differs by at most one 8-bit level on ~0.01% of pixels.
Hardening (JPEG/WebP round trips) amplifies such ties into a different but
equivalent pattern: same visible change, same durability, and each mode is
exactly repeatable on its own.

The GPU is used when ``enabled`` (the default) and a hardware adapter exists;
software-emulated adapters are ignored, as numpy on the CPU beats them. Any
GPU error disables it for the rest of the session and falls back to the CPU.
Set MISTCOAT_GPU=0 to switch it off from the environment.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

log = logging.getLogger(__name__)

# Below this many pixels the upload/download costs more than it saves.
GPU_MIN_PIXELS = 256 * 256

_BLUR_SHADER = """
struct Params { w: u32, h: u32, radius: u32, horizontal: u32 };
@group(0) @binding(0) var<uniform> P: Params;
@group(0) @binding(1) var<storage, read> src: array<f32>;
@group(0) @binding(2) var<storage, read_write> dst: array<f32>;

// One box-blur pass along one axis, edges clamped - the same operation as
// imaging._box_blur_1d, summed directly instead of with a running sum.
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
}
"""


class _Gpu:
    """Lazily created WebGPU device and blur pipeline. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tried = False
        self.device = None
        self.pipeline = None
        self.name: str | None = None
        self.reason: str | None = None       # why there is no GPU, if none
        self.max_binding = 0

    def ensure(self) -> bool:
        with self._lock:
            if not self._tried:
                self._tried = True
                self._init()
            return self.device is not None

    def _init(self) -> None:
        try:
            import wgpu
        except Exception as exc:  # noqa: BLE001 - optional dependency
            self.reason = f"GPU support is not installed ({exc.__class__.__name__})"
            return
        try:
            adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
            if adapter is None:
                self.reason = "No compatible graphics card found"
                return
            info = adapter.info
            if str(info.get("adapter_type", "")).lower() in ("cpu", "software"):
                self.reason = "Only a software graphics driver is available"
                return
            limits = adapter.limits
            self.max_binding = int(limits["max-storage-buffer-binding-size"])
            self.device = adapter.request_device_sync(required_limits={
                "max-storage-buffer-binding-size": self.max_binding,
                "max-buffer-size": int(limits["max-buffer-size"]),
            })
            module = self.device.create_shader_module(code=_BLUR_SHADER)
            self.pipeline = self.device.create_compute_pipeline(
                layout="auto", compute={"module": module, "entry_point": "main"})
            self.name = str(info.get("device") or "GPU")
            self._wgpu = wgpu
        except Exception as exc:  # noqa: BLE001 - drivers vary wildly
            self.device = None
            self.reason = f"The graphics driver could not be used ({exc})"
            log.warning("GPU unavailable: %s", exc)

    def fail(self, exc: Exception) -> None:
        log.warning("GPU blur failed, using the CPU from now on: %s", exc)
        with self._lock:
            self.device = None
            self.reason = f"The graphics card stopped responding ({exc})"

    def blur_plane(self, plane: np.ndarray, radius: int) -> np.ndarray:
        wgpu = self._wgpu
        h, w = plane.shape
        rx, ry = min(radius, max(w - 1, 1)), min(radius, max(h - 1, 1))
        dev = self.device
        usage = wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC | wgpu.BufferUsage.COPY_DST
        with self._lock:      # one queue; keep submissions from threads apart
            a = dev.create_buffer_with_data(
                data=np.ascontiguousarray(plane, dtype=np.float32), usage=usage)
            b = dev.create_buffer(size=w * h * 4, usage=usage)
            uniforms = [dev.create_buffer_with_data(
                data=np.array([w, h, r, horizontal], dtype=np.uint32),
                usage=wgpu.BufferUsage.UNIFORM) for horizontal, r in ((0, ry), (1, rx))]
            layout = self.pipeline.get_bind_group_layout(0)

            def group(u, s, d):
                return dev.create_bind_group(layout=layout, entries=[
                    {"binding": 0, "resource": {"buffer": u}},
                    {"binding": 1, "resource": {"buffer": s}},
                    {"binding": 2, "resource": {"buffer": d}}])

            # (axis, where the data is) -> bind group reading there, writing
            # to the other buffer.
            groups = {(axis, s): group(u, src_buf, dst_buf)
                      for axis, u in enumerate(uniforms)
                      for s, (src_buf, dst_buf) in ((0, (a, b)), (1, (b, a)))}
            enc = dev.create_command_encoder()
            src = 0      # 0: data in a, 1: data in b
            # Same order as the CPU path: three vertical passes, then three
            # horizontal ones.
            for axis in (0, 0, 0, 1, 1, 1):
                p = enc.begin_compute_pass()
                p.set_pipeline(self.pipeline)
                p.set_bind_group(0, groups[(axis, src)])
                p.dispatch_workgroups((w + 15) // 16, (h + 15) // 16, 1)
                p.end()
                src ^= 1
            dev.queue.submit([enc.finish()])
            data = dev.queue.read_buffer(a if src == 0 else b)
            for buf in (a, b, *uniforms):
                buf.destroy()
        return np.frombuffer(data, dtype=np.float32).reshape(h, w).copy()


_gpu = _Gpu()
_enabled = os.environ.get("MISTCOAT_GPU", "1").strip().lower() not in ("0", "off", "false", "no")
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


# ------------------------------------------------------------------ public

def set_gpu_enabled(on: bool) -> None:
    """The user's preference. Takes effect from the next blur."""
    global _enabled
    _enabled = bool(on)


def gpu_enabled() -> bool:
    return _enabled


def gpu_active() -> bool:
    """True when blurs will actually run on the GPU."""
    return _enabled and _gpu.ensure()


def status() -> dict:
    """What is being used and why, for the UI and the self-test."""
    available = _gpu.ensure()
    return {
        "gpu_enabled": _enabled,
        "gpu_available": available,
        "gpu_name": _gpu.name,
        "gpu_reason": None if available else _gpu.reason,
        "using": (f"GPU ({_gpu.name})" if (_enabled and available)
                  else f"CPU ({cpu_workers()} threads)"),
    }


def cpu_workers() -> int:
    return max(1, min(8, os.cpu_count() or 1))


def box_blur3(a: np.ndarray, radius: int, cpu_box_1d) -> np.ndarray:
    """Three box passes per axis (a Gaussian approximation), on the GPU when
    possible, otherwise split across CPU cores. ``a`` is (H, W) or (H, W, C)."""
    a = np.ascontiguousarray(a, dtype=np.float32)
    h, w = a.shape[:2]
    if (_enabled and h * w >= GPU_MIN_PIXELS and _gpu.ensure()
            and h * w * 4 <= _gpu.max_binding):
        try:
            if a.ndim == 2:
                return _gpu.blur_plane(a, radius)
            return np.stack([_gpu.blur_plane(a[:, :, c], radius)
                             for c in range(a.shape[2])], axis=2)
        except Exception as exc:  # noqa: BLE001
            _gpu.fail(exc)
    return _cpu_blur3(a, radius, cpu_box_1d)


def _cpu_blur3(a: np.ndarray, radius: int, cpu_box_1d) -> np.ndarray:
    workers = cpu_workers()
    out = a
    for axis in (0, 1):
        other = 1 - axis
        n = out.shape[other]
        if workers == 1 or n < 4 * workers:
            for _ in range(3):
                out = cpu_box_1d(out, radius, axis)
            continue
        edges = np.linspace(0, n, workers + 1).astype(int)
        res = np.empty_like(out)

        def work(i, src=out, res=res, axis=axis, other=other, edges=edges):
            sl = [slice(None)] * src.ndim
            sl[other] = slice(edges[i], edges[i + 1])
            chunk = src[tuple(sl)]
            for _ in range(3):
                chunk = cpu_box_1d(chunk, radius, axis)
            res[tuple(sl)] = chunk

        list(_get_pool().map(work, range(workers)))
        out = res
    return out


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(cpu_workers(), thread_name_prefix="mistcoat-blur")
        return _pool


# ------------------------------------------------------------ codec work

_codec_pool: ThreadPoolExecutor | None = None


def run_parallel(thunks):
    """Run independent zero-argument callables at once and return their
    results in order. Used for the JPEG/WebP round trips, which only run on
    the CPU but release the GIL while encoding, so they scale across cores.
    A separate pool from the blur's, so neither can starve the other."""
    thunks = list(thunks)
    if len(thunks) < 2 or cpu_workers() < 2:
        return [t() for t in thunks]
    global _codec_pool
    with _pool_lock:
        if _codec_pool is None:
            _codec_pool = ThreadPoolExecutor(min(4, cpu_workers()),
                                             thread_name_prefix="mistcoat-codec")
    return list(_codec_pool.map(lambda t: t(), thunks))
