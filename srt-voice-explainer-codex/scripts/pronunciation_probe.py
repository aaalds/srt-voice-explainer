#!/usr/bin/env python3
"""Create local word-timestamp evidence for high-risk pronunciation review.

This probe deliberately writes ``status: REVIEW_REQUIRED``. ASR can locate
words and expose pauses, but a human/agent must still compare phonemes and
stress against the ledger before creating ``PRONUNCIATION_QA.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_support import RuntimeSession, configure_local_runtime  # noqa: E402
from vconfig import CFG  # noqa: E402

WORK, MASTERED = CFG.work, CFG.mastered
RUNTIME = CFG.runtime
SESSION: RuntimeSession | None = None


def resolve_local_model() -> Path:
    if CFG.asr_model_dir is not None:
        if CFG.asr_model_dir.exists():
            return CFG.asr_model_dir
        raise SystemExit(f"video.config.json -> asr_model_dir not found: {CFG.asr_model_dir}")
    direct = Path(CFG.asr_model)
    if direct.exists():
        return direct
    repo = Path.home() / ".cache" / "huggingface" / "hub" / (
        "models--" + CFG.asr_model.replace("/", "--")
    )
    snapshots = sorted(
        (repo / "snapshots").glob("*"), key=lambda path: path.stat().st_mtime
    ) if (repo / "snapshots").is_dir() else []
    if not snapshots:
        raise SystemExit(
            f"local ASR snapshot not found for {CFG.asr_model}; download it explicitly first"
        )
    return snapshots[-1]


def selected_entries(entries: list[dict], terms: list[str]) -> list[dict]:
    if terms:
        wanted = {term.casefold() for term in terms}
        chosen = [
            entry for entry in entries
            if str(entry.get("surface", "")).casefold() in wanted
            or str(entry.get("normalized", "")).casefold() in wanted
        ]
        missing = sorted(wanted - {
            value.casefold() for entry in chosen
            for value in (str(entry.get("surface", "")), str(entry.get("normalized", "")))
        })
        if missing:
            raise SystemExit(f"terms not found in PRONUNCIATION_LEDGER.json: {missing}")
        return chosen
    chosen = [
        entry for entry in entries
        if entry.get("high_risk") is True
        or bool(entry.get("components"))
        or len(entry.get("occurrences", [])) > 1
    ]
    return chosen or entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terms", nargs="*", default=[])
    parser.add_argument("--beats", nargs="*", default=[])
    parser.add_argument("--output", type=Path, default=WORK / "PRONUNCIATION_PROBE.json")
    args = parser.parse_args()

    ledger_path = WORK / "PRONUNCIATION_LEDGER.json"
    entries = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise SystemExit("PRONUNCIATION_LEDGER.json must be a non-empty list")
    chosen = selected_entries(entries, args.terms)
    beat_ids = list(dict.fromkeys(
        args.beats or [beat for entry in chosen for beat in entry.get("occurrences", [])]
    ))
    if not beat_ids:
        raise SystemExit("no beat ids selected")
    beats = {
        beat["id"]: beat
        for beat in json.loads((WORK / "micro_script.json").read_text(encoding="utf-8"))
    }
    unknown = [beat_id for beat_id in beat_ids if beat_id not in beats]
    if unknown:
        raise SystemExit(f"unknown beat ids: {unknown}")

    configure_local_runtime(CFG)
    session = RuntimeSession(
        name="pronunciation_probe",
        lock_path=WORK / "narration" / ".pronunciation_probe.lock",
        state_path=WORK / "narration" / "pronunciation_probe_state.json",
        heartbeat_s=RUNTIME["heartbeat_s"],
    )
    session.start()
    global SESSION
    SESSION = session
    model_path = resolve_local_model()
    device = 0 if torch.cuda.is_available() else -1
    dtype = torch.float16 if device == 0 else torch.float32
    print(f"[pronunciation] loading local model {model_path}", flush=True)
    with SESSION.stage("load_asr_model", RUNTIME["asr_model_load_timeout_s"]):
        from transformers import pipeline

        asr = pipeline(
            "automatic-speech-recognition",
            model=str(model_path),
            device=device,
            dtype=dtype,
        )

    evidence = []
    for beat_id in beat_ids:
        SESSION.update("transcribe", beat=beat_id)
        audio_path = MASTERED / f"{beat_id}.wav"
        if not audio_path.exists():
            raise SystemExit(f"missing mastered unit: {audio_path}")
        with SESSION.stage("transcribe", RUNTIME["asr_beat_timeout_s"], beat=beat_id):
            result = asr(
                str(audio_path),
                return_timestamps="word",
                generate_kwargs={"language": "zh", "task": "transcribe"},
            )
        evidence.append({
            "beat": beat_id,
            "expected": beats[beat_id]["tts_text"],
            "heard": result.get("text", ""),
            "chunks": result.get("chunks", []),
            "audio": str(audio_path),
        })
        print(f"[pronunciation] {beat_id}: {result.get('text', '')}", flush=True)

    payload = {
        "status": "REVIEW_REQUIRED",
        "model": str(CFG.asr_model),
        "selected_terms": [entry.get("surface") for entry in chosen],
        "instruction": (
            "Compare every occurrence by listening; word timestamps prove boundaries, "
            "not phoneme identity. Record the final verdict in PRONUNCIATION_QA.json."
        ),
        "evidence": evidence,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    SESSION.complete(f"beats={len(evidence)}; review required")
    print(f"[pronunciation] wrote {args.output}; status=REVIEW_REQUIRED")
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except BaseException as exc:
        if SESSION is not None:
            SESSION.fail(f"{type(exc).__name__}: {exc}")
        raise
    else:
        raise SystemExit(result)
