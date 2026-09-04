# =============================================================================
# README REVIEW: ARA / WWTP 2019 TO 2025
#
# Prepared for Katia Soland, Eawag. Reviewer: Lib4RI.
# Date: 2026-08-10.
#
# Reviewed: AUTHOR_DATASET_ReadmeTemplate.txt and
#           Eawag_ARA_2019_2025_Readme.txt
#
# ORIENTATION
#   Section 1  What changed in your readme, and why. The change list.
#   Section 2  The machine-readable sidecar, and how to keep it current.
#
# Your original files are untouched.
# =============================================================================


WHAT IS HERE

# --- 1. The expanded template, and your readme rewritten against it ---

  README_TEMPLATE_v2.txt              The template, extended. Replaces
                                      AUTHOR_DATASET_ReadmeTemplate.txt.
  Eawag_ARA_2019_2025_Readme_v2.txt   Your readme rewritten against it.

# --- 2. The machine-readable sidecar ---

# This is the part that lets software read the dataset's details without a
# person in the loop.

  datapackage.json                    The sidecar itself. Describes the
                                      dataset, its provenance, and its 21
                                      layers.
  schemas/catchment.json              The field definitions for each layer
  schemas/location.json               type, referenced by datapackage.json
  schemas/discharge.json              rather than repeated inside it.
  validate_readme.py                  Checks the readme parses and reports
                                      what is still open.
  test_validate_readme.py             25 tests for the validator itself.


USING THESE FILES

To see what is still outstanding:
  python validate_readme.py Eawag_ARA_2019_2025_Readme_v2.txt datapackage.json

  It lists the required fields still empty, the fields that may stay empty
  until publication, and every [CONFIRM] marker with its field name.
  # 32 markers sit in field values, and a further 15 in the guidance comments,
  # which the validator reports separately. Most are checksums, file sizes and
  # retrieval dates that take one command or one lookup. The eight that change
  # something material are listed under OUTSTANDING QUESTIONS at the end of
  # this document.

To check the sidecar loads:
  python test_validate_readme.py
  frictionless validate datapackage.json

  The second needs the ARA_*.gpkg files alongside datapackage.json, and the
  frictionless package installed. It tests the field rules against the real
  data rather than only checking that the descriptor is well formed.

For the next dataset:
  Copy README_TEMPLATE_v2.txt, fill it in, and keep the [CONFIRM] convention.
  The grammar at the top of the template explains the few rules the validator
  relies on.

Requires Python 3.11 or later. The validator needs nothing installed; only
the optional frictionless check does.


1. THE TEMPLATE

What stays the same:
  README_TEMPLATE_v2.txt keeps your original structure and section order, so
  it will feel familiar.

What is new:
  A stated grammar at the top so the file is parseable, the publication-status
  and provenance sections, versioning, and the convention that [CONFIRM] means
  "we still owe someone an answer" while a blank means "not applicable".

Why the JSON handles unknowns differently:
  The readme uses [CONFIRM] inline. The JSON does not, since a placeholder
  string in a field the specification types as an integer makes the whole
  descriptor unreadable and takes every other field down with it. Unknown
  values are simply absent, and what is still owed is listed under
  eawag:pending at package level and inside each schema.
  # Both files therefore say the same thing. Only the JSON has to say it in a
  # way that keeps the file loadable.

On repeating the template per file:
  The original intention was to repeat this template for each dataset, folder
  or file. The sidecar handles the same idea slightly differently, by
  referencing shared schema files rather than repeating them. Section 2
  explains how.


SOME THINGS SLATED FOR LATER

If FOEN agrees to external release, there are a few additional changes that
you could do to improve the findability:

  1. Fill the four [ON PUBLICATION] fields and settle the licence.
  2. Deposit in ERIC/open, receive the DOI, add it to the citation string and
     to datapackage.json.
  3. Create the geocat.ch record from the crosswalk in datapackage.json.
  4. Move the documentation folder and the ARA_2014 reference so they travel
     with the data.
  5. Generate and paste the checksums.
  6. Run validate_readme.py and confirm no [CONFIRM] markers remain:
       python validate_readme.py Eawag_ARA_2019_2025_Readme_v2.txt \
         datapackage.json

