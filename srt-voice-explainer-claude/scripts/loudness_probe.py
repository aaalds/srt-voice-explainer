#!/usr/bin/env python3
"""Loudness-consistency probe (EBU R128 / ITU-R BS.1770-4, implemented locally).

Measures, for one audio file:
  * per-narration-unit gated integrated loudness (window = alignment.json speech span)
  * the short-term (3 s) loudness curve over speech-active regions
  * spread statistics: P95-P5, max-min, stdev, and the worst offenders

Usage:
  python tools/loudness_probe.py <audio-or-video> [--json out.json] [--label final]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

ROOT, WORK = CFG.root, CFG.work
FFMPEG = CFG.ffmpeg()


from loudness import block_powers, gated_lufs, kweight  # noqa: E402


# ---------------------------------------------------------------- probe
def load_audio(path: Path) -> tuple[np.ndarray, int]:
    if path.suffix.lower() in (".wav", ".flac"):
        audio, sr = sf.read(path, dtype="float64")
    else:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "a.wav"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path),
                            "-map", "0:a:0", "-c:a", "pcm_f32le", "-ac", "1", str(tmp)],
                           check=True)
            audio, sr = sf.read(tmp, dtype="float64")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--label", default="final")
    args = ap.parse_args()

    align = json.loads((WORK / "alignment.json").read_text(encoding="utf-8"))
    audio, sr = load_audio(args.audio)
    y = kweight(audio, sr)

    # --- per narration unit (speech span only) -------------------------------
    units = []
    for beat in align["beats"]:
        i0, i1 = int(beat["start"] * sr), int(min(beat["end"], len(audio) / sr) * sr)
        seg = y[i0:i1]
        if seg.size < int(0.2 * sr):
            continue
        units.append({"id": beat["id"], "chapter_key": beat["chapter_key"],
                      "start": beat["start"], "dur": round(beat["end"] - beat["start"], 3),
                      "lufs": round(gated_lufs(seg, sr), 2)})
    vals = np.array([u["lufs"] for u in units])
    programme = gated_lufs(y, sr)

    # --- short-term (3 s) curve over speech-active blocks --------------------
    st_power, st_t = block_powers(y, sr, 3.0, 0.100)
    st = -0.691 + 10.0 * np.log10(st_power + 1e-12)
    active = st > (programme - 20.0)          # ignore inter-unit silence
    st_a = st[active]

    # --- per chapter ---------------------------------------------------------
    chapters = []
    for ch in align["chapters"]:
        sub = [u["lufs"] for u in units if u["chapter_key"] == ch["chapter_key"]]
        if sub:
            chapters.append({"chapter_key": ch["chapter_key"],
                             "mean_lufs": round(float(np.mean(sub)), 2),
                             "min_lufs": round(float(np.min(sub)), 2),
                             "max_lufs": round(float(np.max(sub)), 2)})

    def spread(a: np.ndarray) -> dict:
        return {
            "n": int(a.size),
            "mean": round(float(a.mean()), 2),
            "stdev": round(float(a.std(ddof=1)), 2) if a.size > 1 else 0.0,
            "min": round(float(a.min()), 2),
            "max": round(float(a.max()), 2),
            "p5": round(float(np.percentile(a, 5)), 2),
            "p95": round(float(np.percentile(a, 95)), 2),
            "range_p5_p95": round(float(np.percentile(a, 95) - np.percentile(a, 5)), 2),
            "range_min_max": round(float(a.max() - a.min()), 2),
        }

    target = float(np.median(vals))
    dev = np.abs(vals - target)
    worst = sorted(units, key=lambda u: -abs(u["lufs"] - target))[:15]

    payload = {
        "label": args.label,
        "file": str(args.audio),
        "programme_lufs": round(programme, 2),
        "unit_median_lufs": round(target, 2),
        "unit_spread": spread(vals),
        "short_term_spread": spread(st_a),
        "units_beyond_1LU": int((dev > 1.0).sum()),
        "units_beyond_2LU": int((dev > 2.0).sum()),
        "units_beyond_3LU": int((dev > 3.0).sum()),
        "chapters": chapters,
        "worst_units": worst,
        "units": units,
    }
    if args.json:
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    u = payload["unit_spread"]
    s = payload["short_term_spread"]
    print(f"[{args.label}] programme      {payload['programme_lufs']:+.2f} LUFS")
    print(f"[{args.label}] per-unit       median {payload['unit_median_lufs']:+.2f}  "
          f"stdev {u['stdev']:.2f} LU  min {u['min']:+.2f}  max {u['max']:+.2f}  "
          f"P5-P95 {u['range_p5_p95']:.2f} LU  min-max {u['range_min_max']:.2f} LU")
    print(f"[{args.label}] short-term 3s  stdev {s['stdev']:.2f} LU  "
          f"P5-P95 {s['range_p5_p95']:.2f} LU  min-max {s['range_min_max']:.2f} LU")
    print(f"[{args.label}] units off median: >1LU {payload['units_beyond_1LU']}  "
          f">2LU {payload['units_beyond_2LU']}  >3LU {payload['units_beyond_3LU']}  (n={u['n']})")
    print(f"[{args.label}] worst:")
    for w in worst[:10]:
        print(f"    {w['id']} {w['chapter_key']:>4} {w['lufs']:+7.2f} LUFS "
              f"({w['lufs'] - target:+.2f} LU)  dur {w['dur']:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
