"""Verify email header recovery from Docling Markdown output.

Docling exports Markdown, so email headers recovered from a PDF export arrive
decorated (**From:**, ## From:, > From:). This test checks that the patterns
match every common decoration WITHOUT creating false positives on ordinary tax
prose.

Two modes:

  * Reference mode (default) — tests the patterns inline. Always runnable, even
    before scripts/apply_patches.py has been applied.
  * Live mode (--live) — imports the real patterns from
    src/amsted_tax_ingestion/email_thread.py and normalization.py, so it FAILS
    if the patch has not been applied.

    python tests/test_markdown_headers.py
    python tests/test_markdown_headers.py --live
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Reference patterns (mirror of the patched src)
# --------------------------------------------------------------------------- #

_MD_LEAD = r"[>\-\*\#\|\s_]*"   # before a label (hyphens allowed)
_SEP_LEAD = r"[>\*\#\|\s_]*"     # before ----- (NO hyphens)
_MD_SEP = r"[\*_\s]*"
_MD_TAIL = r"[\*_\s\|]*"
_MD_TRAIL = re.compile(r"[\*_\s\|]+$")

REF_THREAD_SIGNAL = re.compile(
    rf"(?im)^(?:"
    rf"{_MD_LEAD}from{_MD_SEP}:{_MD_SEP}.+"
    rf"|{_SEP_LEAD}-{{2,}}\s*original\s+message\s*-{{2,}}{_MD_TAIL}"
    rf"|{_SEP_LEAD}-{{2,}}\s*forwarded\s+message\s*-{{2,}}{_MD_TAIL}"
    rf"|{_MD_LEAD}on\s+.{{4,80}}\s+wrote\s*:"
    rf")\s*$"
)

REF_HEADER_BLOCK = re.compile(
    rf"""(?xims)
    ^{_MD_LEAD}from{_MD_SEP}:{_MD_SEP}[ \t]*(?P<sender>.+?)[ \t]*$
    (?:\n{_MD_LEAD}(?:sent|date){_MD_SEP}:{_MD_SEP}[ \t]*(?P<date>.+?)[ \t]*$)?
    (?:\n{_MD_LEAD}to{_MD_SEP}:{_MD_SEP}[ \t]*(?P<to>.+?)[ \t]*$)?
    (?:\n{_MD_LEAD}subject{_MD_SEP}:{_MD_SEP}[ \t]*(?P<subject>.+?)[ \t]*$)?
    """
)


def ref_strip_md(value: str) -> str:
    return _MD_TRAIL.sub("", (value or "").strip()).strip()


# --------------------------------------------------------------------------- #
# Live patterns (from the patched src)
# --------------------------------------------------------------------------- #

def load_live():
    """Import the real patterns. Returns (signal, header_block, strip_md)."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    try:
        from amsted_tax_ingestion import email_thread, normalization
    except ImportError as exc:
        print(f"\nERROR: could not import the package: {exc}")
        print("Run from the project root, with the package installed:")
        print("  pip install -e .")
        sys.exit(1)

    missing = []
    if not hasattr(email_thread, "_strip_md"):
        missing.append("email_thread._strip_md")
    if "_MD_LEAD" not in dir(normalization) and not hasattr(normalization, "_MD_LEAD"):
        missing.append("normalization._MD_LEAD")

    if missing:
        print("\nERROR: the Markdown patch has NOT been applied.")
        print(f"Missing: {', '.join(missing)}")
        print("\nRun:  python scripts/apply_patches.py")
        sys.exit(1)

    return (
        normalization._THREAD_SIGNAL,
        email_thread._HEADER_BLOCK,
        email_thread._strip_md,
    )


# --------------------------------------------------------------------------- #
# Test data
# --------------------------------------------------------------------------- #

SENDER = "Tristan Lopez <tristan.lopez@amsted.com>"
DATE = "Tuesday, August 18, 2026 2:14 PM"

