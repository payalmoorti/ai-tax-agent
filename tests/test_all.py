"""Module unit tests for the Amsted ingestion pipeline.

Covers cleaning, date parsing, subject normalization, thread segmentation,
normalization, chunking, and the search-record contract.

No Azure calls, no network. Runs as a plain script or under pytest.

    python tests/test_all.py
    python tests/test_all.py -v      # show every assertion
    pytest tests/test_all.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make src/ importable when run directly (not installed).
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# tiktoken downloads its BPE file on first use. In an offline environment that
# fails, so fall back to a ~4-chars-per-token estimate. Ratios are preserved,
# so every assertion below still holds.
try:  # pragma: no cover
    import tiktoken

    tiktoken.get_encoding("cl100k_base")
    TOKENIZER = "tiktoken"
except Exception:  # noqa: BLE001 - offline fallback
    import types

    class _Encoding:
        def encode(self, text):
            return list(range(max(1, len(text) // 4)))

        def decode(self, tokens):
            return "x" * (len(tokens) * 4)

    _stub = types.ModuleType("tiktoken")
    _stub.get_encoding = lambda name: _Encoding()
    sys.modules["tiktoken"] = _stub
    TOKENIZER = "stub (tiktoken unavailable offline)"

from amsted_tax_ingestion.chunking import Chunker, EnrichmentMissingError  # noqa: E402
from amsted_tax_ingestion.cleaning import (  # noqa: E402
    clean_message_body,
    redact_contact_details,
    strip_disclaimers,
    strip_repeated_lines,
    strip_signature,
)
from amsted_tax_ingestion.email_thread import (  # noqa: E402
    build_thread,
    normalize_subject,
    parse_date,
    parse_recipients,
    split_thread,
)
from amsted_tax_ingestion.models import NA, ExtractedDocument  # noqa: E402
from amsted_tax_ingestion.normalization import (  # noqa: E402
    guess_tax_year,
    looks_like_email_thread,
    normalize,
)

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
PASSED = FAILED = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        if VERBOSE:
            print(f"  PASS  {name}")
    else:
        FAILED += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

SIGNATURE_EMAIL = """Thanks Payal.

Best regards,
Tristan Lopez
Director, Tax
Amsted Industries Inc.
311 South Wacker Drive, Suite 5300
Chicago, IL 60606
Direct: (312) 555-0142
tristan.lopez@amsted.com"""

THREAD = """Confirming we'll register in Illinois for tax year 2024.

Payal

From: Tristan Lopez <tristan.lopez@amsted.com>
Sent: Tuesday, August 18, 2026 2:14 PM
To: Payal Moorti <payal.moorti@protiviti.com>
Cc: Lynn Soren <lynn.soren@protiviti.com>
Subject: RE: Illinois nexus - remote employees

Does the payroll factor change for 2024?

Best regards,
Tristan Lopez
Director, Tax
Amsted Industries Inc.
Direct: (312) 555-0142

This e-mail is confidential and intended solely for the addressee.

From: Payal Moorti <payal.moorti@protiviti.com>
Sent: Monday, August 17, 2026 9:02 AM
To: Tristan Lopez <tristan.lopez@amsted.com>
Subject: Illinois nexus - remote employees

Under PL 86-272 the engineering support work exceeds solicitation.

Thanks,
Payal
"""

MARKDOWN_THREAD = """**From:** Tristan Lopez <tristan.lopez@amsted.com>
**Sent:** Tuesday, August 18, 2026 2:14 PM
**To:** Payal Moorti <payal.moorti@protiviti.com>
**Subject:** RE: Illinois nexus

Does the payroll factor change?

<!-- page 2 -->

**From:** Payal Moorti <payal.moorti@protiviti.com>
**Sent:** Monday, August 17, 2026 9:02 AM
**To:** Tristan Lopez <tristan.lopez@amsted.com>
**Subject:** Illinois nexus

