#!/usr/bin/env python3
"""Local ASR verification of the generated narration.

Transcribes每个 mastered 旁白单元 with the locally cached openai/whisper-small
and compares it against the intended TTS text. Nothing leaves this machine.

Output: video_work/narration/qa-asr.json  +  a console summary.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_support import RuntimeSession, configure_local_runtime  # noqa: E402
from vconfig import CFG  # noqa: E402

ROOT, WORK, MASTERED = CFG.root, CFG.work, CFG.mastered
MODEL = CFG.asr_model
RUNTIME = CFG.runtime
NUMBA_CACHE_DIR = configure_local_runtime(CFG)
SESSION: RuntimeSession | None = None

# whisper-small 常输出繁体；折叠成简体后再比对，避免把字形差异记成读错
T2S = str.maketrans(
    "縮點擊後來麼頭個學種關係複雜門線層幾過緩優隨長爆為權變數據記憶時間處理術網絡運動節點資訊實現這個問題決定開關遺忘態勢圖與並將從應該價丟碼滿標準當級簡構檢測則規網樣擴讓",
    "缩点击后来么头个学种关系复杂门线层几过缓优随长爆为权变数据记忆时间处理术网络运动节点资讯实现这个问题决定开关遗忘态势图与并将从应该价丢码满标准当级简构检测则规网样扩让",
)

CN_NUM = {
    "零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
}


def normalise(text: str) -> str:
    """Strip everything that ASR and TTS are allowed to disagree about."""
    text = text.translate(T2S).lower()
    text = re.sub(r"[\s，。、：；？！——（）()\"'·,.?!:;\-–—→×∙]", "", text)
    text = "".join(CN_NUM.get(ch, ch) for ch in text)
    text = text.replace("十", "").replace("百", "").replace("千", "").replace("万", "").replace("亿", "")
    text = re.sub(r"[bkmgt](?![a-z])", "", text)
    return text


def similarity(a: str, b: str) -> float:
    """Character-level Levenshtein ratio."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


def resolve_local_model() -> Path:
    if CFG.asr_model_dir is not None:
        if CFG.asr_model_dir.exists():
            return CFG.asr_model_dir
        raise SystemExit(f"video.config.json -> asr_model_dir not found: {CFG.asr_model_dir}")

    direct = Path(MODEL)
    if direct.exists():
        return direct

    repo = Path.home() / ".cache" / "huggingface" / "hub" / (
        "models--" + MODEL.replace("/", "--")
    )
    snapshots = sorted(
        (repo / "snapshots").glob("*"),
        key=lambda path: path.stat().st_mtime,
    ) if (repo / "snapshots").is_dir() else []
    if not snapshots:
        raise SystemExit(
            f"local ASR snapshot not found for {MODEL}; "
            "download it explicitly before running this local-only skill"
        )
    return snapshots[-1]


def main() -> None:
    local_model = resolve_local_model()
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    session = RuntimeSession(
        name="verify_asr",
        lock_path=WORK / "narration" / ".verify_asr.lock",
        state_path=WORK / "narration" / "asr_state.json",
        heartbeat_s=RUNTIME["heartbeat_s"],
    )
    session.start()
    global SESSION
    SESSION = session
    print(f"[runtime] NUMBA_CACHE_DIR={NUMBA_CACHE_DIR}", flush=True)
    print(f"[asr] loading local model {local_model}", flush=True)
    with SESSION.stage("load_asr_model", RUNTIME["asr_model_load_timeout_s"]):
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        processor = WhisperProcessor.from_pretrained(
            str(local_model), local_files_only=True
        )
        model = WhisperForConditionalGeneration.from_pretrained(
            str(local_model), local_files_only=True
        ).to(device).eval()

    beats = json.loads((WORK / "micro_script.json").read_text(encoding="utf-8"))
    forced = processor.get_decoder_prompt_ids(language="zh", task="transcribe")

    results = []
    for beat in beats:
        SESSION.update("transcribe", beat=beat["id"])
        path = MASTERED / f"{beat['id']}.wav"
        audio, sr = sf.read(path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        # whisper wants 16 kHz
        if sr != 16000:
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sr))
            audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
        inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
        with SESSION.stage(
            "transcribe",
            RUNTIME["asr_beat_timeout_s"],
            beat=beat["id"],
        ):
            with torch.no_grad():
                ids = model.generate(
                    inputs.input_features.to(device),
                    forced_decoder_ids=forced,
                    max_new_tokens=110,
                )
        heard = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        score = similarity(normalise(beat["tts_text"]), normalise(heard))
        results.append({
            "id": beat["id"], "chapter_key": beat["chapter_key"],
            "expected": beat["tts_text"], "heard": heard, "similarity": round(score, 3),
        })
        flag = "ok " if score >= 0.80 else "LOW"
        print(f"[asr] {beat['id']} {flag} {score:.2f} | {heard[:44]}", flush=True)

    low = [r for r in results if r["similarity"] < 0.80]
    payload = {
        "model": MODEL,
        "metric": "character-level Levenshtein ratio on normalised text",
        "threshold": 0.80,
        "count": len(results),
        "mean_similarity": round(sum(r["similarity"] for r in results) / len(results), 3),
        "below_threshold": [r["id"] for r in low],
        "results": results,
    }
    (WORK / "narration" / "qa-asr.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[asr] mean={payload['mean_similarity']:.3f} "
          f"below_threshold={len(low)}/{len(results)}")
    for r in low:
        print(f"   {r['id']} {r['similarity']:.2f}\n      want: {r['expected']}\n      got : {r['heard']}")
    SESSION.complete(
        f"mean_similarity={payload['mean_similarity']:.3f}; "
        f"below_threshold={len(low)}/{len(results)}"
    )


if __name__ == "__main__":
    try:
        result = main()
    except BaseException as exc:
        if SESSION is not None:
            SESSION.fail(f"{type(exc).__name__}: {exc}")
        raise
    else:
        sys.exit(result)
