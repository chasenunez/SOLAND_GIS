"""Weekly check for updated Swiss geodata.

Reads watchlist.toml, asks each provider whether anything has changed since the
last run, writes a dated report, and downloads only the datasets you have
explicitly marked as safe to fetch.

    python check_updates.py                  # normal run
    python check_updates.py --dry-run        # check and report, never download
    python check_updates.py --config other.toml
    python check_updates.py --only swisstlm3d

Exit codes, so this can drive a scheduled task:
    0   nothing changed and nothing failed
    1   at least one dataset changed
    2   at least one dataset could not be checked

Design notes worth knowing before you change anything:

  Nothing is ever written outside the folder holding watchlist.toml. Downloads go
  to a staging directory and never near the source data, because a checker that
  can overwrite your archive is a checker you have to think about every time it
  runs.

  A dataset seen for the first time is recorded as "new" and does not count as a
  change. Otherwise the first run would report everything as updated, and a
  report that cries wolf once gets ignored forever after.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sources import Http, Observation, SourceError, fetch_checksum, observe

log = logging.getLogger(__name__)

# TOML reading. tomllib is standard from Python 3.11; on older interpreters the
# `tomli` backport is a drop-in. Imported here rather than at the point of use so
# a missing reader fails once, early, with an instruction instead of a traceback.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

# Status values, in the order they appear in the report. Ordering here is the
# single source of truth for report ordering, so adding a status is one edit.
STATUS_ORDER = ("changed", "new", "error", "manual", "unchanged")


@dataclass
class Result:
    """One target, after comparing what we just saw against what we stored."""

    target_id: str
    dataset_id: str
    title: str
    status: str
    detail: str = ""
    previous_tag: str = ""
    current_tag: str = ""
    download_url: str = ""
    downloaded_to: str = ""


def now_utc() -> str:
    """Timestamp used for every 'when did this happen' field, in one format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collapse(text: str) -> str:
    """Flatten whitespace to a single line.

    TOML multi-line strings are the readable way to write a long note in the
    watchlist, but their newlines break Markdown bullets and CSV cells. Collapsing
    at the boundary keeps the config pleasant to edit and the report well formed.
    """
    return " ".join(text.split())


def load_config(path: Path) -> dict:
    if tomllib is None:
        raise SystemExit(
            "No TOML reader available. Use Python 3.11 or later, "
            "or install the backport with: pip install tomli"
        )
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    if "dataset" not in config:
        raise SystemExit(f"{path} contains no [[dataset]] entries")
    return config