Under PL 86-272 the work exceeds solicitation.
"""

MEMO = "TAX MEMORANDUM\n\n" + ("Nexus analysis for fiscal year 2023. " * 40)


def enriched(doc):
    """Mark a document enriched so the chunker guard passes."""
    doc.metadata = doc.metadata.model_copy(update={
        "enrichment_status": "ok",
        "enrichment_confidence": 0.91,
        "tax_topic": "Nexus",
        "jurisdiction": "Illinois",
        "authority_level": "Internal Email",
        "legal_entity": "Amsted Industries Inc.",
        "business_unit": "Corporate Tax",
    })
    return doc


def make_doc(text, name="thread.pdf", extractor="docling", pages=2, scenarios=None):
    tmp = ROOT / f"_test_{name}"
    tmp.write_text("placeholder", encoding="utf-8")
    try:
        return normalize(
            tmp, f"raw/{name}",
            ExtractedDocument(text=text, extractor=extractor, page_count=pages),
            scenario_ids=scenarios,
        )
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_cleaning() -> None:
    section("CLEANING")

    out = strip_signature(SIGNATURE_EMAIL)
    check("signature block removed", "555-0142" not in out and "Wacker" not in out)
    check("body text preserved", "Thanks Payal." in out)

    check("phone redacted",
          "[phone removed]" in redact_contact_details("Call (312) 555-0142 today"))
    check("street address redacted",
          "[address removed]" in redact_contact_details("311 South Wacker Drive, Suite 5300"))
    check("city/state/zip redacted",
          "[address removed]" in redact_contact_details("Chicago, IL 60606"))
    check("email address preserved by default",
          "payal@protiviti.com" in redact_contact_details("Write to payal@protiviti.com"))
    check("email redacted when requested",
          "[email removed]" in redact_contact_details("Write to payal@protiviti.com",
                                                      redact_email=True))

    disclaimed = strip_disclaimers("Body text.\n\nThis e-mail is confidential and "
                                   "intended solely for the addressee.")
    check("confidentiality disclaimer removed", "confidential" not in disclaimed.lower())
    check("Circular 230 disclaimer removed",
          "circular" not in strip_disclaimers("Body.\n\nIRS Circular 230 disclosure "
                                              "applies here.").lower())

    check("quoted-reply markers stripped",
          ">" not in clean_message_body("> quoted line\nnormal line"))

    repeated = "\n".join(["Amsted Confidential", "Body A", "Amsted Confidential",
                          "Body B", "Amsted Confidential", "Body C",
                          "Amsted Confidential", "Body D"])
    check("repeated PDF header removed",
          "Amsted Confidential" not in strip_repeated_lines(repeated))
    check("repeated-header body kept", "Body A" in strip_repeated_lines(repeated))

    check("substantive text not over-deleted",
          "PL 86-272" in clean_message_body(
              "Under PL 86-272 the work exceeds solicitation.\n\nThanks,\nPayal"))


def test_dates() -> None:
    section("DATE PARSING")
    # parsedate_to_datetime silently DROPS AM/PM on Outlook-style dates.
    check("PM preserved (Outlook style)",
          parse_date("Tuesday, August 18, 2026 2:14 PM") == "2026-08-18T14:14:00",
          parse_date("Tuesday, August 18, 2026 2:14 PM"))
    check("AM preserved",
          parse_date("Monday, August 17, 2026 9:02 AM") == "2026-08-17T09:02:00")
    check("RFC 2822 with offset",
          parse_date("Mon, 17 Aug 2026 09:02:11 -0500").startswith("2026-08-17T09:02:11"))
    check("slash format with PM",
          parse_date("8/18/2026 2:14 PM") == "2026-08-18T14:14:00")
    check("ISO passthrough", parse_date("2026-08-18 14:14") == "2026-08-18T14:14:00")
    check("date only", parse_date("August 18, 2026") == "2026-08-18T00:00:00")
    check("trailing timezone label stripped",
          parse_date("Tuesday, August 18, 2026 2:14 PM (CDT)") == "2026-08-18T14:14:00")
    check("empty returns n/a", parse_date("") == NA)
    check("unparseable returns raw string", parse_date("sometime last week")
          == "sometime last week")


def test_subjects_and_recipients() -> None:
    section("SUBJECT / RECIPIENT PARSING")
    check("RE: stripped", normalize_subject("RE: Illinois nexus") == "Illinois nexus")
    check("nested prefixes stripped",
          normalize_subject("RE: FW: Re: Illinois nexus") == "Illinois nexus")
    check("clean subject untouched",
          normalize_subject("Illinois nexus") == "Illinois nexus")
    check("empty subject is n/a", normalize_subject("") == NA)

    parsed = parse_recipients("Payal Moorti <p@protiviti.com>; Lynn Soren <l@protiviti.com>")
    check("recipients split on semicolon", len(parsed) == 2, str(parsed))
    check("comma inside display name not split",
          len(parse_recipients("Lopez, Tristan <t@amsted.com>")) == 1)
    check("empty recipients", parse_recipients("") == [])


def test_thread_segmentation() -> None:
    section("THREAD SEGMENTATION")

    messages = split_thread(THREAD, default_subject="Illinois nexus - remote employees")
    check("three messages recovered", len(messages) == 3, f"got {len(messages)}")
    check("indices are sequential",
          [m.message_index for m in messages] == [1, 2, 3])
    check("chronological order (earliest first)", "Payal" in messages[0].sender)
    check("recipients parsed", len(messages[1].recipients) == 1)
    check("cc parsed", len(messages[1].cc) == 1)
    check("subjects normalized across thread",
          all(m.subject == "Illinois nexus - remote employees" for m in messages))
    check("signature removed from message body", "555-0142" not in messages[1].body)
    check("disclaimer removed from message body",
          "confidential" not in messages[1].body.lower())
    check("header-less top post retained", any(m.sender == NA for m in messages))

    # Markdown-decorated headers from a Docling PDF export.
    md_messages = split_thread(MARKDOWN_THREAD, default_subject="Illinois nexus")
    check("markdown headers segmented", len(md_messages) == 2, f"got {len(md_messages)}")
    if md_messages:
        check("markdown sender has no decoration",
              "*" not in md_messages[0].sender, repr(md_messages[0].sender))
        check("markdown date parsed",
              md_messages[-1].message_date == "2026-08-18T14:14:00",
              md_messages[-1].message_date)
        check("page number tracked from Docling marker",
              any(m.page_number for m in md_messages))

    subject, native = build_thread(
        headers={"subject": "RE: Illinois nexus",
                 "from": "Tristan Lopez <t@amsted.com>",
                 "to": "Payal Moorti <p@protiviti.com>", "cc": "",
                 "date": "Tue, 18 Aug 2026 14:14:00 -0500"},
        body="Latest reply.\n\nFrom: Payal Moorti <p@protiviti.com>\n"
             "Sent: Monday, August 17, 2026 9:02 AM\n\nEarlier message.",
    )
    check("build_thread normalizes subject", subject == "Illinois nexus", subject)
    check("build_thread recovers quoted history", len(native) == 2, f"got {len(native)}")

    check("single message with no headers", len(split_thread("Just one body.")) == 1)
    check("empty text yields no messages", split_thread("") == [])


def test_normalization() -> None:
    section("NORMALIZATION")

    doc = make_doc(THREAD, scenarios=["SCN-014", "SCN-022"])
    m = doc.metadata

    check("thread detected inside a PDF export", doc.is_email_thread)
    check("messages carried onto the document", len(doc.messages) == 3)
    check("source_type is Email Thread", m.source_type == "Email Thread")
    check("thread_subject normalized",
          m.thread_subject == "Illinois nexus - remote employees", m.thread_subject)
    check("source_date is earliest message",
          m.source_date == "2026-08-17T09:02:00", m.source_date)
    check("message_date is latest message",
          m.message_date == "2026-08-18T14:14:00", m.message_date)
    check("author is earliest sender", "Payal" in m.author)
    check("tax year parsed deterministically",
          m.tax_year_or_effective_period == "2024", m.tax_year_or_effective_period)
    check("primary scenario_id set", m.scenario_id == "SCN-014")
    check("scenario_ids multi-valued", m.scenario_ids == ["SCN-014", "SCN-022"])
    check("source_id derived", m.source_id.startswith("src_"))
    check("content_hash populated", len(doc.content_hash) == 64)
    check("char_count matches content", doc.char_count == len(doc.content))

    check("semantic fields await enrichment",
          m.tax_topic == NA and m.jurisdiction == NA and m.authority_level == NA)
    check("governance fields are n/a",
          (m.validity_status, m.current_or_superseded, m.access_classification)
          == (NA, NA, NA))
    check("enrichment_status starts pending", m.enrichment_status == "pending")

    memo = make_doc(MEMO, name="memo.pdf")
    check("memo not treated as a thread", not memo.is_email_thread)
    check("memo source_type", memo.metadata.source_type == "Tax Memo")
    check("memo tax year parsed",
          memo.metadata.tax_year_or_effective_period == "2023")

    # document_id must not depend on file size or download.
    a = make_doc(THREAD)
    b = make_doc(THREAD + "\n\nextra trailing content that changes size")
    check("document_id depends only on source_path", a.document_id == b.document_id)
    check("content_hash changes when content changes",
          a.content_hash != b.content_hash)

    check("thread signal detection", looks_like_email_thread(THREAD))
    check("markdown thread signal detection", looks_like_email_thread(MARKDOWN_THREAD))
    check("prose is not a thread signal",
          not looks_like_email_thread("Income from Illinois sources is apportioned."))
    check("guess_tax_year finds nothing in plain prose",
          guess_tax_year("No year mentioned here.") == NA)


def test_chunking() -> None:
    section("CHUNKING")

    doc = make_doc(THREAD, scenarios=["SCN-014"])

    try:
        Chunker(200, 40).chunk(doc)
        check("enrichment guard raises", False, "no exception")
    except EnrichmentMissingError:
        check("enrichment guard raises", True)

    check("guard can be bypassed for offline testing",
          len(Chunker(200, 40, require_enrichment=False).chunk(doc)) > 0)

    doc = enriched(doc)
    chunks = Chunker(200, 40).chunk(doc)

    check("one chunk per message", len(chunks) == 3, f"got {len(chunks)}")
    check("email_message strategy",
          all(c.chunk_strategy == "email_message" for c in chunks))
    check("message_index preserved",
          [c.message_index for c in chunks] == [1, 2, 3])
    check("message_total set", all(c.message_total == 3 for c in chunks))
    check("total_chunks set", all(c.total_chunks == len(chunks) for c in chunks))
    check("per-message author differs",
          chunks[0].metadata.author != chunks[1].metadata.author)
    check("per-message date attached",
          chunks[1].metadata.message_date == "2026-08-18T14:14:00")
    check("headers embedded in chunk content",
          "From:" in chunks[1].content and "Subject:" in chunks[1].content)
    check("citation includes message number", "#message=" in chunks[0].citation)
    check("document metadata inherited",
          all(c.metadata.tax_topic == "Nexus" for c in chunks))
    check("scenario_id inherited",
          all(c.metadata.scenario_id == "SCN-014" for c in chunks))
    check("source_type overridden to Email Message",
          all(c.metadata.source_type == "Email Message" for c in chunks))
    check("content_hash carried onto chunks",
          all(c.content_hash == doc.content_hash for c in chunks))

    # chunk_id must not depend on chunk text.
    before = [c.chunk_id for c in Chunker(200, 40).chunk(doc)]
    doc.content = doc.content.replace("payroll", "PAYROLL", 1)
    after = [c.chunk_id for c in Chunker(200, 40).chunk(doc)]
    check("chunk_id stable across content edits", before == after)
    check("chunk_ids unique", len(set(before)) == len(before))

    memo = enriched(make_doc(MEMO, name="memo.pdf"))
    memo_chunks = Chunker(150, 30).chunk(memo)
    check("memo uses token strategy",
          all(c.chunk_strategy == "token" for c in memo_chunks))
    check("memo chunks have no message_index",
          all(c.message_index is None for c in memo_chunks))
    check("memo produced multiple chunks", len(memo_chunks) > 1,
          f"got {len(memo_chunks)}")

    budget = Chunker(150, 30)
    check("chunks respect the token budget",
          all(c.token_count <= budget.size * 1.4 for c in budget.chunk(memo)))

    try:
        Chunker(100, 100)
        check("overlap must be smaller than size", False, "no exception")
    except ValueError:
        check("overlap must be smaller than size", True)


def test_search_record() -> None:
    section("SEARCH RECORD CONTRACT")

    doc = enriched(make_doc(THREAD, scenarios=["SCN-014", "SCN-022"]))
    chunk = Chunker(200, 40).chunk(doc)[0]
    record = chunk.to_search_record()

    required = [
        "scenario_id", "source_id", "source_file", "tax_topic", "legal_entity",
        "business_unit", "jurisdiction", "tax_year_or_effective_period",
        "source_date", "source_type", "author", "message_date", "page_number",
        "thread_subject", "authority_level",
        "validity_status", "current_or_superseded", "access_classification",
    ]
    missing = [f for f in required if f not in record]
    check("all 18 action-plan fields present", not missing, str(missing))

    check("record is flat (no nested metadata object)", "metadata" not in record)
    check("governance fields default to n/a",
          all(record[f] == NA for f in
              ("validity_status", "current_or_superseded", "access_classification")))
    check("scenario_ids is a list", isinstance(record["scenario_ids"], list))
    check("content_vector key present (None before embedding)",
          "content_vector" in record and record["content_vector"] is None)
    check("citation present", bool(record["citation"]))
    check("chunk_strategy recorded", record["chunk_strategy"] == "email_message")

    nested = [k for k, v in record.items() if isinstance(v, dict)]
    check("no nested dict values (index requires flat)", not nested, str(nested))


def main() -> int:
    print("\n" + "=" * 70)
    print("AMSTED INGESTION — MODULE TESTS")
    print("=" * 70)
    print(f"  Tokenizer: {TOKENIZER}")

    test_cleaning()
    test_dates()
    test_subjects_and_recipients()
    test_thread_segmentation()
    test_normalization()
    test_chunking()
    test_search_record()

    section(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAILURES:
        print("\n  Failing assertions:")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("\n  All module tests passed.")
    return 0


# pytest entry points
def test_module_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
