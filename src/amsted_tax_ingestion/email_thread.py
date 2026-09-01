"""Email thread segmentation.

Splits an email thread into individual messages so each can be chunked separately
with its own sender, recipients, date, and position in the thread.

Two input shapes are handled:
  1. Native .msg / .eml — headers come from the parser, quoted history is split
     out of the body.
  2. Threads printed to PDF — Docling gives us flat text, so headers are recovered
     with regex and page numbers are tracked from Docling page markers.
"""
from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime

from .cleaning import clean_message_body
from .models import NA, EmailMessage
from .email_headers import (
ON_WROTE,
clean_boundary,
is_junk_message,
parse_recipients, # replaces the local definition
unwrap_header_lines)

# --------------------------------------------------------------------------- #
# Boundary detection
# --------------------------------------------------------------------------- #

# Outlook/Gmail forwarded-or-replied separators.

# --- Markdown-tolerant header patterns -------------------------------------
# Docling exports Markdown, so PDF email headers arrive decorated:
#   **From:**   __From:__   ## From:   > From:   - **From:**   | From: |
_MD_LEAD = r"[>\-\*\#\|\s_]*"   # decoration before a label (hyphens allowed)
_SEP_LEAD = r"[>\*\#\|\s_]*"     # decoration before ----- (NO hyphens: they are the separator)
_MD_SEP = r"[\*_\s]*"            # decoration around the colon
_MD_TAIL = r"[\*_\s\|]*"           # trailing decoration, e.g. **-----Original Message-----**
_MD_TRAIL = re.compile(r"[\*_\s\|]+$")


def _strip_md(value: str) -> str:
    """Remove Markdown decoration left on a captured header value."""
    return _MD_TRAIL.sub("", (value or "").strip()).strip()


_SEPARATOR = re.compile(
    rf"""(?xim)
    ^(?:
        {_SEP_LEAD}-{{2,}}\s*original\s+message\s*-{{2,}}{_MD_TAIL}
      | {_SEP_LEAD}-{{2,}}\s*forwarded\s+message\s*-{{2,}}{_MD_TAIL}
      | {_SEP_LEAD}_{{5,}}{_MD_TAIL}
      | {_MD_LEAD}on\s+.{{4,80}}?\s+wrote\s*:
      | {_MD_LEAD}from{_MD_SEP}:{_MD_SEP}.+
    )\s*$
    """
)

# A header block: From: ... Sent:/Date: ... To: ... [Cc:] [Bcc:] [Subject:]
_HEADER_BLOCK = re.compile(
    rf"""(?xims)
    ^{_MD_LEAD}from{_MD_SEP}:{_MD_SEP}[ \t]*(?P<sender>.+?)[ \t]*$
    (?:\n{_MD_LEAD}(?:sent|date){_MD_SEP}:{_MD_SEP}[ \t]*(?P<date>.+?)[ \t]*$)?
    (?:\n{_MD_LEAD}to{_MD_SEP}:{_MD_SEP}[ \t]*(?P<to>.+?)[ \t]*$)?
    (?:\n{_MD_LEAD}cc{_MD_SEP}:{_MD_SEP}[ \t]*(?P<cc>.+?)[ \t]*$)?
    (?:\n{_MD_LEAD}bcc{_MD_SEP}:{_MD_SEP}[ \t]*.+?[ \t]*$)?
    (?:\n{_MD_LEAD}subject{_MD_SEP}:{_MD_SEP}[ \t]*(?P<subject>.+?)[ \t]*$)?
    """
)



_PAGE_ANCHOR = re.compile(
    r"(?im)^\s*(?:<!--\s*page[^>]*?(?P<n1>\d+).*?-->|-{3,}\s*page\s*(?P<n2>\d+)\s*-{3,}|\[?page\s+(?P<n3>\d+)(?:\s+of\s+\d+)?\]?)\s*$"
)

_SUBJECT_PREFIX = re.compile(r"(?i)^\s*(?:re|fw|fwd|aw|antwort)\s*:\s*")


def normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes so every message in a thread shares one subject."""
    if not subject or subject == NA:
        return NA
    previous = None
    current = subject.strip()
    while previous != current:
        previous = current
        current = _SUBJECT_PREFIX.sub("", current).strip()
    return current or NA





_AMPM = re.compile(r"(?i)\b[ap]\.?m\.?\b")

_DATE_FORMATS = [
    "%A, %B %d, %Y %I:%M %p", "%A, %B %d, %Y %I:%M:%S %p", "%A, %B %d, %Y %H:%M",
    "%A, %b %d, %Y %I:%M %p",
    "%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p", "%B %d, %Y at %I:%M %p",
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d %B %Y %H:%M", "%d %b %Y %H:%M", "%B %d, %Y", "%b %d, %Y",
]


def parse_date(value: str) -> str:
    """Best-effort date normalization to ISO 8601. Returns the raw string on failure.

    Note: email.utils.parsedate_to_datetime silently DROPS the AM/PM marker on
    Outlook-style dates ("Tuesday, August 18, 2026 2:14 PM" -> 02:14), so explicit
    formats are tried first whenever an AM/PM marker is present.
    """
    if not value or value == NA:
        return NA

    text = value.strip()
    cleaned = re.sub(r"\s+", " ", text).replace("\u202f", " ").replace("\u00a0", " ")
    cleaned = re.sub(r"\s*\([A-Z]{2,5}\)\s*$", "", cleaned).strip()  # trailing (CDT)

    has_ampm = bool(_AMPM.search(cleaned))

    if not has_ampm:
        try:
            return parsedate_to_datetime(text).isoformat()
        except (TypeError, ValueError):
            pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).isoformat()
        except ValueError:
            continue

    if has_ampm:
        try:
            return parsedate_to_datetime(text).isoformat()
        except (TypeError, ValueError):
            pass

    return text  # keep the raw string rather than losing the information


def _page_map(text: str) -> list[tuple[int, int]]:
    """[(char_offset, page_number)] from Docling page markers."""
    pages: list[tuple[int, int]] = []
    for match in _PAGE_ANCHOR.finditer(text):
        number = match.group("n1") or match.group("n2") or match.group("n3")
        if number:
            pages.append((match.start(), int(number)))
    return pages


def _page_for(offset: int, pages: list[tuple[int, int]]) -> int | None:
    if not pages:
        return None
    current = pages[0][1]
    for position, number in pages:
        if position <= offset:
            current = number
        else:
            break
    return current


# --------------------------------------------------------------------------- #
# Thread splitting
# --------------------------------------------------------------------------- #

def split_thread(text: str, *, default_subject: str = NA) -> list[EmailMessage]:
    """Split flat thread text into ordered messages.

    Newest-first is the common Outlook layout, so messages are reversed at the
    end to give chronological order (message_index 1 = earliest).
    """
    if not text or not text.strip():
        return []

    # Fold wrapped recipient lines back onto their header line before parsing.
    text = unwrap_header_lines(text)

    pages = _page_map(text)
    boundaries: list[dict] = []

    # --- Outlook-style header blocks: From:/Sent:/To:/Cc:/Subject: ---
    for match in _HEADER_BLOCK.finditer(text):
        boundary = clean_boundary({
            "start": match.start(),
            "body_start": match.end(),
            "sender": match.group("sender"),
            "date": match.group("date"),
            "to": match.group("to"),
            "cc": match.group("cc"),
            "subject": match.group("subject"),
        })
        boundaries.append(boundary)

    # --- "On <date>, <sender> wrote:" separators ---
    for match in ON_WROTE.finditer(text):
        # Skip if a header block already covers this position.
        if any(abs(match.start() - b["start"]) < 40 for b in boundaries):
            continue
        boundary = clean_boundary({
            "start": match.start(),
            "body_start": match.end(),
            "sender": match.group("sender"),
            "date": match.group("date"),
            "to": "",
            "cc": "",
            "subject": "",
        })
        boundaries.append(boundary)

    boundaries.sort(key=lambda b: b["start"])

    # No recognizable headers: treat the whole thing as one message.
    if not boundaries:
        body = clean_message_body(text)
        if not body:
            return []
        return [EmailMessage(
            message_index=1,
            subject=normalize_subject(default_subject),
            body=body,
            page_number=_page_for(0, pages),
        )]

    messages: list[EmailMessage] = []

    # Text before the first header is the newest message in a top-posted thread.
    preamble = text[: boundaries[0]["start"]].strip()
    if preamble:
        body = clean_message_body(preamble)
        if body:
            messages.append(EmailMessage(
                message_index=0,
                subject=normalize_subject(default_subject),
                body=body,
                page_number=_page_for(0, pages),
            ))

    for position, boundary in enumerate(boundaries):
        end = boundaries[position + 1]["start"] if position + 1 < len(boundaries) else len(text)
        body = clean_message_body(text[boundary["body_start"]:end])
        if not body:
            continue
        subject = normalize_subject(boundary["subject"] or default_subject)
        messages.append(EmailMessage(
            message_index=0,
            sender=boundary["sender"] or NA,
            recipients=parse_recipients(boundary["to"]),
            cc=parse_recipients(boundary["cc"]),
            message_date=parse_date(boundary["date"]),
            subject=subject,
            body=body,
            page_number=_page_for(boundary["start"], pages),
        ))

    # Drop export artifacts: the mailbox owner's name Outlook prints at the top,
    # orphaned address continuation lines, bare header labels.
    messages = [m for m in messages if not is_junk_message(m.sender, m.body)]

    if not messages:
        return []

    # Outlook threads are newest-first; flip to chronological.
    messages.reverse()
    for index, message in enumerate(messages, start=1):
        message.message_index = index

    return messages



def build_thread(
    *,
    headers: dict[str, str],
    body: str,
    page_count: int | None = None,
) -> tuple[str, list[EmailMessage]]:
    """Build the message list for a native .msg/.eml file.

    The outermost headers describe the newest message; older messages are
    recovered from the quoted history inside the body.
    """
    thread_subject = normalize_subject(headers.get("subject", NA))
    quoted = split_thread(body, default_subject=thread_subject)

    top_body_end = len(body)
    first = _SEPARATOR.search(body)
    if first:
        top_body_end = first.start()
    top_body = clean_message_body(body[:top_body_end])

    messages: list[EmailMessage] = []
    if len(quoted) > 1:
        # split_thread already recovered the history; replace its last entry
        # (the newest) with the authoritative parsed headers.
        messages = quoted[:-1]
        messages.append(EmailMessage(
            message_index=0,
            sender=headers.get("from", NA) or NA,
            recipients=parse_recipients(headers.get("to", "")),
            cc=parse_recipients(headers.get("cc", "")),
            message_date=parse_date(headers.get("date", NA)),
            subject=thread_subject,
            body=top_body or (quoted[-1].body if quoted else ""),
        ))
    else:
        messages.append(EmailMessage(
            message_index=0,
            sender=headers.get("from", NA) or NA,
            recipients=parse_recipients(headers.get("to", "")),
            cc=parse_recipients(headers.get("cc", "")),
            message_date=parse_date(headers.get("date", NA)),
            subject=thread_subject,
            body=top_body or clean_message_body(body),
        ))

    messages = [m for m in messages if m.body.strip()]
    for index, message in enumerate(messages, start=1):
        message.message_index = index
    return thread_subject, messages


def render_thread(messages: list[EmailMessage]) -> str:
    """Human-readable rendering of the whole thread for the normalized content field."""
    blocks = []
    for message in messages:
        header = [f"[Message {message.message_index}]"]
        if message.sender != NA:
            header.append(f"From: {message.sender}")
        if message.recipients:
            header.append(f"To: {', '.join(message.recipients)}")
        if message.cc:
            header.append(f"Cc: {', '.join(message.cc)}")
        if message.message_date != NA:
            header.append(f"Date: {message.message_date}")
        if message.subject != NA:
            header.append(f"Subject: {message.subject}")
        blocks.append("\n".join(header) + "\n\n" + message.body)
    return "\n\n---\n\n".join(blocks)
