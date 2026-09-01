"""Pipeline orchestration tests — failure paths, status tracking, artifacts.

Every Azure dependency is stubbed, so this runs offline with no cost. The focus
is the behaviour that is easy to get wrong and hard to notice:

  * a status row must be written even when download or extraction fails
  * skip_enrichment must not collide with the chunker's enrichment guard
  * on_enrichment_failure must control whether a failed classification blocks
  * embedding artifacts must not contain full vectors
  * unsupported files are SKIPPED, not FAILED

    python tests/test_pipeline.py
    python tests/test_pipeline.py -v
    pytest tests/test_pipeline.py
"""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --------------------------------------------------------------------------- #
# Stub external dependencies BEFORE importing the pipeline
# --------------------------------------------------------------------------- #

try:  # pragma: no cover
    import tiktoken

    tiktoken.get_encoding("cl100k_base")
    TOKENIZER = "tiktoken"
except Exception:  # noqa: BLE001
    class _Encoding:
        def encode(self, text):
            return list(range(max(1, len(text) // 4)))

        def decode(self, tokens):
            return "x" * (len(tokens) * 4)

    _tk = types.ModuleType("tiktoken")
    _tk.get_encoding = lambda name: _Encoding()
    sys.modules["tiktoken"] = _tk
    TOKENIZER = "stub (tiktoken unavailable offline)"

for _name, _attrs in {
    "openai": {"AzureOpenAI": object},
    "bs4": {"BeautifulSoup": object},
}.items():
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            _mod = types.ModuleType(_name)
            for _k, _v in _attrs.items():
                setattr(_mod, _k, _v)
            sys.modules[_name] = _mod

if "tenacity" not in sys.modules:
    try:
        import tenacity  # noqa: F401
    except ImportError:
        _tn = types.ModuleType("tenacity")
        _tn.retry = lambda **kwargs: (lambda fn: fn)
        _tn.stop_after_attempt = lambda n: None
        _tn.wait_exponential = lambda **kwargs: None
        sys.modules["tenacity"] = _tn

from amsted_tax_ingestion import pipeline as PL  # noqa: E402
from amsted_tax_ingestion.models import NA  # noqa: E402
from amsted_tax_ingestion.pipeline import Pipeline  # noqa: E402

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
PASSED = FAILED = 0
FAILURES: list[str] = []

OUT = ROOT / "_test_artifacts"
MANIFEST = ROOT / "_test_scenarios.json"


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

THREAD = """Confirming registration for tax year 2024.

From: Tristan Lopez <tristan.lopez@amsted.com>
Sent: Tuesday, August 18, 2026 2:14 PM
To: Payal Moorti <payal.moorti@protiviti.com>
Subject: RE: Illinois nexus

Does the payroll factor change?

Best regards,
Tristan
Direct: (312) 555-0142

From: Payal Moorti <payal.moorti@protiviti.com>
Sent: Monday, August 17, 2026 9:02 AM
To: Tristan Lopez <tristan.lopez@amsted.com>
Subject: Illinois nexus

Under PL 86-272 the work exceeds solicitation.
"""


class Settings:
    azure_storage_connection_string = "stub-connection-string"
    azure_blob_container_name = "raw"
    azure_table_name = "TaxAgentIngestionStatus"
    azure_search_index_name = "amsted-tax-documents"
    azure_search_endpoint = "https://stub.search.windows.net"
    azure_search_api_key = "stub"
    azure_openai_endpoint = "https://stub.openai.azure.com"
    azure_openai_api_key = "stub"
    azure_openai_api_version = "2024-10-21"
    embedding_deployment_name = "text-embedding-3-large"
    embedding_dimensions = 3072
    embedding_batch_size = 16
    chat_deployment_name = "gpt-4o-mini"
    chunk_size_tokens = 200
    chunk_overlap_tokens = 40
    search_upload_batch_size = 100
    output_dir = OUT
    log_level = "CRITICAL"
    scenario_manifest_path = MANIFEST

    def require(self, *names):
        return self


class Blob:
    def __init__(self, name):
        self.name = name
        self.last_modified = "2026-08-20T10:00:00Z"


STATUS_LOG: list[tuple[str, str]] = []


class StubStatusStore:
    def update(self, document_id, file_name, blob_path, document_type,
               status, chunk_count=0, error=""):
        STATUS_LOG.append((blob_path, status))


class StubBlobSource:
    """mode: ok | download_fail"""

    def __init__(self, mode="ok", blob_name="memos/thread.pdf"):
        self.mode = mode
        self.blob_name = blob_name

    def list(self, prefix=""):
        return [Blob(self.blob_name)]

    def download(self, name, target_dir):
        if self.mode == "download_fail":
            raise IOError("403 Forbidden — check the SAS token")
        target = Path(target_dir) / Path(name).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(THREAD, encoding="utf-8")
        return target


class StubRouter:
    """mode: ok | extract_fail"""

    mode = "ok"

    def extract(self, path):
        from amsted_tax_ingestion.models import ExtractedDocument

        if StubRouter.mode == "extract_fail":
            raise ValueError("Docling failed: corrupt or unreadable PDF")
        return ExtractedDocument(
            text=path.read_text(encoding="utf-8"), extractor="docling", page_count=2
        )


class StubEnricher:
    """mode: ok | fail | raise"""

    mode = "ok"

    def __init__(self, settings):
        pass

    def enrich(self, doc):
        if StubEnricher.mode == "raise":
            raise RuntimeError("Azure OpenAI returned 500")
        if StubEnricher.mode == "fail":
            doc.metadata.enrichment_status = "failed"
            doc.metadata.enrichment_rationale = "Rate limited after 3 attempts"
            return doc
        doc.metadata = doc.metadata.model_copy(update={
            "enrichment_status": "ok",
            "enrichment_confidence": 0.91,
            "tax_topic": "Nexus",
            "jurisdiction": "Illinois",
            "authority_level": "Internal Email",
        })
        return doc


class StubEmbedder:
    def __init__(self, settings):
        pass

    def apply(self, chunks):
        for chunk in chunks:
            chunk.content_vector = [0.01] * 8
        return chunks


UPLOADED: list[int] = []


def stub_upload(settings, chunks):
    UPLOADED.append(len(chunks))
    return {"succeeded": len(chunks), "failed": 0}


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

def run(*, source=None, router="ok", enrich="ok", on_fail="index_anyway", **kwargs):
    """Run the pipeline with stubs installed."""
    STATUS_LOG.clear()
    UPLOADED.clear()
    shutil.rmtree(OUT, ignore_errors=True)

    StubRouter.mode = router
    StubEnricher.mode = enrich

    PL.BlobSource = lambda *a, **k: (source or StubBlobSource())
    PL.StatusStore = lambda *a, **k: StubStatusStore()
    PL.ExtractionRouter = StubRouter
    PL.MetadataEnricher = StubEnricher
    PL.EmbeddingGenerator = StubEmbedder
    PL.upload = stub_upload

    return Pipeline(Settings(), on_enrichment_failure=on_fail).run(**kwargs)


def artifact(stage):
    directory = OUT / stage
    return sorted(directory.glob("*.json")) if directory.exists() else []


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_download_failure() -> None:
    section("DOWNLOAD FAILURE — status must still be written")
    result = run(source=StubBlobSource("download_fail"), dry_run=True)

    check("counted as failed", result.failed == 1)
    check("nothing succeeded", result.succeeded == 0)
    check("FAILED status recorded",
          any(s == PL.FAILED for _, s in STATUS_LOG), str(STATUS_LOG))
    check("DISCOVERED recorded before the failure",
          any(s == PL.DISCOVERED for _, s in STATUS_LOG))
    check("error artifact written",
          any(p.name.endswith(".error.json") for p in artifact("indexing_logs")))
    check("failure captured in result", len(result.failures) == 1)


def test_extraction_failure() -> None:
    section("EXTRACTION FAILURE — status must still be written")
    result = run(router="extract_fail", dry_run=True)

    check("counted as failed", result.failed == 1)
    check("FAILED status recorded", any(s == PL.FAILED for _, s in STATUS_LOG))
    check("DOWNLOADED recorded first",
          any(s == PL.DOWNLOADED for _, s in STATUS_LOG))
    check("error message surfaced",
          "Docling" in result.failures[0]["error"], str(result.failures))


def test_skip_enrichment() -> None:
    section("SKIP ENRICHMENT — must not trip the chunker guard")
    result = run(dry_run=True, skip_enrichment=True)

    check("does not fail on the enrichment guard", result.failed == 0,
          str(result.failures))
    check("document succeeded", result.succeeded == 1)
    check("chunks produced", result.chunks_created > 0)
    check("no enrichment failure counted", result.enrichment_failures == 0)

    enriched = artifact("enriched")
    if enriched:
        data = json.loads(enriched[0].read_text())
        check("enrichment_status is 'skipped'",
              data["metadata"]["enrichment_status"] == "skipped",
              data["metadata"]["enrichment_status"])
        check("semantic metadata left n/a",
              data["metadata"]["tax_topic"] == NA)


def test_enrichment_failure_index_anyway() -> None:
    section("ENRICHMENT FAILURE — index_anyway")
    result = run(enrich="fail", dry_run=True, on_fail="index_anyway")

    check("document still succeeds", result.succeeded == 1, str(result.failures))
    check("enrichment failure counted", result.enrichment_failures == 1)
    check("ENRICH_FAILED status recorded",
          any(s == PL.ENRICH_FAILED for _, s in STATUS_LOG), str(STATUS_LOG))
    check("chunks still created", result.chunks_created > 0)


def test_enrichment_failure_strict() -> None:
    section("ENRICHMENT FAILURE — fail")
    result = run(enrich="fail", dry_run=True, on_fail="fail")

    check("document is blocked", result.failed == 1)
    check("nothing succeeded", result.succeeded == 0)
    check("enrichment failure counted", result.enrichment_failures == 1)


def test_enrichment_exception() -> None:
    section("ENRICHMENT RAISES — must be caught")
    result = run(enrich="raise", dry_run=True, on_fail="index_anyway")

    check("run does not crash", isinstance(result.failed, int))
    check("recorded as a failure", result.failed == 1)
    check("FAILED status recorded", any(s == PL.FAILED for _, s in STATUS_LOG))


def test_unsupported_file() -> None:
    section("UNSUPPORTED FILE — skipped, not failed")
    result = run(source=StubBlobSource(blob_name="memos/archive.zip"), dry_run=True)

    check("counted as skipped", result.skipped == 1)
    check("not counted as failed", result.failed == 0)
    check("SKIPPED status recorded",
          any(s == PL.SKIPPED for _, s in STATUS_LOG), str(STATUS_LOG))


def test_dry_run() -> None:
    section("DRY RUN — no embeddings, no indexing")
    result = run(dry_run=True)

    check("document succeeded", result.succeeded == 1)
    check("chunks created", result.chunks_created > 0)
    check("nothing indexed", result.chunks_indexed == 0)
    check("upload never called", not UPLOADED)
    check("DRY_RUN status recorded",
          any(s == PL.DRY_RUN for _, s in STATUS_LOG), str(STATUS_LOG))
    check("no embeddings artifact", not artifact("embeddings"))


def test_full_run() -> None:
    section("FULL RUN — progressive status and artifacts")
    MANIFEST.write_text(json.dumps({"memos/thread.pdf": ["SCN-014", "SCN-022"]}),
                        encoding="utf-8")
    try:
        result = run(dry_run=False)
    finally:
        MANIFEST.unlink(missing_ok=True)

    check("document succeeded", result.succeeded == 1, str(result.failures))
    check("chunks indexed", result.chunks_indexed > 0)
    check("upload called once", len(UPLOADED) == 1)

    states = [s for _, s in STATUS_LOG]
    expected = [PL.DISCOVERED, PL.DOWNLOADED, PL.EXTRACTED, PL.NORMALIZED, PL.ENRICHED]
    check("progressive status sequence", states[:5] == expected, str(states))
    check("terminal status is INDEXED", states[-1] == PL.INDEXED, str(states[-1:]))

    for stage in ("extracted", "normalized", "enriched", "chunked",
                  "embeddings", "indexing_logs"):
        check(f"{stage} artifact written", bool(artifact(stage)))

    normalized = artifact("normalized")
    if normalized:
        data = json.loads(normalized[0].read_text())
        check("scenario_ids read from the manifest",
              data["metadata"]["scenario_ids"] == ["SCN-014", "SCN-022"],
              str(data["metadata"]["scenario_ids"]))
        check("source_path includes the container",
              data["source_path"].startswith("raw/"), data["source_path"])
        check("thread detected inside the PDF", data["is_email_thread"] is True)

    embeddings = artifact("embeddings")
    if embeddings:
        vectors = json.loads(embeddings[0].read_text())
        check("embedding artifact omits full vectors",
              "content_vector" not in vectors[0])
        check("embedding artifact records dimensions",
              "vector_dimensions" in vectors[0])

    summary = OUT / "indexing_logs" / "_run_summary.json"
    check("run summary written", summary.exists())
    if summary.exists():
        data = json.loads(summary.read_text())
        check("summary counts are correct",
              data["succeeded"] == 1 and data["failed"] == 0)


def test_limit_and_prefix() -> None:
    section("LIMIT AND PREFIX")

    class MultiBlobSource(StubBlobSource):
        def list(self, prefix=""):
            names = ["memos/a.pdf", "memos/b.pdf", "threads/c.pdf"]
            if prefix:
                names = [n for n in names if n.startswith(prefix)]
            return [Blob(n) for n in names]

    result = run(source=MultiBlobSource(), dry_run=True, limit=2)
    check("limit honoured", result.discovered == 2, f"got {result.discovered}")

    result = run(source=MultiBlobSource(), dry_run=True, prefix="memos/")
    check("prefix honoured", result.discovered == 2, f"got {result.discovered}")


def main() -> int:
    print("\n" + "=" * 70)
    print("AMSTED INGESTION — PIPELINE TESTS")
    print("=" * 70)
    print(f"  Tokenizer: {TOKENIZER}")
    print(f"  Artifacts: {OUT}")

    try:
        test_download_failure()
        test_extraction_failure()
        test_skip_enrichment()
        test_enrichment_failure_index_anyway()
        test_enrichment_failure_strict()
        test_enrichment_exception()
        test_unsupported_file()
        test_dry_run()
        test_full_run()
        test_limit_and_prefix()
    finally:
        shutil.rmtree(OUT, ignore_errors=True)
        MANIFEST.unlink(missing_ok=True)

    section(f"RESULT: {PASSED} passed, {FAILED} failed")
    if FAILURES:
        print("\n  Failing assertions:")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("\n  All pipeline tests passed.")
    return 0


# pytest entry point
def test_pipeline_suite():
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
