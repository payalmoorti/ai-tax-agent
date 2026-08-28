# Data model

## NormalizedDocument
`document_id`, `source_file_name`, `source_path`, `document_type`, timestamps, deterministic metadata, content, and semantic enrichment.

## Chunk
`chunk_id`, parent `document_id`, ordinal, content, inherited tax metadata, source lineage, citation, and optional vector.

## Table entity
Partition key `document`, row key/document ID, file name, blob path, document type, processing status, created/updated dates, chunk count, and bounded error text. Chunk content is never stored in Table Storage.

## Search index
Content is searchable and semantic. The vector is configured for HNSW. Jurisdiction, topic, scenario, authority, and document type support filtering/faceting. Citation and lineage fields support source attribution.
