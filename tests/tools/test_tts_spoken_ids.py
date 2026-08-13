"""Spoken-id filter: task ids (t_ + 8 hex) are replaced with names.

Deterministic rules under test — see tools/tts_text_normalize for the
canonical spec.
"""

import json
import re
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Filter primitives in tts_text_normalize
# ---------------------------------------------------------------------------


class TestExpandTaskIds:
    def test_resolves_to_task_dash_title(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="Do the thing"
        ):
            assert (
                expand_task_ids_for_tts("see t_8b13866d now") == 'see task "Do the thing" now'
            )

    def test_unresolvable_strict_raises(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch("tools.tts_text_normalize._lookup_task_title", return_value=None):
            try:
                expand_task_ids_for_tts("see t_deadbeef now", strict=True)
            except ValueError as exc:
                assert "Unresolvable" in str(exc)
                assert "t_deadbeef" in str(exc)
            else:
                assert False, "expected ValueError"

    def test_unresolvable_non_strict_leaves_verbatim(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch("tools.tts_text_normalize._lookup_task_title", return_value=None):
            assert (
                expand_task_ids_for_tts("hello t_deadbeef world", strict=False)
                == "hello t_deadbeef world"
            )

    def test_case_insensitive(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="Case Task"
        ) as lookup:
            out = expand_task_ids_for_tts("See T_8B13866D now")
            assert 'task "Case Task"' in out
            # lookup is case-normalized
            assert lookup.call_args[0][0] == "t_8b13866d"

    def test_multiple_ids_and_dedup(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="My Cool Task"
        ) as lookup:
            out = expand_task_ids_for_tts("t_8b13866d and t_8b13866d again")
            assert out.count('task "My Cool Task"') == 2
            assert lookup.call_count == 1  # deduped lookup

    def test_word_boundary_only(self):
        from tools.tts_text_normalize import expand_task_ids_for_tts

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="X"
        ) as lookup:
            # ``xt_8b13866d`` and ``t_8b13866dX`` are not word-bounded
            assert expand_task_ids_for_tts("xt_8b13866d", strict=False) == "xt_8b13866d"
            assert (
                expand_task_ids_for_tts("t_8b13866dX", strict=False) == "t_8b13866dX"
            )
            assert lookup.call_count == 0


class TestPrepareSpokenTaskIds:
    def test_default_resolves(self):
        from tools.tts_text_normalize import prepare_spoken_text

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="My Cool Task"
        ):
            s = prepare_spoken_text("see t_8b13866d please")
            assert 'task "My Cool Task"' in s
            assert "t_8b13866d" not in s.lower()

    def test_resolve_false_leaves_verbatim(self):
        from tools.tts_text_normalize import prepare_spoken_text

        s = prepare_spoken_text("see t_8b13866d now", resolve_task_ids=False)
        assert "t_8b13866d" in s
        assert 'task "' not in s

    def test_unresolvable_raises(self):
        from tools.tts_text_normalize import prepare_spoken_text

        try:
            prepare_spoken_text("see t_deadbeef now")
        except ValueError as exc:
            assert "Unresolvable" in str(exc)
        else:
            assert False, "expected ValueError for unresolvable id"

    def test_code_block_ids_not_spoken(self):
        from tools.tts_text_normalize import prepare_spoken_text

        # Id inside a fenced code block is stripped with the block — no error
        s = prepare_spoken_text("before\n```\nt_8b13866d\n```\nafter")
        assert 'task "' not in s
        assert "t_8b13866d" not in s.lower()
        assert "before" in s and "after" in s

    def test_underscore_italic_guard(self):
        from tools.tts_text_normalize import prepare_spoken_text

        # Two ids look like ``_a .. t_`` to the underscore-italic regex —
        # must not be eaten.
        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="My Cool Task"
        ):
            s = prepare_spoken_text("t_8b13866d and t_aabbccdd done")
            assert s.count('task "My Cool Task"') == 2

    def test_number_context_slash_not_expanded_as_rate(self):
        from tools.tts_text_normalize import prepare_spoken_text

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="My Cool Task"
        ):
            s = prepare_spoken_text("fix 5/t_8b13866d please")
            assert "5 per" not in s
            assert 'task "My Cool Task"' in s
            # still allows real rates
            s2 = prepare_spoken_text("cost is $5/month ok", resolve_task_ids=False)
            assert "5 dollars per month" in s2

    def test_title_punctuation_normalizes_via_symbol_pass(self):
        from tools.tts_text_normalize import prepare_spoken_text

        with patch(
            "tools.tts_text_normalize._lookup_task_title", return_value="Fix 14\u00b0C sensor"
        ):
            s = prepare_spoken_text("see t_8b13866d please")
            assert "14 degrees Celsius" in s

    def test_no_ids_unchanged(self):
        from tools.tts_text_normalize import prepare_spoken_text

        s = prepare_spoken_text("hello world", resolve_task_ids=True)
        assert "hello world" in s
        # no raw id leaked, no task prefix injected
        assert 'task "' not in s

    def test_date_and_rate_guards_still_hold(self):
        from tools.tts_text_normalize import prepare_spoken_text

        s = prepare_spoken_text("due 2026/06/02 ok", resolve_task_ids=False)
        assert "2026/06/02" in s
        s2 = prepare_spoken_text("choose and/or option", resolve_task_ids=False)
        assert "and/or" in s2
        s3 = prepare_spoken_text("status N/A here", resolve_task_ids=False)
        assert "N/A" in s3


