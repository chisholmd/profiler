"""XML streaming parser and CDA context-aware path canonicalizer."""

from .canonicalizer import CanonicalPathBuilder, strip_namespace
from .xml_stream import parse_cda_document

__all__ = [
    "CanonicalPathBuilder",
    "parse_cda_document",
    "strip_namespace",
]