Lib4RI can help with steps 2 and 3 when you get there.

One thing outside your control, but probably worth checking up on:
  The geocat.ch record for the FOEN ARA-DB states both "Opendata BY: Freie
  Nutzung, Quellenangabe ist Pflicht" and a usage restriction of
  "gebuehrenpflichtig". It would be worth asking FOEN
  (wasser@bafu.admin.ch) which applies before you release anything derived
  from it.


A. CORRECTIONS

# Detailed changes in the updated draft.

1. Collection date lands in the future:
     The readme is dated 2026-07-28 but gives "Date of data collection:
     2026-08-28". You've confirmed 2026-07-28 is right.

2. "so 21 datasets in total 21":
     Duplicated fragment in the description.

3. alternative_ARA_Nr is listed twice:
     In the catchment variable list, with the same description both times.
     Either a copy-paste repeat, or there really are two such columns, perhaps
     one for VSA and one for the canton.
     # Worth checking which, because a reuser opening the file and finding two
     # similarly named columns has no way to tell them apart.

4. The year/layer table does not parse:
     The header row reads "Jahr  EZG  ARA_Standorte  Einleitstellen  #" but
     the tab positions put EZG over two columns, so the header and the rows
     do not line up. Fine for a human reading carefully, invisible to anything
     automated. Rewritten in the v2 file as one block per layer type.

5. Typo, "availabel":
     In "ESRI Shapefiles are availabel on request".

6. "(not only anymore)":
     After the ARA_2014 source. The intended meaning is probably that
     ARA_2014 is no longer the only source, but as written it is hard to
     follow.

7. Two template sections were dropped rather than answered:
     "Are there multiple versions of the dataset?" and "Missing data codes".
     Both matter here: the dataset is explicitly a new version of ARA_2014,
     and every GeoPackage will contain missing values of some kind.
     # Left blank, a reader cannot tell "none" from "nobody checked".


B. FAIR IMPROVEMENTS

# --- Findability ---

Persistent identifier:
  Currently no DOI. While internal this is fine; on release, ERIC/open
  (opendata.eawag.ch) is Eawag's institutional repository and issues DataCite
  DOIs.

Keywords:
  Currently none present. Added, drawn from terms already used across Swiss
  geodata.

Bounding box:
  "Switzerland" is not spatially searchable; a WGS84 bounding box is. The v2
  file carries the one-line geopandas command to compute it from the data
  rather than a nominal extent.
  # A computed extent is a fact. A quoted one is a guess.

Short name and version:
  Added ARA_WWTP_2019_2025 and version 1.0. The original title field held
  ARA_WWTP_2026, which names the year of compilation rather than the years
  covered, and reads as if the data were about 2026.

Temporal coverage separated from collection date:
  The data describes 2019 to 2025; it was compiled in 2026. One field cannot
  carry both.

geocat.ch record:
  Swiss geodata is normally also described in geocat.ch, the federal metadata
  catalogue. Your readme already cites a geocat record for the FOEN ARA-DB, so
  the pattern is familiar. On publication, a record there reaches people who
  will never see this file.
  # datapackage.json includes a crosswalk from its own keys to the ISO 19115
  # elements geocat expects, so the record can be seeded rather than retyped.

# --- Accessibility ---

Access level and conditions:
  Neither was stated. Now explicit: internal, on request to you, pending FOEN.
  # Worth writing down even when it feels obvious internally, because the
  # readme will outlast the conversation that made it obvious.

A second contact:
  Three authors are named but only one email appears. A dataset with one
  contact becomes unanswerable the moment that person is on leave or changes
  role. A role mailbox is the more durable option if one exists.

The Q: drive paths:
  Three separate things live only on the Q: drive: the documentation folder,
  the ARA_2014 reference dataset, and the source list. The documentation
  folder is the one that matters most, since the readme points to it for the
  collection methods, the processing methods, and the per-year plant counts.
  # Anyone without Q: access effectively has a readme with three empty
  # sections. Before any external release it should travel with the data.

