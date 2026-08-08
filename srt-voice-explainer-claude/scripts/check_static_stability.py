#!/usr/bin/env python3
"""静态画面稳定性门禁（SKILL.md 铁律 9）——在成片上做，不在中间文件上做。

浏览器侧 `getBoundingClientRect()` 采样只能证明 DOM 没动，证明不了成片没动：
子像素取整、字体回退、编码器都发生在那之后。所以这道门禁直接看输出帧。

做法：以 5 fps 抽帧，逐对比较相邻帧，把「变化像素很少、但变化区域铺满整幅画面」
的连续片段判为**全局漂移**——那正是静止文字或静止背景在「摇/呼吸/漂」时的指纹：

- 有意的前景动画：变化像素可能不多，但**集中在一小块区域**（柱体、方块、下划线）；
- 1 px 级的全局漂移：变化像素同样不多（只有边缘），但**变化框铺满整幅画面**。

两者用「变化框的覆盖面积」区分，而不是用变化量的大小。

MIN_RUN 是必要的：入场瞬间多个元素同时出现也会撑大变化框，但只持续几帧；
真正的漂移会贯穿整个静止窗口。把它设成 3 会在片头产生假阳性（实测过）。

用法：
    python check_static_stability.py <video.mp4> [--json out.json] [--fps 5]

自检（证明门禁能失效，铁律 7）：
    python check_static_stability.py <video.mp4> --selftest
会用 ffmpeg 合成一个「整帧 1 px 往复位移」的坏样例并跑同一套判据，必须判 FAIL；
判不出来说明阈值被调松成了恒真检查，此时脚本整体返回非零。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

PIXEL_DELTA = 2          # 灰度差超过它才算“变了”
MICRO_FRACTION = 0.02    # 变化像素占比 < 2% 才可能是漂移（再多就是正常动画）
SPREAD_W = 0.55          # 变化框宽度覆盖 ≥55% 画幅
SPREAD_H = 0.55          # 变化框高度覆盖 ≥55% 画幅
MIN_RUN = 6              # 连续 6 个采样对（≥1.2 s）才判 FAIL


def extract(video: Path, out_dir: Path, fps: float) -> list[Path]:
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf", f"fps={fps}",
         "-pix_fmt", "gray", str(out_dir / "f%05d.png")],
        check=True,
    )
    return sorted(out_dir.glob("f*.png"))


def analyse(frames: list[Path], fps: float) -> dict:
    total_px = None
    pairs = []
    previous = None
    for index, path in enumerate(frames):
        current = np.asarray(Image.open(path).convert("L"), dtype=np.int16)
        if total_px is None:
            total_px = current.size
            height, width = current.shape
        if previous is not None:
            changed = np.abs(current - previous) > PIXEL_DELTA
            count = int(changed.sum())
            if count:
                rows = np.flatnonzero(changed.any(axis=1))
                cols = np.flatnonzero(changed.any(axis=0))
                box = (int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1]))
                spread_w = (box[2] - box[0] + 1) / width
                spread_h = (box[3] - box[1] + 1) / height
            else:
                box, spread_w, spread_h = (0, 0, 0, 0), 0.0, 0.0
            pairs.append({
                "t": round(index / fps, 2),
                "changed": count,
                "fraction": round(count / total_px, 5),
                "spread_w": round(spread_w, 3),
                "spread_h": round(spread_h, 3),
                "bbox": box,
            })
        previous = current

    def is_drift(p: dict) -> bool:
        return (0 < p["fraction"] < MICRO_FRACTION
                and p["spread_w"] >= SPREAD_W and p["spread_h"] >= SPREAD_H)

    runs, run = [], []
    for p in pairs:
        if is_drift(p):
            run.append(p)
        else:
            if len(run) >= MIN_RUN:
                runs.append(run)
            run = []
    if len(run) >= MIN_RUN:
        runs.append(run)

    findings = [{
        "start_s": r[0]["t"],
        "end_s": r[-1]["t"],
        "samples": len(r),
        "max_changed_px": max(x["changed"] for x in r),
        "max_spread": [max(x["spread_w"] for x in r), max(x["spread_h"] for x in r)],
    } for r in runs]

    perfectly_static = sum(1 for p in pairs if p["changed"] == 0)
    return {
        "fps": fps,
        "pairs": len(pairs),
        "perfectly_static_pairs": perfectly_static,
        "thresholds": {
            "pixel_delta": PIXEL_DELTA, "micro_fraction": MICRO_FRACTION,
            "spread_w": SPREAD_W, "spread_h": SPREAD_H, "min_run": MIN_RUN,
        },
        "drift_findings": findings,
        "status": "PASS" if not findings else "FAIL",
        "worst_micro_pairs": sorted(
            [p for p in pairs if 0 < p["fraction"] < MICRO_FRACTION],
            key=lambda p: -(p["spread_w"] * p["spread_h"]),
        )[:8],
    }


def run_on(video: Path, fps: float) -> dict:
    with tempfile.TemporaryDirectory() as td:
        frames = extract(video, Path(td), fps)
        if len(frames) < 3:
            raise SystemExit(f"too few frames extracted from {video}")
        return analyse(frames, fps)


def make_bad_sample(video: Path, dst: Path) -> None:
    """整帧按 1 px 往复位移——修复前的典型「摇晃」症状。"""
    expr = "crop=w=in_w-2:h=in_h-2:x='1+mod(n\\,2)':y='1+mod(n\\,2)'"
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", str(video), "-vf", expr,
         "-an", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(dst)],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--selftest", action="store_true",
                        help="额外合成一个 1px 往复位移的坏样例，验证门禁能判 FAIL")
    args = parser.parse_args()

    report = run_on(args.video, args.fps)
    report["video"] = str(args.video)

    if args.selftest:
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.mp4"
            make_bad_sample(args.video, bad)
            bad_report = run_on(bad, args.fps)
        report["selftest"] = {
            "description": "同一支片子加 1 px 逐帧往复位移后重跑同一判据",
            "status": bad_report["status"],
            "drift_findings": len(bad_report["drift_findings"]),
            "gate_can_fail": bad_report["status"] == "FAIL",
        }
        print(f"[selftest] 坏样例判定 = {bad_report['status']} "
              f"({len(bad_report['drift_findings'])} 段漂移) "
              f"-> 门禁{'可以' if bad_report['status'] == 'FAIL' else '**不能**'}失效")

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[stability] 采样对 {report['pairs']} / 完全静止 {report['perfectly_static_pairs']}")
    for f in report["drift_findings"]:
        print(f"[FAIL] 全局漂移 {f['start_s']}s → {f['end_s']}s "
              f"({f['samples']} 个采样, 变化 {f['max_changed_px']} px, 覆盖 {f['max_spread']})")
    print(f"[stability] {report['status']}")

    ok = report["status"] == "PASS"
    if args.selftest:
        ok = ok and report["selftest"]["gate_can_fail"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
