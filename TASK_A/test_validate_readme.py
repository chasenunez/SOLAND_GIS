"""Tests for the readme validator. Run with: python test_validate_readme.py

The validator's job is to say whether a readme is complete and machine-readable.
That makes its own failure modes unusually costly: a parser that quietly drops a
line will happily report a file as complete when part of it has gone missing.
Most of what follows is aimed at that.

Standard library only, and no files outside a temporary directory.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import validate_readme as validator

MINIMAL = """\
Readme generated on: 2026-07-28

GENERAL INFORMATION

Title: A test dataset
Description:
  First line of the description.
  Second line, folded onto the first.
Keywords: one, two
Dataset version: 1.0
Publication status: internal

COVERAGE

Temporal coverage: 2019-01-01/2025-12-31
Geographic coverage: Switzerland
Coordinate reference system: CH1903+ / LV95, EPSG:2056

CITATION AND FUNDING

Recommended citation: Someone (2026). A test dataset.

SHARING AND ACCESS

Access level: internal

PROVENANCE

Derived from another source: yes

DATA AND FILE OVERVIEW

File list:
  - one.gpkg | GeoPackage | a file
  - two.gpkg | GeoPackage | another file
"""


class ParserTestCase(unittest.TestCase):
    def parse(self, text: str):
        return validator.parse_readme(text)


class TestGrammar(ParserTestCase):
    def test_reads_simple_fields(self) -> None:
        fields, _ = self.parse(MINIMAL)
        self.assertEqual(validator.first_value(fields, "Title"), "A test dataset")
        self.assertEqual(validator.first_value(fields, "Dataset version"), "1.0")

    def test_folds_continuation_lines_into_one_value(self) -> None:
        fields, _ = self.parse(MINIMAL)
        self.assertEqual(
            validator.first_value(fields, "Description"),
            "First line of the description. Second line, folded onto the first.",
        )

    def test_folds_list_items_into_the_field_above(self) -> None:
        fields, _ = self.parse(MINIMAL)
        value = validator.first_value(fields, "File list")
        self.assertIn("one.gpkg", value)
        self.assertIn("two.gpkg", value)

    def test_section_headings_are_not_fields(self) -> None:
        fields, _ = self.parse(MINIMAL)
        for heading in ("GENERAL INFORMATION", "COVERAGE", "PROVENANCE"):
            self.assertNotIn(heading, fields)

    def test_comments_are_ignored(self) -> None:
        fields, warnings = self.parse("# a comment\nTitle: Kept\n")
        self.assertEqual(validator.first_value(fields, "Title"), "Kept")
        self.assertEqual(warnings, [])

    def test_repeated_fields_are_all_kept(self) -> None:
        # Author blocks and source blocks repeat by design, so the parser must
        # not collapse them onto one value.
        text = "Author name: First\nORCID: 1\n\nAuthor name: Second\nORCID: 2\n"
        fields, _ = self.parse(text)
        self.assertEqual(fields["Author name"], ["First", "Second"])
        self.assertEqual(fields["ORCID"], ["1", "2"])

    def test_a_value_containing_a_colon_survives(self) -> None:
        # URLs and Windows paths both contain colons after the field separator.
        fields, _ = self.parse("Source URL: https://example.org/a?b=c\n")
        self.assertEqual(validator.first_value(fields, "Source URL"), "https://example.org/a?b=c")

    def test_an_empty_value_is_kept_as_empty(self) -> None:
        # Distinguishing "present but blank" from absent is the whole point of
        # the blank versus [CONFIRM] convention.
        fields, _ = self.parse("Funding:\n")
        self.assertEqual(fields["Funding"], [""])


class TestNothingIsDroppedSilently(ParserTestCase):
    """The failure mode that matters most: content vanishing without a word."""

    def test_indented_text_with_no_field_above_is_reported(self) -> None:
        # A blank line ends a value, so this indented line belongs to nothing.
        # An earlier version discarded it in silence.
        text = "Title: Something\n\n  orphaned continuation line\n"
        fields, warnings = self.parse(text)
        self.assertEqual(len(warnings), 1)
        self.assertIn("orphaned continuation", warnings[0])
        self.assertIn("line 3", warnings[0])

    def test_a_line_that_is_not_a_field_at_all_is_reported(self) -> None:
        fields, warnings = self.parse("Title: Something\nthis line has no colon\n")
        self.assertEqual(len(warnings), 1)
        self.assertIn("no colon", warnings[0])

    def test_a_clean_file_produces_no_warnings(self) -> None:
        _, warnings = self.parse(MINIMAL)
        self.assertEqual(warnings, [], f"unexpected warnings: {warnings}")

    def test_the_shipped_readme_parses_cleanly(self) -> None:
        # If this fails, the readme has drifted from its own stated grammar.
        readme = Path(__file__).parent / "Eawag_ARA_2019_2025_Readme_v2.txt"
        if not readme.exists():
            self.skipTest("readme not present next to the validator")
        _, warnings = validator.parse_readme(readme.read_text(encoding="utf-8"))
        self.assertEqual(warnings, [], f"readme does not parse cleanly: {warnings[:3]}")


class TestCompletenessChecks(ParserTestCase):
    def test_a_complete_readme_passes(self) -> None:
        fields, _ = self.parse(MINIMAL)
        self.assertEqual(validator.find_unfilled(fields, validator.REQUIRED_FIELDS), [])

    def test_a_missing_required_field_is_named(self) -> None:
        fields, _ = self.parse(MINIMAL.replace("Keywords: one, two\n", ""))
        self.assertIn("Keywords", validator.find_unfilled(fields, validator.REQUIRED_FIELDS))

    def test_an_empty_required_field_counts_as_missing(self) -> None:
        # Present but blank is not filled in.
        fields, _ = self.parse(MINIMAL.replace("Keywords: one, two", "Keywords:"))
        self.assertIn("Keywords", validator.find_unfilled(fields, validator.REQUIRED_FIELDS))

    def test_on_publication_placeholders_count_as_unfilled(self) -> None:
        fields, _ = self.parse("Licence: [ON PUBLICATION]\n")
        self.assertIn("Licence", validator.find_unfilled(fields, ("Licence",)))

    def test_confirm_markers_are_collected(self) -> None:
        fields, _ = self.parse("Funding: [CONFIRM]\nVertical datum: [CONFIRM] which one\n")
        found = dict(validator.find_confirms(fields))
        self.assertIn("Funding", found)
        self.assertIn("Vertical datum", found)

    def test_a_filled_field_is_not_reported_as_a_confirm(self) -> None:
        fields, _ = self.parse("Funding: Eawag internal resources\n")
        self.assertEqual(validator.find_confirms(fields), [])


class TestCrossCheck(ParserTestCase):
    def test_agreement_produces_no_problems(self) -> None:
        fields, _ = self.parse("Title: A test dataset\nDataset version: 1.0\n")
        package = {"title": "A test dataset", "version": "1.0"}
        self.assertEqual(validator.cross_check(fields, package), [])

    def test_a_wrapped_title_still_matches(self) -> None:
        # The readme wraps long titles across lines; the JSON does not.
        fields, _ = self.parse("Title: A rather long title that\n  wraps across two lines\nDataset version: 1.0\n")
        package = {"title": "A rather long title that wraps across two lines", "version": "1.0"}
        self.assertEqual(validator.cross_check(fields, package), [])

    def test_a_real_disagreement_is_reported(self) -> None:
        fields, _ = self.parse("Title: One thing\nDataset version: 1.0\n")
        problems = validator.cross_check(fields, {"title": "Another thing", "version": "1.0"})
        self.assertEqual(len(problems), 1)
        self.assertIn("Title", problems[0])


class TestCommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_a_complete_readme_exits_zero(self) -> None:
        readme = self.path / "readme.txt"
        readme.write_text(MINIMAL, encoding="utf-8")
        self.assertEqual(validator.main(["validate_readme.py", str(readme)]), 0)

    def test_an_incomplete_readme_exits_nonzero(self) -> None:
        readme = self.path / "readme.txt"
        readme.write_text(MINIMAL.replace("Title: A test dataset\n", ""), encoding="utf-8")
        self.assertEqual(validator.main(["validate_readme.py", str(readme)]), 1)

    def test_a_datapackage_mismatch_exits_nonzero(self) -> None:
        readme = self.path / "readme.txt"
        readme.write_text(MINIMAL, encoding="utf-8")
        package = self.path / "datapackage.json"
        package.write_text(json.dumps({"title": "Different", "version": "9.9"}), encoding="utf-8")
        self.assertEqual(
            validator.main(["validate_readme.py", str(readme), str(package)]), 1
        )

    def test_wrong_argument_count_is_refused(self) -> None:
        self.assertEqual(validator.main(["validate_readme.py"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
