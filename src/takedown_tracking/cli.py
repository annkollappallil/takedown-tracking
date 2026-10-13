"""Command-line interface for the Takedown Accountability Platform."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Dict, List, Optional

from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.ingest.importer import RequestImporter
from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.models.observation import ObservationRecord
from takedown_tracking.models.request import parse_iso_datetime
from takedown_tracking.privacy.filter import PrivacyFilter
from takedown_tracking.web.app import run_server


def load_observations_file(path: Optional[str]) -> Optional[Dict[str, ObservationRecord]]:
    """Load observation records from a JSON file into a request_id map."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Observations file not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Observations JSON must be an array of observation records.")
    return {item["request_id"]: ObservationRecord.from_dict(item) for item in data}


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate synthetic request records."""
    generator = SyntheticDataGenerator(seed=args.seed)
    records = generator.generate(count=args.count, duration_days=args.days)

    out_path = Path(args.output)
    if out_path.suffix.lower() == ".csv":
        SyntheticDataGenerator.export_csv(records, out_path)
    else:
        SyntheticDataGenerator.export_json(records, out_path)

    print(f"[OK] Generated {len(records)} synthetic records (seed={args.seed}) -> {out_path}")

    # Generate companion observations if requested
    if getattr(args, "observations_output", None):
        obs_records = generator.generate_observations(records)
        obs_path = Path(args.observations_output)
        obs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(obs_path, "w", encoding="utf-8") as f:
            json.dump([o.to_dict() for o in obs_records], f, indent=2)
        print(f"[OK] Generated {len(obs_records)} companion verification observations -> {obs_path}")

    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Validate and import request records from CSV or JSON."""
    in_path = Path(args.input)
    if in_path.suffix.lower() == ".csv":
        result = RequestImporter.from_csv_file(in_path)
    else:
        result = RequestImporter.from_json_file(in_path)

    summary = result.summary()
    print("=== Request Import & Validation Audit ===")
    print(f"Total rows processed:       {summary['total_rows_processed']}")
    print(f"Valid records accepted:     {summary['valid_records_imported']}")
    print(f"Invalid rows quarantined:   {summary['invalid_rows_quarantined']}")

    if summary["error_breakdown_by_code"]:
        print("\nValidation Error Breakdown:")
        for code, count in summary["error_breakdown_by_code"].items():
            print(f"  - {code}: {count}")

    if args.quarantine and result.failures:
        q_path = Path(args.quarantine)
        q_path.parent.mkdir(parents=True, exist_ok=True)
        with open(q_path, "w", encoding="utf-8") as f:
            json.dump([f.to_dict() for f in result.failures], f, indent=2)
        print(f"\n[OK] Quarantined failures written to: {q_path}")

    return 0 if result.invalid_count == 0 else 1


def cmd_metrics(args: argparse.Namespace) -> int:
    """Compute and display raw performance metrics."""
    in_path = Path(args.input)
    if in_path.suffix.lower() == ".csv":
        result = RequestImporter.from_csv_file(in_path)
    else:
        result = RequestImporter.from_json_file(in_path)

    deadline_cfg = (
        DeadlineConfig.load_from_file(args.deadlines) if args.deadlines else DeadlineConfig()
    )
    as_of = parse_iso_datetime(args.as_of) if args.as_of else None
    observations = load_observations_file(args.observations)

    engine = MetricsEngine(deadline_cfg)
    metrics = engine.compute_performance_metrics(
        result.valid_records,
        invalid_records_count=result.invalid_count,
        as_of=as_of,
        observations=observations,
    )

    print(json.dumps(metrics.to_dict(), indent=2))
    return 0


