#!/usr/bin/env python3
"""Replace exact narration time slots without changing samples elsewhere.

The patch JSON is either a list or ``{"patches": [...]}``. Each entry needs:

    {"id":"b008", "start_s":51.2, "end_s":57.7,
     "audio":"tmp/v04_units/b008.wav", "fade_ms":0}

Relative audio paths are resolved from the patch JSON directory. Patch audio
must already have exactly the slot's sample count; this script never stretches
speech. It refuses overlapping slots, sample-rate/channel drift, source
overwrite, and implicit trimming/padding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_audio(spec_path: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (spec_path.parent / path).resolve()


def load_spec(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    patches = payload.get("patches") if isinstance(payload, dict) else payload
    if not isinstance(patches, list) or not patches:
        raise SystemExit("patch JSON must contain a non-empty patches list")
    if not all(isinstance(item, dict) for item in patches):
        raise SystemExit("every patch entry must be an object")
    return patches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    base_path = args.base.resolve()
    spec_path = args.patches.resolve()
    output_path = args.output.resolve()
    report_path = (args.report.resolve() if args.report else
                   output_path.with_suffix(".patch_report.json"))
    if output_path == base_path:
        raise SystemExit("refusing to overwrite the base narration")
    if output_path.exists() and not args.force:
        raise SystemExit(f"output exists: {output_path}; pass --force to replace it")

    info = sf.info(base_path)
    base, sample_rate = sf.read(base_path, dtype="float64", always_2d=True)
    output = base.copy()
    occupied = np.zeros(len(base), dtype=bool)
    applied = []

    for index, item in enumerate(load_spec(spec_path)):
        missing = {"start_s", "end_s", "audio"} - set(item)
        if missing:
            raise SystemExit(f"patch {index} missing fields: {sorted(missing)}")
        patch_id = str(item.get("id", f"patch-{index + 1}"))
        start_s, end_s = float(item["start_s"]), float(item["end_s"])
        if not (0 <= start_s < end_s):
            raise SystemExit(f"{patch_id}: invalid slot {start_s}..{end_s}")
        start = round(start_s * sample_rate)
        end = round(end_s * sample_rate)
        if end > len(base):
            raise SystemExit(f"{patch_id}: slot ends after base narration")
        if occupied[start:end].any():
            raise SystemExit(f"{patch_id}: slot overlaps an earlier patch")

        audio_path = resolve_audio(spec_path, str(item["audio"]))
        patch, patch_rate = sf.read(audio_path, dtype="float64", always_2d=True)
        if patch_rate != sample_rate:
            raise SystemExit(
                f"{patch_id}: sample rate {patch_rate} != base {sample_rate}"
            )
        if patch.shape[1] != base.shape[1]:
            raise SystemExit(
                f"{patch_id}: channels {patch.shape[1]} != base {base.shape[1]}"
            )
        expected = end - start
        if len(patch) != expected:
            raise SystemExit(
                f"{patch_id}: {len(patch)} samples, slot requires {expected}; "
                "prepare an exact-length unit without stretching the protected term"
            )

        fade_ms = float(item.get("fade_ms", 0.0))
        fade = min(round(fade_ms * sample_rate / 1000), expected // 2)
        output[start:end] = patch
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, endpoint=False)[:, None]
            output[start:start + fade] = base[start:start + fade] * (1.0 - ramp) + patch[:fade] * ramp
            ramp_out = np.linspace(0.0, 1.0, fade, endpoint=False)[:, None]
            output[end - fade:end] = patch[-fade:] * (1.0 - ramp_out) + base[end - fade:end] * ramp_out
        occupied[start:end] = True
        applied.append({
            "id": patch_id,
            "start_s": start / sample_rate,
            "end_s": end / sample_rate,
            "frames": expected,
            "fade_ms": fade * 1000.0 / sample_rate,
            "audio": str(audio_path),
            "audio_sha256": sha256(audio_path),
        })

    outside_max_diff = float(np.max(np.abs(output[~occupied] - base[~occupied]))) if (~occupied).any() else 0.0
    if outside_max_diff != 0.0:
        raise SystemExit(f"internal error: samples outside patch slots changed by {outside_max_diff}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_path, output, sample_rate, format=info.format, subtype=info.subtype)
    written, written_rate = sf.read(output_path, dtype="float64", always_2d=True)
    if written_rate != sample_rate or written.shape != base.shape:
        raise SystemExit("written narration changed sample rate, channels, or total sample count")
    written_outside_diff = (
        float(np.max(np.abs(written[~occupied] - base[~occupied]))) if (~occupied).any() else 0.0
    )

    report = {
        "status": "PASS" if written_outside_diff == 0.0 else "FAIL",
        "base": str(base_path),
        "base_sha256": sha256(base_path),
        "output": str(output_path),
        "output_sha256": sha256(output_path),
        "sample_rate": sample_rate,
        "channels": int(base.shape[1]),
        "total_frames": int(len(base)),
        "duration_s": len(base) / sample_rate,
        "patches": applied,
        "outside_patch_max_abs_diff": written_outside_diff,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[patch] {len(applied)} slots; {len(base)} frames; "
        f"outside_diff={written_outside_diff:.9g}; {report['status']}"
    )
    print(f"[patch] output={output_path}")
    print(f"[patch] report={report_path}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
