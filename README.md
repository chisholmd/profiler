# CDA Statistical Data Profiler (`cda-profiler`)

A zero-dependency Python CLI and library that scans a folder of **HL7 Clinical Document Architecture (CDA / C-CDA)** XML files and generates a statistical profile of:

1. **Missing Fields & HL7 `nullFlavor` Distributions**:
   - Structurally omitted elements/attributes across documents
   - Explicit HL7 `@nullFlavor` codes (`UNK`, `NI`, `NA`, `ASKU`, `OTH`, etc.)—automatically propagating parent element `@nullFlavor` counts onto child value attributes (e.g., `<birthTime nullFlavor="UNK"/>` $\rightarrow$ `birthTime/@value`)
   - Empty tags/attributes without `@nullFlavor` (`<postalCode></postalCode>`, `extension=""`)
   - Disguised / sentinel null strings (`"N/A"`, `"UNKNOWN"`, `"null"`, `"99999"`)
2. **Apparent Data Type Inference & Non-Conforming Members**:
   - Classifies every populated value into `INTEGER`, `FLOAT`, `HL7_DATETIME`, `ISO_DATETIME`, `BOOLEAN`, `OID`, `UUID`, or `STRING`
   - Resolves the dominant (**apparent**) data type per field (with automatic numeric promotion from `INTEGER` $\rightarrow$ `FLOAT`)
   - Flags minority values that violate the apparent type (e.g., `"120/80"` or `"refused"` in a `FLOAT` observation field, or `"1980-05-12"` in an `HL7_DATETIME` field)
3. **HL7 `@xsi:type` Contradictions, Date Validity & Pattern Outliers**:
   - Detects conflicts between declared `@xsi:type` (e.g., `PQ`, `INT`, `TS`, `BL`) and actual attribute values
   - Validates calendar dates inside HL7 timestamps (catching impossible dates like `19750230`) and flags inconsistent date precisions (`YYYY` mixed with `YYYYMMDD`)
   - Computes character-class pattern masks (e.g., `D{5}` for 5-digit ZIP codes) to flag format outliers in `STRING` fields
   - Tracks streaming mean, standard deviation, min, and max (Welford's algorithm) to flag extreme numerical Z-score outliers

---

## Quick Start

You can run the profiler directly via [`profile_cdas.py`](file:///c:/Dev/Profiler/profile_cdas.py) without installing any external packages:

```powershell
python profile_cdas.py C:\path\to\cda_folder -o .\profile_output
```

Or install it as a CLI package:

```powershell
pip install -e .
cda-profiler C:\path\to\cda_folder -o .\profile_output
```

### CLI Options

| Flag | Default | Description |
| :--- | :--- | :--- |
| `input_folder` | *(required)* | Folder of CDA XML files (or path to a single XML file). |
| `-o, --output-dir` | `./profile_output` | Output folder for HTML, CSV, and JSON reports. |
| `--pattern` | `*.xml` | Glob pattern used to discover CDA files. |
| `--no-recursive` | `False` | Do not recurse into subdirectories. |
| `--no-anchor-templates` | `False` | Use raw structural XPaths instead of anchoring sections/observations by LOINC code and `templateId`. |
| `--type-threshold` | `0.80` | Minimum share ($0.0\text{–}1.0$) of populated values required to establish an apparent type. |
| `--pattern-threshold` | `0.85` | Minimum share ($0.0\text{–}1.0$) of strings required to establish a dominant character mask. |
| `--zscore-threshold` | `3.5` | Z-score threshold for flagging extreme numeric outliers. |
| `-w, --workers` | `1` | Number of parallel worker processes (`0` = use all CPU cores). |

---

## Generated Output Files

Each run produces four outputs in `--output-dir`:
1. **`cda_profile_report.html`**: Interactive, self-contained HTML dashboard with search, filter buttons (*All Fields*, *Non-Conforming Types*, *Missing / NullFlavor*), visual progress bars, and an anomaly audit table.
2. **`cda_field_profile.csv`**: Spreadsheet with one row per canonical CDA path, including coverage %, conditional missing %, `nullFlavor` breakdown, apparent data type, conformance %, numeric stats, and sample anomalies.
3. **`cda_anomalies.csv`**: Line-by-line audit log of every non-conforming value, `xsi:type` violation, invalid calendar date, disguised null, and empty tag with exact `file_name` and `line_number`.
4. **`cda_profile_summary.json`**: Complete machine-readable JSON profile.