def cmd_export_scoreboard(args: argparse.Namespace) -> int:
    """Compute metrics with privacy filter and export PublicScoreboard JSON."""
    in_path = Path(args.input)
    if in_path.suffix.lower() == ".csv":
        result = RequestImporter.from_csv_file(in_path)
    else:
        result = RequestImporter.from_json_file(in_path)

    deadline_cfg = (
        DeadlineConfig.load_from_file(args.deadlines) if args.deadlines else DeadlineConfig()
    )
    as_of = parse_iso_datetime(args.as_of) if args.as_of else None
    observations = load_observations_file(args.observations)

    privacy_filter = PrivacyFilter(
        k_threshold=args.k_threshold,
        is_demonstration_mode=not args.production_mode,
        deadline_config=deadline_cfg,
    )

    scoreboard = privacy_filter.filter_and_publish(
        result.valid_records,
        invalid_records_count=result.invalid_count,
        as_of=as_of,
        observations=observations,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scoreboard.to_dict(), f, indent=2)

    print(f"[OK] Exported privacy-filtered scoreboard to {out_path}")
    print(f"  Analyzed: {len(result.valid_records)} records, k-threshold: {args.k_threshold}")
    if as_of:
        print(f"  Cutoff (as_of): {as_of.isoformat()}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run local dashboard and public API server."""
    run_server(
        host=args.host,
        port=args.port,
        data_file=args.data,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="takedown-tracking",
        description="Social Media Takedown Accountability Platform CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen_parser = subparsers.add_parser("generate", help="Generate synthetic request records")
    gen_parser.add_argument("--count", type=int, default=250, help="Number of records to generate")
    gen_parser.add_argument("--seed", type=int, default=42, help="Pseudo-random generator seed")
    gen_parser.add_argument("--days", type=int, default=30, help="Measurement window in days")
    gen_parser.add_argument(
        "--output",
        type=str,
        default="data/synthetic_requests.json",
        help="Target output file path (.json or .csv)",
    )
    gen_parser.add_argument(
        "--observations-output",
        type=str,
        default=None,
        help="Optional companion observations output path (.json)",
    )
    gen_parser.set_defaults(func=cmd_generate)

    # import
    imp_parser = subparsers.add_parser("import", help="Validate and import request records")
    imp_parser.add_argument("--input", type=str, required=True, help="Input CSV or JSON path")
    imp_parser.add_argument(
        "--quarantine",
        type=str,
        default=None,
        help="Optional path to output quarantined validation errors",
    )
    imp_parser.set_defaults(func=cmd_import)

    # metrics
    met_parser = subparsers.add_parser("metrics", help="Compute performance metrics")
    met_parser.add_argument("--input", type=str, required=True, help="Input CSV or JSON path")
    met_parser.add_argument(
        "--deadlines", type=str, default=None, help="Custom deadline config JSON"
    )
    met_parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="ISO 8601 UTC timestamp measurement cutoff (e.g. '2026-10-01T00:00:00Z')",
    )
    met_parser.add_argument(
        "--observations",
        type=str,
        default=None,
        help="Optional path to verification observations JSON file",
    )
    met_parser.set_defaults(func=cmd_metrics)

    # export-scoreboard
    sb_parser = subparsers.add_parser(
        "export-scoreboard", help="Export public privacy-filtered scoreboard"
    )
    sb_parser.add_argument("--input", type=str, required=True, help="Input CSV or JSON path")
    sb_parser.add_argument(
        "--output",
        type=str,
        default="data/public_scoreboard.json",
        help="Target scoreboard JSON path",
    )
    sb_parser.add_argument(
        "--k-threshold",
        type=int,
        default=10,
        help="k-anonymity suppression threshold (default: 10)",
    )
    sb_parser.add_argument(
        "--deadlines", type=str, default=None, help="Custom deadline config JSON"
    )
    sb_parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="ISO 8601 UTC timestamp measurement cutoff (e.g. '2026-10-01T00:00:00Z')",
    )
    sb_parser.add_argument(
        "--observations",
        type=str,
        default=None,
        help="Optional path to verification observations JSON file",
    )
    sb_parser.add_argument(
        "--production-mode",
        action="store_true",
        help="Flag as authorized production deployment rather than demonstration mode",
    )
    sb_parser.set_defaults(func=cmd_export_scoreboard)

    # serve
    srv_parser = subparsers.add_parser("serve", help="Launch public web dashboard and API")
    srv_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address")
    srv_parser.add_argument("--port", type=int, default=8000, help="Port number")
    srv_parser.add_argument(
        "--data", type=str, default=None, help="Optional dataset file to load on start"
    )
    srv_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
