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
import shutil
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
    for candidate in (root / ".qwen-tts-venv" / "Scripts" / "python.exe",
                      root / ".qwen-tts-venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe",
                      root / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate).replace("\\", "/")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--here", action="store_true", help="scaffold in the current directory")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--name", default=None, help="composition project name, e.g. my-film-9x16")
    parser.add_argument("--srt", default="transcription.srt")
    parser.add_argument("--force", action="store_true", help="overwrite an existing config")
    args = parser.parse_args()

    root = (args.root or Path.cwd()).resolve()
    config_path = root / CONFIG_NAME
    if config_path.exists() and not args.force:
        print(f"[keep] {config_path} already exists (use --force to overwrite)")
        return 0

    config = {
        "project": args.name or f"{root.name.lower().replace(' ', '-')}-9x16",
        "srt": args.srt,
        "python": find_python(root),
        "model_dir": find_tts_snapshot(),
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    from vconfig import Config
    cfg = Config(root)
    cfg.mkdirs()

    print(f"[init] {config_path}")
    print(f"[init] project dir  {cfg.project}")
    print(f"[init] python       {config['python'] or '(not found — create .qwen-tts-venv)'}")
    print(f"[init] TTS snapshot {config['model_dir'] or '(not found — download Qwen3-TTS)'}")

    missing = []
    if not cfg.srt.exists():
        missing.append(f"{cfg.srt}  (the transcript to narrate)")
    for name in ("profile.json", "consent.txt"):
        if not (cfg.voice_dir / name).exists():
            missing.append(f"{cfg.voice_dir / name}")
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg on PATH")
    if missing:
        print("\n[todo] still missing:")
        for item in missing:
            print(f"   - {item}")
    else:
        print("\n[ready] all inputs present")
    print(f"\nDefaults not written to the file stay at their built-in values "
          f"({len(DEFAULTS)} keys); override any of them by adding the key to {CONFIG_NAME}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
