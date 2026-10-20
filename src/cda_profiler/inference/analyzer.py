"""Post-accumulation statistical analysis, type inference, and anomaly generation."""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from ..config import ProfilerConfig
from ..models.profile import (
    AnomalyCategory,
    AnomalyRecord,
    CorpusProfile,
    FieldAccumulator,
    FieldProfile,
    InferredType,
)
from .stats_accumulator import CorpusAccumulator


def _resolve_apparent_type(
    type_counts: Dict[str, int],
    populated_count: int,
    threshold: float,
) -> Tuple[str, Set[str], int]:
    """
    Determine the apparent (dominant) data type for a field, compatible types,
    and count of non-conforming values based on type distribution.

    Returns:
        (apparent_type_label, compatible_type_names, type_mismatch_count)
    """
    if populated_count == 0 or not type_counts:
        return ("NONE", set(), 0)

    int_count = type_counts.get(InferredType.INTEGER.value, 0)
    float_count = type_counts.get(InferredType.FLOAT.value, 0)

    # Evaluate effective counts with numeric promotion (INTEGER is compatible with FLOAT)
    effective_counts: Dict[str, Tuple[int, Set[str]]] = {}
    for t_name, cnt in type_counts.items():
        effective_counts[t_name] = (cnt, {t_name})

    if float_count > 0 and int_count > 0:
        effective_counts[InferredType.FLOAT.value] = (
            float_count + int_count,
            {InferredType.FLOAT.value, InferredType.INTEGER.value},
        )

    # Pick the candidate type with the highest effective count (preferring specific types over STRING on ties)
    best_type = ""
    best_count = -1
    best_compat: Set[str] = set()

    for t_name, (eff_cnt, compat_set) in effective_counts.items():
        if eff_cnt > best_count or (
            eff_cnt == best_count and best_type == InferredType.STRING.value
        ):
            best_type = t_name
            best_count = eff_cnt
            best_compat = compat_set

    best_share = best_count / populated_count

    if best_share >= threshold:
        mismatch_count = sum(
            cnt for t, cnt in type_counts.items() if t not in best_compat
        )
        return (best_type, best_compat, mismatch_count)

    # If no single type meets the strict threshold, check if there is a majority (>= 50%) non-STRING type
    if best_type != InferredType.STRING.value and best_share >= 0.50:
        mismatch_count = sum(
            cnt for t, cnt in type_counts.items() if t not in best_compat
        )
        return (f"MIXED ({best_type} {best_share:.0%})", best_compat, mismatch_count)

    # Otherwise, treat as heterogeneous / STRING
    return (
        InferredType.STRING.value,
        set(type_counts.keys()),
        0,
    )


