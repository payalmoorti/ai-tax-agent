"""Consolidated email header recovery for PDF-extracted threads.

Place at:  src/amsted_tax_ingestion/email_headers.py

Replaces the earlier scattered patches. If you copied `header_recovery.py` or
`header_unwrap.py`, delete them — everything is consolidated here.

Fixes, in the order they bite:

  1. ON_WROTE — the old non-greedy date stopped at the first comma, so
     "On Aug 2, 2023, at 3:45 PM, Sherlock, Kyle <k@a.com> wrote:" produced
     date="Aug " and sender="2, 2023, at 3:45 PM, Sherlock, Kyle".

  2. unwrap_header_lines — pdfplumber preserves the PDF's visual wrapping, so a
     long Cc: spills onto the next line. That orphan line then becomes the
     message body and the Subject: capture fails.

  3. parse_recipients — splitting on every comma turns Outlook's
     "Lopez, Tristan <t@a.com>" into two recipients.

  4. is_junk_message — Outlook prints the mailbox owner's name at the top of the
     export ("Lopez, Tristan"). It is not a message.

  5. sanitize_subject / is_label_only — reject header fragments captured as
     values when a header table is flattened or transposed by Docling.
"""
from __future__ import annotations

import re

NA = "n/a"

_MD_LEAD = r"[>\-\*\#\|\s_]*"
_MD_SEP = r"[\*_\s]*"
_MD_TAIL = r"[\*_\s\|]*"
_MD_TRAIL = re.compile(r"[\*_\s\|]+$")

HEADER_LABELS = (
    "from", "sent", "date", "to", "cc", "bcc",
    "subject", "importance", "attachments", "reply-to",
)

_LABEL_LINE = re.compile(
    rf"(?i)^[>\-\*\#\|\s_]*(?:{'|'.join(HEADER_LABELS)})[\*_\s]*:"
)
_LABEL_ONLY = re.compile(
    rf"(?im)^{_MD_LEAD}(?:{'|'.join(HEADER_LABELS)}){_MD_SEP}:{_MD_SEP}{_MD_TAIL}$"
)


def strip_md(value: str) -> str:
    """Remove Markdown decoration left on a captured header value."""
    return _MD_TRAIL.sub("", (value or "").strip()).strip()


# --------------------------------------------------------------------------- #
# 1. "On <date>, <sender> wrote:"
# --------------------------------------------------------------------------- #

# The sender is anchored on an email address or a short name, which lets the
# date consume the commas in "Aug 2, 2023, at 3:45 PM".
ON_WROTE = re.compile(
    r"(?im)^"
    r"[>\-\*\#\|\s_]*on\s+"
    r"(?P<date>.{4,80}?)"
    r"\s*,\s*"
    r"(?P<sender>[^,<]{2,60}(?:,\s*[^,<]{1,40})?\s*<[^>]+>|[^,<]{2,60})"
    r"\s*wrote\s*:\s*$"
)


# --------------------------------------------------------------------------- #
# 2. Unwrap wrapped header continuation lines
# --------------------------------------------------------------------------- #

_ADDRESSY = re.compile(r"[<@;]")
MAX_CONTINUATIONS = 3
MAX_CONTINUATION_LEN = 140


def is_header_line(line: str) -> bool:
    return bool(_LABEL_LINE.match(line or ""))


def is_label_only(text: str) -> bool:
    """True when a captured value is really another header label ('Cc:')."""
    return bool(_LABEL_ONLY.match((text or "").strip()))


def is_continuation(line: str) -> bool:
    """True when a label-less line looks like recipient overflow.

    Requires one of < @ ; so ordinary body prose is never folded into a header.
    """
    stripped = (line or "").strip()
    if not stripped or len(stripped) > MAX_CONTINUATION_LEN:
        return False
    if is_header_line(stripped) or not _ADDRESSY.search(stripped):
        return False
    if stripped.endswith((".", "!", "?")) and " " in stripped:
        return False   # a sentence is body text
    return True


def unwrap_header_lines(text: str) -> str:
    """Fold wrapped recipient lines back onto their header line."""
    if not text:
        return text

    lines = text.split("\n")
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not is_header_line(line):
            output.append(line)
            index += 1
            continue

        buffer = line.rstrip()
        cursor = index + 1
        folded = 0
        while cursor < len(lines) and folded < MAX_CONTINUATIONS:
            candidate = lines[cursor]
            if not candidate.strip() or is_header_line(candidate):
                break
            if not is_continuation(candidate):
                break
            buffer += " " + candidate.strip()
            cursor += 1
            folded += 1

        output.append(buffer)
        index = cursor

    return "\n".join(output)


