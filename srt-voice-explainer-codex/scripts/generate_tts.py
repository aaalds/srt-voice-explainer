#!/usr/bin/env python3
"""Local voice-cloned narration for the Attention 9:16 explainer.

- Reads video_work/micro_script.json (short 12–35 char narration units).
- Synthesises每个单元 with the local Qwen3-TTS voice clone (my_voice/).
- Never uploads anything: model, reference audio and reference text stay local.
- Cache key = text + model + params + reference-audio hash + reference-text hash;
  任一变化即重生成。失败直接标记失败并换受控 seed 重试，绝不用静音冒充成功。
- Masters每段 (gentle HP filter + local BS.1770-4), then writes alignment.json /
  narration.wav / final_aligned.srt driven by真实音频时长。

Usage:
    ./.qwen-tts-venv/Scripts/python.exe generate_tts.py            # all beats
    ./.qwen-tts-venv/Scripts/python.exe generate_tts.py --only b012 b013
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loudness import normalise, true_peak_dbfs  # noqa: E402
from runtime_support import RuntimeSession, configure_local_runtime  # noqa: E402
from vconfig import CFG  # noqa: E402

ROOT = CFG.root
VOICE_DIR = CFG.voice_dir
WORK = CFG.work
OUT = CFG.narration
RAW = CFG.raw
MASTERED = CFG.mastered

MODEL_DIR = CFG.model_dir
MODEL_ID = CFG.model_id
if MODEL_DIR is None or not MODEL_DIR.exists():
    raise SystemExit(
        "video.config.json -> model_dir must point at the Hugging Face *snapshot* "
        "directory (…/models--Qwen--Qwen3-TTS-…/snapshots/<hash>), not the repo dir."
    )

TTS_PARAMS = CFG.tts_params
BASE_SEED = CFG.base_seed
RUNTIME = CFG.runtime
NUMBA_CACHE_DIR = configure_local_runtime(CFG)
SESSION: RuntimeSession | None = None

# 停顿策略（SKILL.md §3，可在 video.config.json 覆盖）：分句内 80–180ms，句间 220–420ms，章节间 450–700ms
PAUSE_CLAUSE = CFG.pauses["clause"]
PAUSE_SENTENCE = CFG.pauses["sentence"]
PAUSE_CHAPTER = CFG.pauses["chapter"]

SENTENCE_END = CFG.sentence_end
LEAD_IN = CFG.pauses["lead_in"]   # 片头留白
TAIL = CFG.pauses["tail"]         # 片尾留白

# 响度一致性标准（SKILL.md「音频响度一致性」）
UNIT_TARGET_LUFS = CFG.loudness["unit_target_lufs"]
UNIT_CEILING_DBFS = CFG.loudness["unit_ceiling_dbfs"]
PROGRAMME_TARGET_LUFS = CFG.loudness["programme_target_lufs"]
PROGRAMME_CEILING_DBFS = CFG.loudness["programme_ceiling_dbfs"]

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=RUNTIME["subprocess_timeout_s"],
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"command timed out after {RUNTIME['subprocess_timeout_s']}s: "
            f"{' '.join(cmd[:3])}…"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:3])}…\n{proc.stderr[-2000:]}")
    return proc.stdout


def probe_duration(path: Path) -> float:
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def loudness_stats(path: Path) -> dict:
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=I=-17:TP=-1.5:LRA=7:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=RUNTIME["subprocess_timeout_s"],
    )
    match = re.search(r"\{[^{}]*\"input_i\"[\s\S]*?\}", proc.stderr)
    if not match:
        raise RuntimeError(f"loudnorm measurement failed:\n{proc.stderr[-2000:]}")
    return json.loads(match.group(0))


def master(raw: Path, dst: Path) -> dict:
    """Filter → per-unit loudness normalisation → true-peak ceiling.

    ffmpeg's `loudnorm` is deliberately NOT used here: it needs ≥3 s of input
    and silently returns nonsense measurements below that, which attenuated the
    15 sub-3 s units by up to 45 dB. Measurement/gain now come from tools/loudness.py,
    which implements BS.1770-4 gating directly and is valid at any length.

    Only the spectral filter, a linear gain and a look-ahead peak limiter are
    applied — no compression and no time-domain edit — so the sample count is
    identical to the input and the narration timeline stays valid.
    """
    filtered = dst.with_suffix(".filtered.wav")
    duration = probe_duration(raw)
    filt = (f"highpass=f=75,afade=t=in:st=0:d=0.02,"
            f"afade=t=out:st={max(0.0, duration - 0.06):.3f}:d=0.06")
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
         "-af", filt, "-ar", "48000", "-ac", "1", "-c:a", "pcm_f32le", str(filtered)])

    audio, sample_rate = sf.read(filtered, dtype="float64")
    filtered.unlink()
    audio, info = normalise(audio, sample_rate, UNIT_TARGET_LUFS, UNIT_CEILING_DBFS)
    # float32 master: the per-unit gain can be +11 dB, and 16-bit intermediates
    # would fold that much quantisation noise back into the programme.
    sf.write(dst, audio.astype(np.float32), sample_rate, subtype="FLOAT")
    return info


def syllable_weight(text: str) -> float:
    """Rough syllable count, used only to bound a take's plausible duration.

    One CJK character is one syllable; a Latin letter is not. Counting raw
    characters makes any unit with an English term unretriable — "Physical
    Intelligence" is 20 characters but ~6 syllables, so a 2.9 s take got
    rejected against a 4.2 s floor four times in a row and the run aborted.
    Latin letters are therefore weighted 0.4, which keeps the window tight on
    Chinese while staying honest about embedded English terms.
    """
    stripped = re.sub(r"[\s，。、：；？！——“”]", "", text)
    latin = len(re.findall(r"[A-Za-z]", stripped))
    return (len(stripped) - latin) + 0.4 * latin


def trim_edges(audio: np.ndarray, sample_rate: int, floor_db: float = -46.0) -> np.ndarray:
    """Trim leading/trailing near-silence so pause control is ours, not the model's."""
    if audio.size == 0:
        return audio
    window = max(1, int(sample_rate * 0.01))
    frames = audio[: len(audio) // window * window].reshape(-1, window)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    voiced = np.where(db > floor_db)[0]
    if voiced.size == 0:
        return audio
    start = max(0, (voiced[0] - 2) * window)
    end = min(len(audio), (voiced[-1] + 3) * window)
    return audio[start:end]


def onset_offset(audio: np.ndarray, sample_rate: int, floor_db: float = -45.0) -> float:
    """Seconds of sub-speech-level lead-in at the head of a mastered unit."""
    window = max(1, int(sample_rate * 0.01))
    frames = audio[: len(audio) // window * window].reshape(-1, window)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    voiced = np.where(db > floor_db)[0]
    if voiced.size == 0:
        return 0.0
    return max(0.0, (voiced[0] - 1) * window / sample_rate)


def pause_after(beat: dict, nxt: dict | None) -> float:
    if nxt is None:
        return 0.0
    if beat["chapter_key"] != nxt["chapter_key"]:
        return PAUSE_CHAPTER
    return PAUSE_SENTENCE if beat["text"].rstrip()[-1] in SENTENCE_END else PAUSE_CLAUSE


def timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", default=None, help="beat ids to (re)generate")
    parser.add_argument("--force", action="store_true", help="ignore cache")
    parser.add_argument("--seed-offset", type=int, default=0,
                        help="受控换种子重生成（只用于质检失败的段落）")
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--remaster", action="store_true",
                        help="用已有的 raw 原始录音重跑母带（不调用模型），再重新拼装")
    args = parser.parse_args()
    only_ids = {
        raw_id.strip()
        for value in (args.only or [])
        for raw_id in value.split(",")
        if raw_id.strip()
    }

    profile = json.loads((VOICE_DIR / "profile.json").read_text(encoding="utf-8"))
    consent = (VOICE_DIR / "consent.txt").read_text(encoding="utf-8")
    if (not profile.get("owner_confirmed")
            or profile.get("allow_cloud_upload") is not False
            or "状态：已确认" not in consent):
        raise SystemExit("voice profile is not authorised for local-only generation")

    reference_audio = VOICE_DIR / profile["reference_audio"]
    reference_text_path = VOICE_DIR / profile["reference_text"]
    ref_audio_hash = sha256_file(reference_audio)
    if ref_audio_hash != profile["reference_sha256"]:
        raise SystemExit("reference.wav hash does not match profile.json")
    ref_text = reference_text_path.read_text(encoding="utf-8").strip()
    ref_text_hash = hashlib.sha256(ref_text.encode("utf-8")).hexdigest()

    beats = json.loads((WORK / "micro_script.json").read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    MASTERED.mkdir(parents=True, exist_ok=True)

    known_ids = {beat["id"] for beat in beats}
    unknown_ids = sorted(only_ids - known_ids)
    if unknown_ids:
        raise SystemExit(f"--only contains unknown beat ids: {unknown_ids}")

    session = RuntimeSession(
        name="generate_tts",
        lock_path=OUT / ".generate_tts.lock",
        state_path=OUT / "tts_state.json",
        heartbeat_s=RUNTIME["heartbeat_s"],
    )
    session.start()
    global SESSION
    SESSION = session
    print(f"[runtime] NUMBA_CACHE_DIR={NUMBA_CACHE_DIR}", flush=True)

    cache_path = OUT / "cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def cache_key(beat: dict) -> str:
        payload = json.dumps(
            {
                "text": beat["tts_text"],
                "model": MODEL_ID,
                "params": TTS_PARAMS,
                "ref_audio": ref_audio_hash,
                "ref_text": ref_text_hash,
                "trim": [PAUSE_CLAUSE, PAUSE_SENTENCE, PAUSE_CHAPTER],
                "version": 3,
            },
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    todo = []
    for beat in beats:
        if only_ids and beat["id"] not in only_ids:
            continue
        key = cache_key(beat)
        entry = cache.get(beat["id"])
        fresh = (entry and entry.get("key") == key
                 and (RAW / f"{beat['id']}.wav").exists()
                 and (MASTERED / f"{beat['id']}.wav").exists())
        if args.force or not fresh:
            todo.append(beat)

    if args.remaster:
        # 只重跑母带：模型不加载，原始录音一个字节都不动。
        missing = [b["id"] for b in beats if not (RAW / f"{b['id']}.wav").exists()]
        if missing:
            raise SystemExit(f"--remaster 需要全部原始录音，缺失：{missing[:5]}")
        print(f"[master] re-mastering {len(beats)} units from existing raw takes", flush=True)
        report = []
        for beat in beats:
            SESSION.update("remaster", beat=beat["id"])
            before = probe_duration(RAW / f"{beat['id']}.wav")
            info = master(RAW / f"{beat['id']}.wav", MASTERED / f"{beat['id']}.wav")
            after = sf.info(MASTERED / f"{beat['id']}.wav")
            drift = abs(after.frames / after.samplerate - before)
            if drift > 0.002:
                raise SystemExit(f"{beat['id']} 母带改变了时长 ({drift*1000:.1f} ms)，"
                                 "时间轴会失效，已中止")
            info["id"] = beat["id"]
            report.append(info)
            print(f"[master] {beat['id']} {info['input_lufs']:+.1f} → "
                  f"{info['output_lufs']:+.1f} LUFS (gain {info['gain_db']:+.1f} dB, "
                  f"GR {info['limiter_max_gr_db']:.1f} dB, "
                  f"peak {info['sample_peak_dbfs']:+.1f} dBFS)", flush=True)
        (OUT / "mastering_report.json").write_text(
            json.dumps({"unit_target_lufs": UNIT_TARGET_LUFS,
                        "unit_ceiling_dbfs": UNIT_CEILING_DBFS,
                        "units": report}, ensure_ascii=False, indent=2), encoding="utf-8")

    if todo and not args.assemble_only and not args.remaster:
        print(f"[tts] generating {len(todo)}/{len(beats)} units on local GPU", flush=True)
        print("[tts] importing torch and qwen_tts", flush=True)
        with SESSION.stage("import_qwen_tts", RUNTIME["import_timeout_s"]):
            import torch
            from qwen_tts import Qwen3TTSModel
        print("[tts] imports ready", flush=True)

        print(f"[tts] loading local model {MODEL_DIR}", flush=True)
        with SESSION.stage("load_model", RUNTIME["model_load_timeout_s"]):
            model = Qwen3TTSModel.from_pretrained(
                str(MODEL_DIR), device_map="cuda:0", dtype=torch.bfloat16,
                attn_implementation="sdpa", local_files_only=True,
            )
        print("[tts] model ready; building voice-clone prompt", flush=True)
        with SESSION.stage(
            "create_voice_clone_prompt", RUNTIME["clone_prompt_timeout_s"]
        ):
            clone_prompt = model.create_voice_clone_prompt(
                ref_audio=str(reference_audio), ref_text=ref_text
            )
        print("[tts] voice-clone prompt ready", flush=True)

        for beat in todo:
            text = beat["tts_text"]
            index = int(beat["id"][1:])
            chars = syllable_weight(text)
            expected_min = 0.16 * chars
            expected_max = 0.55 * chars + 1.2  # 超过 = 拖音或段内异常长静音
            attempt = 0
            audio = None
            sample_rate = None
            while attempt < 4:
                seed = BASE_SEED + index * 97 + attempt * 7919 + args.seed_offset * 104729
                torch.manual_seed(seed)
                torch.cuda.manual_seed_all(seed)
                max_tokens = max(320, min(760, len(text) * 5 + 180))
                print(
                    f"[tts] {beat['id']} attempt={attempt} seed={seed} start",
                    flush=True,
                )
                with SESSION.stage(
                    "generate_beat",
                    RUNTIME["beat_timeout_s"],
                    beat=beat["id"],
                    attempt=attempt,
                ):
                    wavs, sample_rate = model.generate_voice_clone(
                        text=text, voice_clone_prompt=clone_prompt,
                        max_new_tokens=max_tokens, **TTS_PARAMS,
                    )
                candidate = trim_edges(np.asarray(wavs[0], dtype=np.float32), sample_rate)
                seconds = len(candidate) / sample_rate
                peak = float(np.max(np.abs(candidate))) if candidate.size else 0.0
                ok = (expected_min <= seconds <= expected_max
                      and 0.15 < peak < 0.999)
                print(f"[tts] {beat['id']} attempt={attempt} seed={seed} "
                      f"{seconds:.2f}s peak={peak:.3f} "
                      f"window={expected_min:.2f}~{expected_max:.2f}s "
                      f"{'ok' if ok else 'REJECT'}", flush=True)
                if ok:
                    audio = candidate
                    break
                attempt += 1
            if audio is None:
                raise SystemExit(f"TTS failed for {beat['id']} after {attempt} attempts")

            fade = min(int(sample_rate * 0.012), len(audio) // 2)
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]

            raw_path = RAW / f"{beat['id']}.wav"
            sf.write(raw_path, audio, sample_rate, subtype="PCM_16")
            master(raw_path, MASTERED / f"{beat['id']}.wav")
            cache[beat["id"]] = {
                "key": cache_key(beat),
                "seed": BASE_SEED + index * 97 + args.seed_offset * 104729,
                "attempts": attempt + 1, "sample_rate": sample_rate,
            }
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- assemble ----------
    SESSION.update("assemble")
    print("[assemble] building narration timeline from real audio", flush=True)
    sample_rate = 48000
    pieces: list[np.ndarray] = []
    clock = LEAD_IN
    pieces.append(np.zeros(int(LEAD_IN * sample_rate), dtype=np.float32))
    alignment = []
    for position, beat in enumerate(beats):
        path = MASTERED / f"{beat['id']}.wav"
        audio, sr = sf.read(path, dtype="float32")
        if sr != sample_rate:
            raise SystemExit(f"{path} unexpected sample rate {sr}")
        # 让"发音起点"严格等于节拍起点：把母带后仍残留的低电平引子（换气/底噪）
        # 从头部挪到尾部，片段总长不变，因此整条时间轴与画面完全不受影响。
        lead = onset_offset(audio, sr)
        if lead > 0.06:
            shift = int(lead * sr)
            audio = np.concatenate([audio[shift:], np.zeros(shift, dtype=np.float32)])
            print(f"[assemble] {beat['id']}: 移除 {lead*1000:.0f}ms 引子，"
                  f"使发音起点对齐节拍起点", flush=True)
        seconds = len(audio) / sr
        gap = pause_after(beat, beats[position + 1] if position + 1 < len(beats) else None)
        alignment.append({
            "id": beat["id"], "chapter_key": beat["chapter_key"], "chapter": beat["chapter"],
            "text": beat["text"], "tts_text": beat["tts_text"],
            "semantic_role": beat["semantic_role"], "emphasis_words": beat["emphasis_words"],
            "on_screen_text": beat["on_screen_text"], "visual_metaphor": beat["visual_metaphor"],
            "start": round(clock, 3), "end": round(clock + seconds, 3),
            "speech_duration": round(seconds, 3), "gap_after": round(gap, 3),
            "beat_end": round(clock + seconds + gap, 3),
        })
        pieces.append(audio)
        clock += seconds
        if gap:
            pieces.append(np.zeros(int(gap * sample_rate), dtype=np.float32))
            clock += gap
    pieces.append(np.zeros(int(TAIL * sample_rate), dtype=np.float32))
    total = clock + TAIL

    narration = np.concatenate(pieces)
    stitched = OUT / "narration_raw.wav"
    sf.write(stitched, narration.astype(np.float32), sample_rate, subtype="FLOAT")

    # 节目级只做一次线性增益 + 真峰值限制：不做压缩，因此单元之间已经对齐的
    # 相对响度关系不会被再次打散。
    final = OUT / "narration.wav"
    mastered_programme, programme_info = normalise(
        narration.astype(np.float64), sample_rate,
        PROGRAMME_TARGET_LUFS, PROGRAMME_CEILING_DBFS)
    sf.write(final, mastered_programme.astype(np.float32), sample_rate, subtype="PCM_24")
    programme_info["true_peak_dbtp"] = round(true_peak_dbfs(mastered_programme, sample_rate), 2)
    print(f"[master] programme {programme_info}", flush=True)

    chapters: dict[str, dict] = {}
    for item in alignment:
        entry = chapters.setdefault(item["chapter_key"], {
            "chapter_key": item["chapter_key"], "chapter": item["chapter"],
            "start": item["start"], "end": item["beat_end"], "beats": [],
        })
        entry["end"] = item["beat_end"]
        entry["beats"].append(item["id"])
    ordered = list(chapters.values())
    # 章节边界对齐到旁白之间的静音中点，避免转场切掉词尾
    for position, chapter in enumerate(ordered):
        chapter["start"] = round(chapter["start"] - (LEAD_IN if position == 0 else 0.30), 3)
        if position + 1 < len(ordered):
            chapter["end"] = round(chapter["end"] - 0.30, 3)
    ordered[0]["start"] = 0.0
    ordered[-1]["end"] = round(total, 3)
    for position in range(len(ordered) - 1):
        ordered[position + 1]["start"] = ordered[position]["end"]
    for chapter in ordered:
        chapter["duration"] = round(chapter["end"] - chapter["start"], 3)

    (WORK / "alignment.json").write_text(json.dumps({
        "sample_rate": sample_rate,
        "total_duration": round(total, 3),
        "lead_in": LEAD_IN,
        "tail": TAIL,
        "audio": "video_work/narration/narration.wav",
        "model": MODEL_ID,
        "params": TTS_PARAMS,
        "reference_sha256": ref_audio_hash,
        "reference_text_sha256": ref_text_hash,
        "chapters": ordered,
        "beats": alignment,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    for cue_id, item in enumerate(alignment, start=1):
        lines += [str(cue_id), f"{timestamp(item['start'])} --> {timestamp(item['end'])}",
                  item["text"], ""]
    (OUT / "final_aligned.srt").write_text("\n".join(lines), encoding="utf-8")

    print(f"[done] beats={len(alignment)} total={total:.2f}s -> {final}", flush=True)
    for chapter in ordered:
        print(f"  {chapter['chapter_key']} {chapter['chapter']}: "
              f"{chapter['start']:7.2f} → {chapter['end']:7.2f} ({chapter['duration']:.2f}s)")
    SESSION.complete(f"beats={len(alignment)} total={total:.2f}s")


if __name__ == "__main__":
    try:
        result = main()
    except BaseException as exc:
        if SESSION is not None:
            SESSION.fail(f"{type(exc).__name__}: {exc}")
        raise
    else:
        sys.exit(result)
