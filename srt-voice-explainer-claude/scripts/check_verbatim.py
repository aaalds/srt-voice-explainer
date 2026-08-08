#!/usr/bin/env python3
"""Verify the beat table against the source SRT, character for character.

This is the guard that keeps QUALITY_FIRST honest: the narration units are a
*re-segmentation* of the transcript, never a rewrite. Concatenating every unit's
`text` must reproduce the SRT body exactly — no dropped clause, no smoothed
wording, no silently merged sentence.

Also checks the schema and flags units whose length will read badly.

Under `timing_mode = DURATION_LOCKED` the character-for-character requirement is
relaxed — but not into nothing. The divergence is still printed, and the run
FAILs unless `video_work/SCRIPT_CHANGES.md` exists with content: 压稿可以，
压得没有记录不行。A gate everyone learns to ignore is worse than no gate.

Usage: python <skill>/scripts/check_verbatim.py [--json report.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

REQUIRED = ["id", "chapter", "chapter_key", "text", "tts_text", "semantic_role",
            "emphasis_words", "visual_metaphor", "on_screen_text"]

TIMECODE = re.compile(r"\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->")


def parse_srt(path: Path) -> list[str]:
    """Return cue texts. Tolerates files with no blank line between cues —
    a real transcript exporter emitted that and a blank-line-delimited parser
    silently returned one giant block."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    cues, buf, seen_time = [], [], False
    for line in lines:
        stripped = line.strip()
        if TIMECODE.search(stripped):
            if buf:
                cues.append(" ".join(buf).strip())
                buf = []
            seen_time = True
            continue
        if not seen_time:
            continue
        if stripped.isdigit() and buf:      # next cue's index
            cues.append(" ".join(buf).strip())
            buf = []
            continue
        if stripped:
            buf.append(stripped)
    if buf:
        cues.append(" ".join(buf).strip())
    return [c for c in cues if c]


def normalise(text: str) -> str:
    """Fold only what is genuinely invisible: width, whitespace. Punctuation and
    wording are NOT folded — a changed comma is a changed script."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    beats = json.loads(CFG.micro_script.read_text(encoding="utf-8"))
    source = normalise("".join(parse_srt(CFG.srt)))
    rebuilt = normalise("".join(b["text"] for b in beats))

    locked = CFG.timing_mode == "DURATION_LOCKED"
    changes_doc = CFG.work / "SCRIPT_CHANGES.md"
    problems = []

    if source != rebuilt:
        # locate the first divergence so the fix is a one-liner, not a diff hunt
        i = next((i for i, (a, b) in enumerate(zip(source, rebuilt)) if a != b),
                 min(len(source), len(rebuilt)))
        detail = (
            f"文案与字幕稿不一致：第 {i} 个字符起分叉\n"
            f"    SRT  …{source[max(0, i-30):i]}[{source[i:i+1]}]{source[i+1:i+30]}…\n"
            f"    beats…{rebuilt[max(0, i-30):i]}[{rebuilt[i:i+1]}]{rebuilt[i+1:i+30]}…\n"
            f"    (SRT {len(source)} 字 / beats {len(rebuilt)} 字)")
        if not locked:
            problems.append(detail)
        else:
            # DURATION_LOCKED 允许压稿，但不允许压得没有记录：
            # 这道门禁从「文案不许变」转成「文案变了必须有账」。
            print(f"[locked] {detail}")
            if not changes_doc.exists() or not changes_doc.read_text(encoding="utf-8").strip():
                problems.append(
                    "TIMING_MODE = DURATION_LOCKED 但 video_work/SCRIPT_CHANGES.md 不存在或为空。"
                    "压稿必须逐条记录原句/改句/删了什么，否则改动无法复核（铁律 5）。")

    seen = set()
    for index, beat in enumerate(beats):
        missing = [k for k in REQUIRED if k not in beat]
        if missing:
            problems.append(f"{beat.get('id', index)}: 缺字段 {missing}")
            continue
        if beat["id"] in seen:
            problems.append(f"{beat['id']}: id 重复")
        seen.add(beat["id"])
        for word in beat["emphasis_words"]:
            if word and word not in beat["text"]:
                problems.append(f"{beat['id']}: emphasis_words 中的「{word}」不在本段文案里")

    chapters, order = {}, []
    for beat in beats:
        if beat["chapter_key"] not in chapters:
            chapters[beat["chapter_key"]] = 0
            order.append(beat["chapter_key"])
        chapters[beat["chapter_key"]] += 1
    if len(order) != len(set(order)):
        problems.append("章节顺序不连续：同一 chapter_key 出现在不相邻的位置")

    lengths = [len(normalise(b["text"])) for b in beats]
    long_units = [(b["id"], n) for b, n in zip(beats, lengths) if n > 40]
    short_units = [(b["id"], n) for b, n in zip(beats, lengths) if n < 6]

    report = {
        "srt": str(CFG.srt),
        "timing_mode": CFG.timing_mode,
        "beats": len(beats),
        "chapters": [{"chapter_key": k, "units": chapters[k]} for k in order],
        "characters": len(source),
        "verbatim": source == rebuilt,
        "median_unit_chars": sorted(lengths)[len(lengths) // 2] if lengths else 0,
        "long_units": long_units,
        "short_units": short_units,
        "problems": problems,
    }
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[verbatim] {len(beats)} units / {len(order)} chapters / {len(source)} chars")
    print(f"[verbatim] TIMING_MODE = {CFG.timing_mode}")
    print(f"[verbatim] 逐字符一致: {'YES' if report['verbatim'] else 'NO'}")
    if long_units:
        print(f"[warn] {len(long_units)} 段超过 40 字，画面很难跟上：{long_units[:5]}")
    if short_units:
        print(f"[warn] {len(short_units)} 段少于 6 字，容易切碎语气：{short_units[:5]}")
    for problem in problems:
        print(f"[FAIL] {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
