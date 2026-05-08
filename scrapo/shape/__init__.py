"""Document shaping — HTML to LLM-friendly markdown with provenance per chunk."""

from scrapo.shape.chunker import chunk_markdown
from scrapo.shape.markdown import to_markdown
from scrapo.shape.provenance import shape_document

__all__ = ["chunk_markdown", "shape_document", "to_markdown"]
