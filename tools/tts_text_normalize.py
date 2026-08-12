"""Utilities for preparing assistant text for speech synthesis.

The TTS provider should receive a spoken script, not raw chat Markdown.  This
module centralises the lightweight, deterministic cleanup used by explicit TTS
calls and gateway auto-TTS replies.

Non-ASCII characters are written as escapes on purpose so the file stays free of
invisible/look-alike glyphs.
"""

from __future__ import annotations

import html
import re

# Sentinel appended to former heading lines so smooth_whitespace_for_tts can
# fold a heading into the sentence that follows it ("Weather, it will be sunny")
# rather than leaving a bare "Weather." label that reads abruptly aloud.
_HEAD = "\x00"

_MD_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
_MD_UNDERSCORE_BOLD_RE = re.compile(r"__(.+?)__", flags=re.DOTALL)
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", flags=re.DOTALL)
_MD_UNDERSCORE_ITALIC_RE = re.compile(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", flags=re.DOTALL)
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~", flags=re.DOTALL)
_MD_HEADING_LINE_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", flags=re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", flags=re.MULTILINE)
_MD_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", flags=re.MULTILINE)
_MD_HR_RE = re.compile(r"^\s*[-*_]{3,}\s*$", flags=re.MULTILINE)
_MD_TABLE_PIPE_RE = re.compile(r"\s*\|\s*")
_URL_RE = re.compile(r"https?://\S+")

# Broad emoji / pictograph cleanup.  Voice providers vary a lot here; most read
# emojis as awkward labels, so keep the speech script calm and literal.
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "☀-➿"
    "]+",
    flags=re.UNICODE,
)
_VARIATION_SELECTOR_RE = re.compile("[︎️]")


