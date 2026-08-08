#!/usr/bin/env python3
"""Push alignment.json (真实旁白时间) into the HyperFrames project.

Single source of truth: video_work/alignment.json.
Rewrites, in place:
  - index.html      root data-duration / 每个章节 host 的 data-start+data-duration /
                    stage-fill、rail、audio 的时长 / #main-chapters 数据块
  - compositions/chN.html  #chN-beats 数据块（场景内相对时间）
  - assets/narration.wav   从 video_work/narration/narration.wav 同步

运行：./.qwen-tts-venv/Scripts/python.exe tools/sync_timing.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

ROOT, WORK, PROJECT = CFG.root, CFG.work, CFG.project


def replace_attr(html: str, element_id: str, attr: str, value: str) -> str:
    pattern = re.compile(
        r'(id="' + re.escape(element_id) + r'"(?:[^>]*?))\b' + re.escape(attr) + r'="[^"]*"',
        re.S,
    )
    new, count = pattern.subn(lambda m: f'{m.group(1)}{attr}="{value}"', html, count=1)
    if count != 1:
        raise SystemExit(f"could not set {attr} on #{element_id}")
    return new


def replace_json_block(html: str, script_id: str, payload) -> str:
    pattern = re.compile(
        r'(<script id="' + re.escape(script_id) + r'" type="application/json">)(.*?)(</script>)',
        re.S,
    )
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    new, count = pattern.subn(lambda m: m.group(1) + "\n" + body + "\n" + m.group(3), html, count=1)
    if count != 1:
        raise SystemExit(f"could not fill json block #{script_id}")
    return new


def replace_beats_attr(html: str, comp_id: str, payload) -> str:
    """The runtime lifts <script> nodes out of the cloned template, so a JSON
    <script> data block never reaches the DOM. The beat table rides on the
    composition root as an HTML-escaped data attribute instead."""
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    pattern = re.compile(
        r'(id="' + re.escape(comp_id) + r'-beats"\s+)data-beats="[^"]*"'
    )
    new, count = pattern.subn(lambda m: m.group(1) + f'data-beats="{body}"', html, count=1)
    if count != 1:
        raise SystemExit(f"could not fill data-beats on {comp_id}")
    return new


def main() -> None:
    data = json.loads((WORK / "alignment.json").read_text(encoding="utf-8"))
    total = data["total_duration"]
    chapters = data["chapters"]
    beats = data["beats"]

    # ---------- index.html ----------
    index_path = PROJECT / "index.html"
    html = index_path.read_text(encoding="utf-8")
    html = replace_attr(html, "root", "data-duration", f"{total:.3f}")
    html = replace_attr(html, "stage-fill", "data-duration", f"{total:.3f}")
    html = replace_attr(html, "rail", "data-duration", f"{total:.3f}")
    html = replace_attr(html, "narration", "data-duration", f"{total:.3f}")
    for chapter in chapters:
        key = chapter["chapter_key"]
        html = replace_attr(html, key, "data-start", f"{chapter['start']:.3f}")
        html = replace_attr(html, key, "data-duration", f"{chapter['duration']:.3f}")
    html = replace_json_block(
        html,
        "main-chapters",
        [
            {
                "chapter_key": c["chapter_key"],
                "start": round(c["start"], 3),
                "end": round(c["end"], 3),
                "duration": round(c["duration"], 3),
            }
            for c in chapters
        ],
    )
    index_path.write_text(html, encoding="utf-8")

    # ---------- chapters ----------
    for chapter in chapters:
        key = chapter["chapter_key"]
        path = PROJECT / "compositions" / f"{key}.html"
        if not path.exists():
            print(f"[skip] {path.name} not authored yet")
            continue
        local = [
            {
                "id": b["id"],
                "start": round(b["start"] - chapter["start"], 3),
                "end": round(b["end"] - chapter["start"], 3),
                "hold": round(b["beat_end"] - chapter["start"], 3),
                "dur": round(b["speech_duration"], 3),
                "role": b["semantic_role"],
                "text": b["text"],
                "on": b["on_screen_text"],
                "emph": b["emphasis_words"],
            }
            for b in beats
            if b["chapter_key"] == key
        ]
        source = path.read_text(encoding="utf-8")
        source = replace_beats_attr(source, key, local)
        path.write_text(source, encoding="utf-8")
        print(f"[sync] {key}: {len(local)} beats, {chapter['duration']:.2f}s "
              f"({chapter['start']:.2f} → {chapter['end']:.2f})")

    # ---------- audio ----------
    assets = PROJECT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WORK / "narration" / "narration.wav", assets / "narration.wav")
    print(f"[sync] narration.wav copied, total duration {total:.3f}s")


if __name__ == "__main__":
    main()
