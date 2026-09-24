#!/usr/bin/env python3
"""Assemble deliverables/ with version numbers + manifest.json.

Usage: ./.qwen-tts-venv/Scripts/python.exe tools/deliver.py <final_video.mp4>
Never overwrites: if final_video_v01.mp4 exists, writes v02, and so on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

ROOT, WORK, PROJECT, DELIVER = CFG.root, CFG.work, CFG.project, CFG.deliver
FFPROBE = CFG.ffprobe()
RUNTIME = CFG.runtime
LOUD = CFG.loudness


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def next_version() -> int:
    existing = sorted(DELIVER.glob("final_video_v*.mp4"))
    if not existing:
        return 1
    return max(int(p.stem.split("_v")[-1]) for p in existing) + 1


def probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8",
        timeout=RUNTIME["subprocess_timeout_s"],
    ).stdout
    return json.loads(out)


def command_output(cmd: list[str], fallback: str = "unknown") -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    text = (proc.stdout or proc.stderr).strip()
    return text.splitlines()[0] if proc.returncode == 0 and text else fallback


def local_hyperframes_version() -> str:
    for base in (PROJECT, ROOT):
        package = base / "node_modules" / "hyperframes" / "package.json"
        if package.exists():
            try:
                return json.loads(package.read_text(encoding="utf-8"))["version"]
            except (KeyError, OSError, json.JSONDecodeError):
                pass
    return "unknown; use the project lockfile or recorded CLI output"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    args = parser.parse_args()
    DELIVER.mkdir(parents=True, exist_ok=True)
    v = next_version()
    tag = f"v{v:02d}"

    metrics_path = DELIVER / "qa_metrics.json"
    if not metrics_path.exists():
        raise SystemExit("qa_metrics.json missing; run qa_video.py before deliver.py")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if int(metrics.get("fail_count", 1)) != 0:
        raise SystemExit(
            f"QA still has {metrics.get('fail_count')} failure(s); delivery is blocked"
        )

    required_reports = [
        WORK / "PRONUNCIATION_LEDGER.json",
        WORK / "PRONUNCIATION_QA.json",
        WORK / "VISUAL_STABILITY_REPORT.json",
        WORK / "STYLE_AUDIT.md",
        WORK / "QA_REPORT.md",
        DELIVER / "contact_sheet.png",
    ]
    if CFG.technical_explainer:
        required_reports += [
            WORK / "TECHNICAL_EXPLAINER_AUDIT.md",
            WORK / "TIMESTAMP_AUDIT.md",
        ]
    optional_reports = [
        WORK / "BGM_PROVENANCE.md",
        WORK / "AUDIO_PATCH_REPORT.json",
    ]
    missing_reports = [str(path) for path in required_reports if not path.exists()]
    if missing_reports:
        raise SystemExit("required delivery reports missing:\n  " + "\n  ".join(missing_reports))

    align = json.loads((WORK / "alignment.json").read_text(encoding="utf-8"))
    copies = [
        (args.video, f"final_video_{tag}.mp4"),
        (WORK / "narration" / "narration.wav", f"final_narration_{tag}.wav"),
        (WORK / "narration" / "final_aligned.srt", f"final_aligned_{tag}.srt"),
        (WORK / "micro_script.json", f"micro_script_{tag}.json"),
        (WORK / "STORYBOARD.md", f"STORYBOARD_{tag}.md"),
        (WORK / "SCRIPT.md", f"SCRIPT_{tag}.md"),
        (WORK / "SCRIPT_CHANGES.md", f"SCRIPT_CHANGES_{tag}.md"),
        (WORK / "design.md", f"design_{tag}.md"),
        (WORK / "FACT_CHECK.md", f"FACT_CHECK_{tag}.md"),
        (WORK / "PREFLIGHT.md", f"PREFLIGHT_{tag}.md"),
        (WORK / "alignment.json", f"alignment_{tag}.json"),
        (WORK / "narration" / "qa-asr.json", f"qa_asr_{tag}.json"),
        (WORK / "narration" / "mastering_report.json", f"mastering_report_{tag}.json"),
        (WORK / "PRONUNCIATION_LEDGER.json", f"PRONUNCIATION_LEDGER_{tag}.json"),
        (WORK / "PRONUNCIATION_QA.json", f"PRONUNCIATION_QA_{tag}.json"),
        (WORK / "VISUAL_STABILITY_REPORT.json", f"VISUAL_STABILITY_REPORT_{tag}.json"),
        (WORK / "STYLE_AUDIT.md", f"STYLE_AUDIT_{tag}.md"),
        (WORK / "QA_REPORT.md", f"QA_REPORT_{tag}.md"),
        (DELIVER / "qa_metrics.json", f"qa_metrics_{tag}.json"),
        (DELIVER / "contact_sheet.png", f"contact_sheet_{tag}.png"),
        (DELIVER / "transitions_sheet.png", f"transitions_sheet_{tag}.png"),
    ]
    copies += [
        (path, f"{path.stem}_{tag}{path.suffix}")
        for path in optional_reports if path.exists()
    ]
    if CFG.technical_explainer:
        copies += [
            (WORK / "TECHNICAL_EXPLAINER_AUDIT.md", f"TECHNICAL_EXPLAINER_AUDIT_{tag}.md"),
            (WORK / "TIMESTAMP_AUDIT.md", f"TIMESTAMP_AUDIT_{tag}.md"),
        ]
    written = []
    for src, name in copies:
        if not src.exists():
            print(f"[warn] missing {src}")
            continue
        dst = DELIVER / name
        shutil.copyfile(src, dst)
        written.append(dst)

    info = probe(args.video)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    as_ = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fonts = sorted((PROJECT / "assets" / "fonts").glob("*"))
    audit_hashes = {
        path.name: sha256(path)
        for path in required_reports + optional_reports if path.exists()
    }
    manifest = {
        "project": CFG["project"],
        "version": tag,
        "generated_from": f"skill:srt-voice-explainer + {CFG['srt']}",
        "timing_mode": "QUALITY_FIRST",
        "technical_explainer": CFG.technical_explainer,
        "inputs": {
            CFG["srt"]: sha256(CFG.srt),
            "video.config.json": sha256(ROOT / "video.config.json"),
            f"{CFG['voice_dir']}/reference.wav": sha256(CFG.voice_dir / "reference.wav"),
            f"{CFG['voice_dir']}/reference.txt": sha256(CFG.voice_dir / "reference.txt"),
        },
        "tts": {
            "engine": "qwen3-tts (local)",
            "model": align["model"],
            "params": align["params"],
            "base_seed": CFG.base_seed,
            "seed_formula": f"{CFG.base_seed} + beat_index*97 + attempt*7919",
            "reference_sha256": align["reference_sha256"],
            "reference_text_sha256": align["reference_text_sha256"],
            "cloud_upload": False,
            "pauses_s": dict(CFG.pauses),
            "mastering": "per unit: highpass 75Hz -> BS.1770-4 gated measurement "
                         f"(scripts/loudness.py) -> linear gain to {LOUD['unit_target_lufs']} LUFS "
                         f"-> limiter at {LOUD['unit_ceiling_dbfs']} dBFS; programme -> "
                         f"{LOUD['programme_target_lufs']} LUFS -> limiter at "
                         f"{LOUD['programme_ceiling_dbfs']} dBFS. See mastering_report for "
                         "the actual unit count and measured distribution.",
            "loudness_consistency_targets": CFG.loudness,
        },
        "asr_verification": {
            "model": "openai/whisper-small (local)",
            "metric": "character Levenshtein on normalised text",
        },
        "fonts": [f.name for f in fonts],
        "audit_files": audit_hashes,
        "animation_runtime": "recorded by the HyperFrames project and lockfile",
        "audio_master_step": "See mastering_report and reproduce commands for the actual remux path.",
        "toolchain": {
            "hyperframes": local_hyperframes_version(),
            "node": command_output(["node", "-v"]),
            "ffmpeg": command_output([shutil.which("ffmpeg") or "ffmpeg", "-version"]),
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "gpu": command_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                fallback="not detected",
            ),
        },
        "output": {
            "file": f"final_video_{tag}.mp4",
            "sha256": sha256(args.video),
            "bytes": int(info["format"]["size"]),
            "width": vs["width"], "height": vs["height"],
            "fps": vs["r_frame_rate"],
            "video_codec": vs["codec_name"],
            "audio_codec": as_["codec_name"] if as_ else None,
            "audio_sample_rate": as_["sample_rate"] if as_ else None,
            "duration_s": float(info["format"]["duration"]),
            "narration_duration_s": align["total_duration"],
        },
        "structure": {
            "chapters": len(align["chapters"]),
            "narration_units": len(align["beats"]),
            "visual_beats": len(align["beats"]),
        },
        "qa": {
            "pass": metrics.get("pass_count"),
            "fail": metrics.get("fail_count"),
            "loudness_lufs": metrics.get("loudness_lufs"),
            "true_peak_dbfs": metrics.get("true_peak_dbfs"),
            "loudness_consistency": metrics.get("loudness_consistency"),
            "worst_sync_offset_ms": (metrics.get("sync") or {}).get("worst_offset_ms"),
        },
        "source_project": f"{CFG['work_dir']}/{CFG['project']} (index.html + compositions/*.html)",
        "reproduce": [
            "python <skill>/scripts/run_project.py build_micro_script.py && "
            "python <skill>/scripts/run_project.py check_verbatim.py",
            "python <skill>/scripts/run_project.py generate_tts.py",
            "python <skill>/scripts/run_project.py generate_tts.py --remaster",
            "python <skill>/scripts/run_project.py sync_timing.py",
            "python <skill>/scripts/run_project.py build_docs.py",
            f"<skill>/scripts/hf.sh check   (in {CFG['work_dir']}/{CFG['project']})",
            "<skill>/scripts/hf.sh render <project-specific render arguments>",
            "ffmpeg -i <rendered-video> -i <narration.wav> -c:v copy "
            "<project-specific AAC/remux arguments> <final-video>",
            "python <skill>/scripts/run_project.py qa_video.py <final-video>",
            "python <skill>/scripts/run_project.py deliver.py <final-video>",
        ],
    }
    (DELIVER / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"delivered {tag}:")
    for p in written:
        print(f"  {p}")
    print(f"  {DELIVER / 'manifest.json'}")


if __name__ == "__main__":
    main()
