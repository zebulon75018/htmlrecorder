#!/usr/bin/env python3
"""
genvideo.py — Démonstration complète de htmlrecorder 
================================================================

"""

import argparse
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from htmlrecorder import HtmlRecorder


# ─────────────────────────────────────────────────────────────────────────
# Helpers communs
# ─────────────────────────────────────────────────────────────────────────

def banner(scene_n: int, name: str, desc: str, style: str = "") -> None:
    line = "─" * 64
    style_str = f"  style : {style}" if style else ""
    print(f"\n{line}")
    print(f"  Scène {scene_n:>2} — {name}  ·  {desc}")
    if style_str:
        print(style_str)
    print(line)


def on_start() -> None:
    print("  🎬  Enregistrement démarré — attendez la fin ou fermez la fenêtre.")


def on_stop(path: str) -> None:
    p = Path(path).resolve()
    size = p.stat().st_size / 1_048_576 if p.exists() else 0
    print(f"\n  ✅  Vidéo enregistrée : {p}  ({size:.2f} MB)\n")


def make_rec(output: str, duration, fps: float = 30, w = 1280, h = 720) -> HtmlRecorder:
    """Crée un HtmlRecorder avec les callbacks et dimensions standard."""
    return HtmlRecorder(
        output=output,
        duration=duration,
        fps=fps,
        width=w,
        height=h,
        on_start=on_start,
        on_stop=on_stop,
    )


def style_of(args, default: str) -> str:
    return (getattr(args, "style", None) or default)


# ─────────────────────────────────────────────────────────────────────────
# ── Scènes ────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    template = sys.argv[1]
    w = sys.argv[2]
    h = sys.argv[3]
    with open(sys.argv[1],"r") as f:
         html = f.read()
    make_rec(sys.argv[4], duration=10,w=int(w), h=int(h) ).run_html(html)

if __name__ == "__main__":
    main()
