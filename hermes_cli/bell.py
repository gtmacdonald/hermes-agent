"""Play the Hermes bell sound for task completion.

Greg, 2026-08-10: "When a long task completes, play a school bell sound
instead of the terminal beep."

This module handles playing the school_bell.wav asset when display.bell_on_complete
is enabled in config. The bell is audible and distinct from a simple \a beep.
"""
import subprocess
import sys
from pathlib import Path


def play_bell():
    """Play the school bell sound for task completion.
    
    Uses afplay (macOS) / paplay (Linux) / others as available.
    Fails silently (the bell is a courtesy, not essential to functionality).
    """
    try:
        bell_path = Path(__file__).resolve().parent.parent / "art" / "sound" / "school_bell.wav"
        if not bell_path.exists():
            # Fallback to system beep if asset not found
            sys.stdout.write("\a")
            sys.stdout.flush()
            return
        
        # Try platform-specific audio players
        if sys.platform == "darwin":
            subprocess.run(["afplay", str(bell_path)], 
                         stderr=subprocess.DEVNULL, 
                         stdout=subprocess.DEVNULL,
                         timeout=5)
        elif sys.platform == "linux":
            # Try paplay first (PulseAudio), fall back to aplay
            try:
                subprocess.run(["paplay", str(bell_path)],
                             stderr=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             timeout=5)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                subprocess.run(["aplay", str(bell_path)],
                             stderr=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             timeout=5)
        else:
            # Windows or other: try built-in sound player if available
            try:
                subprocess.run(["powershell", "-Command",
                              f"(New-Object Media.SoundPlayer '{bell_path}').PlaySync()"],
                             stderr=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             timeout=5)
            except FileNotFoundError:
                # Fall back to system beep
                sys.stdout.write("\a")
                sys.stdout.flush()
    except Exception:
        # Any error: fail silently with a beep instead
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except OSError:
            pass
