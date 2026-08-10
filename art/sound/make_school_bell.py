#!/usr/bin/env python3
"""Generate school_bell.wav — a crisp, cheerful school bell sound.

Greg, 2026-08-10: "Let's use a school bell sound for task completion in Hermes,
different from the Syndicate's gong."

A school bell is a bright, brief sound compared to the gong. This implementation
generates a sharp bell tone with multiple harmonics that create a clear,
recognizable "ding-ding-ding" sequence (three rapid rings for completion).

Stock python3 only (wave + math), so the asset can always be regenerated from source.
Each bell ring is ~0.5 seconds with quick attack and decay, creating that
characteristic bell shimmer.

    python3 make_school_bell.py            # writes school_bell.wav next to itself

Mono, 44.1kHz, 16-bit. ~150KB on disk. Deterministic — no randomness, same
file every run.
"""
import math
import struct
import wave
from pathlib import Path

RATE = 44100
SECONDS = 2.0         # 2 seconds — brief, bright bell
F0 = 800.0            # A5 — bright, high pitched like a school bell

# School bell harmonics — bell metal produces these overtones naturally.
# Brighter and more harmonic than a gong, with longer decay to create
# that characteristic bell resonance that rings out.
HARMONICS = [
    (1.00, 1.00, 1.625),  # fundamental — main resonant tone
    (2.10, 0.75, 1.375),  # second harmonic — adds brightness
    (3.20, 0.60, 1.2),    # third harmonic — presence and ring
    (4.50, 0.45, 1.0),    # fourth harmonic — shimmer and sustain
    (5.80, 0.30, 0.8),    # fifth harmonic — metallic edge
    (7.20, 0.15, 0.6),    # sixth harmonic — upper shimmer
]

ATTACK = 0.015        # sharp attack but not a click
FADE = 0.2            # quick fade out


def render_ring():
    """Render the bell tone."""
    n = int(RATE * SECONDS)
    out = [0.0] * n
    
    for ratio, amp, decay in HARMONICS:
        freq = F0 * ratio
        w = 2.0 * math.pi * freq / RATE
        k = -1.0 / (decay * RATE)
        for i in range(n):
            out[i] += amp * math.exp(k * i) * math.sin(w * i)
    
    # soft attack, long fade
    a_n = int(ATTACK * RATE)
    for i in range(a_n):
        out[i] *= i / a_n
    
    f_n = int(FADE * RATE)
    for i in range(f_n):
        out[n - 1 - i] *= i / f_n
    
    return out


def render():
    """Render a single school bell strike with long resonant tail."""
    out = render_ring()
    
    # normalize to safe peak — reduce volume to 0.25 (quarter amplitude)
    peak = max(abs(s) for s in out)
    scale = 0.25 * 32767 / peak
    return b"".join(struct.pack("<h", int(s * scale)) for s in out)


def write(dest):
    """Atomic: rendered to a temp file, then os.replace'd in."""
    dest = Path(dest)
    tmp = dest.with_suffix(".tmp")
    
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(render())
    import os
    os.replace(tmp, dest)
    return dest


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent / "school_bell.wav")
    a = ap.parse_args()
    
    dest = write(a.out)
    print(f"wrote {dest} ({dest.stat().st_size / 1024:.1f} KB)")