# --------------------------------------------------------------------------- #
# 3. Recipient parsing
# --------------------------------------------------------------------------- #

def parse_recipients(value: str) -> list[str]:
    """Split a recipient header into individual addresses.

    Outlook delimits with ';'. A ',' is ambiguous: it separates addresses in
    RFC-2822 style but also appears inside "Last, First" display names, so bare
    name fragments are re-joined to the address that follows them.
    """
    if not value or value == NA:
        return []
    if ";" in value:
        parts = [p.strip() for p in value.split(";")]
    else:
        parts, buffer = [], ""
        for segment in (s.strip() for s in value.split(",")):
            candidate = f"{buffer}, {segment}".strip(", ") if buffer else segment
            if "@" in segment or "<" in segment:
                parts.append(candidate)
                buffer = ""
            else:
                buffer = candidate
        if buffer:
            parts.append(buffer)
    return [p for p in parts if p and not is_label_only(p)]


# --------------------------------------------------------------------------- #
# 4. Junk message detection
# --------------------------------------------------------------------------- #

MIN_BODY_CHARS = 25

_ADDRESS_FRAGMENT = re.compile(r"^\s*<?[\w.+-]+@[\w.-]+>?\s*$")
_NAME_ONLY = re.compile(r"^\s*[A-Z][\w.'-]+(?:,\s*[A-Z][\w.'-]+){0,2}\s*$")


def is_junk_message(sender: str, body: str, *, min_chars: int = MIN_BODY_CHARS) -> bool:
    """True when a parsed 'message' is an export artifact rather than content.

    Catches:
      * the mailbox owner's name Outlook prints at the top ("Lopez, Tristan")
      * an orphaned address continuation line ("<dcastillo@amsted.com>")
      * a bare header label captured as a body

    Keeps genuinely short replies such as "Thanks, that works for me."
    """
    body = (body or "").strip()
    if not body:
        return True
    if is_label_only(body):
        return True
    if _ADDRESS_FRAGMENT.match(body):
        return True
    if len(body) < min_chars:
        # Short AND unattributed AND name-shaped: an export artifact.
        if sender in ("", NA) and _NAME_ONLY.match(body):
            return True
        if sender in ("", NA) and len(body) < 15:
            return True
    return False


# --------------------------------------------------------------------------- #
# 5. Subject sanitation
# --------------------------------------------------------------------------- #

def looks_like_flattened_table(value: str, *, max_labels: int = 2) -> bool:
    """Detect a header row that Docling flattened into a single line."""
    value = value or ""
    found = sum(
        1 for label in HEADER_LABELS
        if re.search(rf"\b{label}\s*:", value, re.IGNORECASE)
    )
    if found >= max_labels or len(value) > 200:
        return True
    # A single embedded label plus a long value is also a flattened row:
    # a real sender never contains "Subject:" or "Sent:".
    if found >= 1 and len(value) > 60:
        return True
    if value.count(";") >= 2 or len(value) > 120:
        return True
    return False


def sanitize_subject(value: str) -> str:
    """Trim a subject that swallowed a flattened header row."""
    value = strip_md(value)
    if not value:
        return NA
    # Keep only what follows the LAST 'Subject:' in the line.
    parts = re.split(r"(?i)\bsubject\s*:\s*", value)
    if len(parts) > 1:
        value = parts[-1].strip()
    # Cut at the next header label if one follows.
    value = re.split(rf"(?i)\b(?:{'|'.join(HEADER_LABELS)})\s*:", value)[0].strip()
    if not value or len(value) > 200:
        return NA
    return value


def clean_boundary(boundary: dict) -> dict:
    """Sanitize captured header values; blank anything that is really a label."""
    sender = strip_md(boundary.get("sender", ""))
    if is_label_only(sender) or looks_like_flattened_table(sender):
        sender = ""

    for field in ("to", "cc", "date"):
        value = strip_md(boundary.get(field, ""))
        boundary[field] = "" if is_label_only(value) else value

    boundary["sender"] = sender or NA
    subject = boundary.get("subject", "")
    boundary["subject"] = sanitize_subject(subject) if subject else ""
    return boundary
