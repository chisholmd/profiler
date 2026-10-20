"""Fast C-backed XML parser (pyexpat) with exact line tracking and CDA path anchoring."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from xml.parsers import expat

from ..inference.stats_accumulator import CorpusAccumulator
from .canonicalizer import (
    ANCHORABLE_CLINICAL_STATEMENTS,
    KNOWN_TEMPLATE_IDS,
    CanonicalPathBuilder,
)


def _normalize_expat_name(raw_name: str) -> str:
    """Normalize an expat namespace-separated name ('uri}local' or 'prefix:local')."""
    if "}" in raw_name:
        uri, local = raw_name.rsplit("}", 1)
        uri_lower = uri.lower()
        if "sdtc" in uri_lower:
            return f"sdtc:{local}"
        if "xmlschema-instance" in uri_lower:
            return f"xsi:{local}"
        return local

    if ":" in raw_name:
        prefix, local = raw_name.split(":", 1)
        prefix_lower = prefix.lower()
        if prefix_lower in ("sdtc", "xsi"):
            return f"{prefix_lower}:{local}"
        return local

    return raw_name


class _XmlNode:
    """Lightweight in-memory representation of a single XML element with line provenance."""

    __slots__ = ("tag", "attrib", "line", "text_parts", "children")

    def __init__(self, tag: str, attrib: Dict[str, str], line: int) -> None:
        self.tag: str = tag
        self.attrib: Dict[str, str] = attrib
        self.line: int = line
        self.text_parts: List[str] = []
        self.children: List[_XmlNode] = []


def _parse_xml_tree(file_path: Path) -> _XmlNode:
    """Parse an XML file using C pyexpat and return the root _XmlNode with line numbers."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True

    stack: List[_XmlNode] = []
    root_holder: List[_XmlNode] = []

    def on_start_element(name: str, attrs: Dict[str, str]) -> None:
        norm_tag = _normalize_expat_name(name)
        norm_attrs = {_normalize_expat_name(k): v for k, v in attrs.items()}
        node = _XmlNode(
            tag=norm_tag,
            attrib=norm_attrs,
            line=parser.CurrentLineNumber,
        )
        if stack:
            stack[-1].children.append(node)
        else:
            root_holder.append(node)
        stack.append(node)

    def on_end_element(name: str) -> None:
        if stack:
            stack.pop()

    def on_char_data(data: str) -> None:
        if stack:
            stack[-1].text_parts.append(data)

    parser.StartElementHandler = on_start_element
    parser.EndElementHandler = on_end_element
    parser.CharacterDataHandler = on_char_data

    with file_path.open("rb") as fh:
        parser.ParseFile(fh)

    if not root_holder:
        raise ValueError("Empty XML document (no root element found)")
    return root_holder[0]


def _extract_anchor_metadata(node: _XmlNode) -> tuple[Optional[str], Optional[str]]:
    """
    Inspect direct children of a section or clinical statement to find
    its LOINC section code and/or HL7 templateId/@root.
    """
    section_code: Optional[str] = None
    template_root: Optional[str] = None

    if node.tag == "section" or node.tag in ANCHORABLE_CLINICAL_STATEMENTS:
        candidate_roots: List[str] = []
        for child in node.children:
            if child.tag == "code" and section_code is None:
                section_code = child.attrib.get("code")
            elif child.tag == "templateId":
                root_val = child.attrib.get("root")
                if root_val:
                    candidate_roots.append(root_val)

        if candidate_roots:
            # Prefer a recognized C-CDA templateId if multiple templateIds exist
            for r in candidate_roots:
                if r in KNOWN_TEMPLATE_IDS:
                    template_root = r
                    break
            if template_root is None:
                template_root = candidate_roots[0]

    return section_code, template_root


def parse_cda_document(
    file_path: Path,
    accumulator: CorpusAccumulator,
    path_builder: CanonicalPathBuilder,
    display_name: Optional[str] = None,
) -> bool:
    """
    Parse a single CDA XML document and feed all elements and attributes into `accumulator`.
    Returns True if parsed successfully, False if an XML error occurred.
    """
    doc_label = display_name or file_path.name
    try:
        root = _parse_xml_tree(file_path)
    except (expat.ExpatError, ValueError, OSError) as exc:
        accumulator.record_parse_error(doc_label, str(exc))
        return False

    accumulator.begin_document()

    # Iterative DFS stack: (node, parent_canonical_path)
    stack: List[tuple[_XmlNode, str]] = [(root, "")]
    while stack:
        node, parent_path = stack.pop()
        section_code, template_root = _extract_anchor_metadata(node)
        segment = path_builder.format_segment(
            local_tag=node.tag,
            section_code=section_code,
            template_root=template_root,
        )
        current_path = f"{parent_path}/{segment}"
        text_val = "".join(node.text_parts)

        accumulator.observe_element(
            elem_path=current_path,
            parent_path=parent_path,
            text_value=text_val,
            attributes=node.attrib,
            has_children=bool(node.children),
            file_name=doc_label,
            line_number=node.line,
        )

        # Push children in reverse order so document order is preserved
        for child in reversed(node.children):
            stack.append((child, current_path))

    accumulator.end_document()
    return True
