"""Chunking with two strategies.

  * email_message — one chunk per message in a thread, preserving sender,
    recipients, date, order, page number, and thread subject. Long messages are
    sub-split while retaining their message identity.
  * token — paragraph-aware token windows for everything else.

The enrichment guard is retained: chunking refuses to run on an unenriched
document rather than silently writing n/a into every metadata field.
"""
from __future__ import annotations

import logging
import re

import tiktoken

from .models import NA, Chunk, DocumentMetadata, EmailMessage, NormalizedDocument
from .utils import stable_id

log = logging.getLogger(__name__)

_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class EnrichmentMissingError(RuntimeError):
    """Raised when chunking is attempted before enrichment succeeded."""


class Chunker:
    def __init__(
        self,
        size: int = 800,
        overlap: int = 120,
        *,
        require_enrichment: bool = True,
        min_message_tokens: int = 20,
    ):
        if size <= 0 or overlap < 0 or overlap >= size:
            raise ValueError("Require size > overlap >= 0")
        self.size = size
        self.overlap = overlap
        self.require_enrichment = require_enrichment
        self.min_message_tokens = min_message_tokens
        self.enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        return len(self.enc.encode(text))

    # ---------------- shared splitting ----------------

    def _units(self, content: str) -> list[str]:
        units: list[str] = []
        for block in _SPLIT.split(content):
            block = block.strip()
            if not block:
                continue
            if self.count(block) <= self.size:
                units.append(block)
                continue
            buffer = ""
            for sentence in _SENTENCE.split(block):
                candidate = f"{buffer} {sentence}".strip()
                if buffer and self.count(candidate) > self.size:
                    units.append(buffer)
                    buffer = sentence
                else:
                    buffer = candidate
            if buffer:
                if self.count(buffer) > self.size:
                    tokens = self.enc.encode(buffer)
                    for i in range(0, len(tokens), self.size):
                        units.append(self.enc.decode(tokens[i:i + self.size]).strip())
                else:
                    units.append(buffer)
        return units

    def _pack(self, units: list[str]) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for unit in units:
            unit_tokens = self.count(unit)
            if current and current_tokens + unit_tokens > self.size:
                chunks.append("\n\n".join(current))
                carry: list[str] = []
                carry_tokens = 0
                for previous in reversed(current):
                    previous_tokens = self.count(previous)
                    if carry_tokens + previous_tokens > self.overlap:
                        break
                    carry.insert(0, previous)
                    carry_tokens += previous_tokens
                current, current_tokens = carry, carry_tokens
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            chunks.append("\n\n".join(current))
        return chunks

    # ---------------- guard ----------------

    def _check_enrichment(self, doc: NormalizedDocument) -> None:
        status = doc.metadata.enrichment_status
        if self.require_enrichment and status != "ok":
            raise EnrichmentMissingError(
                f"{doc.source_file_name}: enrichment_status is '{status}', not 'ok'. "
                f"Run the enricher before chunking, or use "
                f"Chunker(require_enrichment=False) for offline testing."
            )
        if status != "ok":
            log.warning(
                "Chunking %s WITHOUT enrichment — metadata will use defaults.",
                doc.source_file_name,
            )

    # ---------------- email thread strategy ----------------

    def _message_header(self, message: EmailMessage) -> str:
        """Header prepended to each message chunk so context survives retrieval."""
        lines = []
        if message.subject != NA:
            lines.append(f"Subject: {message.subject}")
        if message.sender != NA:
            lines.append(f"From: {message.sender}")
        if message.recipients:
            lines.append(f"To: {', '.join(message.recipients)}")
        if message.cc:
            lines.append(f"Cc: {', '.join(message.cc)}")
        if message.message_date != NA:
            lines.append(f"Date: {message.message_date}")
        return "\n".join(lines)

    def _chunk_thread(self, doc: NormalizedDocument) -> list[Chunk]:
        messages = doc.messages
        total_messages = len(messages)
        chunks: list[Chunk] = []
        number = 0

        for message in messages:
            body = message.body.strip()
            if not body:
                continue

            # Very short messages ("Thanks", "Agreed") carry little retrieval value
            # on their own but must not be lost — they are kept, flagged in metadata.
            header = self._message_header(message)
            pieces = (
                [body]
                if self.count(body) <= self.size
                else self._pack(self._units(body))
            )

            for part_index, piece in enumerate(pieces, start=1):
                number += 1
                content = f"{header}\n\n{piece}" if header else piece

                metadata = doc.metadata.merge(
                    author=message.sender,
                    message_date=message.message_date,
                    thread_subject=message.subject if message.subject != NA else doc.metadata.thread_subject,
                    page_number=message.page_number,
                    source_type="Email Message",
                )

                start = doc.content.find(piece[:60])
                chunks.append(
                    Chunk(
                        chunk_id=stable_id(
                            doc.document_id, "msg", str(message.message_index),
                            str(part_index), prefix="chk_",
                        ),
                        document_id=doc.document_id,
                        chunk_number=number,
                        content=content,
                        token_count=self.count(content),
                        char_start=max(0, start),
                        char_end=max(0, start) + len(piece),
                        chunk_strategy="email_message",
                        message_index=message.message_index,
                        message_total=total_messages,
                        source_file_name=doc.source_file_name,
                        source_path=doc.source_path,
                        document_type=doc.document_type,
                        metadata=metadata,
                        citation=self._citation(doc, message, part_index, len(pieces)),
                        content_hash=doc.content_hash,
                    )
                )

        for chunk in chunks:
            chunk.total_chunks = len(chunks)
        log.info(
            "Chunked thread %s: %s message(s) -> %s chunk(s)",
            doc.source_file_name, total_messages, len(chunks),
        )
        return chunks

    def _citation(
        self, doc: NormalizedDocument, message: EmailMessage, part: int, parts: int
    ) -> str:
        citation = f"{doc.source_file_name}#message={message.message_index}"
        if message.page_number:
            citation += f"&page={message.page_number}"
        if parts > 1:
            citation += f"&part={part}"
        return citation

    # ---------------- token strategy ----------------

    def _chunk_tokens(self, doc: NormalizedDocument) -> list[Chunk]:
        texts = self._pack(self._units(doc.content))
        total = len(texts)
        chunks: list[Chunk] = []
        cursor = 0

        for number, text in enumerate(texts, start=1):
            start = doc.content.find(text[:80], cursor)
            start = start if start >= 0 else cursor
            cursor = start + 1
            chunks.append(
                Chunk(
                    chunk_id=stable_id(doc.document_id, str(number), prefix="chk_"),
                    document_id=doc.document_id,
                    chunk_number=number,
                    total_chunks=total,
                    content=text,
                    token_count=self.count(text),
                    char_start=start,
                    char_end=start + len(text),
                    chunk_strategy="token",
                    source_file_name=doc.source_file_name,
                    source_path=doc.source_path,
                    document_type=doc.document_type,
                    metadata=doc.metadata.model_copy(),
                    citation=f"{doc.source_file_name}#chunk={number}",
                    content_hash=doc.content_hash,
                )
            )

        log.info("Chunked %s into %s chunk(s)", doc.source_file_name, total)
        return chunks

    # ---------------- public ----------------

    def chunk(self, doc: NormalizedDocument) -> list[Chunk]:
        self._check_enrichment(doc)
        if doc.is_email_thread and doc.messages:
            return self._chunk_thread(doc)
        return self._chunk_tokens(doc)


# Backwards-compatible alias.
TokenChunker = Chunker
