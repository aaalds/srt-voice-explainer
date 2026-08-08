#!/usr/bin/env python3
"""Local ITU-R BS.1770-4 / EBU R128 loudness DSP.

Everything here is deterministic, length-preserving and runs offline.

Why this exists: ffmpeg's `loudnorm` silently produces garbage for inputs
shorter than 3 s (its own measurement window). 15 of the 86 narration units
are shorter than that, and the first master attenuated them by up to 45 dB.
So loudness measurement and normalisation are done here instead, where the
gating window is ours and short inputs are handled correctly.

Public API
    gated_lufs(x, sr)              -> integrated loudness, absolute+relative gated
    short_term(x, sr)              -> (loudness curve, block start times), 3 s / 100 ms
    true_peak_dbfs(x, sr)          -> 4x-oversampled true peak in dBTP
    normalise(x, sr, target, ceil) -> (y, info) gain to target LUFS under a TP ceiling
    limit_peaks(x, sr, ceiling)    -> look-ahead peak limiter (length preserved)
"""

from __future__ import annotations

import math

import numpy as np

try:                                            # both are optional fast paths
    from scipy.signal import lfilter, resample_poly
    from scipy.ndimage import maximum_filter1d, minimum_filter1d
    _SCIPY = True
except Exception:                               # pragma: no cover
    _SCIPY = False

ABS_GATE = -70.0        # LUFS, BS.1770-4 absolute gate
REL_GATE = -10.0        # LU below the ungated mean, BS.1770-4 relative gate


