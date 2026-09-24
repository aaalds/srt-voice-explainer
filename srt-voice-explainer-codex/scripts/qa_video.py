#!/usr/bin/env python3
"""Automated QA gates for the rendered film.

Runs: 视频技术指标 / 音频 / 同步 / 黑帧·冻结帧 / contact sheet
Writes: deliverables/qa_metrics.json (+ contact_sheet.png)

Usage: ./.qwen-tts-venv/Scripts/python.exe tools/qa_video.py <video.mp4> [--label draft]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loudness import gated_lufs, short_term, true_peak_dbfs  # noqa: E402
from vconfig import CFG  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT, WORK, DELIVER, PROJECT = CFG.root, CFG.work, CFG.deliver, CFG.project
LOUD = CFG.loudness
RUNTIME = CFG.runtime
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
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


def probe(path: Path) -> dict:
    out = run([FFPROBE, "-v", "error", "-show_streams", "-show_format",
               "-of", "json", str(path)]).stdout
    return json.loads(out)


def check(name: str, ok: bool, measured, expected) -> dict:
    return {"check": name, "status": "PASS" if ok else "FAIL",
            "measured": measured, "expected": expected}


def markdown_audit_pass(path: Path) -> tuple[bool, str]:
    """Require an explicit machine-readable PASS line in a manual audit.

    The report remains human-readable Markdown, but must contain `status: PASS`
    on its own line. Merely creating an empty file cannot satisfy the gate.
    """
    if not path.exists():
        return False, f"missing {path.name}"
    text = path.read_text(encoding="utf-8")
    passed = bool(re.search(r"(?im)^\s*(?:status|overall)\s*:\s*PASS\s*$", text))
    return passed, f"{path.name}: {'PASS' if passed else 'missing explicit status: PASS'}"


def pronunciation_ledger_pass(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"missing {path.name}"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid JSON: {exc}"
    if not isinstance(entries, list) or not entries:
        return False, "ledger must be a non-empty JSON list"
    required = {"surface", "normalized", "canonical_tts", "source", "occurrences", "verified"}
    bad = [i for i, item in enumerate(entries)
           if not isinstance(item, dict) or not required.issubset(item) or item.get("verified") is not True]
    return not bad, f"{len(entries)} entries; invalid/unverified={bad[:8]}"


def pronunciation_qa_pass(path: Path) -> tuple[bool, str]:
    """Validate the blocking, evidence-bearing pronunciation review report."""
    if not path.exists():
        return False, f"missing {path.name}"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid JSON: {exc}"
    if not isinstance(report, dict) or str(report.get("status", "")).upper() != "PASS":
        return False, "report status must be PASS"
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        return False, "checks must be a non-empty list"
    problems = []
    occurrence_count = 0
    for index, item in enumerate(checks):
        if not isinstance(item, dict) or not str(item.get("term", "")).strip():
            problems.append(f"check[{index}] missing term")
            continue
        occurrences = item.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            problems.append(f"{item.get('term')}: no occurrences")
            continue
        occurrence_count += len(occurrences)
        for occurrence in occurrences:
            if (not isinstance(occurrence, dict)
                    or not str(occurrence.get("beat", "")).strip()
                    or str(occurrence.get("result", "")).upper() != "PASS"
                    or not str(occurrence.get("evidence", "")).strip()):
                problems.append(f"{item.get('term')}: invalid occurrence evidence")
                break
        if item.get("components"):
            if item.get("component_pronunciations_match") is not True:
                problems.append(f"{item.get('term')}: component pronunciations do not match")
        if item.get("continuity_required") is True:
            try:
                gap = float(item["max_gap_ms"])
                allowed = float(item.get("allowed_gap_ms", 80))
                if gap < 0 or gap > allowed:
                    problems.append(f"{item.get('term')}: gap {gap}ms exceeds {allowed}ms")
            except (KeyError, TypeError, ValueError):
                problems.append(f"{item.get('term')}: invalid max_gap_ms/allowed_gap_ms")
    return not problems, (
        f"{len(checks)} checks / {occurrence_count} occurrences; problems={problems[:6]}"
    )


def intentional_freezes_pass(freezes: list[dict], path: Path) -> tuple[bool, str]:
    if not freezes:
        return True, "0 segments"
    if not path.exists():
        return False, f"{len(freezes)} segments; missing {path.name}"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid {path.name}: {exc}"
    holds = report.get("intentional_holds", []) if isinstance(report, dict) else []

    def covers(hold: object, freeze: dict) -> bool:
        if not isinstance(hold, dict) or hold.get("reviewed") is not True:
            return False
        if not str(hold.get("reason", "")).strip():
            return False
        try:
            return (float(hold["start"]) <= freeze["start"] + 0.25
                    and float(hold["end"]) >= freeze["end"] - 0.25)
        except (KeyError, TypeError, ValueError):
            return False

    uncovered = []
    for freeze in freezes:
        covered = any(covers(hold, freeze) for hold in holds)
        if not covered:
            uncovered.append(freeze)
    status_ok = str(report.get("status", "")).upper() == "PASS"
    return status_ok and not uncovered, (
        f"{len(freezes)} detected; {len(uncovered)} uncovered; report_status={report.get('status')}"
    )


def loudness_consistency(video: Path, align: dict) -> dict:
    """Per-unit / per-chapter / short-term loudness spread of the finished film.

    Measured on the delivered audio track, so an encoder or mux step that
    changes levels cannot slip through. Implemented in tools/loudness.py rather
    than via ffmpeg's loudnorm, which is invalid below 3 s of input — and 15 of
    the 86 narration units are shorter than that.
    """
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "track.wav"
        run([FFMPEG, "-y", "-v", "error", "-i", str(video), "-map", "0:a:0",
             "-c:a", "pcm_f32le", "-ac", "1", "-ar", "48000", str(wav)])
        audio, sr = sf.read(wav, dtype="float64")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    programme = gated_lufs(audio, sr)
    units = []
    for beat in align["beats"]:
        seg = audio[int(beat["start"] * sr):int(min(beat["end"], len(audio) / sr) * sr)]
        if seg.size < int(0.2 * sr):
            continue
        units.append({"id": beat["id"], "chapter_key": beat["chapter_key"],
                      "lufs": round(gated_lufs(seg, sr), 2)})
    vals = np.array([u["lufs"] for u in units])
    median = float(np.median(vals))

    curve, _ = short_term(audio, sr)
    active = curve[curve > programme - 20.0]        # ignore the pauses between units

    chapter_dev = []
    for chapter in align["chapters"]:
        sub = [u["lufs"] for u in units if u["chapter_key"] == chapter["chapter_key"]]
        if sub:
            chapter_dev.append((chapter["chapter_key"], round(float(np.mean(sub)) - programme, 2)))
    worst_chapter = max(chapter_dev, key=lambda c: abs(c[1])) if chapter_dev else ("n/a", 0.0)

    def spread(a: np.ndarray) -> dict:
        return {"n": int(a.size), "mean": round(float(a.mean()), 2),
                "stdev": round(float(a.std(ddof=1)), 2) if a.size > 1 else 0.0,
                "min": round(float(a.min()), 2), "max": round(float(a.max()), 2),
                "range_p5_p95": round(float(np.percentile(a, 95) - np.percentile(a, 5)), 2),
                "range_min_max": round(float(a.max() - a.min()), 2)}

    return {
        "programme_lufs": round(programme, 2),
        "unit_median_lufs": round(median, 2),
        "unit_spread": spread(vals),
        "short_term_spread": spread(active),
        "units_beyond_1LU": int((np.abs(vals - median) > 1.0).sum()),
        "units_beyond_2LU": int((np.abs(vals - median) > 2.0).sum()),
        "chapter_deviation": chapter_dev,
        "chapter_max_dev": abs(worst_chapter[1]),
        "chapter_worst": worst_chapter[0],
        "true_peak_dbtp": round(true_peak_dbfs(audio, sr), 2),
        "worst_units": sorted(units, key=lambda u: -abs(u["lufs"] - median))[:10],
        "units": units,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--label", default="final")
    args = parser.parse_args()
    video = args.video
    DELIVER.mkdir(parents=True, exist_ok=True)

    align = json.loads((WORK / "alignment.json").read_text(encoding="utf-8"))
    results: list[dict] = []

    # ---------------- A. video technical ----------------
    info = probe(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fmt = info["format"]
    vdur = float(v.get("duration") or fmt["duration"])
    adur = float(a.get("duration") or fmt["duration"]) if a else 0.0

    results += [
        check(f"分辨率 {CFG.width}×{CFG.height}", (v["width"], v["height"]) == (CFG.width, CFG.height), f"{v['width']}x{v['height']}", f"{CFG.width}x{CFG.height}"),
        check("像素宽高比 1:1", v.get("sample_aspect_ratio", "1:1") in ("1:1", "N/A"), v.get("sample_aspect_ratio", "N/A"), "1:1"),
        check("显示比例 9:16", v.get("display_aspect_ratio", "9:16") in ("9:16", "N/A"), v.get("display_aspect_ratio", "N/A"), "9:16"),
        check("帧率 30fps", v["r_frame_rate"] in ("30/1", "30000/1000"), v["r_frame_rate"], "30/1"),
        check("视频编码 H.264", v["codec_name"] == "h264", v["codec_name"], "h264"),
        check("音频编码 AAC", bool(a) and a["codec_name"] == "aac", a["codec_name"] if a else "none", "aac"),
        check("音频采样率 48kHz", bool(a) and int(a["sample_rate"]) == 48000, a["sample_rate"] if a else "none", "48000"),
        check("文件非空", int(fmt["size"]) > 1_000_000, f"{int(fmt['size'])/1e6:.1f} MB", "> 1 MB"),
        check("时长与旁白一致(±0.5s)", abs(vdur - align["total_duration"]) <= 0.5,
              f"{vdur:.3f}s", f"{align['total_duration']:.3f}s"),
        check("音视频时长差 ≤ 50ms", abs(vdur - adur) <= 0.05, f"{abs(vdur - adur)*1000:.1f}ms", "≤ 50ms"),
    ]

    # ---------------- B. black / freeze frames ----------------
    bd = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(video),
              "-vf", "blackdetect=d=0.20:pic_th=0.98:pix_th=0.06", "-an", "-f", "null", "-"]).stderr
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", bd)
    fd = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(video),
              "-vf", "freezedetect=n=-58dB:d=4", "-an", "-f", "null", "-"]).stderr
    freeze_starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", fd)]
    freeze_ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", fd)]
    if len(freeze_ends) < len(freeze_starts):
        freeze_ends.extend([vdur] * (len(freeze_starts) - len(freeze_ends)))
    freezes = [
        {"start": start, "end": end, "duration": round(end - start, 3)}
        for start, end in zip(freeze_starts, freeze_ends)
    ]
    freeze_ok, freeze_measured = intentional_freezes_pass(
        freezes, WORK / "VISUAL_STABILITY_REPORT.json"
    )
    results += [
        check("无黑帧段(≥0.2s)", len(blacks) == 0, f"{len(blacks)} 段 {blacks[:3]}", "0"),
        check("≥4s 静止段均为已审计的有意停留", freeze_ok, freeze_measured,
              "0 段，或 VISUAL_STABILITY_REPORT.json 逐段覆盖并 PASS"),
    ]

    # ---------------- C. audio ----------------
    ln = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(video),
              "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"]).stderr
    integrated = re.search(r"I:\s+(-?[\d.]+) LUFS", ln[-3000:])
    truepeak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", ln[-3000:])
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", ln[-3000:])
    lufs = float(integrated.group(1)) if integrated else 0.0
    tp = float(truepeak.group(1)) if truepeak else 0.0

    astats = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(video),
                  "-af", "astats=metadata=1:reset=0", "-f", "null", "-"]).stderr
    dc = re.search(r"DC offset:\s+(-?[\d.]+)", astats)
    flat = re.search(r"Flat factor:\s+(-?[\d.]+)", astats)
    peak_count = re.search(r"Peak count:\s+(\d+)", astats)

    sil = run([FFMPEG, "-hide_banner", "-nostats", "-i", str(video),
               "-af", "silencedetect=n=-50dB:d=1.6", "-f", "null", "-"]).stderr
    silences = re.findall(r"silence_start: ([\d.]+)[\s\S]*?silence_duration: ([\d.]+)", sil)
    long_sil = [(float(s), float(d)) for s, d in silences if float(d) > 1.6 and float(s) < align["total_duration"] - 1.5]

    programme_lo = LOUD["programme_target_lufs"] - 1.0
    programme_hi = LOUD["programme_target_lufs"] + 1.0
    results += [
        check(f"综合响度 {programme_lo:.0f}~{programme_hi:.0f} LUFS",
              programme_lo <= lufs <= programme_hi, f"{lufs:.2f} LUFS",
              f"{programme_lo:.0f} ~ {programme_hi:.0f} LUFS"),
        check(f"真峰值 ≤ {LOUD['true_peak_max_dbtp']:.1f} dBTP",
              tp <= LOUD["true_peak_max_dbtp"], f"{tp:.2f} dBFS",
              f"≤ {LOUD['true_peak_max_dbtp']:.1f}"),
        check("无削波", tp < 0.0 and int(peak_count.group(1) if peak_count else 0) < 20,
              f"peak_count={peak_count.group(1) if peak_count else 'n/a'}", "无 0 dBFS 峰值堆积"),
        check("DC 偏移 < 0.01", abs(float(dc.group(1))) < 0.01 if dc else False,
              dc.group(1) if dc else "n/a", "< 0.01"),
        check("无异常长静音(>1.6s)", len(long_sil) == 0, f"{len(long_sil)} 段 {long_sil[:3]}", "0"),
    ]

    # ---------------- C2. 响度一致性 ----------------
    # 只看一个"整体响度"数字会漏掉「时大时小」：那是段与段之间的离散度问题。
    # 这里逐个旁白单元、逐章、以及 3 秒短时窗口分别测量，全部在成片的音轨上做。
    loud = loudness_consistency(video, align)
    unit = loud["unit_spread"]
    st = loud["short_term_spread"]
    results += [
        check("逐单元响度离散 σ ≤ 1.0 LU", unit["stdev"] <= 1.0, f"σ = {unit['stdev']:.2f} LU", "≤ 1.0 LU"),
        check("逐单元响度极差 ≤ 3.0 LU", unit["range_min_max"] <= 3.0,
              f"{unit['range_min_max']:.2f} LU（{unit['min']:.2f} … {unit['max']:.2f}）", "≤ 3.0 LU"),
        check("无单元偏离中位数 > 2 LU", loud["units_beyond_2LU"] == 0,
              f"{loud['units_beyond_2LU']} 个 {[u['id'] for u in loud['worst_units'][:3]]}", "0"),
        check("短时响度(3s) σ ≤ 1.5 LU", st["stdev"] <= 1.5, f"σ = {st['stdev']:.2f} LU", "≤ 1.5 LU"),
        check("短时响度 P5–P95 ≤ 4.0 LU", st["range_p5_p95"] <= 4.0,
              f"{st['range_p5_p95']:.2f} LU", "≤ 4.0 LU"),
        check("逐章平均响度偏差 ≤ 1.0 LU", loud["chapter_max_dev"] <= 1.0,
              f"最大 {loud['chapter_max_dev']:.2f} LU（{loud['chapter_worst']}）", "≤ 1.0 LU"),
        check("4× 过采样真峰值 ≤ −1 dBTP", loud["true_peak_dbtp"] <= -1.0,
              f"{loud['true_peak_dbtp']:.2f} dBTP", "≤ −1.0 dBTP"),
    ]

    # ---------------- D. sync: measured speech onsets vs alignment.json ----------------
    wav = WORK / "narration" / "narration.wav"
    audio, sr = sf.read(wav, dtype="float32")
    win = int(sr * 0.01)
    frames = audio[: len(audio) // win * win].reshape(-1, win)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    voiced = db > -45.0
    onsets = []
    prev = False
    for i, flag in enumerate(voiced):
        if flag and not prev:
            onsets.append(i * 0.01)
        prev = flag
    offsets = []
    for beat in align["beats"]:
        near = [o for o in onsets if abs(o - beat["start"]) < 0.6]
        if near:
            offsets.append((beat["id"], min(near, key=lambda o: abs(o - beat["start"])) - beat["start"]))
    matched = len(offsets)
    worst = max((abs(d) for _, d in offsets), default=0.0)
    over = [(bid, round(d, 3)) for bid, d in offsets if abs(d) > 0.2]

    results += [
        check("旁白起点可测到(≥90%)", matched >= 0.9 * len(align["beats"]),
              f"{matched}/{len(align['beats'])}", "≥ 90%"),
        check("发音起点与 alignment 偏差 ≤ 200ms", not over,
              f"最大 {worst*1000:.0f}ms，超限 {len(over)} 个 {over[:3]}", "≤ 200ms"),
    ]

    # 章节 clip 窗口 == alignment 章节窗口
    index = (PROJECT / "index.html").read_text(encoding="utf-8")
    mismatch = []
    for chapter in align["chapters"]:
        key = chapter["chapter_key"]
        m = re.search(r'id="' + key + r'"[\s\S]{0,400}?data-start="([\d.]+)"[\s\S]{0,200}?data-duration="([\d.]+)"', index)
        if not m:
            mismatch.append((key, "not found"))
            continue
        if abs(float(m.group(1)) - chapter["start"]) > 0.005 or abs(float(m.group(2)) - chapter["duration"]) > 0.005:
            mismatch.append((key, m.group(1), m.group(2)))
    results.append(check("章节 clip 窗口 == 旁白章节窗口", not mismatch, f"{len(mismatch)} 处不一致", "0"))

    # ---------------- D2. manual-but-blocking audit artifacts ----------------
    ledger_ok, ledger_measured = pronunciation_ledger_pass(WORK / "PRONUNCIATION_LEDGER.json")
    pronunciation_ok, pronunciation_measured = pronunciation_qa_pass(
        WORK / "PRONUNCIATION_QA.json"
    )
    results += [
        check("全局发音台账完整且全部 verified", ledger_ok, ledger_measured,
              "非空 JSON；字段完整；全部 verified=true"),
        check("逐词发音专项验收", pronunciation_ok, pronunciation_measured,
              "PRONUNCIATION_QA.json: status PASS；每个 occurrence 有证据"),
    ]
    if CFG.technical_explainer:
        tech_ok, tech_measured = markdown_audit_pass(WORK / "TECHNICAL_EXPLAINER_AUDIT.md")
        time_ok, time_measured = markdown_audit_pass(WORK / "TIMESTAMP_AUDIT.md")
        results += [
            check("技术内容一致性审计", tech_ok, tech_measured,
                  "TECHNICAL_EXPLAINER_AUDIT.md 含独立行 status: PASS"),
            check("指定时间点与内部遮挡审计", time_ok, time_measured,
                  "TIMESTAMP_AUDIT.md 含独立行 status: PASS"),
        ]

    # ---------------- E. contact sheet ----------------
    sheet = DELIVER / "contact_sheet.png"
    picks = []
    for chapter in align["chapters"]:
        s, e = chapter["start"], chapter["end"]
        picks += [(chapter["chapter_key"], "开场", s + 1.2),
                  (chapter["chapter_key"], "中点", (s + e) / 2),
                  (chapter["chapter_key"], "结尾", e - 1.0)]
    tiles = DELIVER / "_tiles"
    tiles.mkdir(exist_ok=True)
    for i, (key, tag, t) in enumerate(picks):
        label = f"{key} {tag}  {t:6.2f}s"
        run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}",
             "-i", str(video), "-frames:v", "1",
             "-vf", f"scale=300:-1,drawtext=text='{label}':x=8:y=8:fontsize=15:"
                    f"fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=4",
             str(tiles / f"t{i:03d}.png")])
    sheet_rows = max(1, math.ceil(len(picks) / 3))
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(tiles / "t%03d.png"), "-filter_complex",
         f"tile=3x{sheet_rows}", str(sheet)])
    results.append(check("contact sheet 已生成", sheet.exists(), str(sheet), "存在"))

    # 章节转场前后各抓一帧
    tsheet = DELIVER / "transitions_sheet.png"
    ttiles = DELIVER / "_ttiles"
    ttiles.mkdir(exist_ok=True)
    boundaries = [c["start"] for c in align["chapters"][1:]]
    n = 0
    for b in boundaries:
        for off, tag in ((-0.30, "前"), (0.30, "后")):
            label = f"{tag} {b + off:7.2f}s"
            run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{b + off:.3f}",
                 "-i", str(video), "-frames:v", "1",
                 "-vf", f"scale=300:-1,drawtext=text='{label}':x=8:y=8:fontsize=15:"
                        f"fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=4",
                 str(ttiles / f"t{n:03d}.png")])
            n += 1
    transition_cols = min(4, max(1, n))
    transition_rows = max(1, math.ceil(n / transition_cols))
    if n:
        run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(ttiles / "t%03d.png"), "-filter_complex",
             f"tile={transition_cols}x{transition_rows}", str(tsheet)])
    results.append(check("章节转场帧已抓取", n == 0 or tsheet.exists(),
                         f"{n} 帧 -> {tsheet.name if n else 'single chapter'}", f"{n} 帧"))

    payload = {
        "label": args.label,
        "video": str(video),
        "video_duration": vdur,
        "audio_duration": adur,
        "narration_duration": align["total_duration"],
        "loudness_lufs": lufs,
        "true_peak_dbfs": tp,
        "loudness_consistency": loud,
        "lra": float(lra.group(1)) if lra else None,
        "black_segments": blacks,
        "freeze_segments": freezes,
        "long_silences": long_sil,
        "sync": {"matched_beats": matched, "total_beats": len(align["beats"]),
                 "worst_offset_ms": round(worst * 1000, 1), "over_200ms": over},
        "checks": results,
        "pass_count": sum(1 for r in results if r["status"] == "PASS"),
        "fail_count": sum(1 for r in results if r["status"] == "FAIL"),
    }
    (DELIVER / "qa_metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        print(f"[{r['status']}] {r['check']}: {r['measured']}  (expected {r['expected']})")
    print(f"\nPASS {payload['pass_count']} / FAIL {payload['fail_count']}")


if __name__ == "__main__":
    main()
