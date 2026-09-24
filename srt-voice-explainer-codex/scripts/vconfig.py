#!/usr/bin/env python3
"""Project configuration for the srt-voice-explainer pipeline.

Every script in this skill reads its paths and thresholds from a single
`video.config.json` at the project root, so the same toolchain runs against any
project without editing code. Run `init_project.py` to create one.

Resolution order for the project root: $SRT_VIDEO_ROOT, else the nearest parent
of the current working directory that contains video.config.json.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

CONFIG_NAME = "video.config.json"

DEFAULTS = {
    "project": "explainer-9x16",
    "technical_explainer": False,       # True -> require formula/code content audits
    "srt": "transcription.srt",
    "voice_dir": "my_voice",
    "work_dir": "video_work",
    "deliver_dir": "deliverables",
    "python": None,                       # None -> the interpreter running this
    "model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "model_dir": None,                    # HF *snapshot* dir, not the repo dir
    "asr_model": "openai/whisper-small",
    "asr_model_dir": None,                # optional local HF snapshot override
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "tts_params": {
        "language": "Chinese",
        "do_sample": True,
        "temperature": 0.25,
        "top_p": 0.85,
        "repetition_penalty": 1.08,
    },
    "base_seed": 20260727,
    "runtime": {
        "heartbeat_s": 20,
        "import_timeout_s": 90,
        "model_load_timeout_s": 300,
        "clone_prompt_timeout_s": 120,
        "beat_timeout_s": 180,
        "asr_model_load_timeout_s": 180,
        "asr_beat_timeout_s": 90,
        "subprocess_timeout_s": 600,
        "preflight_timeout_s": 60,
    },
    "pauses": {"clause": 0.14, "sentence": 0.32, "chapter": 0.60,
               "lead_in": 0.35, "tail": 0.90},
    "sentence_end": "。！？",
    "loudness": {
        "unit_target_lufs": -17.0,
        "unit_ceiling_dbfs": -1.6,
        "programme_target_lufs": -17.0,
        "programme_ceiling_dbfs": -1.4,
        "unit_stdev_max_lu": 1.0,
        "unit_range_max_lu": 3.0,
        "short_term_stdev_max_lu": 1.5,
        "short_term_p5_p95_max_lu": 4.0,
        "chapter_deviation_max_lu": 1.0,
        "true_peak_max_dbtp": -1.0,
    },
    "safe_area": {"top": 140, "bottom": 260, "right": 140, "side": 84},
    "min_font_px": 24,
}


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("SRT_VIDEO_ROOT")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).exists():
            return candidate
    raise SystemExit(
        f"no {CONFIG_NAME} found in {here} or any parent.\n"
        f"Run:  python <skill>/scripts/init_project.py --here"
    )


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, root: Path):
        self.root = root
        raw = json.loads((root / CONFIG_NAME).read_text(encoding="utf-8"))
        self.data = _merge(DEFAULTS, raw)

        self.srt = root / self.data["srt"]
        self.voice_dir = root / self.data["voice_dir"]
        self.work = root / self.data["work_dir"]
        self.deliver = root / self.data["deliver_dir"]
        self.project = self.work / self.data["project"]
        self.technical_explainer = bool(self.data["technical_explainer"])
        self.compositions = self.project / "compositions"
        self.assets = self.project / "assets"
        self.renders = self.project / "renders"
        self.narration = self.work / "narration"
        self.raw = self.narration / "raw"
        self.mastered = self.narration / "mastered"
        self.alignment = self.work / "alignment.json"
        self.micro_script = self.work / "micro_script.json"

        self.model_id = self.data["model_id"]
        self.model_dir = Path(self.data["model_dir"]) if self.data["model_dir"] else None
        self.asr_model = self.data["asr_model"]
        self.asr_model_dir = (
            Path(self.data["asr_model_dir"]) if self.data["asr_model_dir"] else None
        )
        self.tts_params = self.data["tts_params"]
        self.base_seed = self.data["base_seed"]
        self.runtime = self.data["runtime"]
        self.pauses = self.data["pauses"]
        self.sentence_end = self.data["sentence_end"]
        self.loudness = self.data["loudness"]
        self.safe_area = self.data["safe_area"]
        self.width = self.data["width"]
        self.height = self.data["height"]
        self.fps = self.data["fps"]
        self.python = self.data["python"] or sys.executable

    def __getitem__(self, key):
        return self.data[key]

    def ffmpeg(self) -> str:
        return shutil.which("ffmpeg") or "ffmpeg"

    def ffprobe(self) -> str:
        return shutil.which("ffprobe") or "ffprobe"

    def mkdirs(self) -> None:
        for path in (self.work, self.deliver, self.project, self.compositions,
                     self.assets, self.renders, self.narration, self.raw, self.mastered):
            path.mkdir(parents=True, exist_ok=True)


_CFG: Config | None = None


def __getattr__(name):
    """Resolve CFG on first use, not at import time — init_project.py imports this
    module precisely when no config exists yet."""
    if name == "CFG":
        global _CFG
        if _CFG is None:
            _CFG = Config(find_root())
        return _CFG
    raise AttributeError(name)
