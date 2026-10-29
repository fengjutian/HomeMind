---
name: novel-analysis
description: Extract and analyze EPUB, PDF, or TXT novels for characters, events, plot causality, and knowledge graphs.
---

# Novel analysis

Use `scripts/extract_novel.py INPUT OUTPUT` before analyzing an uploaded novel. The output is UTF-8 text with stable chapter/page markers suitable for evidence citations.

For long works, process one chapter or a bounded group of pages at a time. Maintain stable entity IDs across batches. Never claim coverage beyond the extracted and analyzed ranges. If PDF extraction yields little or no text, report that OCR is required.

Produce both a human-readable Mermaid graph and structured JSON with `nodes` and `edges`. Every inferred edge must contain a source marker and confidence (`high`, `medium`, or `low`).
