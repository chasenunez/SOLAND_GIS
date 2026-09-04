"""Check that a dataset readme written against README_TEMPLATE_v2 is complete
and machine-readable.

Why this exists: a readme is only "Interoperable" in the FAIR sense if software
can read it without a human in the loop. Running this script is how you find out
whether that is still true, rather than assuming it. It is deliberately small and
dependency-free so it keeps working years from now.

Usage:
    python validate_readme.py <readme.txt> [datapackage.json]

Exit code 0 if the readme parses and all required fields are filled, 1 otherwise.
Unresolved [CONFIRM] markers are reported but do not fail the run, because a
readme can be legitimately incomplete while a dataset is still internal.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Fields every readme must carry before it is considered complete. Kept short on
# purpose: a long mandatory list gets worked around rather than filled in.
REQUIRED_FIELDS = (
    "Title",
    "Description",
    "Keywords",
    "Dataset version",
    "Publication status",
    "Temporal coverage",
    "Geographic coverage",
    "Coordinate reference system",
    "Recommended citation",
    "Access level",
    "Derived from another source",
    "File list",
)

# Fields that may stay empty while a dataset is internal, but must be filled
# before external release. Reported separately so the two cases don't blur.
ON_PUBLICATION_FIELDS = (
    "Persistent identifier (DOI)",
    "Intended repository",
    "Metadata record (geocat.ch)",
    "Licence",
)

SECTION_RE = re.compile(r"^[A-Z][A-Z /-]+$")
FIELD_RE = re.compile(r"^([^:#][^:]*):\s*(.*)$")


def parse_readme(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Turn readme text into ({field name: [values]}, warnings).

    Returns a list per field because several fields legitimately repeat, one per
    author or per source. Continuation lines (indented two spaces) and list items
    ("- ") are folded into the field that precedes them, so a caller sees one
    value per occurrence rather than a stream of fragments.

    The warnings matter as much as the fields. A blank line ends a value, so an
    indented line after one has no field to attach to. An earlier version dropped
    those without a word, which is the worst possible behaviour for a tool whose
    whole job is to tell you the file is complete: content could vanish and the
    validator would still report everything as present.
    """
    fields: dict[str, list[str]] = {}
    warnings: list[str] = []
    current: str | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("#"):  # comment line
            continue
        if not raw.strip():  # blank line ends the current value
            current = None
            continue
        if SECTION_RE.match(raw.strip()) and ":" not in raw:
            current = None  # section heading, not a field
            continue

        # Indented text and list items continue the field above them.
        if raw.startswith("  ") or raw.lstrip().startswith("- "):
            if current is None:
                warnings.append(
                    f"line {number}: indented text with no field above it, so it belongs "
                    f"to nothing and is being ignored: {raw.strip()[:60]!r}"
                )
            else:
                fields[current][-1] = (fields[current][-1] + " " + raw.strip()).strip()
            continue

        match = FIELD_RE.match(raw)
        if match:
            current = match.group(1).strip()
            fields.setdefault(current, []).append(match.group(2).strip())
        else:
            current = None
            warnings.append(
                f"line {number}: not a field, a section heading or a continuation, "
                f"so it is being ignored: {raw.strip()[:60]!r}"
            )

    return fields, warnings


def first_value(fields: dict[str, list[str]], name: str) -> str:
    """Return the first value for a field, or an empty string if absent."""
    values = fields.get(name, [])
    return values[0] if values else ""


def find_unfilled(fields: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    """Return the names whose first value is empty or still a placeholder."""
    unfilled = []
    for name in names:
        value = first_value(fields, name)
        if not value or value.startswith("[ON PUBLICATION]"):
            unfilled.append(name)
    return unfilled


def find_confirms(fields: dict[str, list[str]]) -> list[tuple[str, str]]:
    """Return (field, value) pairs still carrying a [CONFIRM] marker."""
    return [
        (name, value)
        for name, values in fields.items()
        for value in values
        if "[CONFIRM]" in value or "[CONFIRM " in value
    ]


def cross_check(fields: dict[str, list[str]], package: dict) -> list[str]:
    """Compare the few values that appear in both files.

    Only checks fields where a mismatch would actively mislead someone. Keeping
    this list short means it stays worth reading when it does complain.
    """
    problems = []
    pairs = (
        ("Title", package.get("title", "")),
        ("Dataset version", package.get("version", "")),
    )
    for field, packaged in pairs:
        written = first_value(fields, field)
        # Compare on collapsed whitespace: the readme wraps long values.
        if " ".join(written.split()) != " ".join(str(packaged).split()):
            problems.append(f"{field}: readme has {written!r}, datapackage has {packaged!r}")
    return problems


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print(__doc__)
        return 1

    readme_path = Path(argv[1])
    fields, warnings = parse_readme(readme_path.read_text(encoding="utf-8"))
    print(f"Parsed {len(fields)} distinct fields from {readme_path.name}\n")

    failed = False

    if warnings:
        print(f"Lines the parser could not place ({len(warnings)}):")
        for warning in warnings:
            print(f"  ! {warning}")
        print()

    missing = find_unfilled(fields, REQUIRED_FIELDS)
    if missing:
        failed = True
        print("MISSING required fields:")
        for name in missing:
            print(f"  - {name}")
    else:
        print("All required fields present.")

    pending = find_unfilled(fields, ON_PUBLICATION_FIELDS)
    if pending:
        print("\nEmpty, allowed while internal, required before release:")
        for name in pending:
            print(f"  - {name}")

    confirms = find_confirms(fields)
    raw_total = readme_path.read_text(encoding="utf-8").count("[CONFIRM")
    if confirms:
        print(f"\nUnresolved [CONFIRM] markers in field values ({len(confirms)}):")
        for name, value in confirms:
            print(f"  - {name}: {value[:90]}")
        # The two counts differ because comment lines are stripped before
        # parsing, and several markers live in the guidance comments rather than
        # in a field value. Reporting only the parsed figure invites someone to
        # grep the file, get a bigger number, and wonder which to trust.
        if raw_total > len(confirms):
            print(
                f"\n  ({raw_total} occurrences of '[CONFIRM' appear in the file overall; "
                f"the {raw_total - len(confirms)} not listed above sit inside guidance "
                "comments rather than field values.)"
            )

    if len(argv) == 3:
        package = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        problems = cross_check(fields, package)
        print()
        if problems:
            failed = True
            print("Mismatches between readme and datapackage.json:")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("Readme and datapackage.json agree on title and version.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