# --- Interoperability ---

Vertical datum:
  The location layer stores m_ueber_Meer, but no vertical datum is given.
  Swiss elevations are either LN02 or LHN95 and they differ by up to roughly
  half a metre. A reuser cannot safely assume which. Worth a single line.

Complete variable list with types and units:
  The current list says "redundant variables are not mentioned". That saves
  the writer a few minutes and costs every subsequent reader considerably
  more, because they cannot tell whether an undescribed column is redundant or
  simply undocumented. The v2 file lists each variable as:
    name | type | unit | allowed values | definition

Missing data codes:
  Left blank. Source data of this kind commonly carries more than one sentinel
  in the same file. As a live example from this project, the LN lookup table
  used in the TLM workflow contains three different ones: <Null>, NULL, and
  <zero>.
  # Whatever is in the ARA files, naming it prevents a reuser silently
  # treating a sentinel as a real value.

Language declaration:
  Attribute names and values are German, the readme is English, and the field
  names mix conventions (ARA_Nr, m_ueber_Meer, erhebungsjahr, NAME). Declaring
  the language costs one line and explains the mixture.

A machine-readable sidecar:
  datapackage.json and the schemas/ files carry the same metadata in a form
  software can read, including a field-level definition for every column. This
  is the part of Interoperable that a plain text readme structurally cannot
  deliver on its own. Section 2 covers it properly.

# --- Reusability ---

Licence:
  The current entry is a warranty disclaimer. A disclaimer limits your
  liability; it says nothing about what a reuser may do, which is the question
  they actually have. The v2 file keeps your disclaimer verbatim under
  "Warranty and disclaimer" and leaves "Licence" for release.
  # My suggestion then is CC-BY-4.0, compatible with the Opendata BY terms on
  # the FOEN source and keeping attribution mandatory. That is a decision for
  # you, Lib4RI and FOEN jointly.

Provenance with versions and retrieval dates:
  The four source families are named, which is more than most readmes carry.
  What is missing is which vintage of each was used. geodienste
  data changes continuously and per canton; the ARA-DB carries a 2014
  reference date but its catalogue entry was last touched 2026-03-19.
  # Without a retrieval date the starting point cannot be reproduced, by a
  # reuser or by the compilers themselves two years on.

Known limitations and uncertainty:
  The original has no such section, and it is typically the most-read section
  of a readme that gets reused. A draft has been added covering uneven
  cantonal coverage, disputed plant numbering, reconstruction rather than
  observation, catchments carrying more uncertainty than point locations, and
  plants with no receiving water body.
  # This draft was written from second-hand description and needs review by
  # the compilers, who know the material directly.

"What this is not":
  The dataset can be mistaken for a federal product. One paragraph stating
  that collecting cantonal data is FOEN's responsibility, that their
  collection has been inconsistent since 2014, and that this is an interim
  Eawag reconstruction, prevents almost every plausible misuse.

Version history:
  The template has the field and the filled version dropped it, yet this
  dataset is explicitly a revision of ARA_2014. Added as a section that grows
  downward with each release.

Checksums:
  None present. One command generates them, and they let a recipient confirm
  they got the file intact. They cannot be reconstructed after the fact.

Format and software:
  Your note that the files open in QGIS, Python or R is useful and kept.
  Added: the Shapefile alternative truncates field names to ten characters, so
  several attribute names would be shortened.
  # Worth flagging before someone requests Shapefiles and then wonders why
  # alternative_ARA_Nr arrived as alternativ.


2. THE MACHINE-READABLE SIDECAR

What it is:
  datapackage.json plus the three files in schemas/. Together they are a
  Frictionless Data Package, a small and stable open format, not something
  invented for this project. Any tool that understands it can read your
  dataset's description without anyone explaining the layout first.

What datapackage.json holds:
  Everything that describes the dataset as a whole: title, description,
  keywords, contributors, sources and their provenance, spatial and temporal
  coverage, and a list of 21 resources, one per layer, three layers across
  seven years.

