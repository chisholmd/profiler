"""Core dataclasses for field accumulation, type inference, and final profile output."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class InferredType(str, Enum):
    """Primitive data types inferred from raw CDA text and attribute values."""

    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    HL7_DATETIME = "HL7_DATETIME"
    ISO_DATETIME = "ISO_DATETIME"
    BOOLEAN = "BOOLEAN"
    OID = "OID"
    UUID = "UUID"
    STRING = "STRING"


class AnomalyCategory(str, Enum):
    """Categories of data quality or conformance anomalies discovered in a field."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    XSI_TYPE_CONFLICT = "XSI_TYPE_CONFLICT"
    INVALID_DATE_OR_PRECISION = "INVALID_DATE_OR_PRECISION"
    PATTERN_OUTLIER = "PATTERN_OUTLIER"
    DISGUISED_NULL = "DISGUISED_NULL"
    EMPTY_WITHOUT_NULLFLAVOR = "EMPTY_WITHOUT_NULLFLAVOR"
    NUMERIC_OUTLIER = "NUMERIC_OUTLIER"


@dataclass
class ValueSample:
    """A concrete sample of a value observed at a specific file and line number."""

    file_name: str
    line_number: int
    raw_value: str
    inferred_type: str
    extra_info: str = ""


@dataclass
class AnomalyRecord:
    """A flagged non-conforming or anomalous value with full provenance."""

    canonical_path: str
    category: AnomalyCategory
    file_name: str
    line_number: int
    raw_value: str
    observed_type: str
    expected_type_or_rule: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "category": self.category.value,
            "file_name": self.file_name,
            "line_number": self.line_number,
            "raw_value": self.raw_value,
            "observed_type": self.observed_type,
            "expected_type_or_rule": self.expected_type_or_rule,
            "reason": self.reason,
        }


