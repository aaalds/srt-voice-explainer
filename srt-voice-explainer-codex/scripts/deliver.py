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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    args = parser.parse_args()
    DELIVER.mkdir(parents=True, exist_ok=True)
    v = next_version()
    tag = f"v{v:02d}"

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
    metrics_path = DELIVER / "qa_metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}

    fonts = sorted((PROJECT / "assets" / "fonts").glob("*"))
    manifest = {
        "project": CFG["project"],
        "version": tag,
        "generated_from": f"skill:srt-voice-explainer + {CFG['srt']}",
        "timing_mode": "QUALITY_FIRST",
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
            "pauses_s": {"clause": 0.14, "sentence": 0.32, "chapter": 0.60,
                         "lead_in": align["lead_in"], "tail": align["tail"]},
            "mastering": "per unit: highpass 75Hz -> BS.1770-4 gated measurement "
                         "(tools/loudness.py) -> linear gain to -17 LUFS -> look-ahead "
                         "true-peak limiter at -1.6 dBFS; programme: concat -> single "
                         "linear gain to -17 LUFS -> limiter at -1.4 dBFS -> 48kHz mono PCM24. "
                         "ffmpeg loudnorm is deliberately not used: it is invalid below 3s "
                         "of input and 15 of the 86 units are shorter than that.",
            "loudness_consistency_targets": CFG.loudness,
        },
        "asr_verification": {
            "model": "openai/whisper-small (local)",
            "metric": "character Levenshtein on normalised text",
        },
        "fonts": [f.name for f in fonts],
        "animation_runtime": "GSAP 3.14.2 (vendored, offline)",
        "audio_master_step": (
            "renderer applies ~+3.1 dB to the embedded track; the delivered file "
            "re-muxes the -17 LUFS narration master with -c:v copy (picture untouched)"
        ),
        "toolchain": {
            "hyperframes": "0.7.76 (offline npx cache)",
            "node": subprocess.run(
                ["node", "-v"], capture_output=True, text=True, timeout=30
            ).stdout.strip(),
            "ffmpeg": subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-version"],
                                     capture_output=True, text=True,
                                     timeout=30).stdout.split("\n")[0],
            "python": platform.python_version(),
            "os": f"{platform.system()} {platform.release()}",
            "gpu": "NVIDIA GeForce RTX 3060 Ti (CUDA 12.8)",
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
            "./.qwen-tts-venv/Scripts/python.exe tools/build_micro_script.py",
            "./.qwen-tts-venv/Scripts/python.exe generate_tts.py",
            "./.qwen-tts-venv/Scripts/python.exe generate_tts.py --remaster   "
            "# 只重跑母带（不动模型/原始录音），用于响度一致性修复",
            "./.qwen-tts-venv/Scripts/python.exe tools/sync_timing.py",
            "./.qwen-tts-venv/Scripts/python.exe tools/build_docs.py",
            "tools/hf.sh check   (in video_work/attention-9x16)",
            "tools/hf.sh render -q high --crf 18 -o renders/final.mp4",
            "ffmpeg -i renders/final.mp4 -i video_work/narration/narration.wav "
            "-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -ar 48000 -ac 1 "
            "-shortest -movflags +faststart renders/final-mastered.mp4   "
            "# 渲染器给音轨加了约 +3 dB；用母带 wav 重挂音轨把成片拉回 -17 LUFS",
            "./.qwen-tts-venv/Scripts/python.exe tools/qa_video.py <video>",
            "./.qwen-tts-venv/Scripts/python.exe tools/deliver.py <video>",
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