# ----------------------------------------------------------------- K-weighting
def _kweight_coeffs(fs: int):
    """BS.1770-4 stage 1 (high shelf) + stage 2 (RLB high pass), designed for fs."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** 0.4996667741545416
    denom = 1.0 + k / q + k * k
    b1 = np.array([(vh + vb * k / q + k * k) / denom,
                   2.0 * (k * k - vh) / denom,
                   (vh - vb * k / q + k * k) / denom])
    a1 = np.array([1.0, 2.0 * (k * k - 1.0) / denom, (1.0 - k / q + k * k) / denom])

    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    denom = 1.0 + k / q + k * k
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1.0, 2.0 * (k * k - 1.0) / denom, (1.0 - k / q + k * k) / denom])
    return (b1, a1), (b2, a2)


def _biquad(x: np.ndarray, b: np.ndarray, a: np.ndarray) -> np.ndarray:
    if _SCIPY:
        return lfilter(b, a, x)
    y = np.empty_like(x)
    z1 = z2 = 0.0
    for n in range(x.shape[0]):                 # pragma: no cover - fallback only
        xn = x[n]
        yn = b[0] * xn + z1
        z1 = b[1] * xn - a[1] * yn + z2
        z2 = b[2] * xn - a[2] * yn
        y[n] = yn
    return y


def kweight(x: np.ndarray, sr: int) -> np.ndarray:
    (b1, a1), (b2, a2) = _kweight_coeffs(sr)
    return _biquad(_biquad(np.asarray(x, dtype=np.float64), b1, a1), b2, a2)


# ----------------------------------------------------------------- measurement
def _block_power(y: np.ndarray, sr: int, block_s: float, hop_s: float):
    n = int(round(block_s * sr))
    hop = max(1, int(round(hop_s * sr)))
    if y.size < n:
        return np.empty(0), np.empty(0)
    starts = np.arange(0, y.size - n + 1, hop)
    csum = np.concatenate([[0.0], np.cumsum(y * y)])
    return (csum[starts + n] - csum[starts]) / n, starts / sr


block_powers = _block_power        # public alias used by loudness_probe.py


def gated_lufs(x: np.ndarray, sr: int, pre_weighted: bool = False) -> float:
    """Integrated loudness (LUFS) with BS.1770-4 absolute + relative gating.

    Unlike ffmpeg's loudnorm this is valid for inputs well under 3 s: a 400 ms
    block grid plus the two gates is exactly what the standard specifies, and
    nothing here needs a minimum programme length.
    """
    y = x if pre_weighted else kweight(x, sr)
    power, _ = _block_power(y, sr, 0.400, 0.100)
    if power.size == 0:
        power = np.array([float((y * y).mean())]) if y.size else np.array([1e-12])
    block_lufs = -0.691 + 10.0 * np.log10(power + 1e-12)
    keep = block_lufs > ABS_GATE
    if not keep.any():
        return float("-inf")
    relative = -0.691 + 10.0 * np.log10(power[keep].mean() + 1e-12) + REL_GATE
    keep2 = keep & (block_lufs > relative)
    if not keep2.any():
        keep2 = keep
    return float(-0.691 + 10.0 * np.log10(power[keep2].mean() + 1e-12))


def short_term(x: np.ndarray, sr: int, block_s: float = 3.0, hop_s: float = 0.1):
    """EBU R128 short-term loudness curve (default 3 s window / 100 ms hop)."""
    power, times = _block_power(kweight(x, sr), sr, block_s, hop_s)
    return -0.691 + 10.0 * np.log10(power + 1e-12), times


def true_peak_dbfs(x: np.ndarray, sr: int, oversample: int = 4) -> float:
    """4x-oversampled true peak, in dBTP. Chunked so long programmes stay cheap."""
    if not _SCIPY:                              # pragma: no cover - fallback only
        return float(20.0 * np.log10(np.abs(x).max() + 1e-12))
    peak = 0.0
    step, pad = 1 << 20, 1 << 10
    for start in range(0, x.size, step):
        lo, hi = max(0, start - pad), min(x.size, start + step + pad)
        up = resample_poly(x[lo:hi].astype(np.float32), oversample, 1)
        peak = max(peak, float(np.abs(up).max()))
    return float(20.0 * np.log10(max(peak, 1e-12)))


# ----------------------------------------------------------------- processing
def limit_peaks(x: np.ndarray, sr: int, ceiling: float,
                lookahead_ms: float = 5.0, hold_ms: float = 20.0,
                smooth_ms: float = 15.0) -> tuple[np.ndarray, float]:
    """Look-ahead peak limiter. Returns (audio, max gain reduction in dB).

    Gain is derived from a forward-looking max envelope, held briefly, then
    smoothed with a Hann kernel so there is no click; a final elementwise min
    against the instantaneous requirement guarantees the ceiling is never
    exceeded. Sample count is unchanged, so timelines built on this audio stay
    valid.
    """
    mag = np.abs(x)
    if mag.max() <= ceiling:
        return x, 0.0
    hard = np.minimum(1.0, ceiling / np.maximum(mag, 1e-12))

    look = max(1, int(lookahead_ms * sr / 1000))
    hold = max(1, int(hold_ms * sr / 1000))
    smooth = max(3, int(smooth_ms * sr / 1000)) | 1

    if _SCIPY:
        env = maximum_filter1d(mag, size=2 * look + 1, mode="nearest")
        gain = np.minimum(1.0, ceiling / np.maximum(env, 1e-12))
        # hold: keep the reduction for `hold` samples after the transient
        gain = minimum_filter1d(gain, size=hold, mode="nearest",
                                origin=-(hold // 2) + (hold - 1) // 2)
        window = np.hanning(smooth)
        window /= window.sum()
        # edge-pad before smoothing: np.convolve(mode="same") zero-pads, which
        # would carve a spurious gain dip into the first/last half-window of
        # every clip (it read as a constant "6 dB of limiting" on every unit).
        pad = smooth // 2
        padded = np.concatenate([np.full(pad, gain[0]), gain, np.full(pad, gain[-1])])
        gain = np.convolve(padded, window, mode="valid")[: gain.size]
    else:                                       # pragma: no cover - fallback only
        gain = hard
    gain = np.minimum(gain, hard)
    return x * gain, float(-20.0 * np.log10(max(gain.min(), 1e-12)))


def normalise(x: np.ndarray, sr: int, target_lufs: float, ceiling_dbfs: float,
              max_gain_db: float = 18.0, iterations: int = 4) -> tuple[np.ndarray, dict]:
    """Gain `x` to `target_lufs` while keeping the sample peak under the ceiling.

    Pure gain plus transparent peak limiting — no compression, no EQ, no time
    modification, so the natural dynamics inside a take are preserved and only
    the take-to-take level is equalised.
    """
    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    y = np.asarray(x, dtype=np.float64)
    applied, reduction = 0.0, 0.0
    for _ in range(iterations):
        measured = gated_lufs(y, sr)
        delta = target_lufs - measured
        if abs(delta) < 0.05:
            break
        delta = float(np.clip(delta, -max_gain_db - applied, max_gain_db - applied))
        applied += delta
        y = y * (10.0 ** (delta / 20.0))
        y, gr = limit_peaks(y, sr, ceiling)
        reduction = max(reduction, gr)
    final = gated_lufs(y, sr)
    return y, {
        "input_lufs": round(gated_lufs(np.asarray(x, dtype=np.float64), sr), 2),
        "output_lufs": round(final, 2),
        "gain_db": round(applied, 2),
        "limiter_max_gr_db": round(reduction, 2),
        "sample_peak_dbfs": round(float(20.0 * np.log10(np.abs(y).max() + 1e-12)), 2),
    }
