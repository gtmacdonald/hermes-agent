"""Tests for the bell sound module."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.bell import play_bell


class TestBell:
    """Test bell playback functionality."""

    def test_bell_file_exists(self):
        """Verify the school bell WAV file exists."""
        bell_path = Path(__file__).resolve().parent.parent.parent / "art" / "sound" / "school_bell.wav"
        assert bell_path.exists(), f"Bell file not found at {bell_path}"
        assert bell_path.stat().st_size > 100000, "Bell file is too small (should be 8s of audio)"

    def test_play_bell_success_macos(self):
        """Test successful bell playback on macOS."""
        with patch("sys.platform", "darwin"):
            with patch("subprocess.run") as mock_run:
                play_bell()
                # Verify afplay was called with the bell file
                assert mock_run.called
                call_args = mock_run.call_args
                assert "afplay" in str(call_args)
                assert "school_bell.wav" in str(call_args)

    def test_play_bell_success_linux(self):
        """Test successful bell playback on Linux (paplay)."""
        with patch("sys.platform", "linux"):
            with patch("subprocess.run") as mock_run:
                play_bell()
                # Verify paplay or aplay was called
                assert mock_run.called
                call_args = str(mock_run.call_args)
                assert "paplay" in call_args or "aplay" in call_args

    def test_play_bell_fallback_to_beep(self):
        """Test that missing bell file falls back to system beep."""
        with patch("pathlib.Path.exists", return_value=False):
            with patch("sys.stdout.write") as mock_write:
                play_bell()
                # Should fall back to beep
                mock_write.assert_called_with("\a")

    def test_play_bell_subprocess_error_handled(self):
        """Test that subprocess errors are handled gracefully."""
        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=FileNotFoundError("afplay not found")):
                with patch("sys.stdout.write") as mock_write:
                    play_bell()
                    # Should fall back to beep
                    mock_write.assert_called_with("\a")

    def test_play_bell_timeout_handled(self):
        """Test that subprocess timeout is handled gracefully."""
        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("afplay", 5)):
                with patch("sys.stdout.write") as mock_write:
                    play_bell()
                    # Should fall back to beep
                    mock_write.assert_called_with("\a")

    def test_play_bell_no_exceptions_raised(self):
        """Test that play_bell never raises exceptions (fails silently)."""
        # Force all possible error paths
        with patch("sys.platform", "darwin"):
            with patch("subprocess.run", side_effect=Exception("Unexpected error")):
                with patch("sys.stdout.write") as mock_write:
                    # Should not raise, but will try to fall back to beep
                    play_bell()
                    mock_write.assert_called_with("\a")
