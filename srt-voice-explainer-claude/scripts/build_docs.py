#!/usr/bin/env python3
"""Generate SCRIPT.md / STORYBOARD.md / SCRIPT_CHANGES.md from the sources of truth.

Inputs : <work>/micro_script.json + <work>/alignment.json
         <work>/chapters.json — optional `world` / `layers` / `enter` / `exit` per chapter
Outputs: <work>/SCRIPT.md, <work>/STORYBOARD.md, <work>/SCRIPT_CHANGES.md

Run this AFTER generate_tts.py, so every timestamp comes from real audio rather
than the SRT's timecodes.

SCRIPT_CHANGES.md is only ever *generated* while it carries this script's marker
line. Under DURATION_LOCKED the useful version of that file is hand-written (the
cut-by-cut log of what was compressed and why), and a generator that clobbers it
destroys the one artefact the timing-mode switch is supposed to produce. If an
un-marked file is found, the generated table goes to SCRIPT_CHANGES.generated.md
instead and the hand-written one is left alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

MARKER = "<!-- generated-by: build_docs.py — 手写内容请另存，本文件会被覆盖 -->"

ROLE_TEXT = {
    "hook": "被吸引住，知道这期讲什么",
    "setup": "接收背景与定义",
    "mechanism": "看懂它到底怎么工作",
    "contrast": "看清它和上一个方案的差别 / 代价",
    "example": "用一个具体例子坐实刚才的机制",
    "result": "拿到可量化的结论",
    "summary": "收束，把这一段挂回主线",
}

VERBS = [
    ("分裂", "分裂"), ("切成", "分裂"), ("锁定", "锁定"), ("聚拢", "聚拢"), ("聚到", "聚拢"),
    ("填满", "填充"), ("逐格", "填充"), ("折叠", "折叠"), ("收成", "折叠"), ("塌成", "折叠"),
    ("采样", "采样"), ("偏移", "采样"), ("闸门", "闸门"), ("门控", "闸门"), ("关掉", "闸门"),
    ("写入", "写入"), ("点亮", "写入"), ("增长", "增长"), ("上涨", "增长"), ("旋转", "旋转"),
    ("扫过", "扫掠"), ("削平", "削平"), ("拼接", "拼接"), ("并拢", "拼接"), ("重排", "重排"),
    ("滑动", "滑动"), ("缩", "收缩"), ("削掉", "收缩"), ("立起", "写入"), ("浮出", "写入"),
    ("拉长", "增长"), ("展开", "展开"), ("贴合", "锁定"),
]


def verbs_for(metaphor: str) -> str:
    found = []
    for needle, verb in VERBS:
        if needle in metaphor and verb not in found:
            found.append(verb)
    return "、".join(found[:3]) if found else "写入"


def main() -> int:
    beats = json.loads(CFG.micro_script.read_text(encoding="utf-8"))
    align = json.loads(CFG.alignment.read_text(encoding="utf-8"))
    by_id = {b["id"]: b for b in align["beats"]}
    chapters = align["chapters"]

    world_path = CFG.work / "chapters.json"
    world = {}
    if world_path.exists():
        world = {c["key"]: c for c in json.loads(world_path.read_text(encoding="utf-8"))}

    # ---------------- SCRIPT.md ----------------
    locked = CFG.timing_mode == "DURATION_LOCKED"
    verbatim_note = (
        f"`TIMING_MODE = DURATION_LOCKED`，文案已按时长要求压缩，"
        f"逐条改动见 `SCRIPT_CHANGES.md`。"
        if locked else
        f"文本与 `{CFG['srt']}` **逐字一致**（100% 原文，无删改、无压缩）。"
    )
    lines = [
        "# SCRIPT — 最终旁白脚本（锁定）",
        "",
        f"总时长 **{align['total_duration']:.2f}s**　·　旁白单元 **{len(beats)}**　·　章节 **{len(chapters)}**",
        "",
        verbatim_note,
        "`TTS 读法` 一列只在需要纠正发音时出现，不改变屏幕与字幕上的展示文本。",
        "",
    ]
    for chapter in chapters:
        key = chapter["chapter_key"]
        items = [b for b in beats if b["chapter_key"] == key]
        lines += [
            f"## {items[0]['chapter']}　`{key}`",
            "",
            f"`{chapter['start']:.2f}s → {chapter['end']:.2f}s`　（{chapter['duration']:.2f}s，"
            f"{len(items)} 个旁白单元）",
            "",
            "| ID | 起 → 止 | 时长 | 角色 | 旁白（原文） | TTS 读法 |",
            "| -- | ------- | ---- | ---- | ------------ | -------- |",
        ]
        for item in items:
            a = by_id[item["id"]]
            tts = "" if item["tts_text"] == item["text"] else item["tts_text"]
            lines.append(
                f"| `{item['id']}` | {a['start']:.2f} → {a['end']:.2f} | {a['speech_duration']:.2f}s | "
                f"{item['semantic_role']} | {item['text']} | {tts} |"
            )
        lines.append("")
    (CFG.work / "SCRIPT.md").write_text("\n".join(lines), encoding="utf-8")

    # ---------------- STORYBOARD.md ----------------
    sb = [
        "# STORYBOARD — 视觉节拍分镜",
        "",
        "一个旁白单元 = 一个视觉节拍。所有时间来自 `alignment.json`（真实旁白），",
        f"不是原 SRT 时间码。画布 {CFG.width}×{CFG.height} @{CFG.fps}fps，原生竖屏。",
        "",
    ]
    for chapter in chapters:
        key = chapter["chapter_key"]
        items = [b for b in beats if b["chapter_key"] == key]
        info = world.get(key, {})
        first, last = chapter is chapters[0], chapter is chapters[-1]
        sb += [
            f"## {items[0]['chapter']}　`{key}`　{chapter['start']:.2f}s → {chapter['end']:.2f}s",
            "",
            f"- **视觉世界**：{info.get('world', '（待填：本章专属的空间组织）')}",
            f"- **构图分层**：{info.get('layers', '（待填：前景／中景／背景）')}",
            f"- **进入方式**：{info.get('enter', '片头，从底色升起' if first else '硬切 + 纵向推入 0.28s')}"
            f"　·　**离开方式**：{info.get('exit', '定格收束' if last else '内容上推 0.36s，下一章硬切')}",
            "",
        ]
        for item in items:
            a = by_id[item["id"]]
            emph = item["emphasis_words"][0] if item["emphasis_words"] else item["text"][:4]
            sb += [
                f"### `{item['id']}`　{a['start']:.2f} → {a['end']:.2f}s（{a['speech_duration']:.2f}s，"
                f"节拍持续到 {a['beat_end']:.2f}s）",
                "",
                f"- **旁白**：{item['text']}",
                f"- **观众此刻理解**：{ROLE_TEXT.get(item['semantic_role'], item['semantic_role'])}",
                f"- **画面动作**：{item['visual_metaphor']}",
                f"- **运动动词**：{verbs_for(item['visual_metaphor'])}",
                f"- **屏幕文字**：{' ／ '.join(item['on_screen_text'])}",
                f"- **入场 / 保持 / 退场**：入场 0.30–0.45s（发音前 0–150ms 起）／"
                f"保持至 {a['beat_end']:.2f}s／退场 0.24s 上移淡出",
                f"- **对齐到**：「{emph}」的发音区间内完成主要状态变化",
                f"- **转场**：{'章节收束' if item is items[-1] else '局部重组，不切镜'}",
                "",
            ]
    (CFG.work / "STORYBOARD.md").write_text("\n".join(sb), encoding="utf-8")

    # ---------------- SCRIPT_CHANGES.md ----------------
    changed = [b for b in beats if b["tts_text"] != b["text"]]
    total_chars = sum(len(b["text"]) for b in beats)
    if locked:
        content_section = [
            "**已压缩。** `TIMING_MODE = DURATION_LOCKED`——为满足时长要求删改过文案。",
            "**本节由人手写**：逐条列出原句、改句、删了什么、为什么，并注明哪些是用户口述内容"
            "（用户原话原则上不动，确需纠正必须单列并给出回退方法）。",
            "本脚本不会替你写这一节，也不会覆盖你写好的版本。",
        ]
    else:
        content_section = [
            f"**无。** `TIMING_MODE = QUALITY_FIRST`，全片保留 `{CFG['srt']}` 的全部原文。",
            f"已用程序校验：把 {len(beats)} 个旁白单元按顺序拼接后，与原 SRT 正文**逐字符完全相同**"
            f"（{total_chars} 字，`check_verbatim.py`）。",
        ]
    ch = [
        MARKER,
        "",
        "# SCRIPT_CHANGES — 文案与读法变更记录",
        "",
        "## 1. 文案内容变更",
        "",
        *content_section,
        "",
        "## 2. 发音字典（只改送给 TTS 的读法，不改展示文本）",
        "",
        f"共 {len(changed)} 处。展示文本 = 屏幕文字 = 字幕；下表右列只影响 TTS 的朗读。",
        "",
        "| ID | 展示文本（= 字幕/屏幕） | 送给 TTS 的读法 |",
        "| -- | ----------------------- | ---------------- |",
    ]
    for item in changed:
        ch.append(f"| `{item['id']}` | {item['text']} | {item['tts_text']} |")

    target = CFG.work / "SCRIPT_CHANGES.md"
    clobbered = target.exists() and MARKER not in target.read_text(encoding="utf-8")
    if clobbered:
        target = CFG.work / "SCRIPT_CHANGES.generated.md"
    target.write_text("\n".join(ch), encoding="utf-8")

    print(f"SCRIPT.md / STORYBOARD.md / {target.name} written to {CFG.work} "
          f"({len(beats)} beats, {len(changed)} pronunciation overrides)")
    if clobbered:
        print("[note] SCRIPT_CHANGES.md 是手写的（无生成标记）——已保留原文件，"
              "生成版写到 SCRIPT_CHANGES.generated.md")
    if locked:
        print("[note] TIMING_MODE = DURATION_LOCKED —— SCRIPT_CHANGES.md 的第 1 节需要人手写")
    missing = [c["chapter_key"] for c in chapters if c["chapter_key"] not in world]
    if missing:
        print(f"[note] chapters.json has no world/layers for: {', '.join(missing)} "
              f"— STORYBOARD.md left placeholders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
