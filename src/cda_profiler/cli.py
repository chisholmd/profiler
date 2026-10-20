"""Command-line interface and folder orchestration for the CDA Statistical Profiler."""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import List, Optional, Tuple

from .config import ProfilerConfig
from .inference.analyzer import analyze_corpus
from .inference.stats_accumulator import CorpusAccumulator
from .models.profile import CorpusProfile
from .parser.canonicalizer import CanonicalPathBuilder
from .parser.xml_stream import parse_cda_document
from .reporting import (
    export_anomalies_csv,
    export_fields_csv,
    export_json_profile,
    generate_html_report,
    print_console_summary,
)


def _process_file_chunk(
    args: Tuple[List[Path], Path, ProfilerConfig],
) -> CorpusAccumulator:
    """Worker function that parses a chunk of CDA XML files and returns a local accumulator."""
    file_paths, base_dir, config = args
    acc = CorpusAccumulator(config)
    acc.total_files_discovered = len(file_paths)
    path_builder = CanonicalPathBuilder(anchor_templates=config.anchor_templates)

    for fp in file_paths:
        try:
            rel_name = str(fp.relative_to(base_dir))
        except ValueError:
            rel_name = fp.name
        parse_cda_document(
            file_path=fp,
            accumulator=acc,
            path_builder=path_builder,
            display_name=rel_name,
        )
    return acc


def discover_cda_files(
    input_dir: Path,
    pattern: str = "*.xml",
    recursive: bool = True,
) -> List[Path]:
    """Find all matching CDA XML files in `input_dir`."""
    if input_dir.is_file():
        return [input_dir]
    if recursive:
        files = sorted(p for p in input_dir.rglob(pattern) if p.is_file())
    else:
        files = sorted(p for p in input_dir.glob(pattern) if p.is_file())
    return files


def profile_folder(
    input_path: Path | str,
    config: Optional[ProfilerConfig] = None,
) -> CorpusProfile:
    """
    Scan a folder of CDA XML files and compute a full statistical data quality profile.
    """
    cfg = config or ProfilerConfig()
    base_dir = Path(input_path).resolve()
    if not base_dir.exists():
        raise FileNotFoundError(f"Input path does not exist: {base_dir}")

    files = discover_cda_files(
        base_dir,
        pattern=cfg.file_pattern,
        recursive=cfg.recursive,
    )
    anchor_root = base_dir if base_dir.is_dir() else base_dir.parent

    workers = cfg.workers
    if workers is None or workers <= 0:
        workers = os.cpu_count() or 1

    # Use single process for small file counts to avoid process spawn overhead on Windows
    if workers == 1 or len(files) < 16:
        master_acc = _process_file_chunk((files, anchor_root, cfg))
    else:
        master_acc = CorpusAccumulator(cfg)
        chunk_size = max(1, (len(files) + workers * 2 - 1) // (workers * 2))
        chunks = [
            (files[i : i + chunk_size], anchor_root, cfg)
            for i in range(0, len(files), chunk_size)
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for worker_acc in pool.map(_process_file_chunk, chunks):
                master_acc.merge(worker_acc)

    return analyze_corpus(
        accumulator=master_acc,
        input_folder=str(base_dir),
        config=cfg,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cda-profiler",
        description=(
            "Statistical data quality and apparent data-type conformance profiler "
            "for folders of HL7 CDA / C-CDA XML documents."
        ),
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Path to the folder of CDA XML files (or a single XML file).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("./profile_output"),
        help="Directory where HTML, CSV, and JSON profile reports will be written (default: ./profile_output).",
    )
    parser.add_argument(
        "--pattern",
        default="*.xml",
        help="File glob pattern to match CDA documents (default: *.xml).",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Do not scan subdirectories recursively.",
    )
    parser.add_argument(
        "--no-anchor-templates",
        action="store_true",
        help="Use raw structural XPaths without anchoring sections/statements by LOINC or templateId.",
    )
    parser.add_argument(
        "--type-threshold",
        type=float,
        default=0.80,
        help="Minimum fraction (0.0-1.0) of values required to establish an apparent type (default: 0.80).",
    )
    parser.add_argument(
        "--pattern-threshold",
        type=float,
        default=0.85,
        help="Minimum fraction (0.0-1.0) of string values required to establish a dominant mask (default: 0.85).",
    )
    parser.add_argument(
        "--zscore-threshold",
        type=float,
        default=3.5,
        help="Z-score threshold for flagging extreme numerical outliers (default: 3.5).",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=25,
        help="Maximum anomaly examples retained per field/type bucket (default: 25).",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=1,
        help="Number of parallel worker processes (0 = all CPU cores, default: 1).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console summary table output.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = ProfilerConfig(
        dominant_type_threshold=args.type_threshold,
        dominant_pattern_threshold=args.pattern_threshold,
        numeric_outlier_zscore=args.zscore_threshold,
        max_samples_per_bucket=args.max_samples,
        anchor_templates=not args.no_anchor_templates,
        file_pattern=args.pattern,
        recursive=not args.no_recursive,
        workers=args.workers,
    )

    try:
        profile = profile_folder(args.input_folder, config=config)
    except FileNotFoundError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if not args.quiet:
        print_console_summary(profile)

    out_dir: Path = args.output_dir.resolve()
    html_path = generate_html_report(profile, out_dir / "cda_profile_report.html")
    fields_csv_path = export_fields_csv(profile, out_dir / "cda_field_profile.csv")
    anomalies_csv_path = export_anomalies_csv(profile, out_dir / "cda_anomalies.csv")
    json_path = export_json_profile(profile, out_dir / "cda_profile_summary.json")

    if not args.quiet:
        print(f"Reports generated in: {out_dir}")
        print(f"  - Interactive HTML : {html_path}")
        print(f"  - Field Profile CSV: {fields_csv_path}")
        print(f"  - Anomaly Audit CSV: {anomalies_csv_path}")
        print(f"  - Full JSON Profile: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
