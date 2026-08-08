#!/usr/bin/env python3
"""Run a bundled skill script with the interpreter configured by the project."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_support import configure_local_runtime  # noqa: E402
from vconfig import CFG  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", help="bundled script filename, e.g. generate_tts.py")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    target = (scripts_dir / ns.script).resolve()
    if target.parent != scripts_dir or not target.is_file() or target.suffix != ".py":
        raise SystemExit(f"invalid bundled script: {ns.script}")
    if target == Path(__file__).resolve():
        raise SystemExit("run_project.py cannot launch itself")

    python = Path(CFG.python).expanduser()
    if not python.exists():
        raise SystemExit(
            f"configured Python does not exist: {python}\n"
            "Update video.config.json -> python, then rerun init_project.py."
        )

    cache_dir = configure_local_runtime(CFG)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [str(python), "-u", str(target), *ns.args]
    print(
        f"[run] python={python}\n[run] script={target.name}\n"
        f"[run] NUMBA_CACHE_DIR={cache_dir}",
        flush=True,
    )
    return subprocess.run(cmd, cwd=CFG.root, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
