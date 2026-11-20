"""Protection engines.

``signal`` is the built-in engine: numpy only, fast, no model download. It is
what ships in the base app and what the GUI uses today.

``ml`` is the planned second engine: a targeted attack optimised against a real
text-image model (CLIP), which can push an image's embedding toward a chosen
wrong concept rather than merely degrading it. It needs PyTorch and a model
download, so it will be an optional add-on. ``available()`` is the hook the GUI
uses to decide whether to offer it.
"""
from __future__ import annotations

SIGNAL = "signal"
ML = "ml"


def available(engine: str) -> bool:
    if engine == SIGNAL:
        return True
    if engine == ML:
        try:
            import torch  # noqa: F401
        except ImportError:
            return False
        return True
    return False
