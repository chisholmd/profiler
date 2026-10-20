"""Value classification, HL7 date validation, pattern masking, and xsi:type checks."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Optional, Set, Tuple

from ..models.profile import InferredType

# Precompiled regexes for fast classification
_RE_INTEGER = re.compile(r"^[+-]?\d+$")
_RE_FLOAT = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$|^[+-]?\d+[eE][+-]?\d+$")
_RE_BOOLEAN = re.compile(r"^(?:true|false)$", re.IGNORECASE)
_RE_OID = re.compile(r"^[0-2](?:\.(?:0|[1-9]\d*))+$")
_RE_UUID = re.compile(
    r"^(?:urn:uuid:)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RE_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
# HL7 TS format: YYYY[MM[DD[HH[MM[SS[.S+]]]]]][+/-ZZZZ]
_RE_HL7_TS = re.compile(
    r"^(\d{4}|\d{6}|\d{8}|\d{10}|\d{12}|\d{14})(?:\.(\d+))?(?:([+-]\d{4}))?$"
)
_RE_TIME_PATH = re.compile(
    r"(?:birthtime|effectivetime|/time|deceasedtime)/(?:(?:low|high|center|width)/)?@value$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClassificationResult:
    """Detailed result of classifying a single populated value."""

    inferred_type: InferredType
    numeric_value: Optional[float] = None
    date_precision: Optional[str] = None
    is_valid_date: bool = True
    pattern_mask: str = ""


def is_disguised_null(raw_value: str, null_tokens: Set[str]) -> bool:
    """Return True if a non-empty value matches a known placeholder/sentinel null string."""
    normalized = raw_value.strip().lower()
    return normalized in null_tokens


def compute_pattern_mask(val: str, max_len: int = 64) -> str:
    """
    Convert a string into a run-length encoded character-class mask.
    Example: '90210-1234' -> 'D{5}-D{4}', 'tel:+1-555-0199' -> 'L{3}:+D-D{3}-D{4}'
    """
    trimmed = val.strip()[:max_len]
    if not trimmed:
        return "EMPTY"

    tokens = []
    for ch in trimmed:
        if ch.isdigit():
            tokens.append("D")
        elif ch.isalpha():
            tokens.append("L")
        elif ch.isspace():
            tokens.append("S")
        else:
            tokens.append(ch)

    # Run-length encode consecutive identical tokens ('D', 'L', 'S')
    compressed = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        count = 1
        if tok in ("D", "L", "S"):
            while i + count < n and tokens[i + count] == tok:
                count += 1
            compressed.append(f"{tok}{{{count}}}" if count > 1 else tok)
        else:
            compressed.append(tok)
        i += count

    return "".join(compressed)


def _validate_hl7_ts_digits(digits: str, tz: Optional[str]) -> Tuple[bool, str]:
    """
    Validate calendar components of an HL7 TS digit string.
    Returns (is_valid, precision_label).
    """
    length = len(digits)
    precision_map = {
        4: "YYYY",
        6: "YYYYMM",
        8: "YYYYMMDD",
        10: "YYYYMMDDHH",
        12: "YYYYMMDDHHMM",
        14: "YYYYMMDDHHMMSS",
    }
    precision = precision_map.get(length, f"LEN_{length}")
    if tz:
        precision += "±TZ"

    year = int(digits[0:4])
    if year < 1800 or year > 2150:
        return False, precision

    if length >= 6:
        month = int(digits[4:6])
        if month < 1 or month > 12:
            return False, precision
        if length >= 8:
            day = int(digits[6:8])
            max_day = calendar.monthrange(year, month)[1]
            if day < 1 or day > max_day:
                return False, precision
            if length >= 10:
                hour = int(digits[8:10])
                if hour < 0 or hour > 23:
                    return False, precision
                if length >= 12:
                    minute = int(digits[10:12])
                    if minute < 0 or minute > 59:
                        return False, precision
                    if length >= 14:
                        second = int(digits[12:14])
                        if second < 0 or second > 59:
                            return False, precision

    if tz:
        tz_hh = int(tz[1:3])
        tz_mm = int(tz[3:5])
        if tz_hh > 14 or tz_mm > 59:
            return False, precision

    return True, precision


def _validate_iso_date(val: str) -> bool:
    """Check whether an ISO-formatted date string has valid calendar year/month/day."""
    try:
        year = int(val[0:4])
        month = int(val[5:7])
        day = int(val[8:10])
        if year < 1800 or year > 2150 or month < 1 or month > 12:
            return False
        max_day = calendar.monthrange(year, month)[1]
        return 1 <= day <= max_day
    except (ValueError, IndexError):
        return False


def classify_value(
    raw_value: str,
    canonical_path: str = "",
    declared_xsi_type: str = "",
) -> ClassificationResult:
    """
    Classify a non-empty string value into its most specific InferredType.
    Uses both lexical format and HL7 CDA path context.
    """
    val = raw_value.strip()
    mask = compute_pattern_mask(val)
    is_time_context = bool(_RE_TIME_PATH.search(canonical_path)) or (
        declared_xsi_type.upper().endswith("TS")
    )

    # 1. Check HL7 Timestamp first if on a time path OR if it has explicit HL7 TS structure
    ts_match = _RE_HL7_TS.match(val)
    if ts_match:
        digits, frac, tz = ts_match.groups()
        is_valid_ts, precision = _validate_hl7_ts_digits(digits, tz)
        if frac:
            precision += ".S"

        # If on a time path, any 4/6/8/10/12/14 digit sequence is intended as HL7_DATETIME
        if is_time_context:
            return ClassificationResult(
                inferred_type=InferredType.HL7_DATETIME,
                date_precision=precision,
                is_valid_date=is_valid_ts,
                pattern_mask=mask,
            )

        # Outside a time path, treat 8/12/14 digit strings with explicit TZ/frac or valid YYYYMMDD
        # as HL7_DATETIME unless declared as numeric/code/id
        is_id_or_code_attr = canonical_path.endswith(
            ("/@extension", "/@code", "/@value")
        ) and not is_time_context
        if (tz or frac or (len(digits) in (8, 12, 14) and is_valid_ts)) and not is_id_or_code_attr:
            return ClassificationResult(
                inferred_type=InferredType.HL7_DATETIME,
                date_precision=precision,
                is_valid_date=is_valid_ts,
                pattern_mask=mask,
            )

    # 2. Check ISO-8601 Date/Time (e.g. '1980-05-12')
    if _RE_ISO_DATETIME.match(val):
        valid_iso = _validate_iso_date(val)
        return ClassificationResult(
            inferred_type=InferredType.ISO_DATETIME,
            date_precision="ISO-8601",
            is_valid_date=valid_iso,
            pattern_mask=mask,
        )

    # 3. Check Boolean ('true' / 'false')
    if _RE_BOOLEAN.match(val):
        return ClassificationResult(
            inferred_type=InferredType.BOOLEAN,
            pattern_mask=mask,
        )

    # 4. Check OID ('2.16.840.1.113883...')
    if _RE_OID.match(val) and val.count(".") >= 2:
        return ClassificationResult(
            inferred_type=InferredType.OID,
            pattern_mask="OID",
        )

    # 5. Check UUID
    if _RE_UUID.match(val):
        return ClassificationResult(
            inferred_type=InferredType.UUID,
            pattern_mask="UUID",
        )

    # 6. Check Integer
    if _RE_INTEGER.match(val):
        # Avoid classifying leading-zero codes (like ZIP '02115' or code '01') as INTEGER
        # unless the value is '0' or declared as INT/PQ
        has_leading_zero = len(val.lstrip("+-")) > 1 and val.lstrip("+-").startswith("0")
        is_declared_numeric = declared_xsi_type.upper() in ("INT", "PQ", "REAL", "IVL_PQ", "IVL_INT")
        if not has_leading_zero or is_declared_numeric:
            try:
                num = float(int(val))
                return ClassificationResult(
                    inferred_type=InferredType.INTEGER,
                    numeric_value=num,
                    pattern_mask=mask,
                )
            except ValueError:
                pass

    # 7. Check Float / Decimal
    if _RE_FLOAT.match(val):
        try:
            num = float(val)
            return ClassificationResult(
                inferred_type=InferredType.FLOAT,
                numeric_value=num,
                pattern_mask=mask,
            )
        except ValueError:
            pass

    # 8. Fallback: STRING
    return ClassificationResult(
        inferred_type=InferredType.STRING,
        pattern_mask=mask,
    )


def validate_xsi_type_compatibility(
    declared_xsi_type: str,
    attr_or_elem_path: str,
    inferred_type: InferredType,
) -> Optional[str]:
    """
    Verify whether an observed value's inferred type contradicts an explicit HL7 @xsi:type.
    Returns an error reason string if incompatible, or None if compatible.
    """
    if not declared_xsi_type:
        return None

    # Strip namespace prefix if present (e.g. 'hl7:PQ' -> 'PQ')
    short_xsi = declared_xsi_type.split(":")[-1].upper()

    # Only check primary value attributes or element text
    is_value_attr = attr_or_elem_path.endswith("/@value")
    is_element_text = "/@" not in attr_or_elem_path.split("/")[-1]

    if short_xsi in ("PQ", "REAL", "MO") and is_value_attr:
        if inferred_type not in (InferredType.INTEGER, InferredType.FLOAT):
            return f"Declared xsi:type='{declared_xsi_type}' requires numeric @value, got {inferred_type.value}"

    if short_xsi == "INT" and is_value_attr:
        if inferred_type != InferredType.INTEGER:
            return f"Declared xsi:type='{declared_xsi_type}' requires INTEGER @value, got {inferred_type.value}"

    if short_xsi in ("TS", "IVL_TS", "SXCM_TS") and is_value_attr:
        if inferred_type != InferredType.HL7_DATETIME:
            return f"Declared xsi:type='{declared_xsi_type}' requires HL7_DATETIME @value, got {inferred_type.value}"

    if short_xsi == "BL" and (is_value_attr or is_element_text):
        if inferred_type != InferredType.BOOLEAN:
            return f"Declared xsi:type='{declared_xsi_type}' requires BOOLEAN value, got {inferred_type.value}"

    return None
