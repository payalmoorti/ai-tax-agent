"""Apply the Markdown email-header patch to src/amsted_tax_ingestion/.

WHY THIS EXISTS
---------------
Docling exports Markdown, so email headers recovered from a PDF export usually
arrive decorated:

    **From:** Tristan Lopez <tristan.lopez@amsted.com>
    **Sent:** Tuesday, August 18, 2026 2:14 PM

The shipped patterns in email_thread.py and normalization.py anchor on
``^\\s*from\\s*:``, so the leading ``**`` breaks the match. The failure is silent:
the thread falls back to token chunking with no per-message sender, date, or
ordering.

WHAT IT CHANGES
---------------
  1. email_thread.py  — _SEPARATOR and _HEADER_BLOCK become Markdown-tolerant.
  2. email_thread.py  — split_thread() wraps captured values in _strip_md(), so a
                        bolded "**From: Tristan**" does not leave a trailing "**"
                        on the sender.
  3. normalization.py — _THREAD_SIGNAL and _SUBJECT_LINE become Markdown-tolerant.

USAGE
-----
    python scripts/apply_patches.py --check    # report only, change nothing
    python scripts/apply_patches.py            # apply (writes .bak backups)
    python scripts/apply_patches.py --revert   # restore from .bak

Idempotent: running twice is a no-op. Verify with:

    python tests/test_markdown_headers.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "amsted_tax_ingestion"

MARKER = "_MD_LEAD"

# --------------------------------------------------------------------------- #
# Replacement blocks
# --------------------------------------------------------------------------- #

MD_HELPERS = '''
# --- Markdown-tolerant header patterns -------------------------------------
# Docling exports Markdown, so PDF email headers arrive decorated:
#   **From:**   __From:__   ## From:   > From:   - **From:**   | From: |
_MD_LEAD = r"[>\\-\\*\\#\\|\\s_]*"   # decoration before a label (hyphens allowed)
_SEP_LEAD = r"[>\\*\\#\\|\\s_]*"     # decoration before ----- (NO hyphens: they are the separator)
_MD_SEP = r"[\\*_\\s]*"            # decoration around the colon
_MD_TAIL = r"[\\*_\\s\\|]*"           # trailing decoration, e.g. **-----Original Message-----**
_MD_TRAIL = re.compile(r"[\\*_\\s\\|]+$")


def _strip_md(value: str) -> str:
    """Remove Markdown decoration left on a captured header value."""
    return _MD_TRAIL.sub("", (value or "").strip()).strip()

'''

EMAIL_PATTERNS = '''
_SEPARATOR = re.compile(
    rf"""(?xim)
    ^(?:
        {_SEP_LEAD}-{{2,}}\\s*original\\s+message\\s*-{{2,}}{_MD_TAIL}
      | {_SEP_LEAD}-{{2,}}\\s*forwarded\\s+message\\s*-{{2,}}{_MD_TAIL}
      | {_SEP_LEAD}_{{5,}}{_MD_TAIL}
      | {_MD_LEAD}on\\s+.{{4,80}}?\\s+wrote\\s*:
      | {_MD_LEAD}from{_MD_SEP}:{_MD_SEP}.+
    )\\s*$
    """
)

# A header block: From: ... Sent:/Date: ... To: ... [Cc:] [Bcc:] [Subject:]
_HEADER_BLOCK = re.compile(
    rf"""(?xims)
    ^{_MD_LEAD}from{_MD_SEP}:{_MD_SEP}[ \\t]*(?P<sender>.+?)[ \\t]*$
    (?:\\n{_MD_LEAD}(?:sent|date){_MD_SEP}:{_MD_SEP}[ \\t]*(?P<date>.+?)[ \\t]*$)?
    (?:\\n{_MD_LEAD}to{_MD_SEP}:{_MD_SEP}[ \\t]*(?P<to>.+?)[ \\t]*$)?
    (?:\\n{_MD_LEAD}cc{_MD_SEP}:{_MD_SEP}[ \\t]*(?P<cc>.+?)[ \\t]*$)?
    (?:\\n{_MD_LEAD}bcc{_MD_SEP}:{_MD_SEP}[ \\t]*.+?[ \\t]*$)?
    (?:\\n{_MD_LEAD}subject{_MD_SEP}:{_MD_SEP}[ \\t]*(?P<subject>.+?)[ \\t]*$)?
    """
)

'''

NORM_PATTERNS = '''
# Heuristics for detecting an email thread inside a PDF/DOCX export.
_THREAD_SIGNAL = re.compile(
    rf"(?im)^(?:"
    rf"{_MD_LEAD}from{_MD_SEP}:{_MD_SEP}.+"
    rf"|{_SEP_LEAD}-{{2,}}\\s*original\\s+message\\s*-{{2,}}{_MD_TAIL}"
    rf"|{_SEP_LEAD}-{{2,}}\\s*forwarded\\s+message\\s*-{{2,}}{_MD_TAIL}"
    rf"|{_MD_LEAD}on\\s+.{{4,80}}\\s+wrote\\s*:"
    rf")\\s*$"
)

_SUBJECT_LINE = re.compile(
    rf"(?im)^{_MD_LEAD}subject{_MD_SEP}:{_MD_SEP}[ \\t]*(.+?)[ \\t]*$"
)

'''

# split_thread() boundary capture — wrap values in _strip_md().
OLD_HEADER_CAPTURE = '''            "sender": (match.group("sender") or "").strip(),
            "date": (match.group("date") or "").strip(),
            "to": (match.group("to") or "").strip(),
            "cc": (match.group("cc") or "").strip(),
            "subject": (match.group("subject") or "").strip(),'''

NEW_HEADER_CAPTURE = '''            "sender": _strip_md(match.group("sender")),
            "date": _strip_md(match.group("date")),
            "to": _strip_md(match.group("to")),
            "cc": _strip_md(match.group("cc")),
            "subject": _strip_md(match.group("subject")),'''

OLD_ONWROTE_CAPTURE = '''            "sender": (match.group("sender") or "").strip(),
            "date": (match.group("date") or "").strip(),
            "to": "", "cc": "", "subject": "",'''

NEW_ONWROTE_CAPTURE = '''            "sender": _strip_md(match.group("sender")),
            "date": _strip_md(match.group("date")),
            "to": "", "cc": "", "subject": "",'''


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else '!! '}] {name}" + (f" — {detail}" if detail else ""))


def already_patched(text: str) -> bool:
    return MARKER in text


def cut(text: str, start_anchor: str, end_anchor: str) -> tuple[int, int] | None:
    """Return (start, end) offsets for the block to replace, or None."""
    start = text.find(start_anchor)
    end = text.find(end_anchor)
    if start < 0 or end < 0 or end <= start:
        return None
    return start, end


# --------------------------------------------------------------------------- #
# Patches
# --------------------------------------------------------------------------- #

def patch_email_thread(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []

    span = cut(text, "_SEPARATOR = re.compile(", "_ON_WROTE = re.compile(")
    if span is None:
        raise ValueError(
            "could not locate _SEPARATOR.._ON_WROTE block — apply manually"
        )
    start, end = span
    text = text[:start] + MD_HELPERS + EMAIL_PATTERNS + text[end:]
    notes.append("patterns replaced")

    if OLD_HEADER_CAPTURE in text:
        text = text.replace(OLD_HEADER_CAPTURE, NEW_HEADER_CAPTURE)
        notes.append("header capture wrapped in _strip_md")
    else:
        notes.append("WARNING: header capture not found — wrap manually")

    if OLD_ONWROTE_CAPTURE in text:
        text = text.replace(OLD_ONWROTE_CAPTURE, NEW_ONWROTE_CAPTURE)
        notes.append("on-wrote capture wrapped in _strip_md")

    return text, notes


def patch_normalization(text: str) -> tuple[str, list[str]]:
    span = cut(text, "_THREAD_SIGNAL = re.compile(", "_TAX_YEAR = re.compile(")
    if span is None:
        raise ValueError(
            "could not locate _THREAD_SIGNAL.._TAX_YEAR block — apply manually"
        )
    start, end = span
    text = text[:start] + MD_HELPERS + NORM_PATTERNS + text[end:]
    return text, ["patterns replaced"]


PATCHES = {
    "email_thread.py": patch_email_thread,
    "normalization.py": patch_normalization,
}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="Apply the Markdown header patch")
    parser.add_argument("--check", action="store_true", help="Report only")
    parser.add_argument("--revert", action="store_true", help="Restore from .bak")
    args = parser.parse_args()

    print("\n" + "=" * 68)
    print("MARKDOWN EMAIL-HEADER PATCH")
    print("=" * 68)
    print(f"  Target: {SRC}")
    print()

    if not SRC.exists():
        report("src package", False, f"not found at {SRC}")
        print("\n  Run this from the project root: python scripts/apply_patches.py")
        return 1

    # --- revert -------------------------------------------------------------
    if args.revert:
        restored = 0
        for name in PATCHES:
            backup = SRC / f"{name}.bak"
            if backup.exists():
                (SRC / name).write_text(backup.read_text(encoding="utf-8"),
                                        encoding="utf-8")
                backup.unlink()
                report(name, True, "restored from .bak")
                restored += 1
            else:
                report(name, False, "no .bak found")
        print(f"\n{restored} file(s) restored.")
        return 0

    # --- check / apply ------------------------------------------------------
    changed, needs_patch, failed = 0, 0, 0

    for name, patch_fn in PATCHES.items():
        path = SRC / name
        if not path.exists():
            report(name, False, "file not found")
            failed += 1
            continue

        text = path.read_text(encoding="utf-8")

        if already_patched(text):
            report(name, True, "already patched")
            continue

        if args.check:
            report(name, False, "NEEDS PATCH")
            needs_patch += 1
            continue

        try:
            patched, notes = patch_fn(text)
        except ValueError as exc:
            report(name, False, str(exc))
            failed += 1
            continue

        # Syntax-check before writing anything.
        try:
            compile(patched, str(path), "exec")
        except SyntaxError as exc:
            report(name, False, f"patch produced invalid syntax at line {exc.lineno}")
            failed += 1
            continue

        path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
        path.write_text(patched, encoding="utf-8")
        report(name, True, "; ".join(notes))
        changed += 1

    print()
    if args.check:
        if needs_patch:
            print(f"{needs_patch} file(s) need patching. Run without --check to apply.")
            return 1
        print("Everything is already patched.")
        return 0

    if failed:
        print(f"{failed} file(s) FAILED. See docs/markdown_headers_patch.md "
              f"to apply manually.")
        return 1

    print(f"{changed} file(s) changed. Backups written as *.bak")
    print("\nVerify:  python tests/test_markdown_headers.py")
    print("Revert:  python scripts/apply_patches.py --revert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
