"""Smoothing for noisy per-frame readings."""
from __future__ import annotations

from collections import deque

import numpy as np


class Smoother:
    """Rolling mean over the last ``window`` values, ignoring anything more
    than ``sigma`` standard deviations from the window mean (the head's
    original outlier rejection). Prefilled with ``initial`` so the first
    readings are pulled gently from the resting value."""

    def __init__(self, window: int = 10, sigma: float = 2.0, initial: float = 0.0) -> None:
        self.window = window
        self.sigma = sigma
        self._initial = initial
        self._buf: deque[float] = deque([initial] * window, maxlen=window)

    def reset(self, value: float | None = None) -> None:
        v = self._initial if value is None else value
        self._buf = deque([v] * self.window, maxlen=self.window)

    def push(self, value: float) -> float:
        self._buf.append(float(value))
        arr = np.asarray(self._buf)
        mean, std = arr.mean(), arr.std()
        kept = arr[np.abs(arr - mean) <= self.sigma * std]
        return float(kept.mean()) if len(kept) else float(mean)
