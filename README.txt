# =============================================================================
# EAWAG RESEARCH DATA MANAGEMENT: THREE TASKS
#
# Prepared by Lib4RI for Katia Soland, Eawag.
# Date: 2026-09-04.
#
# Three pieces of work, one folder each. Every folder has its own README with
# the detail; this file says what the three are, what was asked for, and what
# was delivered.
#
# ORIENTATION
#   Start here     TASK_A/TASK_A_README.txt, the change memo. It is the
#                  shortest route into what was done and why.
#   Then           TASK_C/TASK_C_code_review.txt, the code review, if the
#                  land use workflow is the priority.
#   To see output  TASK_B/reports/EXAMPLE_report.md, a sample weekly report.
#
# All source data on the GeoData drive was treated as read-only throughout.
# Nothing was written, moved or deleted there.
# =============================================================================


WHAT IS IN EACH FOLDER

TASK_A  Readme and metadata for the wastewater treatment plant dataset.
        An expanded readme template, the ARA 2019 to 2025 readme rewritten
        against it, a machine-readable metadata sidecar, and a memo listing
        every change with its reasoning.

TASK_B  A weekly check for updates to the external datasets this work
        depends on. Watches swisstopo, FOEN and cantonal sources, reports
        what changed, and downloads only what it has been told it may.

TASK_C  The swissTLM3D and agricultural land use combination workflow. The
        processing logic as a tested Python package, the two notebooks
        rewritten as drivers, and a code review covering twenty-two findings.


TASK A: READMES AND METADATA

What was asked for:
  A gold standard readme for wastewater treatment data in Switzerland,
  starting from the empty template and the completed ARA readme, highlighting
  changes, additions and subtractions that improve the utility and FAIRness of
  the data.

What was delivered:
  README_TEMPLATE_v2.txt              The template, extended.
  Eawag_ARA_2019_2025_Readme_v2.txt   The ARA readme rewritten against it.
  datapackage.json + schemas/         The machine-readable sidecar.
  TASK_A_README.txt                   The change memo.
  validate_readme.py                  Completeness checker, plus 25 tests.

The main changes:
  Seven factual errors or inconsistencies were found in the original and are
  listed individually in the memo. Beyond those, the substantial additions are
  a licence field distinct from the warranty disclaimer, provenance with a
  retrieval date per source, a known limitations section, a statement of what
  the dataset is not, and a complete variable list with types and units.

  Everything is written for internal use, following the decision to hold
  publication pending FOEN's response. Fields that only apply on release are
  marked rather than removed.


TASK B: WEEKLY UPDATE CHECK

What was asked for:
  A simple and robust way to check whether agency datasets have been updated
  and alert someone when they have, with a per-dataset choice between
  automatic update and notification only. The three source pages named in the
  brief were to be checked for an API or other machine-readable route.

What was delivered:
  watchlist.toml         The datasets watched, and the policy for each.
  check_updates.py       The checker, its state handling and its reports.
  sources.py             Three adapters and a rate-limited HTTP client.
  test_check_updates.py  42 tests, all offline.
  reports/               A sample report showing the output format.

The three links, and what was found:
  swisstopo news about geodata   No feed found. Superseded by the STAC API
                                 below, which is machine-readable.
  swisstopo landscape models     All seven models are published through the
                                 federal STAC API at data.geo.admin.ch. The
                                 two this project uses are watched; the other
                                 five are a four-line entry each if wanted.
  FOEN water geodata             Mixed. Most layers are in the same STAC
                                 catalogue. A few are plain zip files with no
                                 API, and are watched by their HTTP headers
                                 instead. Two have no checkable endpoint at
                                 all and are flagged for manual checking.

  A fourth route was found and used: geodienste.ch publishes a services API
  giving a publication timestamp per canton, which is how the cantonal data is
  watched.


TASK C: THE TLM AND LAND USE WORKFLOW

What was asked for:
  A Python workflow combining swissTLM3D with the cantonal
  agricultural land use data, a thorough review of the existing notebooks
  against the conceptual model, clear fixes, and plans for expansion.

What was delivered:
  tlm_ln/                      The processing logic, six modules.
  01_tlm_prepare.ipynb         Driver notebook, run once per TLM release.
  02_ln_tlm_yearly_calc.ipynb  Driver notebook, run once per year.
  config.toml                  Paths and settings, replacing hardcoded ones.
  test_tlm_ln.py               64 unit tests.
  test_pipeline.py             24 end-to-end tests.
  TASK_C_code_review.txt       Twenty-two findings, ranked by severity.



RUNNING THE CODE

Requires Python 3.11 or later. Task B needs nothing installed; Task C needs
the packages in TASK_C/requirements.txt.

  cd TASK_A && python validate_readme.py Eawag_ARA_2019_2025_Readme_v2.txt \
    datapackage.json
  cd TASK_A && python test_validate_readme.py

  cd TASK_B && python check_updates.py --dry-run
  cd TASK_B && python test_check_updates.py

  cd TASK_C && pip install -r requirements.txt
  cd TASK_C && python test_tlm_ln.py && python test_pipeline.py