HEADER_FORMATS = {
    "plain text": f"From: {SENDER}\nSent: {DATE}",
    "bold label": f"**From:** {SENDER}\n**Sent:** {DATE}",
    "bold whole line": f"**From: {SENDER}**\n**Sent: {DATE}**",
    "underscore label": f"__From:__ {SENDER}\n__Sent:__ {DATE}",
    "markdown heading": f"## From: {SENDER}\nSent: {DATE}",
    "blockquote": f"> From: {SENDER}\n> Sent: {DATE}",
    "list item": f"- **From:** {SENDER}\n- **Sent:** {DATE}",
    "table pipe": f"| From: {SENDER}\n| Sent: {DATE}",
}

THREAD_SIGNALS = {
    "plain header": f"From: {SENDER}",
    "bold header": f"**From:** {SENDER}",
    "heading header": f"## From: {SENDER}",
    "original message": "-----Original Message-----",
    "forwarded message": "---------- Forwarded message ----------",
    "on wrote": "On Tuesday, August 18, 2026, Tristan Lopez wrote:",
    "bold original message": "**-----Original Message-----**",
    "bold forwarded message": "**---------- Forwarded message ----------**",
}

# Ordinary tax-corpus text that must NOT be mistaken for an email header.
NOT_HEADERS = {
    "memo prose": "Income from Illinois sources is apportioned by payroll factor.",
    "table row": "| Revenue from operations | 1,240 | 1,180 |",
    "citation": "Guidance from: IRS Publication 541, page 12.",
    "plain heading": "## Background",
    "list of sources": "- Data from: internal trial balance",
    "sentence with colon": "The exclusion applies from: January 1, 2024.",
    "bullet prose": "* Amounts received from customers are included.",
    "quoted prose": "> The taxpayer argued the exclusion applied.",
    "horizontal rule": "-----",
    "markdown hr": "---",
}

PASSED = FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def section(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown header recovery tests")
    parser.add_argument("--live", action="store_true",
                        help="Test the real patterns in src (fails if unpatched)")
    args = parser.parse_args()

    if args.live:
        thread_signal, header_block, strip_md = load_live()
        mode = "LIVE — patterns imported from src"
    else:
        thread_signal, header_block, strip_md = (
            REF_THREAD_SIGNAL, REF_HEADER_BLOCK, ref_strip_md
        )
        mode = "REFERENCE — inline patterns (use --live to test src)"

    print("\n" + "=" * 68)
    print("MARKDOWN EMAIL-HEADER RECOVERY")
    print("=" * 68)
    print(f"  Mode: {mode}")

    section("HEADER PARSING — sender must be extracted cleanly")
    for label, text in HEADER_FORMATS.items():
        match = header_block.search(text)
        if not match:
            check(label, False, "(no match)")
            continue
        sender = strip_md(match.group("sender"))
        check(label, sender == SENDER, f"got {sender!r}")

    section("DATE CAPTURE — decoration must not leak into the value")
    for label, text in HEADER_FORMATS.items():
        match = header_block.search(text)
        if not match or not match.group("date"):
            check(f"{label} date", False, "(not captured)")
            continue
        date = strip_md(match.group("date"))
        check(f"{label} date", date == DATE, f"got {date!r}")

    section("THREAD DETECTION — these ARE email structures")
    for label, text in THREAD_SIGNALS.items():
        check(label, bool(thread_signal.search(text)), "(not detected)")

    section("FALSE POSITIVES — these are NOT email headers")
    for label, text in NOT_HEADERS.items():
        check(label, not thread_signal.search(text), f"matched: {text[:50]!r}")

    section(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAILED:
        if not args.live:
            print("\n  Reference patterns failed — this indicates a bug in the test.")
        else:
            print("\n  Live patterns failed. Re-apply:")
            print("    python scripts/apply_patches.py --revert")
            print("    python scripts/apply_patches.py")
        return 1

    if not args.live:
        print("\n  Reference patterns verified.")
        print("  Now confirm your src is patched:")
        print("    python tests/test_markdown_headers.py --live")
    else:
        print("\n  src is correctly patched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
