#!/usr/bin/env python3
"""Expand the authored beat table into video_work/micro_script.json.

You author two small files; this fills in ids, chapter names and duration
estimates so nothing is hand-numbered (renumbering 86 beats by hand after an
insert is how ids drift out of sync with the compositions).

  <work>/chapters.json   [{"key": "ch1", "name": "开场 · 七个概念", "srt_id": 1}, ...]
  <work>/beats.jsonl     one JSON object per line, in playback order:
      {"chapter": "ch1",
       "text": "今天一期视频，把Attention家族最核心的七个概念一次讲透：",
       "tts": "…",                     // optional; only when the reading needs fixing
       "role": "hook",                 // hook|setup|mechanism|contrast|example|result|summary
       "emphasis": ["七个概念"],
       "metaphor": "标题从墨色画布中立起，七个刻度沿纵轴依次点亮",
       "screen": ["ATTENTION 家族", "七个核心概念"]}

`text` must stay verbatim from the SRT — check_verbatim.py enforces that.
`tts` changes only what the TTS *says*, never what the screen shows.

Usage: python <skill>/scripts/build_micro_script.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

# rough chars→seconds, for the storyboard draft only; real time comes from TTS
CHARS_PER_SECOND = 5.1
ROLES = {"hook", "setup", "mechanism", "contrast", "example", "result", "summary"}


def estimate(text: str) -> float:
    han = len(re.findall(r"[一-鿿]", text))
    other = len(re.sub(r"[\s，。、：；？！——“”]", "", text)) - han
    return round(han / CHARS_PER_SECOND + other * 0.09 + 0.35, 2)


def main() -> int:
    chapters_path = CFG.work / "chapters.json"
    beats_path = CFG.work / "beats.jsonl"
    for path in (chapters_path, beats_path):
        if not path.exists():
            raise SystemExit(f"missing {path} — see the docstring of this script for the format")

    chapters = json.loads(chapters_path.read_text(encoding="utf-8"))
    meta = {c["key"]: c for c in chapters}

    out, problems = [], []
    for index, line in enumerate(beats_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        beat = json.loads(line)
        key = beat["chapter"]
        if key not in meta:
            problems.append(f"line {index}: unknown chapter {key!r}")
            continue
        if beat.get("role") not in ROLES:
            problems.append(f"line {index}: role {beat.get('role')!r} not in {sorted(ROLES)}")
        tts = beat.get("tts") or beat["text"]
        out.append({
            "id": f"b{len(out) + 1:03d}",
            "source_srt_ids": [meta[key].get("srt_id", meta[key].get("key"))],
            "chapter": meta[key]["name"],
            "chapter_key": key,
            "text": beat["text"],
            "tts_text": tts,
            "semantic_role": beat.get("role", "setup"),
            "emphasis_words": beat.get("emphasis", []),
            "visual_metaphor": beat.get("metaphor", ""),
            "on_screen_text": beat.get("screen", []),
            "estimated_duration_s": estimate(tts),
        })

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        return 1

    CFG.work.mkdir(parents=True, exist_ok=True)
    CFG.micro_script.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(b["estimated_duration_s"] for b in out)
    print(f"beats={len(out)} estimated_speech={total:.1f}s -> {CFG.micro_script}")
    for chapter in chapters:
        items = [b for b in out if b["chapter_key"] == chapter["key"]]
        chars = sum(len(b["text"]) for b in items)
        secs = sum(b["estimated_duration_s"] for b in items)
        print(f"  {chapter['key']} {chapter['name']}: beats={len(items):2d} "
              f"chars={chars:3d} est={secs:6.1f}s")
    overrides = sum(1 for b in out if b["tts_text"] != b["text"])
    print(f"pronunciation overrides: {overrides}")
    print("next: python <skill>/scripts/check_verbatim.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
