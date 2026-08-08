#!/usr/bin/env python3
"""Blocking pronunciation-consistency gate (SKILL.md 铁律 8).

铁律 8 used to be prose only — the ledger existed but nothing forced the script
and the audio to agree with it. This is that gate. Two stages:

  --stage script   BEFORE generation. Every ledger surface that appears in a
                   beat's `text` must have its canonical reading present in that
                   beat's `tts_text`; the ledger's `occurrences` must match the
                   beats exactly; no un-ledgered Latin token may reach the TTS.
                   Catches 局部覆盖 / 大小写分叉 / 同词多种替换.

  --stage audio    AFTER generation. Transcribes every mastered unit with the
                   local ASR model and checks, for each ledger term, that every
                   occurrence came back as the surface itself or as one of the
                   `asr_variants` a human has already adjudicated and written
                   back into the ledger. An unlisted rendering is a FAIL: either
                   the audio is wrong, or the variant is acceptable and belongs
                   in the ledger with a reason. Nothing gets silently waved
                   through on "I listened to it once".

`asr_variants` is the important field. ASR orthography for a single English word
embedded in Chinese is genuinely unstable (`Sol` comes back as Soul/SAL/So/扫),
so transcript equality is the wrong bar. The right bar is: a human decided once
which renderings are the same phoneme family, and that decision is now in the
ledger where the next run — and the next person — can see it.

Ledger schema (video_work/PRONUNCIATION_LEDGER.json):

    {"entries": [
      {"surface": "Sol", "normalized": "sol", "canonical_tts": "Sol",
       "source": "产品名，官方读作 /sɔːl/", "occurrences": ["b006", "b007", …],
       "verified": true,
       "asr_variants": ["Soul", "soul", "SAL", "扫"],
       "asr_note": "全部落在 /sɔːl/–/soʊl/ 家族内，无一次被拆成 S-O-L 字母读法"}
    ]}

Exit code 1 on any failure. Nothing leaves this machine.

Usage:
    python audit_pronunciation.py --stage script
    python audit_pronunciation.py --stage audio [--lang zh] [--require-verified]
    python audit_pronunciation.py --stage script --selftest   # 证明门禁能失效
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

LEDGER = CFG.work / "PRONUNCIATION_LEDGER.json"


def surface_pattern(surface: str) -> re.Pattern:
    """Word-bounded for pure-ASCII-word surfaces, literal otherwise."""
    escaped = re.escape(surface)
    if re.fullmatch(r"[A-Za-z]+", surface):
        return re.compile(rf"(?<![A-Za-z]){escaped}(?![A-Za-z])")
    return re.compile(escaped)


def load() -> tuple[list[dict], list[dict]]:
    if not LEDGER.exists():
        raise SystemExit(f"missing {LEDGER} — 预检阶段必须先建立发音台账（铁律 8）")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["entries"]
    beats = json.loads(CFG.micro_script.read_text(encoding="utf-8"))
    return ledger, beats


# --------------------------------------------------------------------------- #
# stage: script
# --------------------------------------------------------------------------- #

def check_script(ledger: list[dict], beats: list[dict]) -> list[str]:
    problems: list[str] = []

    by_norm: dict[str, list[dict]] = {}
    for entry in ledger:
        by_norm.setdefault(entry["normalized"], []).append(entry)
    for normalized, entries in by_norm.items():
        if len(entries) > 1:
            problems.append(f"normalized 冲突：{normalized!r} 出现在 {len(entries)} 个条目里")
        readings = {e["canonical_tts"] for e in entries}
        if len(readings) > 1:
            problems.append(f"{normalized!r} 有多种 canonical_tts：{sorted(readings)}")

    for entry in ledger:
        pattern = surface_pattern(entry["surface"])
        found = [b["id"] for b in beats if pattern.search(b["text"])]
        if found != entry.get("occurrences"):
            problems.append(
                f"{entry['surface']!r} occurrences 不符："
                f"台账 {entry.get('occurrences')} / 实际 {found}")
        for beat in beats:
            if not pattern.search(beat["text"]):
                continue
            expected = entry["canonical_tts"]
            if expected not in beat["tts_text"]:
                problems.append(
                    f"{beat['id']}: 含 {entry['surface']!r} 但 tts_text 缺少规范读法 "
                    f"{expected!r}\n      tts_text = {beat['tts_text']}")

    # nothing Latin may reach the TTS without a ledger entry behind it
    covered = [surface_pattern(e["surface"]) for e in ledger]
    canonical = [e["canonical_tts"] for e in ledger]
    for beat in beats:
        residue = beat["tts_text"]
        for reading in sorted(canonical, key=len, reverse=True):
            residue = residue.replace(reading, " ")
        for token in re.findall(r"[A-Za-z][A-Za-z.\-]*", residue):
            if len(token) == 1:            # spelled-out letters from a canonical reading
                continue
            if any(p.search(token) for p in covered):
                continue
            problems.append(f"{beat['id']}: tts_text 里的 {token!r} 不在发音台账内")
    return problems


def selftest_script(ledger: list[dict], beats: list[dict]) -> tuple[bool, str]:
    """Prove the gate can fail: revert one beat's tts_text to the raw spelling.

    Picks the first beat that actually carries a ledger substitution. If the
    gate still passes on that sabotaged input, the gate is a tautology.
    """
    for entry in ledger:
        if entry["canonical_tts"] == entry["surface"]:
            continue
        for beat in beats:
            if entry["canonical_tts"] not in beat["tts_text"]:
                continue
            bad = copy.deepcopy(beats)
            for item in bad:
                if item["id"] == beat["id"]:
                    item["tts_text"] = item["tts_text"].replace(
                        entry["canonical_tts"], entry["surface"])
            failed = bool(check_script(ledger, bad))
            return failed, f"{beat['id']} 的 {entry['canonical_tts']!r} 回退成 {entry['surface']!r}"
    return False, "台账里没有任何替换项，无法构造坏样例（这本身值得复核）"


# --------------------------------------------------------------------------- #
# stage: audio
# --------------------------------------------------------------------------- #

def transcribe_all(beats: list[dict], lang: str) -> dict[str, str]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from transformers import pipeline

    model = CFG.asr_model
    local = CFG.root / model
    asr = pipeline("automatic-speech-recognition",
                   model=str(local if local.exists() else model),
                   device=0 if torch.cuda.is_available() else -1,
                   dtype=torch.float16 if torch.cuda.is_available() else torch.float32)

    transcripts: dict[str, str] = {}
    for beat in beats:
        audio, sample_rate = sf.read(CFG.mastered / f"{beat['id']}.wav", dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sample_rate != 16000:
            audio = resample_poly(audio, 16000, sample_rate)
        result = asr({"raw": audio.astype(np.float32), "sampling_rate": 16000},
                     generate_kwargs={"language": lang, "task": "transcribe", "num_beams": 5})
        transcripts[beat["id"]] = result["text"].strip()
        print(f"[asr] {beat['id']} | {transcripts[beat['id']]}", flush=True)
    return transcripts


def check_audio(ledger: list[dict], transcripts: dict[str, str],
                require_verified: bool) -> list[str]:
    problems: list[str] = []
    for entry in ledger:
        occurrences = entry.get("occurrences") or []
        if not occurrences:
            continue
        accepted = [entry["surface"], *entry.get("asr_variants", [])]
        if len(occurrences) > 1:
            print(f"\n[term] {entry['surface']} → {entry['canonical_tts']!r}")
        for beat_id in occurrences:
            text = transcripts.get(beat_id, "")
            if len(occurrences) > 1:
                print(f"        {beat_id}: {text}")
            if not any(a.lower() in text.lower() for a in accepted if a):
                problems.append(
                    f"{beat_id}: {entry['surface']!r} 的回读 {text!r} 既不是原词也不在 "
                    f"asr_variants {entry.get('asr_variants', [])} 里。"
                    f"\n      要么这段读错了要重生成，要么这个变体可接受——"
                    f"那就把它连同理由写进台账的 asr_variants / asr_note。")
        if require_verified and not entry.get("verified"):
            problems.append(f"{entry['surface']!r} 的 verified 仍为假——人工试听结论未回填台账")
    return problems


# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["script", "audio"], default="script")
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--require-verified", action="store_true",
                        help="audio 阶段额外要求每个台账条目的 verified 为真")
    parser.add_argument("--selftest", action="store_true",
                        help="script 阶段额外构造一个坏样例，验证门禁能判 FAIL")
    args = parser.parse_args()

    ledger, beats = load()

    if args.stage == "script":
        problems = check_script(ledger, beats)
    else:
        transcripts = transcribe_all(beats, args.lang)
        (CFG.work / "pronunciation_asr.json").write_text(
            json.dumps(transcripts, ensure_ascii=False, indent=2), encoding="utf-8")
        problems = check_audio(ledger, transcripts, args.require_verified)

    for problem in problems:
        print(f"[FAIL] {problem}")

    ok = not problems
    if args.selftest and args.stage == "script":
        failed, how = selftest_script(ledger, beats)
        print(f"[selftest] {how} -> 门禁{'可以' if failed else '**不能**'}失效")
        ok = ok and failed

    print(f"[pronunciation:{args.stage}] {'PASS' if not problems else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
