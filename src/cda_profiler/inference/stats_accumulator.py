"""Streaming accumulator for corpus-wide and per-field CDA statistics."""

from __future__ import annotations

from typing import Dict, List, Set

from ..config import ProfilerConfig
from ..models.profile import (
    DocumentParseError,
    FieldAccumulator,
    InferredType,
    ValueSample,
)
from .classifiers import (
    classify_value,
    is_disguised_null,
    validate_xsi_type_compatibility,
)


class CorpusAccumulator:
    """Accumulates field statistics across a batch or entire folder of CDA XML files."""

    def __init__(self, config: ProfilerConfig) -> None:
        self.config = config
        self.total_files_discovered: int = 0
        self.total_documents_parsed: int = 0
        self.parse_errors: List[DocumentParseError] = []
        self.fields: Dict[str, FieldAccumulator] = {}

        # Per-document temporary tracking reset at start of each document
        self._doc_field_counts: Dict[str, int] = {}
        self._doc_populated_fields: Set[str] = set()
        self._doc_null_flavor_fields: Set[str] = set()

    def get_or_create_field(
        self,
        canonical_path: str,
        node_kind: str,
        parent_path: str,
    ) -> FieldAccumulator:
        acc = self.fields.get(canonical_path)
        if acc is None:
            acc = FieldAccumulator(
                canonical_path=canonical_path,
                node_kind=node_kind,
                parent_path=parent_path,
            )
            self.fields[canonical_path] = acc
        return acc

    def begin_document(self) -> None:
        self._doc_field_counts.clear()
        self._doc_populated_fields.clear()
        self._doc_null_flavor_fields.clear()

    def end_document(self) -> None:
        self.total_documents_parsed += 1
        for path, count in self._doc_field_counts.items():
            acc = self.fields[path]
            acc.docs_with_field += 1
            if count > acc.max_occurrences_in_single_doc:
                acc.max_occurrences_in_single_doc = count
        for path in self._doc_populated_fields:
            self.fields[path].docs_with_populated_value += 1
        for path in self._doc_null_flavor_fields:
            self.fields[path].docs_with_null_flavor += 1

    def record_parse_error(self, file_name: str, error_message: str) -> None:
        self.parse_errors.append(
            DocumentParseError(file_name=file_name, error_message=error_message)
        )

    def observe_element(
        self,
        elem_path: str,
        parent_path: str,
        text_value: str,
        attributes: Dict[str, str],
        has_children: bool,
        file_name: str,
        line_number: int,
    ) -> None:
        """Record an XML element, its text content (if any), and all its attributes."""
        elem_acc = self.get_or_create_field(elem_path, "element", parent_path)
        elem_acc.total_occurrences += 1
        self._doc_field_counts[elem_path] = self._doc_field_counts.get(elem_path, 0) + 1

        null_flavor = attributes.get("nullFlavor")
        xsi_type = attributes.get("xsi:type", "")

        if xsi_type:
            elem_acc.xsi_type_counts[xsi_type] = (
                elem_acc.xsi_type_counts.get(xsi_type, 0) + 1
            )

        if null_flavor:
            elem_acc.null_flavor_counts[null_flavor] = (
                elem_acc.null_flavor_counts.get(null_flavor, 0) + 1
            )
            self._doc_null_flavor_fields.add(elem_path)

        # Data-bearing attributes (excluding nullFlavor and xsi:* schema hints)
        data_attrs = {
            k: v
            for k, v in attributes.items()
            if k != "nullFlavor" and not k.startswith("xsi:") and not k.startswith("xmlns")
        }

        stripped_text = text_value.strip()
        if stripped_text:
            self._record_populated_value(
                acc=elem_acc,
                raw_value=stripped_text,
                declared_xsi_type=xsi_type,
                file_name=file_name,
                line_number=line_number,
            )
        elif not has_children and not data_attrs and not null_flavor:
            # Completely empty leaf element (<given></given> or <birthTime/>) without nullFlavor
            elem_acc.empty_count += 1
            elem_acc.add_sample(
                "empty",
                ValueSample(
                    file_name=file_name,
                    line_number=line_number,
                    raw_value="<empty element>",
                    inferred_type="EMPTY",
                ),
                max_samples=self.config.max_samples_per_bucket,
            )

        # Observe each data attribute as its own canonical field (e.g. .../birthTime/@value)
        for attr_name, attr_raw_val in data_attrs.items():
            attr_path = f"{elem_path}/@{attr_name}"
            attr_acc = self.get_or_create_field(attr_path, "attribute", elem_path)
            attr_acc.total_occurrences += 1
            self._doc_field_counts[attr_path] = (
                self._doc_field_counts.get(attr_path, 0) + 1
            )
            if xsi_type:
                attr_acc.xsi_type_counts[xsi_type] = (
                    attr_acc.xsi_type_counts.get(attr_type_key := xsi_type, 0) + 1
                )

            attr_trimmed = attr_raw_val.strip()
            if not attr_trimmed:
                attr_acc.empty_count += 1
                attr_acc.add_sample(
                    "empty",
                    ValueSample(
                        file_name=file_name,
                        line_number=line_number,
                        raw_value='""',
                        inferred_type="EMPTY",
                    ),
                    max_samples=self.config.max_samples_per_bucket,
                )
            else:
                self._record_populated_value(
                    acc=attr_acc,
                    raw_value=attr_trimmed,
                    declared_xsi_type=xsi_type,
                    file_name=file_name,
                    line_number=line_number,
                )

    def _record_populated_value(
        self,
        acc: FieldAccumulator,
        raw_value: str,
        declared_xsi_type: str,
        file_name: str,
        line_number: int,
    ) -> None:
        max_samples = self.config.max_samples_per_bucket

        # 1. Check if this value is a disguised/implicit null placeholder ("N/A", "UNKNOWN", etc.)
        if is_disguised_null(raw_value, self.config.disguised_null_tokens):
            acc.disguised_null_count += 1
            acc.add_sample(
                "disguised_null",
                ValueSample(
                    file_name=file_name,
                    line_number=line_number,
                    raw_value=raw_value,
                    inferred_type="DISGUISED_NULL",
                ),
                max_samples=max_samples,
            )
            return

        # 2. Classify non-null value
        acc.populated_count += 1
        self._doc_populated_fields.add(acc.canonical_path)

        res = classify_value(
            raw_value=raw_value,
            canonical_path=acc.canonical_path,
            declared_xsi_type=declared_xsi_type,
        )
        t_name = res.inferred_type.value
        acc.type_counts[t_name] = acc.type_counts.get(t_name, 0) + 1

        # Record sample in the type bucket so non-conforming types have provenance ready
        acc.add_sample(
            f"type:{t_name}",
            ValueSample(
                file_name=file_name,
                line_number=line_number,
                raw_value=raw_value,
                inferred_type=t_name,
                extra_info=res.date_precision or res.pattern_mask,
            ),
            max_samples=max_samples,
        )

        # 3. Track pattern masks for strings and codes
        if res.pattern_mask:
            acc.pattern_masks[res.pattern_mask] = (
                acc.pattern_masks.get(res.pattern_mask, 0) + 1
            )
            acc.add_sample(
                f"mask:{res.pattern_mask}",
                ValueSample(
                    file_name=file_name,
                    line_number=line_number,
                    raw_value=raw_value,
                    inferred_type=t_name,
                    extra_info=res.pattern_mask,
                ),
                max_samples=max_samples,
            )

        # 4. Track date precisions and calendar validity
        if res.inferred_type in (InferredType.HL7_DATETIME, InferredType.ISO_DATETIME):
            if res.date_precision:
                acc.date_precision_counts[res.date_precision] = (
                    acc.date_precision_counts.get(res.date_precision, 0) + 1
                )
                acc.add_sample(
                    f"date_prec:{res.date_precision}",
                    ValueSample(
                        file_name=file_name,
                        line_number=line_number,
                        raw_value=raw_value,
                        inferred_type=t_name,
                        extra_info=res.date_precision,
                    ),
                    max_samples=max_samples,
                )
            if not res.is_valid_date:
                acc.invalid_date_count += 1
                acc.add_sample(
                    "invalid_date",
                    ValueSample(
                        file_name=file_name,
                        line_number=line_number,
                        raw_value=raw_value,
                        inferred_type=t_name,
                        extra_info=f"Invalid calendar date/time ({res.date_precision})",
                    ),
                    max_samples=max_samples,
                )

        # 5. Track numeric statistics and keep extreme min/max samples
        if res.numeric_value is not None:
            is_new_min = res.numeric_value < acc.numeric_stats.min_val
            is_new_max = res.numeric_value > acc.numeric_stats.max_val
            acc.numeric_stats.update(res.numeric_value)
            if is_new_min or is_new_max or acc.numeric_stats.count <= max_samples:
                acc.add_sample(
                    "numeric_values",
                    ValueSample(
                        file_name=file_name,
                        line_number=line_number,
                        raw_value=raw_value,
                        inferred_type=t_name,
                        extra_info=str(res.numeric_value),
                    ),
                    max_samples=max_samples * 2,
                )

        # 6. Check HL7 @xsi:type conflict
        if declared_xsi_type:
            conflict_reason = validate_xsi_type_compatibility(
                declared_xsi_type=declared_xsi_type,
                attr_or_elem_path=acc.canonical_path,
                inferred_type=res.inferred_type,
            )
            if conflict_reason:
                acc.xsi_conflict_count += 1
                acc.add_sample(
                    "xsi_conflict",
                    ValueSample(
                        file_name=file_name,
                        line_number=line_number,
                        raw_value=raw_value,
                        inferred_type=t_name,
                        extra_info=conflict_reason,
                    ),
                    max_samples=max_samples,
                )

    def merge(self, other: CorpusAccumulator) -> None:
        """Merge another worker's CorpusAccumulator into this instance."""
        self.total_files_discovered += other.total_files_discovered
        self.total_documents_parsed += other.total_documents_parsed
        self.parse_errors.extend(other.parse_errors)
        for path, other_field in other.fields.items():
            if path not in self.fields:
                self.fields[path] = other_field
            else:
                self.fields[path].merge(
                    other_field, max_samples=self.config.max_samples_per_bucket
                )
