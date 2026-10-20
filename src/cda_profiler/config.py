"""Configuration and thresholds for the CDA Statistical Profiler."""

from dataclasses import dataclass, field
from typing import Set


DEFAULT_DISGUISED_NULL_TOKENS: Set[str] = {
    "n/a",
    "na",
    "null",
    "none",
    "unknown",
    "unk",
    "nil",
    "tbd",
    "missing",
    "not available",
    "not applicable",
    "-",
    "--",
    "---",
    "?",
    "99999",
    "999999999",
    "00000",
    "000000000",
    "0000-00-00",
    "00000000",
}


@dataclass
class ProfilerConfig:
    """Runtime settings for profiling a folder of CDA documents."""

    # Minimum share of non-null values (0.0 - 1.0) required to establish an apparent type
    dominant_type_threshold: float = 0.80

    # Minimum share of string values required to establish a dominant character-mask pattern
    dominant_pattern_threshold: float = 0.85

    # Z-score threshold for flagging extreme numerical outliers (requires >= 10 numeric values)
    numeric_outlier_zscore: float = 3.5

    # Maximum anomaly / reservoir samples stored per (field, type) bucket
    max_samples_per_bucket: int = 25

    # Whether to anchor paths inside <section> and clinical statements using LOINC / templateId
    anchor_templates: bool = True

    # Glob pattern for discovering CDA files in the input directory
    file_pattern: str = "*.xml"

    # Whether to recurse into subdirectories
    recursive: bool = True

    # Number of parallel worker processes (0 or None = auto CPU count, 1 = single-process)
    workers: int = 1

    # Lowercase tokens treated as implicit / disguised nulls when appearing as values
    disguised_null_tokens: Set[str] = field(
        default_factory=lambda: set(DEFAULT_DISGUISED_NULL_TOKENS)
    )
