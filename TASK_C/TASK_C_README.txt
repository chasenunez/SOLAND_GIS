# =============================================================================
# TLM/LN LAND USE COMBINATION
#
# Combines swissTLM3D with the yearly cantonal agricultural land use data
# (Landwirtschaftliche Nutzungsflaechen) to produce a detailed, yearly land
# use dataset for Switzerland.
#
# ORIENTATION
#   Only file you edit  config.toml
#   Run these           01_tlm_prepare.ipynb, then 02_ln_tlm_yearly_calc.ipynb
#   The logic           tlm_ln/, a package the notebooks import
#   The reasoning       TASK_C_code_review.txt, for why anything changed
#
# Requires Python 3.11 or later for tomllib. On 3.10, pip install tomli.
#
# Install with: pip install -r requirements.txt
# Test with:    python test_tlm_ln.py  and  python test_pipeline.py
# =============================================================================


QUICK START

Commands:
  pip install -r requirements.txt
  python test_tlm_ln.py       # 64 unit tests, no data needed, under a second
  python test_pipeline.py     # 24 end-to-end tests, builds real files, ~2 s

Then, in order:
  1. Edit the paths in config.toml.
  2. Run 01_tlm_prepare.ipynb. Builds TLM_total from swissTLM3D. Once per TLM
     release, not once per year.
  3. Run 02_ln_tlm_yearly_calc.ipynb. Combines it with one year of LN data.

Before the first real run:
  Set write=False in the run cell to try it without touching the output.
  # Running the tests first is worth the twenty seconds: if they pass you know
  # the environment is sound before you point anything at the real files.


HOW IT IS ORGANISED

The split:
  The processing logic lives in the tlm_ln package. The notebooks are drivers
  that run it, plot the maps and show the checks. Paths live in config.toml.
  # That split is the point. Logic in a module can be tested, and the tests
  # are what let you change something later without wondering what you broke.
  # Paths in a config mean moving to a different machine is not a code edit.

The modules:
  config.py    Settings and paths from TOML, plus the shared constants
  io.py        Reading, writing, geometry cleaning, the run manifest
  classify.py  Lookup joins, class assignment, validity and consistency checks
  geometry.py  Merge, erase, de-duplicate, find overlaps, selection
  validate.py  Area reconciliation, gap report, completeness, class summary
  pipeline.py  The two workflows from the concept diagram


WHAT CHANGED IN THE OUTPUT

# Two changes alter results. Explained in full in TASK_C_code_review.txt.

Selection is on group code 99, not class code 6:
  Class 6 is "Flaechen ausserhalb der LN" and contains real ground cover:
  ponds and ditches, ruderal areas, dry stone walls, unpaved paths,
  region-specific biodiversity areas, home gardens. Those were being dropped
  and never restored. Group 99 is "Ueberlagernde Flaechen" and contains only
  the four codes that sit on top of other LN polygons.

The output has one classification schema:
  Previously the TLM layers carried lowercase class, class_code and the LN
  layers carried capitalised Class, Class_Code, and the merge produced both
  families half-populated. The LN columns are now renamed onto the lowercase
  names before merging, and the columns that existed only to get the join done
  are dropped afterwards.

Columns in TLM_LN_<year>:
  class, class_code, group_code, group_de, group_en, class_en,
  pest_group, pest_code, bff_qi, ueberlagernd, source,
  lnf_code, objektart, geometry
  # lnf_code and objektart are kept deliberately: they are what lets you trace
  # a polygon back to the classification it came from.

If you have downstream code:
  Anything that referenced Class_Code on the output should now use class_code.


THE CHECKS EACH RUN PRODUCES

Written to:
  reports_<year>/ beside the output, as CSV.
  # Next to the data rather than only in the log, because "was anything odd
  # about the 2023 run?" gets asked later.

The reports:
  areas.csv                   Feature counts and areas per layer. Does the
                              arithmetic hold?
  code_validity.csv           Any land use code outside its validity window
                              for this year
  overlap_flag_mismatch.csv   Codes flagged ueberlagernd = 1 that group 99
                              does not exclude
  gaps.csv                    Area left uncovered by the selection step. Set
                              compute_gap_report = false to skip it.
  completeness.csv            How much of the output carries no classification
  by_class.csv                Area and feature count per class

Reading code_validity.csv:
  An empty file is good news. Rows mean a stale cantonal delivery, a
  mislabelled download year, or a lookup predating the year being processed.
  # It warns rather than fails, since it is not established whether cantonal
  # entry systems restrict farmers to currently valid codes.

Also written each run:
  <output>.manifest.json, with input paths and checksums, package versions,
  and the settings used.
  # That is what ties an output back to the exact inputs that made it.


THE SETTING WORTH THINKING ABOUT

Setting:
  erase_with_selected_only in config.toml. Default false.

What it does:
  TLM_total is erased with the LN layer, then only part of that layer is
  merged back, so the difference becomes a hole. Overlaying features sit on
  top of other LN polygons by definition, so most of the dropped area is
  covered by something that stays. The part that is not is a real gap, and
  isolated trees and avenues are the likely source.

Choosing:
  false matches the concept diagram and reproduces existing results.
  true closes the gaps.
  # Run 2025 once, read reports_2025/gaps.csv, and decide from the number
  # rather than from anyone's opinion.


RUNNING A BACK SERIES

The year is a parameter, so:
  from tlm_ln import config, pipeline

  for year in range(2019, 2026):
      pipeline.run_year(config.load_year("config.toml", year))

Each year writes:
  Its own GeoPackage, manifest and reports.

One thing to decide first:
  Each year is joined against whichever lookup config.toml names, and the
  lookup carries validity windows, which implies it has a history. Joining a
  2019 dataset against a Feb 2026 lookup is worth doing knowingly rather than
  by default. Per-year overrides go in [years.<year>] sections.


WHAT THE TESTS COVER, AND WHAT THEY DO NOT

# Two suites, deliberately different in kind.

test_tlm_ln.py, 64 tests:
  Built from squares and rectangles whose areas can be worked out on paper.
  They verify the erase arithmetic, the selection rule, deterministic
  de-duplication, overlap detection, validity checking, schema harmonisation,
  the gap measurement, lookup reading with all three sentinels, and config
  path resolution. Under a second.

test_pipeline.py, 24 tests:
  Builds real GeoPackages and shapefiles in a temporary directory and runs
  both pipelines over them.
  # This is the suite that matters most. For a while the unit tests were green
  # while the two functions the notebooks actually call had never been
  # executed by anything. It also asserts that the published layer carries one
  # lowercase schema, which is the check that caught a real defect.

What neither suite does:
  Verify results against your data, which was not reachable from where this
  was written. They verify logic. The first real run is yours, and the area
  table in notebook 01 is the first thing to read when you do it.


REQUIREMENTS

Python:
  3.11 or later, for tomllib in the standard library. On 3.10, pip install
  tomli works as a drop-in.

Packages:
  See requirements.txt. Minimums rather than exact pins, with the reason for
  each minimum recorded there.

Verified working on:
  2026-08-21, with geopandas 1.1.4, pandas 2.3.3, shapely 2.1.2, pyogrio
  0.13.0.
  # Every run also writes the versions it actually used into
  # <output>.manifest.json, which is the record that matters when someone asks
  # months later why a result changed.
