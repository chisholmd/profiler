"""JSON and CSV exporters for field profiles and anomaly audit logs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..models.profile import CorpusProfile


def export_json_profile(profile: CorpusProfile, output_path: Path) -> Path:
    """Write the complete CorpusProfile to a formatted JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(profile.to_dict(), fh, indent=2, ensure_ascii=False)
    return output_path


def export_fields_csv(profile: CorpusProfile, output_path: Path) -> Path:
    """Write a flat CSV table containing one row per profiled CDA field."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "canonical_path",
        "node_kind",
        "total_documents",
        "docs_present",
        "docs_missing",
        "doc_coverage_pct",
        "conditional_missing_pct",
        "effective_missing_pct",
        "total_occurrences",
        "populated_count",
        "null_flavor_total",
        "null_flavor_breakdown",
        "empty_count",
        "disguised_null_count",
        "apparent_type",
        "dominant_pattern_mask",
        "declared_xsi_types",
        "type_distribution",
        "conformance_pct",
        "non_conforming_count",
        "invalid_date_count",
        "xsi_conflict_count",
        "numeric_min",
        "numeric_max",
        "numeric_mean",
        "numeric_std_dev",
        "sample_anomalies",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)

        for fp in profile.field_profiles:
            num = fp.numeric_summary or {}
            nf_str = "; ".join(f"{k}:{v}" for k, v in sorted(fp.null_flavor_breakdown.items()))
            type_str = "; ".join(f"{k}:{v}" for k, v in sorted(fp.type_distribution.items()))
            xsi_str = "; ".join(f"{k}:{v}" for k, v in sorted(fp.declared_xsi_types.items()))
            sample_anom_str = " | ".join(
                f"{a.raw_value!r} ({a.category.value} @ {a.file_name}:{a.line_number})"
                for a in fp.anomalies[:5]
            )

            writer.writerow(
                [
                    fp.canonical_path,
                    fp.node_kind,
                    fp.total_documents,
                    fp.docs_present,
                    fp.docs_missing,
                    f"{fp.doc_coverage_rate * 100:.2f}",
                    f"{fp.conditional_missing_rate * 100:.2f}",
                    f"{fp.effective_missing_rate * 100:.2f}",
                    fp.total_occurrences,
                    fp.populated_count,
                    fp.null_flavor_total,
                    nf_str,
                    fp.empty_count,
                    fp.disguised_null_count,
                    fp.apparent_type,
                    fp.dominant_pattern_mask or "",
                    xsi_str,
                    type_str,
                    f"{fp.conformance_rate * 100:.2f}",
                    fp.non_conforming_count,
                    fp.invalid_date_count,
                    fp.xsi_conflict_count,
                    num.get("min", ""),
                    num.get("max", ""),
                    num.get("mean", ""),
                    num.get("std_dev", ""),
                    sample_anom_str,
                ]
            )

    return output_path


def export_anomalies_csv(profile: CorpusProfile, output_path: Path) -> Path:
    """Write a detailed CSV audit log of all non-conforming and anomalous field values."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "canonical_path",
        "category",
        "file_name",
        "line_number",
        "raw_value",
        "observed_type",
        "expected_type_or_rule",
        "reason",
    ]

    with output_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for anom in profile.all_anomalies:
            writer.writerow(
                [
                    anom.canonical_path,
                    anom.category.value,
                    anom.file_name,
                    anom.line_number,
                    anom.raw_value,
                    anom.observed_type,
                    anom.expected_type_or_rule,
                    anom.reason,
                ]
            )

    return output_path
