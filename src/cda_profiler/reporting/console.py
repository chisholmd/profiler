"""Console summary formatter for terminal output."""

from __future__ import annotations

import sys
from typing import TextIO

from ..models.profile import CorpusProfile


def print_console_summary(profile: CorpusProfile, stream: TextIO = sys.stdout) -> None:
    """Print a concise human-readable statistical summary to the console."""
    sep = "=" * 92
    sub = "-" * 92

    stream.write(f"\n{sep}\n")
    stream.write("CDA STATISTICAL DATA QUALITY & TYPE CONFORMANCE PROFILE\n")
    stream.write(f"{sep}\n")
    stream.write(f"Input Folder             : {profile.input_folder}\n")
    stream.write(
        f"Documents Parsed         : {profile.total_documents_parsed} / {profile.total_files_discovered}"
        f" (Parse Errors: {len(profile.parse_errors)})\n"
    )
    stream.write(f"Unique Fields Profiled   : {profile.total_unique_fields}\n")
    stream.write(f"Fields w/ Missing/Null   : {profile.fields_with_missing_values}\n")
    stream.write(f"Fields w/ Type Anomalies : {profile.fields_with_type_anomalies}\n")
    stream.write(f"Total Anomaly Samples    : {profile.total_anomaly_instances}\n")

    # Section 1: Fields with Non-Conforming Data Types or Schema Violations
    type_issue_fields = [
        fp
        for fp in profile.field_profiles
        if fp.non_conforming_count > 0
        or fp.disguised_null_count > 0
        or fp.empty_count > 0
    ]
    type_issue_fields.sort(
        key=lambda f: (
            f.non_conforming_count + f.disguised_null_count + f.empty_count,
            1.0 - f.conformance_rate,
        ),
        reverse=True,
    )

    stream.write(f"\n{sub}\n")
    stream.write(
        f"TOP FIELDS WITH NON-CONFORMING TYPES / DISGUISED NULLS ({len(type_issue_fields)} fields)\n"
    )
    stream.write(f"{sub}\n")
    if not type_issue_fields:
        stream.write("  None — all populated fields conform 100% to their apparent data types.\n")
    else:
        stream.write(
            f"{'Canonical Field Path':<52} | {'Apparent Type':<13} | {'Conform %':>9} | {'Issues / Examples'}\n"
        )
        stream.write(f"{sub}\n")
        for fp in type_issue_fields[:25]:
            short_path = (
                fp.canonical_path
                if len(fp.canonical_path) <= 52
                else "..." + fp.canonical_path[-49:]
            )
            examples = ", ".join(
                f"{a.raw_value!r} [{a.category.value}]" for a in fp.anomalies[:2]
            )
            stream.write(
                f"{short_path:<52} | {fp.apparent_type:<13} | "
                f"{fp.conformance_rate * 100:>8.1f}% | {examples}\n"
            )

    # Section 2: Fields with Missingness / NullFlavors
    missing_fields = [
        fp for fp in profile.field_profiles if fp.effective_missing_rate > 0.0
    ]
    missing_fields.sort(key=lambda f: f.effective_missing_rate, reverse=True)

    stream.write(f"\n{sub}\n")
    stream.write(
        f"TOP FIELDS WITH MISSING VALUES OR HL7 NULLFLAVORS ({len(missing_fields)} fields)\n"
    )
    stream.write(f"{sub}\n")
    if not missing_fields:
        stream.write("  None — all profiled fields are 100% present and populated.\n")
    else:
        stream.write(
            f"{'Canonical Field Path':<52} | {'Doc Cov %':>9} | {'Missing %':>9} | {'Breakdown'}\n"
        )
        stream.write(f"{sub}\n")
        for fp in missing_fields[:25]:
            short_path = (
                fp.canonical_path
                if len(fp.canonical_path) <= 52
                else "..." + fp.canonical_path[-49:]
            )
            parts = []
            if fp.docs_missing:
                parts.append(f"Omitted:{fp.docs_missing}")
            for nf_k, nf_v in sorted(fp.null_flavor_breakdown.items()):
                parts.append(f"nullFlavor({nf_k}):{nf_v}")
            if fp.empty_count:
                parts.append(f"Empty:{fp.empty_count}")
            if fp.disguised_null_count:
                parts.append(f"DisguisedNull:{fp.disguised_null_count}")
            breakdown_str = ", ".join(parts)
            stream.write(
                f"{short_path:<52} | {fp.doc_coverage_rate * 100:>8.1f}% | "
                f"{fp.effective_missing_rate * 100:>8.1f}% | {breakdown_str}\n"
            )

    stream.write(f"{sep}\n\n")
