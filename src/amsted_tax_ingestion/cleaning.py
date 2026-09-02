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
    (?:\b(?:tel|telephone|phone|mobile|cell|direct|office|fax)\b[ \t]*[:.]?[ \t]*)?
    (?:\+?\d{1,3}[ \t.\-]?)?
    (?:\(\d{3}\)|\d{3})[ \t.\-]\d{3}[ \t.\-]\d{4}
    (?:[ \t]*(?:x|ext\.?|extension)[ \t]*\d{1,6})?
    """
)

_STREET_SUFFIX = (
    "street|st|avenue|ave|boulevard|blvd|road|rd|drive|dr|lane|ln|way|court|ct|"
    "place|pl|parkway|pkwy|highway|hwy|suite|ste|floor|fl|plaza|tower|centre|center"
)

_STREET = re.compile(
    rf"""(?xim)
    ^.*?\b(?:
        \d{{1,6}}
      | One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten
    )\s+[\w.\-]+(?:\s+[\w.\-]+){{0,4}}\s+(?:{_STREET_SUFFIX})\b.*$
    """
)

_US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|"
    "Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|"
    "Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|"
    "Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|"
    "New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|"
    "Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|"
    "Virginia|Washington|West Virginia|Wisconsin|Wyoming"
)


_CITY_STATE_ZIP = re.compile(
    rf"(?im)^\s*[A-Z][\w.\-]+(?:[ \-][A-Z][\w.\-]+)*,\s*"
    rf"(?:[A-Z]{{2}}|{_US_STATES})\s+\d{{5}}(?:-\d{{4}})?\s*$"
)


_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL_ADDR = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# --------------------------------------------------------------------------- #
# Signature / disclaimer
# --------------------------------------------------------------------------- #
_PERSON_NAME = re.compile(
    r"^\s*[A-Z][a-z'\-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]+){1,2}\s*$"
)

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
        vice\s+president | senior\s+\w+ | managing\s+director | general\s+counsel |
        assistant\s+\w+ | executive\s+\w+ | corporate\s+\w+ | director | manager |
        partner | principal | associate | analyst | consultant | controller |
        treasurer | counsel | attorney | cpa | tax\s+\w+ |
        chief\s+\w+\s+officer | c[efot]o | vp
    )\b
    """
)


_COMPANY_SUFFIX = re.compile(
    r"(?i)\b(?:inc|inc\.|incorporated|llc|l\.l\.c\.|llp|ltd|limited|corp|"
    r"corporation|company|co|plc|group|holdings|industries|partners)\b\.?"
)

# Placeholders make redaction visible and reversible in review.
PHONE_TOKEN = "[phone removed]"
ADDRESS_TOKEN = "[address removed]"
SIGNATURE_TOKEN = "[signature removed]"
DISCLAIMER_TOKEN = "[disclaimer removed]"


def contact_density(line: str) -> int:
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

def _is_signature_line(line: str, *, max_len: int = 70) -> bool:
    """A single line that could belong to a signature block.

    Short, not a sentence, and either a name, a contact detail, or an
    affiliation. Prose fails on length or sentence shape.
    """
    if len(line) > max_len:
        return False
    words = line.split()
    if len(words) > 9:
        return False
    if line.endswith((".", "!", "?")) and len(words) > 5:
        return False
    if contact_density(line) >= 1:
        return True
    return bool(_PERSON_NAME.match(line))


def looks_like_signature_block(lines: list[str]) -> bool:
    """Shape test for a signature with no sign-off and no delimiter.

    Legal and corporate signatures often appear as a bare run of lines:

        Rosemary G. Feit
        Assistant General Counsel—Litigation
        Amsted Industries Incorporated

    No "Best regards", no "--". Detected by shape: a person's name followed by
    short affiliation lines carrying at least one contact signal.
    """
    lines = [l for l in lines if l.strip()]
    if not 2 <= len(lines) <= 8:
        return False

    for line in lines:
        stripped = line.strip()
        if len(stripped) > 70:
            return False
        if stripped.endswith((".", "!", "?")) and len(stripped.split()) > 6:
            return False

    first = lines[0].strip()
    if not _PERSON_NAME.match(first):
        return False
    # "Assistant General Counsel" is name-shaped but is a title, not a person.
    if _JOB_TITLE.search(first) or _COMPANY_SUFFIX.search(first):
        return False

    return sum(contact_density(l) for l in lines) >= 1




def strip_signature(text: str, *, max_block_lines: int = 12) -> str:
    """Remove a trailing signature block.

    Anchors, in order:
      1. an explicit "--" delimiter
      2. a sign-off line ("Best regards,")
      3. a dense contact cluster in the tail
      4. signature-block SHAPE — name line plus short affiliation lines
    """
    if not text.strip():
        return text

    lines = text.split("\n")

    # 1. Explicit delimiter.
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
            score = sum(contact_density(l) for l in block)
            nonblank = [l for l in block if l.strip()]
            if score >= 2 or (nonblank and len(nonblank) <= 6 and score >= 1):
                return "\n".join(lines[: index + 1]).rstrip()
            return text.rstrip()

    # 3 + 4. Walk UPWARD while lines still look signature-like.
    cut = len(lines)
    for index in range(len(lines) - 1, max(-1, len(lines) - max_block_lines - 1), -1):
        stripped = lines[index].strip()
        if not stripped:
            if cut < len(lines):
                continue          # a blank line inside the block is fine
            cut = index
            continue
        if _is_signature_line(stripped):
            cut = index
            continue
        break                     # prose: the block starts below this line

    if cut >= len(lines):
        return text.rstrip()

    block = lines[cut:]
    nonblank = [l for l in block if l.strip()]
    if not nonblank or len(nonblank) > 8:
        return text.rstrip()

    score = sum(contact_density(l) for l in block)
    if score >= 4 or looks_like_signature_block(block):
        return "\n".join(lines[:cut]).rstrip() if cut > 0 else text.rstrip()

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