def load_state(path: Path) -> dict:
    """Read the record of what we saw last time, tolerating a missing file."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path} is corrupt ({error}). Move it aside and rerun to rebuild.")


def save_state(path: Path, state: dict) -> None:
    """Write state via a temporary file, so an interrupted run cannot corrupt it."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def sha256_of(path: Path) -> str:
    """Hash a file in chunks, so file size does not dictate memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def md5_of(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - integrity check, not a security control
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_download(path: Path, expected: str | None, algorithm: str | None) -> str:
    """Compare a downloaded file against the checksum the provider advertised.

    Returns a short human-readable verdict rather than raising, because a failed
    checksum is worth reporting loudly but is not a reason to abandon the rest of
    the run. Note that data.geo.admin.ch publishes multihash values: sha2-256
    digests carry a '1220' prefix that has to be stripped before comparison.
    """
    if not expected:
        return "no checksum published"

    if algorithm == "md5":
        actual = md5_of(path)
    else:
        actual = sha256_of(path)
        if expected.startswith("1220") and len(expected) == 68:
            expected = expected[4:]  # multihash prefix: 0x12 sha2-256, 0x20 length

    return "checksum ok" if actual.lower() == expected.lower() else "CHECKSUM MISMATCH"


def compare(observation: Observation, stored: dict | None) -> tuple[str, str]:
    """Decide the status of one target. Returns (status, previous tag)."""
    if stored is None:
        return "new", ""
    previous = stored.get("tag", "")
    return ("changed" if previous != observation.tag else "unchanged"), previous


def process_entry(
    entry: dict,
    http: Http,
    state: dict,
    staging_dir: Path,
    dry_run: bool,
    downloads_left: list[int],
) -> list[Result]:
    """Check one watchlist entry and return one Result per target.

    `downloads_left` is a single-element list used as a mutable counter shared
    across entries. It enforces the per-run download cap, which exists because
    geodienste allows one dataset per offering per day and a topic can expand to
    26 cantons.
    """
    dataset_id = entry["id"]
    title = entry.get("title", dataset_id)

    if entry.get("policy") == "manual":
        return [
            Result(
                target_id=dataset_id,
                dataset_id=dataset_id,
                title=title,
                status="manual",
                detail=collapse(entry.get("manual_reason", "no automated check available")),
                download_url=entry.get("url", ""),
            )
        ]

    try:
        observations = observe(entry, http)
    except SourceError as error:
        return [
            Result(
                target_id=dataset_id,
                dataset_id=dataset_id,
                title=title,
                status="error",
                detail=collapse(str(error)),
            )
        ]

    results = []
    for observation in observations:
        status, previous = compare(observation, state.get(observation.target_id))
        result = Result(
            target_id=observation.target_id,
            dataset_id=dataset_id,
            title=title,
            status=status,
            detail=describe(observation),
            previous_tag=previous,
            current_tag=observation.tag,
            download_url=observation.download_url or "",
        )

        should_fetch = (
            status == "changed"
            and entry.get("policy") == "fetch"
            and observation.download_url
            and not dry_run
        )
        if should_fetch and downloads_left[0] <= 0:
            result.detail += " | download skipped: per-run limit reached"
        elif should_fetch:
            downloads_left[0] -= 1
            result.downloaded_to = fetch_one(observation, http, staging_dir, result)

        # Record what we saw, whatever the outcome, so the next run compares
        # against reality rather than against the last successful download.
        state[observation.target_id] = {
            "tag": observation.tag,
            "label": observation.label,
            "title": title,
            "last_checked": now_utc(),
            "last_changed": now_utc() if status in ("changed", "new") else
                            state.get(observation.target_id, {}).get("last_changed", now_utc()),
            "download_url": observation.download_url or "",
        }
        results.append(result)

    return results


def describe(observation: Observation) -> str:
    """One-line human summary of an observation, for the report."""
    parts = [observation.label]
    for key in ("canton", "update_cycle", "publication", "data_reference_date"):
        value = observation.extra.get(key)
        if value:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def fetch_one(observation: Observation, http: Http, staging_dir: Path, result: Result) -> str:
    """Download one changed dataset into staging and verify it.

    The checksum is looked up here rather than during the regular check, because
    it costs a request and is only useful once there is a file to compare it
    against. Only geodienste publishes one; geo.admin entries are notify-only so
    nothing is downloaded, and the plain FOEN zips publish no checksum at all.
    """
    filename = observation.download_url.rstrip("/").split("/")[-1] or "download.bin"
    destination = staging_dir / observation.target_id.replace("/", "_") / filename

    checksum, algorithm = fetch_checksum(observation, http)

    try:
        size = http.download(observation.download_url, destination)
    except SourceError as error:
        result.detail += f" | download failed: {error}"
        return ""

    verdict = verify_download(destination, checksum, algorithm)
    result.detail += f" | downloaded {size:,} bytes, {verdict}"
    if verdict == "CHECKSUM MISMATCH":
        # Loud, and the file is kept: a mismatch is worth looking at, and
        # deleting the evidence makes that harder.
        log.error(
            "%s: checksum mismatch after download. Expected %s (%s). "
            "The file is left at %s so it can be inspected.",
            observation.target_id, checksum, algorithm, destination,
        )
    return str(destination)


def write_reports(results: list[Result], report_dir: Path, started: str) -> tuple[Path, Path]:
    """Write the dated Markdown report and its CSV twin.

    Markdown for a person skimming on a Monday morning, CSV for anyone who wants
    to track the history in a spreadsheet. Same rows, same order.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = started[:10]
    markdown_path = report_dir / f"update_report_{stamp}.md"
    csv_path = report_dir / f"update_report_{stamp}.csv"

    counts = {status: sum(1 for r in results if r.status == status) for status in STATUS_ORDER}
    ordered = sorted(results, key=lambda r: (STATUS_ORDER.index(r.status), r.target_id))

    lines = [
        f"# Dataset update report, {stamp}",
        "",
        f"Run at {started}. Checked {len(results)} targets.",
        "",
        "| Status | Count |",
        "|---|---|",
    ]
    lines += [f"| {status} | {counts[status]} |" for status in STATUS_ORDER if counts[status]]
    lines.append("")

    if counts["changed"]:
        lines += ["## Action needed", "", "These have changed since the last run.", ""]
        for result in ordered:
            if result.status == "changed":
                lines += [
                    f"### {result.title} ({result.target_id})",
                    "",
                    f"- was: `{result.previous_tag}`",
                    f"- now: `{result.current_tag}`",
                    f"- {result.detail}",
                ]
                if result.download_url:
                    lines.append(f"- source: {result.download_url}")
                if result.downloaded_to:
                    lines.append(f"- staged at: `{result.downloaded_to}`")
                lines.append("")

    for heading, status in (
        ("Newly tracked", "new"),
        ("Could not be checked", "error"),
        ("Manual check required", "manual"),
    ):
        if counts[status]:
            lines += [f"## {heading}", ""]
            lines += [
                f"- **{r.title}** (`{r.target_id}`): {r.detail}"
                for r in ordered
                if r.status == status
            ]
            lines.append("")

    if counts["unchanged"]:
        lines += [
            "## Unchanged",
            "",
            f"{counts['unchanged']} targets were unchanged. Listed in the CSV.",
            "",
        ]

    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["run_at", "target_id", "dataset_id", "title", "status",
             "previous_tag", "current_tag", "detail", "download_url", "downloaded_to"]
        )
        for result in ordered:
            writer.writerow([
                started, result.target_id, result.dataset_id, result.title, result.status,
                result.previous_tag, result.current_tag, result.detail,
                result.download_url, result.downloaded_to,
            ])

    return markdown_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "watchlist.toml")
    parser.add_argument("--dry-run", action="store_true", help="check and report, never download")
    parser.add_argument("--only", help="check a single dataset id")
    arguments = parser.parse_args(argv)

    config = load_config(arguments.config)
    settings = config.get("settings", {})
    base = arguments.config.parent  # every path is relative to the config file

    state_path = base / settings.get("state_file", "state.json")
    report_dir = base / settings.get("report_dir", "reports")
    staging_dir = base / settings.get("staging_dir", "staging")

    entries = config["dataset"]
    if arguments.only:
        entries = [e for e in entries if e["id"] == arguments.only]
        if not entries:
            raise SystemExit(f"no dataset with id {arguments.only!r} in {arguments.config}")

    http = Http(
        min_interval=float(settings.get("min_seconds_between_requests", 3.0)),
        timeout=float(settings.get("timeout_seconds", 30.0)),
    )
    state = load_state(state_path)
    downloads_left = [int(settings.get("max_downloads_per_run", 2))]
    started = now_utc()

    results: list[Result] = []
    for entry in entries:
        results.extend(
            process_entry(entry, http, state, staging_dir, arguments.dry_run, downloads_left)
        )

    save_state(state_path, state)
    markdown_path, csv_path = write_reports(results, report_dir, started)

    changed = sum(1 for r in results if r.status == "changed")
    errors = sum(1 for r in results if r.status == "error")
    print(f"{len(results)} targets checked, {changed} changed, {errors} could not be checked")
    print(f"report: {markdown_path}")
    print(f"csv:    {csv_path}")

    if errors:
        return 2
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
