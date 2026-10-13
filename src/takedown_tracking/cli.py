"""Command-line interface for the Takedown Accountability Platform."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from takedown_tracking.disputes import (
    DisputableMetric,
    DisputeError,
    DisputeReason,
    ReportCycle,
    apply_resolutions,
    check_request_ids,
    load_cycle,
    platform_statement,
    public_dispute_summary,
    save_cycle,
)
from takedown_tracking.generator.synthetic_generator import SyntheticDataGenerator
from takedown_tracking.ingest.importer import RequestImporter
from takedown_tracking.metrics.deadline import DeadlineConfig
from takedown_tracking.metrics.engine import MetricsEngine
from takedown_tracking.integrity.metrics import build_integrity_summary
from takedown_tracking.models.observation import ObservationRecord, ObservationValidationError
from takedown_tracking.models.request import parse_iso_datetime
from takedown_tracking.privacy.filter import PrivacyFilter
from takedown_tracking.verifier import REUPLOAD_DETECTORS, RunnerConfig, simulate_observations
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
    observations: Dict[str, ObservationRecord] = {}
    for index, item in enumerate(data):
        try:
            obs = ObservationRecord.from_dict(item)
        except ObservationValidationError as exc:
            # These messages name the rule broken, never the row's contents.
            raise ValueError(f"Invalid observation at index {index}: {exc}") from None
        except KeyError as exc:
            raise ValueError(f"Invalid observation at index {index}: missing field {exc}") from None
        except (TypeError, ValueError):
            # Enum/date parse errors can echo raw values, so report only the position.
            raise ValueError(f"Invalid observation at index {index}: malformed value") from None
        if obs.request_id in observations:
            raise ValueError(f"Duplicate observation for one request at index {index}")
        observations[obs.request_id] = obs
    return observations


def load_requests(path: str):
    in_path = Path(path)
    if in_path.suffix.lower() == ".csv":
        return RequestImporter.from_csv_file(in_path)
    return RequestImporter.from_json_file(in_path)


def cmd_verify(args: argparse.Namespace) -> int:
    """Run the simulated verifier over synthetic requests and write observations."""
    result = load_requests(args.input)
    records = result.valid_records
    if not records:
        print("[!] No valid request records to verify.")
        return 1
    if not all(r.is_synthetic for r in records):
        print(
            "[!] The simulated verifier only runs on synthetic records. "
            "Real deployments must plug in their own AvailabilityChecker."
        )
        return 2

    as_of = parse_iso_datetime(args.as_of) if args.as_of else None
    if as_of is None:
        as_of = max(r.sent_at for r in records) + timedelta(days=7)

    config = RunnerConfig(
        check_interval_minutes=args.check_interval,
        reupload_window_days=args.reupload_window_days,
    )
    observations = simulate_observations(
        records, as_of, seed=args.seed, config=config, reupload_detector=args.reupload_detector
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump([o.to_dict() for o in observations], f, indent=2)

    by_status = Counter(o.observation_status.value for o in observations)
    print(f"[OK] Verified {len(observations)} takedown requests (as of {as_of.isoformat()}) -> {out_path}")
    for status, count in sorted(by_status.items()):
        print(f"  - {status}: {count}")
    reuploads = sum(1 for o in observations if o.reupload_detected_at is not None)
    print(f"  re-uploads found ({args.reupload_detector} detector): {reuploads}")
    return 0


def cmd_pdq(args: argparse.Namespace) -> int:
    """Compare harmless test images with PDQ. Prints quality and distance, never the hashes."""
    from takedown_tracking.hashing.images import load_image
    from takedown_tracking.hashing.pdq import pdq_dihedral_from_rgb

    try:
        images = [load_image(p) for p in args.images]
    except RuntimeError as exc:
        print(f"[!] {exc}")
        return 2
    hashes = [pdq_dihedral_from_rgb(im.width, im.height, im.pixels) for im in images]
    for path, im, hs in zip(args.images, images, hashes):
        note = "" if hs[0].is_useful else "  (too featureless to match reliably)"
        print(f"{path}: {im.width}x{im.height}, PDQ quality {hs[0].quality}{note}")
    if len(hashes) == 2:
        distance = min(hashes[0][0].distance(h) for h in hashes[1])
        verdict = "MATCH" if distance <= args.threshold else "NO MATCH"
        print(f"Distance {distance} of 256 bits (threshold {args.threshold}): {verdict}")
    return 0


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

    records = result.valid_records
    period_start = period_end = None
    cycle = adjustments = None
    now = _now(args)
    if args.cycle:
        cycle = load_cycle(args.cycle)
        if as_of is not None and as_of != cycle.as_of:
            print("[!] --as-of differs from the report cycle's as_of; leave it out when using --cycle.")
            return 2
        if not args.draft:
            blockers = cycle.publish_blockers(now)
            if blockers:
                print("[!] Cannot publish yet: " + "; ".join(blockers))
                print("    Use --draft to produce the draft for platforms instead.")
                return 3
        records, observations, adjustments = apply_resolutions(cycle, records, observations)
        as_of, period_start, period_end = cycle.as_of, cycle.period_start, cycle.period_end
    elif args.draft:
        print("[!] --draft needs --cycle.")
        return 2

    scoreboard = privacy_filter.filter_and_publish(
        records,
        invalid_records_count=result.invalid_count,
        period_start=period_start,
        period_end=period_end,
        as_of=as_of,
        observations=observations,
    )
    if observations is not None:
        # Same category scope as the rest of the scoreboard
        scoped = [r for r in records if r.category in privacy_filter.published_categories]
        scoreboard.integrity_summary = build_integrity_summary(
            scoped,
            observations,
            as_of or scoreboard.metadata.as_of,
            k=args.k_threshold,
        )
    if cycle is not None:
        summary = scoreboard.integrity_summary or {}
        disputes = public_dispute_summary(cycle, adjustments, draft=args.draft)
        summary["disputes"] = disputes
        summary["disputes_active"] = sum(p["open"] for p in disputes["by_platform"])
        summary["disputes_resolved"] = sum(p["accepted"] + p["rejected"] for p in disputes["by_platform"])
        scoreboard.integrity_summary = summary
        if not args.draft:
            cycle.mark_published(now)
            save_cycle(cycle, args.cycle)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(scoreboard.to_dict(), f, indent=2)

    label = "DRAFT scoreboard (for platform review)" if args.draft else "privacy-filtered scoreboard"
    print(f"[OK] Exported {label} to {out_path}")
    print(f"  Analyzed: {len(records)} records, k-threshold: {args.k_threshold}")
    if cycle is not None and not args.draft:
        print(f"  Report cycle {cycle.cycle_id} marked published.")
    if as_of:
        print(f"  Cutoff (as_of): {as_of.isoformat()}")
    return 0


def _now(args: argparse.Namespace) -> datetime:
    at = getattr(args, "at", None)
    return parse_iso_datetime(at) if at else datetime.now(timezone.utc)


def _dispute_errors(func):
    def wrapper(args: argparse.Namespace) -> int:
        try:
            return func(args)
        except DisputeError as exc:
            print(f"[!] {exc}")
            return 2
    return wrapper


@_dispute_errors
def cmd_dispute_open(args: argparse.Namespace) -> int:
    cycle = ReportCycle(
        cycle_id=args.id,
        period_start=parse_iso_datetime(args.period_start),
        period_end=parse_iso_datetime(args.period_end),
        as_of=parse_iso_datetime(args.as_of),
        draft_shared_at=_now(args),
        dispute_window_days=args.window_days,
    )
    if Path(args.cycle).exists():
        print(f"[!] {args.cycle} already exists; refusing to overwrite a report cycle.")
        return 2
    save_cycle(cycle, args.cycle)
    print(f"[OK] Opened report cycle {cycle.cycle_id}; disputes accepted until {cycle.window_closes_at.isoformat()}")
    return 0


@_dispute_errors
def cmd_dispute_statement(args: argparse.Namespace) -> int:
    cycle = load_cycle(args.cycle)
    records = load_requests(args.input).valid_records
    statement = platform_statement(cycle, records, load_observations_file(args.observations), args.platform)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(statement, indent=2), encoding="utf-8")
    print(f"[OK] Statement for {args.platform}: {len(statement['requests'])} takedown requests -> {out}")
    return 0


@_dispute_errors
def cmd_dispute_raise(args: argparse.Namespace) -> int:
    cycle = load_cycle(args.cycle)
    request_ids = tuple(x.strip() for x in (args.request_ids or "").split(",") if x.strip())
    if request_ids:
        check_request_ids(cycle, load_requests(args.input).valid_records, args.platform, request_ids)
    dispute = cycle.raise_dispute(
        platform=args.platform,
        metric=DisputableMetric(args.metric),
        reason=DisputeReason(args.reason),
        statement=args.statement,
        now=_now(args),
        request_ids=request_ids,
    )
    save_cycle(cycle, args.cycle)
    print(f"[OK] Raised {dispute.dispute_id} ({dispute.reason.value}, {len(dispute.request_ids)} request(s))")
    return 0


@_dispute_errors
def cmd_dispute_resolve(args: argparse.Namespace) -> int:
    cycle = load_cycle(args.cycle)
    corrections = {}
    for item in args.correct or []:
        rid, _, ts = item.partition("=")
        parsed = parse_iso_datetime(ts) if ts else None
        if not rid or parsed is None:
            print("[!] --correct takes REQUEST_ID=ISO_TIMESTAMP")
            return 2
        corrections[rid.strip()] = parsed
    dispute = cycle.resolve(args.id, accept=args.decision == "accept", note=args.note, now=_now(args),
                            corrections=corrections or None)
    save_cycle(cycle, args.cycle)
    print(f"[OK] {dispute.dispute_id} {dispute.status.value}")
    return 0


@_dispute_errors
def cmd_dispute_withdraw(args: argparse.Namespace) -> int:
    cycle = load_cycle(args.cycle)
    dispute = cycle.withdraw(args.id, now=_now(args))
    save_cycle(cycle, args.cycle)
    print(f"[OK] {dispute.dispute_id} withdrawn")
    return 0


def cmd_dispute_list(args: argparse.Namespace) -> int:
    cycle = load_cycle(args.cycle)
    now = _now(args)
    state = "published" if cycle.published_at else ("window open" if cycle.window_open(now) else "window closed")
    print(f"Report cycle {cycle.cycle_id} ({state}; window closes {cycle.window_closes_at.isoformat()})")
    for d in cycle.disputes:
        print(f"  {d.dispute_id}  {d.status.value:9s}  {d.platform:12s}  {d.reason.value:20s}  {d.metric.value}  ({len(d.request_ids)} request(s))")
    blockers = cycle.publish_blockers(now)
    print("Ready to publish." if not blockers else "Not ready to publish: " + "; ".join(blockers))
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

    # verify
    ver_parser = subparsers.add_parser(
        "verify", help="Run the simulated verifier and write observations (synthetic data only)"
    )
    ver_parser.add_argument("--input", type=str, required=True, help="Synthetic request log (.json or .csv)")
    ver_parser.add_argument(
        "--output", type=str, default="data/observations.json", help="Observations output path (.json)"
    )
    ver_parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Cutoff; no checks after this time. Use the same value for export-scoreboard. "
        "Default: 7 days after the latest request.",
    )
    ver_parser.add_argument("--seed", type=int, default=42, help="Simulator seed")
    ver_parser.add_argument("--check-interval", type=float, default=15.0, help="Minutes between checks")
    ver_parser.add_argument(
        "--reupload-window-days", type=float, default=7.0, help="Days after removal to scan for re-uploads"
    )
    ver_parser.add_argument(
        "--reupload-detector",
        choices=REUPLOAD_DETECTORS,
        default="simulated",
        help="simulated: read re-uploads from ground truth (fast). "
        "pdq: find them by PDQ hash matching on generated test images (slower, realistic).",
    )
    ver_parser.set_defaults(func=cmd_verify)

    # pdq
    pdq_parser = subparsers.add_parser(
        "pdq", help="Compare test images with PDQ perceptual hashing (needs Pillow; harmless images only)"
    )
    pdq_parser.add_argument("images", nargs="+", help="One image to check quality, or two to compare")
    pdq_parser.add_argument("--threshold", type=int, default=31, help="Match threshold in bits (default 31)")
    pdq_parser.set_defaults(func=cmd_pdq)

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
        "--cycle",
        type=str,
        default=None,
        help="Report cycle file: applies accepted disputes and blocks publishing until all are resolved",
    )
    sb_parser.add_argument(
        "--draft",
        action="store_true",
        help="With --cycle: export the draft for platform review instead of publishing",
    )
    sb_parser.add_argument("--at", type=str, default=None, help="Current time for cycle checks (default now)")
    sb_parser.add_argument(
        "--production-mode",
        action="store_true",
        help="Flag as authorized production deployment rather than demonstration mode",
    )
    sb_parser.set_defaults(func=cmd_export_scoreboard)

    # dispute
    dsp_parser = subparsers.add_parser("dispute", help="Report cycles and platform disputes")
    dsp_sub = dsp_parser.add_subparsers(dest="dispute_command", required=True)

    def add_common(p, at_help="Time of the action (ISO 8601 UTC); default now"):
        p.add_argument("--cycle", type=str, required=True, help="Report cycle file (police-held, JSON)")
        p.add_argument("--at", type=str, default=None, help=at_help)

    d_open = dsp_sub.add_parser("open-cycle", help="Share the draft and open the dispute window")
    add_common(d_open, "When the draft is shared; default now")
    d_open.add_argument("--id", type=str, required=True, help="Cycle ID, e.g. 2026-09")
    d_open.add_argument("--period-start", type=str, required=True)
    d_open.add_argument("--period-end", type=str, required=True)
    d_open.add_argument("--as-of", type=str, required=True)
    d_open.add_argument("--window-days", type=float, default=7.0)
    d_open.set_defaults(func=cmd_dispute_open)

    d_stmt = dsp_sub.add_parser("statement", help="Write one platform's statement of its requests")
    add_common(d_stmt)
    d_stmt.add_argument("--input", type=str, required=True, help="Request log")
    d_stmt.add_argument("--observations", type=str, default=None)
    d_stmt.add_argument("--platform", type=str, required=True)
    d_stmt.add_argument("--output", type=str, required=True)
    d_stmt.set_defaults(func=cmd_dispute_statement)

    d_raise = dsp_sub.add_parser("raise", help="Record a platform's dispute")
    add_common(d_raise)
    d_raise.add_argument("--input", type=str, required=True, help="Request log (to check request IDs)")
    d_raise.add_argument("--platform", type=str, required=True)
    d_raise.add_argument("--metric", choices=[m.value for m in DisputableMetric], required=True)
    d_raise.add_argument("--reason", choices=[r.value for r in DisputeReason], required=True)
    d_raise.add_argument("--statement", type=str, required=True, help="Short explanation, no links")
    d_raise.add_argument("--request-ids", type=str, default="", help="Comma-separated request IDs")
    d_raise.set_defaults(func=cmd_dispute_raise)

    d_res = dsp_sub.add_parser("resolve", help="Accept or reject a dispute")
    add_common(d_res)
    d_res.add_argument("--id", type=str, required=True)
    d_res.add_argument("decision", choices=["accept", "reject"])
    d_res.add_argument("--note", type=str, required=True, help="Reason for the decision, no links")
    d_res.add_argument("--correct", action="append", help="REQUEST_ID=ISO time (accepted wrong_timestamp only)")
    d_res.set_defaults(func=cmd_dispute_resolve)

    d_wd = dsp_sub.add_parser("withdraw", help="Withdraw a dispute on the platform's request")
    add_common(d_wd)
    d_wd.add_argument("--id", type=str, required=True)
    d_wd.set_defaults(func=cmd_dispute_withdraw)

    d_list = dsp_sub.add_parser("list", help="Show disputes and whether the report can be published")
    add_common(d_list)
    d_list.set_defaults(func=cmd_dispute_list)

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