What the schemas/ files hold:
  The field definitions, one per layer type: catchment.json, location.json,
  discharge.json. Each lists that layer's fields with a name, a type, a human
  description, and where the values follow a rule, a constraint.

Why the arrangement is indirect:
  Each of the 21 resources points at whichever of the three schemas applies.
  Twenty-one resources with three field definitions means a correction to a
  field description is made once instead of twenty-one times, and the seven
  years cannot drift apart from each other unnoticed.

The eawag: prefix:
  Anything in these files that is not part of the Frictionless specification
  carries an eawag: prefix, for instance eawag:layer and eawag:pending.
  # A standard tool ignores a prefixed key it does not recognise, whereas an
  # unprefixed one can make it refuse the whole file. The prefixes should be
  # left in place.


WHAT IT CAN DO THAT THE README CANNOT

The constraints are machine-checkable:
  ARA_Nr is declared as an integer between 100 and 999999, Kanton as exactly
  two capital letters, PLZ as four digits between 1000 and 9999, and ARA_Nr is
  the primary key of all three layers, which means it has to be unique.

To check them:
  Put the ARA_*.gpkg files next to datapackage.json and run:
    frictionless validate datapackage.json

  The rules are then tested against the actual data on every layer, for every
  year. This overlaps with the existing quality assurance procedure: the check
  that the three layers of a year hold an equal number of features is close to
  what the recorded eawag:featureCount plus primary key uniqueness reports,
  with the difference that it runs automatically and leaves a record.


MAKING CHANGES AND UPDATES

1. Adding a year:
     Copy the three resource blocks for the most recent year in
     datapackage.json, change the year in the name, path, layer and temporal
     coverage, and set the feature count. Leave schemas/ alone.
     # Adding 2026 should not involve opening a schema file at all.

2. A field changes, or a new one appears:
     Edit the one schema file. All seven years follow automatically.

3. Answering a [CONFIRM]:
     Fill it in the readme AND delete the matching line from eawag:pending,
     either at package level or inside the schema. The two files are meant to
     say the same thing, and this is the step at which they most often drift
     apart.
     # validate_readme.py will catch a title or version that has fallen out of
     # step, but it cannot detect a question answered in one file and not the
     # other.

4. If a year's structure diverges:
     For example, 2026 gains a column that earlier years do not have. Copy the
     schema to a new file, catchment_2026.json, and point only the new
     resources at it, rather than adding conditional logic or a column
     documented as "only present in some years".
     # Two separate schemas describe the data accurately. One schema covering
     # both cases describes neither.


OUTSTANDING QUESTIONS

# Of the [CONFIRM] markers in the rewritten readme, most are values that take
# one command or one lookup. These eight change something material and need an
# answer from the people who compiled the data.

1. Do the yearly layers represent the status at year end, a fixed reference
   date, or the changes accumulated during that year? The original wording,
   "reflecting all the changes that happened in <year>", reads as the third.

2. Is alternative_ARA_Nr one column or two? See correction 3 above.

3. What is the vertical datum for m_ueber_Meer: LN02 or LHN95?

4. Were any attributes carried forward unchanged from ARA_2014 without
   re-verification? If so, naming them would help reusers judge what to trust.
   This is normal practice and undocumented it is simply invisible.

5. Funding: internal Eawag resources, or a named source? The original recorded
   "-", which cannot be distinguished from an unanswered question.

6. Was any of the compilation scripted, or was it done manually? "Manual" is a
   legitimate answer and more useful than silence.

7. Retrieval dates for the four sources. Approximate months would be enough.
   This is the one with the largest effect on reproducibility, since
   geodienste data changes continuously and per canton.

8. What does GEWISS stand for? It appears as a keyword in the FOEN geocat
   record for the ARA-DB and is clearly the register the GEWISS_Nr values come
   from, but the expansion was not verified and so has not been written down.
   The Water Division (wasser@bafu.admin.ch) can confirm it.