# ---------------------------------------------------------------------------
# Tool-level: text_to_speech + new param
# ---------------------------------------------------------------------------


class TestTextToSpeechToolSpokenIds:
    def test_unresolvable_fails_tool(self):
        from tools.tts_tool import text_to_speech_tool

        result = json.loads(text_to_speech_tool(text="see t_deadbeef now"))
        assert result["success"] is False
        assert "Unresolvable" in result["error"]

    def test_resolve_false_allows_unresolvable(self):
        from tools.tts_tool import text_to_speech_tool

        # With resolve_task_ids=false the text is normalized but not validated;
        # the tool will proceed to synthesis.  We fake the synthesis layer so
        # the path is exercised without needing a real TTS backend.
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            fake = Path(tmpdir) / "fake.mp3"
            fake.write_bytes(b"\x00" * 100)
            with patch("tools.tts_tool._text_to_speech_single") as m:
                m.return_value = json.dumps(
                    {"success": True, "file_path": str(fake), "voice_compatible": False}
                )
                with patch(
                    "tools.tts_tool._repair_ogg_container", side_effect=lambda p: p
                ), patch("tools.tts_tool._build_audio_delivery_files") as build:
                    build.return_value = ([str(fake)], 1)
                    result = json.loads(
                        text_to_speech_tool(text="see t_deadbeef now", resolve_task_ids=False)
                    )
                    assert result["success"] is True

    def test_resolve_param_accepts_string_false(self):
        from tools.tts_tool import text_to_speech_tool

        import tempfile
        from pathlib import Path

        # Model tool calls may pass the boolean as a string — must coerce.
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = Path(tmpdir) / "fake2.mp3"
            fake.write_bytes(b"\x00" * 100)
            with patch("tools.tts_tool._text_to_speech_single") as m:
                m.return_value = json.dumps(
                    {"success": True, "file_path": str(fake), "voice_compatible": False}
                )
                with patch(
                    "tools.tts_tool._repair_ogg_container", side_effect=lambda p: p
                ), patch("tools.tts_tool._build_audio_delivery_files") as build:
                    build.return_value = ([str(fake)], 1)
                    result2 = json.loads(
                        text_to_speech_tool(text="see t_deadbeef now", resolve_task_ids="false")
                    )
                    assert result2["success"] is True

    def test_schema_has_new_param(self):
        from tools.tts_tool import TTS_SCHEMA

        assert "resolve_task_ids" in TTS_SCHEMA["parameters"]["properties"]
        assert TTS_SCHEMA["parameters"]["properties"]["resolve_task_ids"]["type"] == "boolean"
