#!/usr/bin/env python3
"""Overlay 竖屏平台安全区蒙版到快照上，并统计核心区外的"亮像素"占比。

安全区（reference/visual-language.md）：顶部 140px、底部 260px、右侧 140px 为平台界面覆盖区，
核心信息（标题/关键数字/公式/字幕）不得进入；装饰可以。
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vconfig import CFG  # noqa: E402

_SA = CFG.safe_area
TOP, BOTTOM, RIGHT, SIDE = _SA["top"], _SA["bottom"], _SA["right"], _SA["side"]
THRESH = 120  # 与背景的亮度差阈值：文字/图形属于"内容"，背景辉光与颗粒不算


def content_mask(lum: np.ndarray) -> np.ndarray:
    """内容 = 与背景基调差得够远的像素，深底浅底都成立。

    早先这里写死 `lum > 120`，等于假定深色底 + 浅色字。换成暖纸浅底的片子后，
    背景本身就有 239 的亮度，整幅画面都被判成"内容"，安全区统计恒等于 100%，
    检查失效。改用「与画面亮度中位数的偏离」，两种极性都能用。
    """
    return np.abs(lum - float(np.median(lum))) > THRESH


def main(paths: list[Path], out: Path) -> None:
    tiles = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        a = np.asarray(im).astype(np.int16)
        lum = (0.2126 * a[:, :, 0] + 0.7152 * a[:, :, 1] + 0.0722 * a[:, :, 2])
        content = content_mask(lum)
        top_hits = int(content[:TOP, :].sum())
        bottom_hits = int(content[-BOTTOM:, :].sum())
        right_hits = int(content[TOP:-BOTTOM, -RIGHT:].sum())
        total = int(content.sum()) or 1
        draw = ImageDraw.Draw(im, "RGBA")
        draw.rectangle([0, 0, im.width, TOP], fill=(224, 73, 42, 60))
        draw.rectangle([0, im.height - BOTTOM, im.width, im.height], fill=(224, 73, 42, 60))
        draw.rectangle([im.width - RIGHT, TOP, im.width, im.height - BOTTOM], fill=(224, 73, 42, 45))
        draw.rectangle([SIDE, TOP, im.width - SIDE, im.height - BOTTOM], outline=(120, 220, 140, 255), width=3)
        label = (f"{p.name}  top={top_hits}  bottom={bottom_hits}  right={right_hits}"
                 f"  ({100*(top_hits+bottom_hits+right_hits)/total:.2f}% of content)")
        draw.rectangle([0, TOP, im.width, TOP + 34], fill=(0, 0, 0, 190))
        draw.text((10, TOP + 8), label, fill=(255, 255, 255))
        print(label)
        tiles.append(im.resize((im.width // 3, im.height // 3)))
    cols = min(4, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    w, h = tiles[0].size
    sheet = Image.new("RGB", (cols * w, rows * h), (12, 11, 10))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * w, (i // cols) * h))
    sheet.save(out)
    print("wrote", out)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("frames", nargs="+", type=Path, help="快照 PNG（若干帧）")
    parser.add_argument("-o", "--out", type=Path, default=None,
                        help="输出蒙版拼图（默认 <work_dir>/safe_area.png）")
    args = parser.parse_args()
    main(args.frames, args.out or (CFG.work / "safe_area.png"))
