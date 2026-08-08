#!/usr/bin/env python3
"""Scaffold a new srt-voice-explainer project.

Creates video.config.json + the directory layout, auto-detects the local
Qwen3-TTS snapshot and a Python interpreter with the needed packages, and
reports what is still missing (voice profile, consent, SRT).

Usage:
    python <skill>/scripts/init_project.py --here [--name my-film] [--srt transcription.srt]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CONFIG_NAME, DEFAULTS  # noqa: E402

HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"


def find_tts_snapshot() -> str | None:
    """Locate the Qwen3-TTS *snapshot* dir. Pointing at the repo dir instead is
    the single most common setup error — it fails with 'Unrecognized model'."""
    for repo in sorted(HF_HUB.glob("models--*Qwen3-TTS*")):
        snaps = sorted((repo / "snapshots").glob("*")) if (repo / "snapshots").is_dir() else []
        if snaps:
            return str(snaps[-1]).replace("\\", "/")
    return None


def find_python(root: Path) -> str | None:
    configured = os.environ.get("QWEN_TTS_PYTHON")
    if configured and Path(configured).exists():
        return str(Path(configured).resolve()).replace("\\", "/")
    for candidate in (root / ".qwen-tts-venv" / "Scripts" / "python.exe",
                      root / ".qwen-tts-venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe",
                      root / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate).replace("\\", "/")
    return None


def check_tts_runtime(cfg) -> tuple[bool, str]:
    python = Path(cfg.python)
    if not python.exists():
        return False, f"configured Python does not exist: {python}"

    cache_dir = cfg.work / ".cache" / "numba"
    cache_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "NUMBA_CACHE_DIR": str(cache_dir),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONUNBUFFERED": "1",
    })
    code = (
        "import json, torch; "
        "from qwen_tts import Qwen3TTSModel; "
        "print(json.dumps({'torch': torch.__version__, "
        "'cuda': torch.cuda.is_available(), "
        "'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
    )
    try:
        proc = subprocess.run(
            [str(python), "-u", "-c", code],
            cwd=cfg.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.runtime["preflight_timeout_s"],
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"qwen_tts import exceeded {cfg.runtime['preflight_timeout_s']}s; "
            f"NUMBA_CACHE_DIR={cache_dir}"
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[-1600:]
        return False, f"qwen_tts import failed:\n{detail}"
    try:
        info = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return False, f"runtime probe returned unexpected output:\n{proc.stdout[-1600:]}"
    if not info.get("cuda"):
        return False, f"PyTorch {info.get('torch')} loaded, but CUDA is unavailable"
    return True, (
        f"PyTorch {info.get('torch')}; CUDA device={info.get('device')}; "
        f"NUMBA_CACHE_DIR={cache_dir}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--here", action="store_true", help="scaffold in the current directory")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--name", default=None, help="composition project name, e.g. my-film-9x16")
    parser.add_argument("--srt", default="transcription.srt")
    parser.add_argument("--force", action="store_true", help="overwrite an existing config")
    parser.add_argument(
        "--skip-runtime-check",
        action="store_true",
        help="skip the configured Python/qwen_tts/CUDA import probe",
    )
    args = parser.parse_args()

    root = (args.root or Path.cwd()).resolve()
    config_path = root / CONFIG_NAME
    if config_path.exists() and not args.force:
        print(f"[keep] {config_path} already exists (use --force to overwrite)")
    else:
        config = {
            "project": args.name or f"{root.name.lower().replace(' ', '-')}-9x16",
            "srt": args.srt,
            "python": find_python(root),
            "model_dir": find_tts_snapshot(),
        }
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    from vconfig import Config
    cfg = Config(root)
    cfg.mkdirs()

    print(f"[init] {config_path}")
    print(f"[init] project dir  {cfg.project}")
    print(f"[init] python       {cfg.python}")
    print(f"[init] TTS snapshot {cfg.model_dir or '(not found — download Qwen3-TTS)'}")

    missing = []
    if not cfg.srt.exists():
        missing.append(f"{cfg.srt}  (the transcript to narrate)")
    for name in ("profile.json", "consent.txt", "reference.wav", "reference.txt"):
        if not (cfg.voice_dir / name).exists():
            missing.append(f"{cfg.voice_dir / name}")
    if cfg.model_dir is None or not cfg.model_dir.exists():
        missing.append("local Qwen3-TTS Hugging Face snapshot")
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg on PATH")
    if not shutil.which("node"):
        missing.append("node on PATH")
    if missing:
        print("\n[todo] still missing:")
        for item in missing:
            print(f"   - {item}")
    else:
        print("\n[ready] all static inputs present")

    runtime_ok = False
    if not args.skip_runtime_check and not any(
        item for item in missing if "Qwen3-TTS" in item or "python" in item.lower()
    ):
        print("\n[check] importing qwen_tts with the configured Python", flush=True)
        runtime_ok, detail = check_tts_runtime(cfg)
        print(f"[{'PASS' if runtime_ok else 'FAIL'}] {detail}")
    elif args.skip_runtime_check:
        print("\n[skip] runtime import check")

    if not shutil.which("sox"):
        print("[warn] SoX executable not found; 12 Hz Qwen3-TTS can still run, "
              "but the package emits a warning and 25 Hz paths may fail.")
    print(f"\nDefaults not written to the file stay at their built-in values "
          f"({len(DEFAULTS)} keys); override any of them by adding the key to {CONFIG_NAME}.")
    return 0 if not missing and (runtime_ok or args.skip_runtime_check) else 1


if __name__ == "__main__":
    sys.exit(main())