@dataclass
class NumericStats:
    """Streaming numerical summary using Welford's algorithm for mean/variance."""

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    min_val: float = float("inf")
    max_val: float = float("-inf")

    def update(self, x: float) -> None:
        if math.isnan(x) or math.isinf(x):
            return
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.m2 += delta * delta2
        if x < self.min_val:
            self.min_val = x
        if x > self.max_val:
            self.max_val = x

    def merge(self, other: NumericStats) -> None:
        if other.count == 0:
            return
        if self.count == 0:
            self.count = other.count
            self.mean = other.mean
            self.m2 = other.m2
            self.min_val = other.min_val
            self.max_val = other.max_val
            return
        total = self.count + other.count
        delta = other.mean - self.mean
        self.mean += delta * other.count / total
        self.m2 += other.m2 + (delta ** 2) * self.count * other.count / total
        self.count = total
        self.min_val = min(self.min_val, other.min_val)
        self.max_val = max(self.max_val, other.max_val)

    @property
    def variance(self) -> float:
        return self.m2 / (self.count - 1) if self.count > 1 else 0.0

    @property
    def std_dev(self) -> float:
        return math.sqrt(self.variance)

    def to_dict(self) -> Optional[Dict[str, float]]:
        if self.count == 0:
            return None
        return {
            "count": self.count,
            "mean": round(self.mean, 4),
            "std_dev": round(self.std_dev, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
        }


@dataclass
class FieldAccumulator:
    """Mutable streaming accumulator for a single canonical CDA path."""

    canonical_path: str
    node_kind: str = "element"  # "element" or "attribute"
    parent_path: str = ""

    # Document-level presence tracking
    docs_with_field: int = 0
    docs_with_populated_value: int = 0
    docs_with_null_flavor: int = 0
    max_occurrences_in_single_doc: int = 0

    # Occurrence-level counts
    total_occurrences: int = 0
    populated_count: int = 0
    empty_count: int = 0
    disguised_null_count: int = 0
    null_flavor_counts: Dict[str, int] = field(default_factory=dict)

    # Type & format distributions
    type_counts: Dict[str, int] = field(default_factory=dict)
    xsi_type_counts: Dict[str, int] = field(default_factory=dict)
    pattern_masks: Dict[str, int] = field(default_factory=dict)
    date_precision_counts: Dict[str, int] = field(default_factory=dict)
    invalid_date_count: int = 0
    xsi_conflict_count: int = 0

    # Numeric distribution
    numeric_stats: NumericStats = field(default_factory=NumericStats)

    # Bounded sample reservoirs keyed by bucket name (e.g., "type:STRING", "mask:D{5}", "xsi_conflict")
    sample_reservoirs: Dict[str, List[ValueSample]] = field(default_factory=dict)

    def add_sample(
        self,
        bucket: str,
        sample: ValueSample,
        max_samples: int = 25,
    ) -> None:
        bucket_list = self.sample_reservoirs.setdefault(bucket, [])
        if len(bucket_list) < max_samples:
            bucket_list.append(sample)

    def merge(self, other: FieldAccumulator, max_samples: int = 25) -> None:
        self.docs_with_field += other.docs_with_field
        self.docs_with_populated_value += other.docs_with_populated_value
        self.docs_with_null_flavor += other.docs_with_null_flavor
        self.max_occurrences_in_single_doc = max(
            self.max_occurrences_in_single_doc,
            other.max_occurrences_in_single_doc,
        )
        self.total_occurrences += other.total_occurrences
        self.populated_count += other.populated_count
        self.empty_count += other.empty_count
        self.disguised_null_count += other.disguised_null_count
        self.invalid_date_count += other.invalid_date_count
        self.xsi_conflict_count += other.xsi_conflict_count

        for k, v in other.null_flavor_counts.items():
            self.null_flavor_counts[k] = self.null_flavor_counts.get(k, 0) + v
        for k, v in other.type_counts.items():
            self.type_counts[k] = self.type_counts.get(k, 0) + v
        for k, v in other.xsi_type_counts.items():
            self.xsi_type_counts[k] = self.xsi_type_counts.get(k, 0) + v
        for k, v in other.pattern_masks.items():
            self.pattern_masks[k] = self.pattern_masks.get(k, 0) + v
        for k, v in other.date_precision_counts.items():
            self.date_precision_counts[k] = self.date_precision_counts.get(k, 0) + v

        self.numeric_stats.merge(other.numeric_stats)

        for bucket, samples in other.sample_reservoirs.items():
            dest = self.sample_reservoirs.setdefault(bucket, [])
            remaining = max_samples - len(dest)
            if remaining > 0:
                dest.extend(samples[:remaining])


@dataclass
class FieldProfile:
    """Evaluated statistical profile for a single canonical CDA path."""

    canonical_path: str
    node_kind: str
    total_documents: int

    # Document-level coverage & missingness
    docs_present: int
    docs_missing: int
    doc_coverage_rate: float  # docs_present / total_documents
    parent_docs_present: int
    conditional_missing_rate: float  # missing when parent is present

    # Occurrence-level completeness
    total_occurrences: int
    populated_count: int
    null_flavor_total: int
    null_flavor_breakdown: Dict[str, int]
    empty_count: int
    disguised_null_count: int
    effective_missing_rate: float  # (missing_docs + null_flavor + empty + disguised_null) / expected

    # Apparent data type & conformance
    apparent_type: str
    dominant_pattern_mask: Optional[str]
    declared_xsi_types: Dict[str, int]
    type_distribution: Dict[str, int]
    conformance_rate: float  # 0.0 to 1.0 among populated values
    non_conforming_count: int

    # Date & numeric specifics
    date_precision_distribution: Dict[str, int]
    invalid_date_count: int
    xsi_conflict_count: int
    numeric_summary: Optional[Dict[str, float]]
    max_cardinality_per_doc: int

    # Concrete anomalies discovered for this field
    anomalies: List[AnomalyRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "node_kind": self.node_kind,
            "total_documents": self.total_documents,
            "docs_present": self.docs_present,
            "docs_missing": self.docs_missing,
            "doc_coverage_rate": round(self.doc_coverage_rate, 4),
            "parent_docs_present": self.parent_docs_present,
            "conditional_missing_rate": round(self.conditional_missing_rate, 4),
            "total_occurrences": self.total_occurrences,
            "populated_count": self.populated_count,
            "null_flavor_total": self.null_flavor_total,
            "null_flavor_breakdown": self.null_flavor_breakdown,
            "empty_count": self.empty_count,
            "disguised_null_count": self.disguised_null_count,
            "effective_missing_rate": round(self.effective_missing_rate, 4),
            "apparent_type": self.apparent_type,
            "dominant_pattern_mask": self.dominant_pattern_mask,
            "declared_xsi_types": self.declared_xsi_types,
            "type_distribution": self.type_distribution,
            "conformance_rate": round(self.conformance_rate, 4),
            "non_conforming_count": self.non_conforming_count,
            "date_precision_distribution": self.date_precision_distribution,
            "invalid_date_count": self.invalid_date_count,
            "xsi_conflict_count": self.xsi_conflict_count,
            "numeric_summary": self.numeric_summary,
            "max_cardinality_per_doc": self.max_cardinality_per_doc,
            "anomalies": [a.to_dict() for a in self.anomalies],
        }


@dataclass
class DocumentParseError:
    """Record of an XML file that failed to parse."""

    file_name: str
    error_message: str


@dataclass
class CorpusProfile:
    """Complete statistical profile for a folder of CDA documents."""

    input_folder: str
    total_files_discovered: int
    total_documents_parsed: int
    parse_errors: List[DocumentParseError]
    total_unique_fields: int
    fields_with_missing_values: int
    fields_with_type_anomalies: int
    total_anomaly_instances: int
    field_profiles: List[FieldProfile]
    all_anomalies: List[AnomalyRecord]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": {
                "input_folder": self.input_folder,
                "total_files_discovered": self.total_files_discovered,
                "total_documents_parsed": self.total_documents_parsed,
                "parse_error_count": len(self.parse_errors),
                "total_unique_fields": self.total_unique_fields,
                "fields_with_missing_values": self.fields_with_missing_values,
                "fields_with_type_anomalies": self.fields_with_type_anomalies,
                "total_anomaly_instances": self.total_anomaly_instances,
            },
            "parse_errors": [
                {"file_name": e.file_name, "error_message": e.error_message}
                for e in self.parse_errors
            ],
            "fields": [f.to_dict() for f in self.field_profiles],
            "anomalies": [a.to_dict() for a in self.all_anomalies],
        }