def strip_markdown_for_tts(text: str) -> str:
    """Strip Markdown/Telegram formatting while preserving readable words."""
    if not text:
        return ""

    text = html.unescape(str(text))
    text = _MD_CODE_BLOCK_RE.sub(" ", text)
    text = _MD_IMAGE_RE.sub(lambda m: f" {m.group(1)} " if m.group(1) else " ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub("", text)
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_UNDERSCORE_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_UNDERSCORE_ITALIC_RE.sub(r"\1", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    # Mark headings (do not just delete the marker): the whitespace pass folds a
    # heading into the sentence after it so speech says "Weather, it will be
    # sunny" instead of a clipped "Weather." then a separate sentence.
    text = _MD_HEADING_LINE_RE.sub(lambda m: m.group(1).rstrip() + _HEAD, text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    text = _MD_LIST_ITEM_RE.sub("", text)
    text = _MD_HR_RE.sub("", text)

    # Pipe tables are terrible read aloud.  Turn any leftover pipes into pauses
    # instead of letting a provider speak "vertical bar".
    text = _MD_TABLE_PIPE_RE.sub("; ", text)
    return text


def _normalize_temperature_ranges(text: str) -> str:
    # 11-17 degrees C -> "11 to 17 degrees Celsius" (en/em dash or hyphen).
    text = re.sub(
        r"(?<!\w)([-+\u2212]?\d+(?:\.\d+)?)\s*[\u2013\u2014-]\s*([-+\u2212]?\d+(?:\.\d+)?)\s*°\s*C\b",
        lambda m: f"{m.group(1).replace(chr(0x2212), '-')} to {m.group(2).replace(chr(0x2212), '-')} degrees Celsius",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(?<!\w)([-+\u2212]?\d+(?:\.\d+)?)\s*[\u2013\u2014-]\s*([-+\u2212]?\d+(?:\.\d+)?)\s*°\s*F\b",
        lambda m: f"{m.group(1).replace(chr(0x2212), '-')} to {m.group(2).replace(chr(0x2212), '-')} degrees Fahrenheit",
        text,
        flags=re.IGNORECASE,
    )
    return text


def normalize_symbols_for_tts(text: str) -> str:
    """Expand common symbols/shorthand into words a TTS engine reads well."""
    if not text:
        return ""

    text = str(text)
    text = re.sub("[   ]", " ", text)  # non-breaking / thin spaces
    text = text.replace("\u2212", "-")  # minus sign
    text = text.replace("…", "...")  # ellipsis
    text = _normalize_temperature_ranges(text)

    # Temperatures with a number.  Do this before generic degree handling.
    text = re.sub(r"(?<!\w)([-+]?\d+(?:\.\d+)?)\s*°\s*C\b", r"\1 degrees Celsius", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<!\w)([-+]?\d+(?:\.\d+)?)\s*°\s*F\b", r"\1 degrees Fahrenheit", text, flags=re.IGNORECASE)
    # Bare units with no leading number ("measured in degrees C").
    text = re.sub(r"°\s*C\b", "degrees Celsius", text, flags=re.IGNORECASE)
    text = re.sub(r"°\s*F\b", "degrees Fahrenheit", text, flags=re.IGNORECASE)
    # Any remaining degree symbol (angles, stray cases).
    text = re.sub(r"(?<!\w)([-+]?\d+(?:\.\d+)?)\s*°", r"\1 degrees", text)
    text = text.replace("°", " degrees")

    # Common weather/travel units.
    text = re.sub(r"(?<=\d)\s*km\s*/\s*h\b", " kilometres per hour", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*km/h\b", " kilometres per hour", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*mm\b", " millimetres", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*cm\b", " centimetres", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)\s*m\b", " metres", text, flags=re.IGNORECASE)

    # Numeric rates only ("5/month" -> "5 per month").  Requiring digit-then-letter
    # keeps "and/or", "N/A", "TCP/IP" and dates like "2026/06" intact.
    text = re.sub(r"(?<=\d)\s*/\s*(?=[A-Za-z])", " per ", text)

    # Money and percentages.  The integer part must END in a digit so a trailing
    # comma ("A$50, ...") is not swallowed into the spoken amount.
    text = re.sub(r"NZ\$\s*([\d,]*\d(?:\.\d+)?)", r"\1 New Zealand dollars", text, flags=re.IGNORECASE)
    text = re.sub(r"A\$\s*([\d,]*\d(?:\.\d+)?)", r"\1 Australian dollars", text, flags=re.IGNORECASE)
    text = re.sub(r"US\$\s*([\d,]*\d(?:\.\d+)?)", r"\1 US dollars", text, flags=re.IGNORECASE)
    text = re.sub(r"€\s*([\d,]*\d(?:\.\d+)?)", r"\1 euros", text)
    text = re.sub(r"£\s*([\d,]*\d(?:\.\d+)?)", r"\1 pounds", text)
    text = re.sub(r"\$\s*([\d,]*\d(?:\.\d+)?)", r"\1 dollars", text)
    text = re.sub(r"(?<=\d)\s*%", " percent", text)

    # Operators and separators that commonly leak from formatted answers.
    text = text.replace("&", " and ")
    text = re.sub("[•◦▪▫]", " ", text)  # bullet glyphs
    text = text.replace("→", " to ")  # ->
    text = text.replace("⇒", " to ")  # =>
    text = text.replace("≈", " about ")  # almost equal
    text = text.replace("~", " about ")

    text = _VARIATION_SELECTOR_RE.sub("", text)
    text = _EMOJI_RE.sub("", text)
    return text


def smooth_whitespace_for_tts(text: str) -> str:
    """Collapse visual formatting into calm spoken paragraphs.

    A former heading line (marked with the _HEAD sentinel) folds into the next
    content line as a spoken lead-in: "Weather" + "It will be sunny" becomes
    "Weather, It will be sunny."  A heading with no content after it becomes its
    own short sentence.
    """
    if not text:
        return ""

    raw_lines = text.splitlines()
    add_sentence_pauses = sum(1 for raw_line in raw_lines if raw_line.replace(_HEAD, "").strip()) > 1
    lines: list[str] = []
    pending_heading: str | None = None

    def flush_pending() -> None:
        nonlocal pending_heading
        if pending_heading is not None:
            lines.append(pending_heading.rstrip(".:;,") + ".")
            pending_heading = None

    for raw_line in raw_lines:
        is_heading = raw_line.rstrip().endswith(_HEAD)
        line = raw_line.replace(_HEAD, "").strip()
        if not line:
            # Hold a pending heading across blank lines so it still folds into
            # the next real content line; otherwise just collapse the blank.
            if pending_heading is None and lines and lines[-1] != "":
                lines.append("")
            continue
        if is_heading:
            flush_pending()
            pending_heading = line.rstrip(".:;,")
            continue
        if pending_heading is not None:
            line = f"{pending_heading.rstrip('.:;,')}, {line}"
            pending_heading = None
        if add_sentence_pauses and line[-1] not in ".!?;:":
            line += "."
        lines.append(line)

    flush_pending()

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"\.{4,}", "...", text)
    return text.strip()


# Reasoning blocks: models with ``/reasoning show`` enabled emit
# ``<think>...</think>`` blocks in the final assistant message.  Users want to
# SEE reasoning, not hear it read aloud (#34213).
_THINK_BLOCK_RE = re.compile(r"<think[\s>].*?</think>", flags=re.DOTALL | re.IGNORECASE)
# An unterminated block (streaming cut-off) should still not be spoken.
_THINK_BLOCK_OPEN_RE = re.compile(r"<think[\s>].*\Z", flags=re.DOTALL | re.IGNORECASE)

# Turn-end file-mutation verifier footer appended by run_agent.py
# (``_format_file_mutation_failure_footer``).  It's a UI affordance — reading
# "warning file mutation verifier, 2 files were NOT modified..." aloud is
# noise (#40772).  The footer is a ``⚠️ File-mutation verifier:`` header line
# followed by indented ``•`` bullet lines; strip the whole block.
_VERIFIER_FOOTER_RE = re.compile(
    r"^\s*⚠️?\s*File-mutation verifier:.*(?:\n[ \t]+•.*)*",
    flags=re.MULTILINE,
)


def strip_nonspoken_blocks(text: str) -> str:
    """Remove blocks that must never reach a speech provider.

    Currently: ``<think>`` reasoning blocks and the end-of-turn
    file-mutation verifier footer.
    """
    if not text:
        return ""
    text = _THINK_BLOCK_RE.sub(" ", text)
    text = _THINK_BLOCK_OPEN_RE.sub(" ", text)
    text = _VERIFIER_FOOTER_RE.sub(" ", text)
    return text


def flatten_newlines_for_payload(text: str) -> str:
    """Collapse newlines into sentence breaks for single-line TTS payloads.

    Some OpenAI-compatible backends (e.g. Kokoro) truncate synthesis at the
    first newline (#9004).  The smoothing pass already terminates each line
    with punctuation, so newlines can safely become plain spaces.
    """
    if not text:
        return ""
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"(?<=[.!?;:,])\n", " ", text)
    text = text.replace("\n", ". ")
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# Kanban task ids are ``t_`` + 8 hex chars (``secrets.token_hex(4)`` in
# ``hermes_cli/kanban_db._new_task_id``).  They are opaque for speech — a TTS
# engine would read ``t_8b13866d`` as a jumble of letters and numbers.  The
# spoken-id filter replaces each id with its human title: ``task - Foo bar``.
# Unresolvable ids are a hard error (the caller must fix the reference, not
# ship garbled speech).  Replacement happens after markdown stripping so ids
# inside fenced code blocks (which are removed, not spoken) do not trigger
# lookups or errors, but before symbol normalisation so ``5/t_xxx`` style
# digit-slash-letter sequences are not mis-expanded as rates (``5 per ...``).
_TASK_ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b", flags=re.IGNORECASE)


def _lookup_task_title(task_id: str) -> str | None:
    """Return the title for *task_id* by scanning every kanban board.

    Boards are discovered via ``hermes_cli.kanban_db.list_boards`` so the
    default board (``~/.hermes/kanban.db``) and every ``boards/<slug>/``
    board are checked.  The first hit wins — ids are globally unique by
    construction (``token_hex(4)``), so there is no ambiguity.
    """
    tid = task_id.lower()
    try:
        from hermes_cli import kanban_db
    except Exception:
        return None
    try:
        boards = kanban_db.list_boards(include_archived=True)
    except Exception:
        return None
    for meta in boards:
        slug = (meta.get("slug") or "").strip() or None
        # ``connect(board=slug)`` honours the default-board special path and
        # the kanban_home() resolution; fall back to an explicit db_path for
        # any synthetic test board.
        conn = None
        try:
            if slug:
                conn = kanban_db.connect(board=slug)
            else:
                db_path = meta.get("db_path")
                if not db_path:
                    continue
                from pathlib import Path as _Path

                conn = kanban_db.connect(db_path=_Path(db_path))
            task = kanban_db.get_task(conn, tid)
            if task is not None and getattr(task, "title", None):
                return str(task.title).strip()
        except Exception:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return None


def expand_task_ids_for_tts(text: str, *, strict: bool = True) -> str:
    """Replace bare kanban task ids (``t_`` + 8 hex) with ``task - <title>``.

    Args:
        text: Already markdown-stripped text (code fences removed).
        strict: When ``True`` (default) an id with no matching task raises
            ``ValueError`` listing every unresolvable id.  When ``False``
            unknown ids are left verbatim so callers that prefer best-effort
            can still produce speech.

    Number-context: the task id is consumed as a single atomic token
    (``\\b``-bounded), so surrounding digits/punctuation are preserved and
    the later ``normalize_symbols_for_tts`` pass never sees the raw hex as
    a numeric rate, date, or percentage.  The replacement title itself is
    returned verbatim — the normal symbol pass that follows will still
    expand any numbers/units *inside* the title.
    """
    if not text or "t_" not in text.lower():
        return text
    # Collect unique ids case-insensitively but preserve first-seen casing for
    # error messages.
    seen: dict[str, str] = {}  # lower -> first raw form
    for m in _TASK_ID_RE.finditer(text):
        raw = m.group(0)
        low = raw.lower()
        if low not in seen:
            seen[low] = raw
    if not seen:
        return text
    # Resolve each unique id once.
    resolved: dict[str, str | None] = {}
    missing: list[str] = []
    for low, raw in seen.items():
        title = _lookup_task_title(low)
        if title:
            # Normalise whitespace in the title so a multi-line or double-
            # spaced title does not inject extra pauses.
            title = re.sub(r"\s+", " ", title).strip()
            resolved[low] = f"task - {title}"
        else:
            resolved[low] = None
            missing.append(raw)
    if strict and missing:
        missing_str = ", ".join(sorted(set(missing)))
        raise ValueError(
            f"Unresolvable task id(s) for TTS: {missing_str} — "
            f"no matching task title found across boards. "
            f"Fix the reference or set resolve_task_ids=false to leave ids verbatim."
        )

    def _repl(m: re.Match[str]) -> str:
        low = m.group(0).lower()
        rep = resolved.get(low)
        return rep if rep is not None else m.group(0)

    return _TASK_ID_RE.sub(_repl, text)


def prepare_spoken_text(
    text: str, max_chars: int | None = 4000, *, resolve_task_ids: bool = True
) -> str:
    """Return a TTS-friendly script from assistant text.

    Deterministic cleanup, not a semantic rewrite: it removes ``<think>``
    reasoning blocks and the file-mutation verifier footer, removes Markdown,
    expands common symbols such as a degree-Celsius sign to "degrees Celsius",
    turns visual line formatting into speakable sentence pauses, and flattens
    the result to a single line so newline-sensitive providers (Kokoro) speak
    the whole script.

    When ``resolve_task_ids`` is ``True`` (default) any bare kanban task id
    of the form ``t_`` + 8 hex chars is replaced with ``task - <title>``
    looked up across all boards; an unresolvable id raises ``ValueError``.
    Set ``resolve_task_ids`` to ``False`` to leave ids verbatim (e.g. for
    debugging or when the caller will resolve them itself).
    """
    spoken = strip_nonspoken_blocks(text)
    # Task ids contain underscores (``t_8b13...``) which the Markdown
    # underscore-italic pass (``_foo_`` -> ``foo``) would otherwise consume
    # when two ids appear in one sentence (``t_a .. t_b`` looks like
    # ``_a .. t_`` italic).  Protect ids with sentinels before the Markdown
    # pass so ``_MD_UNDERSCORE_ITALIC_RE`` cannot eat them.  Ids inside
    # fenced code blocks are removed with the block (no lookup, no error)
    # because the sentinel is inside the `````...````` region that
    # ``strip_markdown_for_tts`` replaces with a space.
    _sentinels: dict[str, str] = {}
    if resolve_task_ids and text and "t_" in str(text).lower():
        # Use a sentinel with no underscores/spaces so no Markdown rule
        # touches it.  ``\\x01`` is already the heading sentinel and never
        # appears in user text.
        def _to_sentinel(m: re.Match[str]) -> str:
            tok = f"\x01TASK{len(_sentinels)}\x01"
            _sentinels[tok] = m.group(0)
            return tok

        spoken = _TASK_ID_RE.sub(_to_sentinel, spoken)
    spoken = strip_markdown_for_tts(spoken)
    if _sentinels:
        for tok, raw in _sentinels.items():
            spoken = spoken.replace(tok, raw)
        # Number-context: a digit-slash-id like ``5/t_8b13866d`` must not be
        # expanded as a numeric rate (``5 per t_...``) by the symbol pass.
        # Convert the slash to a spoken pause before the id is resolved, so
        # the later ``normalize_symbols_for_tts`` rate rule never sees it.
        # ``5/t_xxx`` -> ``5 , t_xxx`` -> ``5 , task - Title``.
        spoken = re.sub(
            r"(?<=\d)\s*/\s*(?=t_[0-9a-f]{8}\b)",
            " , ",
            spoken,
            flags=re.IGNORECASE,
        )
        spoken = expand_task_ids_for_tts(spoken, strict=True)
    elif resolve_task_ids:
        # No sentinel was created (no ``t_`` in the original), but still
        # handle any ids that survived markdown stripping (e.g. introduced
        # elsewhere).  This path is cheap — ``expand`` early-returns when
        # no ``t_`` is present.
        spoken = expand_task_ids_for_tts(spoken, strict=True)
    spoken = normalize_symbols_for_tts(spoken)
    spoken = smooth_whitespace_for_tts(spoken)
    spoken = flatten_newlines_for_payload(spoken)
    if max_chars is not None and max_chars > 0 and len(spoken) > max_chars:
        spoken = spoken[:max_chars].rstrip()
    return spoken
