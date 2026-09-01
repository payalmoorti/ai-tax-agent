"""Noise removal for tax source documents.

Handles the cleanup the action plan calls for:
  * email signature blocks
  * phone numbers and street addresses
  * legal/confidentiality disclaimers
  * quoted-reply markers, page markers, running headers/footers

Design note: signature detection is anchored on explicit sign-off markers and
contact-density scoring rather than aggressive heuristics, because over-deletion
in a tax corpus is worse than leaving a stray line. Redaction preserves a
placeholder token so the removal is visible and auditable.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

# --------------------------------------------------------------------------- #
# Whitespace / unicode
# --------------------------------------------------------------------------- #

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_TRAILING_WS = re.compile(r"[ \t]+(?=\n)")
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{3,}")

_PAGE_MARKER = re.compile(
    r"^\s*(?:<!--\s*page.*?-->|-{3,}\s*page\s*\d+\s*-{3,}|\[?page\s+\d+(?:\s+of\s+\d+)?\]?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_EMPTY_MD_ROW = re.compile(r"^\s*\|(?:\s*\|)+\s*$", re.MULTILINE)
_QUOTE_MARKER = re.compile(r"^\s*>{1,}\s?", re.MULTILINE)

# --------------------------------------------------------------------------- #
# Contact details
# --------------------------------------------------------------------------- #

_PHONE = re.compile(
    r"""(?xi)
    (?:(?:tel|telephone|phone|mobile|cell|direct|office|fax|f|t|m|o|d)\s*[:.]?\s*)?
    (?:\+?\d{1,3}[\s.\-]?)?
    (?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}
    (?:\s*(?:x|ext\.?|extension)\s*\d{1,6})?
    """
)

_STREET = re.compile(
    r"""(?xim)
    ^.*?\b\d{1,6}\s+[\w.\-]+(?:\s+[\w.\-]+){0,4}\s+
    (?:street|st|avenue|ave|boulevard|blvd|road|rd|drive|dr|lane|ln|way|court|ct|
       place|pl|parkway|pkwy|highway|hwy|suite|ste|floor|fl|plaza)\b
    .*$
    """
)

_CITY_STATE_ZIP = re.compile(
    r"(?im)^\s*[A-Z][\w.\-]+(?:[ \-][A-Z][\w.\-]+)*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*$"
)

_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL_ADDR = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# --------------------------------------------------------------------------- #
# Signature / disclaimer
# --------------------------------------------------------------------------- #

_SIG_DELIMITER = re.compile(r"^\s*(?:--\s*|__+|—{2,}|\*{3,})\s*$", re.MULTILINE)

_SIGNOFF = re.compile(
    r"""(?xim)
    ^\s*(?:
        best\s+regards | kind\s+regards | warm\s+regards | best\s+wishes |
        regards | sincerely | thanks(?:\s+again)? | thank\s+you |
        cheers | respectfully | yours\s+truly | many\s+thanks | best
    )\s*[,.]?\s*$
    """
)

_DISCLAIMER = re.compile(
    r"""(?xis)
    (?:^|\n)\s*
    (?:
        (?:this|the)\s+(?:e-?mail|message|communication|transmission)\b
            [^\n]{0,120}(?:confidential|privileged|intended\s+(?:only\s+)?(?:solely\s+)?for)
      | confidentiality\s+notice
      | privileged\s+and\s+confidential\s+communication
      | notice\s*:\s*this\s+(?:e-?mail|message)
      | if\s+you\s+(?:have\s+received|are\s+not)\s+th(?:is|e\s+intended)
      | irs\s+circular\s+230
      | any\s+tax\s+advice\s+contained
      | please\s+consider\s+the\s+environment
      | p\.?\s*lease\s+do\s+not\s+print
    )
    .*$
    """
)

_JOB_TITLE = re.compile(
    r"""(?xi)
    \b(?:
        vice\s+president | senior\s+manager | managing\s+director | director |
        manager | partner | principal | associate | analyst | consultant |
        controller | treasurer | counsel | attorney | cpa | tax\s+\w+ |
        chief\s+\w+\s+officer | c[efot]o
    )\b
    """
)

_COMPANY_SUFFIX = re.compile(r"(?i)\b(?:inc|llc|llp|ltd|corp|corporation|company|co|plc|group)\b\.?")

# Placeholders make redaction visible and reversible in review.
PHONE_TOKEN = "[phone removed]"
ADDRESS_TOKEN = "[address removed]"
SIGNATURE_TOKEN = "[signature removed]"
DISCLAIMER_TOKEN = "[disclaimer removed]"


def _contact_density(line: str) -> int:
    """How strongly a line looks like signature contact material."""
    score = 0
    if _PHONE.search(line):
        score += 2
    if _EMAIL_ADDR.search(line):
        score += 2
    if _URL.search(line):
        score += 1
    if _CITY_STATE_ZIP.match(line):
        score += 2
    if _STREET.match(line):
        score += 2
    if _JOB_TITLE.search(line):
        score += 1
    if _COMPANY_SUFFIX.search(line):
        score += 1
    return score


def strip_signature(text: str, *, max_block_lines: int = 12) -> str:
    """Remove a trailing signature block.

    Anchors on an explicit delimiter ("--") or a sign-off line, then only removes
    the trailing block if it scores as contact material. Falls back to scanning
    the tail for a dense contact cluster.
    """
    if not text.strip():
        return text

    lines = text.split("\n")

    # 1. Explicit delimiter — everything after the LAST one, if it's short enough.
    for match in reversed(list(_SIG_DELIMITER.finditer(text))):
        head, tail = text[: match.start()], text[match.end():]
        if 0 < len(tail.strip().split("\n")) <= max_block_lines:
            return head.rstrip()

    # 2. Sign-off anchor near the end.
    for index in range(len(lines) - 1, max(-1, len(lines) - max_block_lines - 4), -1):
        if _SIGNOFF.match(lines[index]):
            block = lines[index + 1:]
            if not block:
                return "\n".join(lines[: index + 1]).rstrip()
            score = sum(_contact_density(l) for l in block)
            nonblank = [l for l in block if l.strip()]
            # Keep the sign-off itself; drop the contact block beneath it.
            if score >= 2 or (nonblank and len(nonblank) <= 6 and score >= 1):
                return "\n".join(lines[: index + 1]).rstrip()
            return text.rstrip()

    # 3. No anchor: look for a dense contact cluster in the tail.
    tail_start = max(0, len(lines) - max_block_lines)
    tail = lines[tail_start:]
    cut = None
    for offset, line in enumerate(tail):
        if not line.strip():
            continue
        window = [l for l in tail[offset:] if l.strip()]
        if not window or len(window) > 8:
            continue
        score = sum(_contact_density(l) for l in window)
        if score >= 4 and all(len(l) < 120 for l in window):
            cut = tail_start + offset
            break
    if cut is not None and cut > 0:
        return "\n".join(lines[:cut]).rstrip()

    return text.rstrip()


def strip_disclaimers(text: str) -> str:
    return _DISCLAIMER.sub(f"\n{DISCLAIMER_TOKEN}", text)


def redact_contact_details(text: str, *, redact_email: bool = False) -> str:
    """Remove phone numbers and street addresses from body text.

    Email addresses are preserved by default because sender/recipient identity is
    meaningful tax metadata; pass redact_email=True to strip them from bodies.
    """
    text = _PHONE.sub(PHONE_TOKEN, text)
    text = _STREET.sub(ADDRESS_TOKEN, text)
    text = _CITY_STATE_ZIP.sub(ADDRESS_TOKEN, text)
    if redact_email:
        text = _EMAIL_ADDR.sub("[email removed]", text)
    # Collapse runs of adjacent placeholders.
    text = re.sub(rf"(?:{re.escape(ADDRESS_TOKEN)}\s*){{2,}}", ADDRESS_TOKEN + "\n", text)
    text = re.sub(rf"(?:{re.escape(PHONE_TOKEN)}\s*){{2,}}", PHONE_TOKEN + " ", text)
    return text


def strip_repeated_lines(text: str, min_repeats: int = 4) -> str:
    """Drop running headers/footers that repeat across most pages of a PDF."""
    lines = text.split("\n")
    counts = Counter(
        l.strip() for l in lines
        if 3 < len(l.strip()) < 90 and not l.strip().startswith(("#", "|", "-", "*"))
    )
    noise = {l for l, c in counts.items() if c >= min_repeats}
    if not noise:
        return text
    return "\n".join(l for l in lines if l.strip() not in noise)


def normalize_whitespace(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = _PAGE_MARKER.sub("", text)
    text = _EMPTY_MD_ROW.sub("", text)
    text = _MULTI_SPACE.sub("  ", text)
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def clean_message_body(
    text: str,
    *,
    remove_signature: bool = True,
    remove_contact: bool = True,
    remove_disclaimer: bool = True,
) -> str:
    """Full cleaning chain for a single email message body."""
    text = normalize_whitespace(text)
    text = _QUOTE_MARKER.sub("", text)
    if remove_disclaimer:
        text = strip_disclaimers(text)
    if remove_signature:
        text = strip_signature(text)
    if remove_contact:
        text = redact_contact_details(text)
    text = _MULTI_BLANK.sub("\n\n", text)
    # Drop a trailing placeholder-only tail.
    text = re.sub(rf"(?:\s*(?:{re.escape(DISCLAIMER_TOKEN)}|{re.escape(PHONE_TOKEN)}|{re.escape(ADDRESS_TOKEN)}))+\s*$", "", text)
    return text.strip()


def clean_document_text(text: str, *, document_type: str = "") -> str:
    """Full cleaning chain for a non-email document."""
    text = normalize_whitespace(text)
    if document_type == "pdf":
        text = strip_repeated_lines(text)
    text = strip_disclaimers(text)
    text = redact_contact_details(text)
    return _MULTI_BLANK.sub("\n\n", text).strip()