def analyze_corpus(
    accumulator: CorpusAccumulator,
    input_folder: str,
    config: Optional[ProfilerConfig] = None,
) -> CorpusProfile:
    """Analyze a populated CorpusAccumulator and generate the final CorpusProfile."""
    cfg = config or accumulator.config
    total_docs = accumulator.total_documents_parsed

    field_profiles: List[FieldProfile] = []
    all_anomalies: List[AnomalyRecord] = []

    for path in sorted(accumulator.fields.keys()):
        acc = accumulator.fields[path]
        parent_acc = accumulator.fields.get(acc.parent_path) if acc.parent_path else None

        # Determine if this is a pure structural container element (never holds text, empty leaf, or nullFlavor)
        is_pure_container = (
            acc.node_kind == "element"
            and acc.populated_count == 0
            and acc.empty_count == 0
            and acc.disguised_null_count == 0
            and not acc.null_flavor_counts
        )
        if is_pure_container and acc.docs_with_field == total_docs:
            # Skip boilerplate 100%-present container wrappers (e.g. /ClinicalDocument, /recordTarget)
            continue

        parent_docs_present = (
            parent_acc.docs_with_field if parent_acc is not None else total_docs
        )

        # Combine direct nullFlavors with parent element's nullFlavors when this is a value attribute
        effective_null_flavors = dict(acc.null_flavor_counts)
        if (
            acc.node_kind == "attribute"
            and parent_acc is not None
            and parent_acc.null_flavor_counts
        ):
            for nf_code, nf_cnt in parent_acc.null_flavor_counts.items():
                effective_null_flavors[nf_code] = (
                    effective_null_flavors.get(nf_code, 0) + nf_cnt
                )

        null_flavor_total = sum(effective_null_flavors.values())
        docs_present = acc.docs_with_field
        docs_missing = max(0, total_docs - docs_present)
        doc_coverage_rate = (docs_present / total_docs) if total_docs > 0 else 0.0

        conditional_missing_docs = max(0, parent_docs_present - docs_present)
        conditional_missing_rate = (
            (conditional_missing_docs / parent_docs_present)
            if parent_docs_present > 0
            else 0.0
        )

        # Effective missing rate across expected occurrences
        expected_occurrences = max(
            total_docs,
            acc.total_occurrences + docs_missing,
        )
        missing_or_null_occurrences = (
            docs_missing
            + null_flavor_total
            + acc.empty_count
            + acc.disguised_null_count
        )
        effective_missing_rate = (
            min(1.0, missing_or_null_occurrences / expected_occurrences)
            if expected_occurrences > 0
            else 0.0
        )

        if is_pure_container:
            apparent_type = "CONTAINER"
            compat_types: Set[str] = set()
            type_mismatch_count = 0
        else:
            apparent_type, compat_types, type_mismatch_count = _resolve_apparent_type(
                type_counts=acc.type_counts,
                populated_count=acc.populated_count,
                threshold=cfg.dominant_type_threshold,
            )

        anomalies: List[AnomalyRecord] = []
        seen_anomaly_keys: Set[Tuple[str, int, str, str]] = set()

        def _add_anomaly(record: AnomalyRecord) -> None:
            key = (
                record.file_name,
                record.line_number,
                record.raw_value,
                record.category.value,
            )
            if key not in seen_anomaly_keys:
                seen_anomaly_keys.add(key)
                anomalies.append(record)
                all_anomalies.append(record)

        # 1. Type Mismatch Anomalies (values whose type != apparent_type)
        if type_mismatch_count > 0:
            apparent_share = (
                (acc.populated_count - type_mismatch_count) / acc.populated_count
                if acc.populated_count > 0
                else 0.0
            )
            for obs_type, cnt in acc.type_counts.items():
                if obs_type not in compat_types:
                    samples = acc.sample_reservoirs.get(f"type:{obs_type}", [])
                    for s in samples:
                        _add_anomaly(
                            AnomalyRecord(
                                canonical_path=path,
                                category=AnomalyCategory.TYPE_MISMATCH,
                                file_name=s.file_name,
                                line_number=s.line_number,
                                raw_value=s.raw_value,
                                observed_type=obs_type,
                                expected_type_or_rule=apparent_type,
                                reason=(
                                    f"Field apparent type is {apparent_type} "
                                    f"({apparent_share:.1%} of populated values), "
                                    f"but found {obs_type} ({cnt} occurrence(s))"
                                ),
                            )
                        )

        # 2. HL7 @xsi:type Conflict Anomalies
        if acc.xsi_conflict_count > 0:
            for s in acc.sample_reservoirs.get("xsi_conflict", []):
                _add_anomaly(
                    AnomalyRecord(
                        canonical_path=path,
                        category=AnomalyCategory.XSI_TYPE_CONFLICT,
                        file_name=s.file_name,
                        line_number=s.line_number,
                        raw_value=s.raw_value,
                        observed_type=s.inferred_type,
                        expected_type_or_rule="xsi:type conformance",
                        reason=s.extra_info,
                    )
                )

        # 3. Invalid Calendar Date or Inconsistent Date Precision Anomalies
        if acc.invalid_date_count > 0:
            for s in acc.sample_reservoirs.get("invalid_date", []):
                _add_anomaly(
                    AnomalyRecord(
                        canonical_path=path,
                        category=AnomalyCategory.INVALID_DATE_OR_PRECISION,
                        file_name=s.file_name,
                        line_number=s.line_number,
                        raw_value=s.raw_value,
                        observed_type=s.inferred_type,
                        expected_type_or_rule="Valid HL7/ISO Calendar Date",
                        reason=s.extra_info,
                    )
                )

        if acc.date_precision_counts and acc.populated_count >= 5:
            total_dates = sum(acc.date_precision_counts.values())
            dom_prec, dom_prec_cnt = max(
                acc.date_precision_counts.items(), key=lambda kv: kv[1]
            )
            if total_dates > 0 and (dom_prec_cnt / total_dates) >= cfg.dominant_pattern_threshold:
                for prec, p_cnt in acc.date_precision_counts.items():
                    if prec != dom_prec:
                        for s in acc.sample_reservoirs.get(f"date_prec:{prec}", []):
                            _add_anomaly(
                                AnomalyRecord(
                                    canonical_path=path,
                                    category=AnomalyCategory.INVALID_DATE_OR_PRECISION,
                                    file_name=s.file_name,
                                    line_number=s.line_number,
                                    raw_value=s.raw_value,
                                    observed_type=f"Precision:{prec}",
                                    expected_type_or_rule=f"Precision:{dom_prec}",
                                    reason=(
                                        f"Inconsistent date precision: {dom_prec_cnt}/{total_dates} "
                                        f"({dom_prec_cnt / total_dates:.1%}) use '{dom_prec}', "
                                        f"but {p_cnt} use '{prec}'"
                                    ),
                                )
                            )

        # 4. Dominant Pattern Mask & Pattern Outliers (for STRING fields)
        dominant_mask: Optional[str] = None
        pattern_outlier_count = 0
        if acc.pattern_masks and acc.populated_count >= 5:
            top_mask, top_mask_cnt = max(
                acc.pattern_masks.items(), key=lambda kv: kv[1]
            )
            mask_share = top_mask_cnt / acc.populated_count
            if mask_share >= cfg.dominant_pattern_threshold:
                dominant_mask = top_mask
                if apparent_type == InferredType.STRING.value:
                    for m_str, m_cnt in acc.pattern_masks.items():
                        if m_str != top_mask:
                            pattern_outlier_count += m_cnt
                            for s in acc.sample_reservoirs.get(f"mask:{m_str}", []):
                                _add_anomaly(
                                    AnomalyRecord(
                                        canonical_path=path,
                                        category=AnomalyCategory.PATTERN_OUTLIER,
                                        file_name=s.file_name,
                                        line_number=s.line_number,
                                        raw_value=s.raw_value,
                                        observed_type=f"Mask:{m_str}",
                                        expected_type_or_rule=f"Mask:{top_mask}",
                                        reason=(
                                            f"Pattern mismatch: {mask_share:.1%} of values match "
                                            f"mask '{top_mask}', but value has mask '{m_str}'"
                                        ),
                                    )
                                )

        # 5. Numeric Z-Score Outliers
        numeric_outlier_count = 0
        if (
            apparent_type in (InferredType.INTEGER.value, InferredType.FLOAT.value)
            and acc.numeric_stats.count >= 10
            and acc.numeric_stats.std_dev > 0
        ):
            mean = acc.numeric_stats.mean
            std = acc.numeric_stats.std_dev
            for s in acc.sample_reservoirs.get("numeric_values", []):
                try:
                    val_num = float(s.extra_info or s.raw_value)
                    z_score = abs(val_num - mean) / std
                    if z_score >= cfg.numeric_outlier_zscore:
                        numeric_outlier_count += 1
                        _add_anomaly(
                            AnomalyRecord(
                                canonical_path=path,
                                category=AnomalyCategory.NUMERIC_OUTLIER,
                                file_name=s.file_name,
                                line_number=s.line_number,
                                raw_value=s.raw_value,
                                observed_type=s.inferred_type,
                                expected_type_or_rule=f"Mean={mean:.2f}, SD={std:.2f}",
                                reason=(
                                    f"Extreme numeric outlier (Z={z_score:.2f} >= "
                                    f"{cfg.numeric_outlier_zscore}, mean={mean:.2f}, sd={std:.2f})"
                                ),
                            )
                        )
                except ValueError:
                    pass

        # 6. Disguised Nulls ("N/A", "UNKNOWN", "99999", etc.)
        if acc.disguised_null_count > 0:
            for s in acc.sample_reservoirs.get("disguised_null", []):
                _add_anomaly(
                    AnomalyRecord(
                        canonical_path=path,
                        category=AnomalyCategory.DISGUISED_NULL,
                        file_name=s.file_name,
                        line_number=s.line_number,
                        raw_value=s.raw_value,
                        observed_type="DISGUISED_NULL",
                        expected_type_or_rule="HL7 @nullFlavor",
                        reason=(
                            f"Sentinel/placeholder null string '{s.raw_value}' used instead "
                            f"of an explicit HL7 @nullFlavor attribute"
                        ),
                    )
                )

        # 7. Empty Elements / Attributes without @nullFlavor
        if acc.empty_count > 0:
            for s in acc.sample_reservoirs.get("empty", []):
                _add_anomaly(
                    AnomalyRecord(
                        canonical_path=path,
                        category=AnomalyCategory.EMPTY_WITHOUT_NULLFLAVOR,
                        file_name=s.file_name,
                        line_number=s.line_number,
                        raw_value=s.raw_value,
                        observed_type="EMPTY",
                        expected_type_or_rule="Populated value or @nullFlavor",
                        reason="Element or attribute is present but empty without @nullFlavor",
                    )
                )

        # Total non-conforming count across type, xsi:type, date validity, and pattern outliers
        non_conforming_count = max(
            type_mismatch_count + acc.invalid_date_count + pattern_outlier_count + numeric_outlier_count,
            acc.xsi_conflict_count,
        )
        non_conforming_count = min(non_conforming_count, acc.populated_count)

        conformance_rate = (
            max(0.0, (acc.populated_count - non_conforming_count) / acc.populated_count)
            if acc.populated_count > 0
            else 1.0
        )

        fp = FieldProfile(
            canonical_path=path,
            node_kind=acc.node_kind,
            total_documents=total_docs,
            docs_present=docs_present,
            docs_missing=docs_missing,
            doc_coverage_rate=doc_coverage_rate,
            parent_docs_present=parent_docs_present,
            conditional_missing_rate=conditional_missing_rate,
            total_occurrences=acc.total_occurrences,
            populated_count=acc.populated_count,
            null_flavor_total=null_flavor_total,
            null_flavor_breakdown=effective_null_flavors,
            empty_count=acc.empty_count,
            disguised_null_count=acc.disguised_null_count,
            effective_missing_rate=effective_missing_rate,
            apparent_type=apparent_type,
            dominant_pattern_mask=dominant_mask,
            declared_xsi_types=dict(acc.xsi_type_counts),
            type_distribution=dict(acc.type_counts),
            conformance_rate=conformance_rate,
            non_conforming_count=non_conforming_count,
            date_precision_distribution=dict(acc.date_precision_counts),
            invalid_date_count=acc.invalid_date_count,
            xsi_conflict_count=acc.xsi_conflict_count,
            numeric_summary=acc.numeric_stats.to_dict(),
            max_cardinality_per_doc=acc.max_occurrences_in_single_doc,
            anomalies=anomalies,
        )
        field_profiles.append(fp)

    fields_with_missing = sum(
        1 for f in field_profiles if f.effective_missing_rate > 0.0
    )
    fields_with_type_anomalies = sum(
        1
        for f in field_profiles
        if f.non_conforming_count > 0 or f.disguised_null_count > 0 or f.empty_count > 0
    )

    return CorpusProfile(
        input_folder=str(input_folder),
        total_files_discovered=accumulator.total_files_discovered,
        total_documents_parsed=total_docs,
        parse_errors=accumulator.parse_errors,
        total_unique_fields=len(field_profiles),
        fields_with_missing_values=fields_with_missing,
        fields_with_type_anomalies=fields_with_type_anomalies,
        total_anomaly_instances=len(all_anomalies),
        field_profiles=field_profiles,
        all_anomalies=all_anomalies,
    )
