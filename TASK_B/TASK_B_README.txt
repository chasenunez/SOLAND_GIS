# =============================================================================
# WEEKLY DATASET UPDATE CHECKER
#
# Tells you when swisstopo, FOEN or cantonal data you depend on has been
# updated.
#
# ORIENTATION
#   Read this first    reports/EXAMPLE_report.md, to see what you will get
#   Only file you edit watchlist.toml
#   Everything else    code and tests. Ignore until you want to change it.
#
# Requires Python 3.11 or later. Nothing to install.
#
# Run with:   python check_updates.py --dry-run
# Test with:  python test_check_updates.py
# =============================================================================


QUICK START

Commands:
  python check_updates.py --dry-run     # check and report, download nothing
  python check_updates.py               # normal run
  python check_updates.py --only swisstlm3d
  python test_check_updates.py          # 42 tests, runs offline

What the first run does:
  Records what everything looks like now and reports nothing as changed. From
  the second run on, it tells you what moved.
  # A first run that reported every dataset as updated would produce a report
  # of false positives, which undermines confidence in every later report.

Where output goes:
  reports/, as a dated Markdown file and a matching CSV.
  # reports/EXAMPLE_report.md was generated from recorded responses, so you
  # can see the shape of a report before the first live run.


HOW IT DECIDES SOMETHING CHANGED

The mechanism:
  Each watched thing produces a single string, its tag, compared against the
  tag stored in state.json from last time. Different tag means changed.
  # That is the whole mechanism. Keeping the comparison down to one string is
  # what makes the results easy to reason about and easy to trust.

Where the tag comes from:
  geoadmin_stac  the collection's "updated" timestamp.
                 Used for swisstopo and most FOEN layers.
  geodienste     each canton's updated_at, one target per canton.
                 Used for cantonal data.
  http_headers   ETag, else Last-Modified, else Content-Length.
                 Used for the FOEN zips that have no API.
  manual         nothing. Reported every run with a reason.
                 Used where there is no checkable endpoint at all.

Why cantons are watched individually:
  They publish independently. A single timestamp for a whole topic would hide
  the event you most want to know about, which is one more canton coming
  online.


ADDING A DATASET

How:
  Add a [[dataset]] block to watchlist.toml.

Check the identifier first:
  A mistyped STAC collection id or geodienste topic returns an empty response
  rather than an error, so a broken watch looks exactly like a quiet dataset.

The geodienste parameter trap:
  base_topics takes an unversioned topic such as lwb_nutzungsflaechen.
  topics takes a versioned one such as lwb_nutzungsflaechen_v2_0.
  # Passing an unversioned name to topics returns an empty list rather than an
  # error, which is an easy way to conclude wrongly that a topic does not
  # exist. It is the mistake this project actually made.

Run the tests after editing:
  They check the watchlist for duplicate ids, unknown source types, missing
  required fields per source type, and manual entries without a real
  explanation.


DOWNLOADING

Default policy:
  notify. Report it, do nothing else.

To fetch as well:
  Set policy = "fetch" on an entry. The file lands in staging/ and the
  checksum is verified where the provider publishes one.

Nothing is ever written outside this folder:
  Downloads go to staging/, never near /Volumes/GeoData or any source
  archive.
  # A checker that can overwrite your data is one you have to think about
  # every time it runs.

Downloads are capped per run:
  max_downloads_per_run in watchlist.toml.
  # geodienste's operating terms allow one dataset per offering per day, and a
  # single topic expands to 26 cantons, so an uncapped fetch policy would
  # breach their terms on the first busy week. Requests to the same host are
  # also spaced by min_seconds_between_requests, set to 3 seconds against
  # their published limit of 20 per minute.

Which sources publish a checksum:
  geodienste       md5, read from the per-canton STAC item and matched on
                   the download URL.
  geo.admin        None used. These entries are notify-only, so nothing is
                   downloaded.
  plain FOEN zips  None published.
  # Spelled out because "checksums are verified" sounds broader than it is.

When the digest is fetched:
  At download time, not on every check.
  # It costs a request, and it tells you nothing about whether the file
  # changed, since the timestamp already did that.

A wrinkle worth recording:
  geodienste's STAC assets carry a file:checksum field alongside md5, and it
  is not a multihash. On the ZH item it reads
  d51039386239386663366636386430303163, whose hex decodes to the ASCII text
  98b98fc6f68d001c, the first sixteen characters of the md5 next to it.
  Comparing against it would fail on every download, so the md5 field is used
  instead. There is a test pinning this.


EXIT CODES

For driving a scheduled task:
  0  nothing changed, nothing failed
  1  at least one dataset changed
  2  at least one dataset could not be checked

Why 2 outranks 1:
  An unreachable source is a louder signal than a changed one, because silence
  from a broken watch is indistinguishable from good news.


FILES

  watchlist.toml         What to watch, and what to do about it. The only file
                         you normally edit.
  sources.py             The three adapters and the rate-limited HTTP client.
  check_updates.py       Comparison, state, reports, command line.
  test_check_updates.py  Tests. Offline.
  fixtures/              API responses used by the tests. See the README there
                         for which are real captures and which were made up.
  state.json             What was seen last run. Created automatically. Safe
                         to delete: the next run rebuilds it and reports
                         everything as new.


WHAT IT CANNOT DO

# Said plainly, so nobody assumes otherwise.

Datasets with the version in the URL path:
  data.bafu.admin.ch publishes at addresses like .../v2026-03-17/..., so a new
  version appears at a different URL entirely and watching a fixed one would
  never see it. No documented listing endpoint was found, so these are manual
  entries.
  # An endpoint inferred from a naming pattern would fail silently, which is
  # less useful than recording the limitation.

Downloads available only on request:
  The FOEN ARA-DB is one. Its catalogue entry is watched, so you learn when to
  go and ask, but the file itself cannot be polled.

Content changes that leave no trace in headers:
  For the plain zips, a file edited without changing its ETag, modification
  time or size will not be noticed.
  # That is a property of the publishing, not of the checker.

Whether an update matters:
  It reports that swissTLM3D moved. Whether that justifies rerunning the
  preparation workflow is a judgement for the data owner.


PROVENANCE OF THE WATCHLIST

Verified on:
  2026-08-10. Every collection id, topic and URL in watchlist.toml was queried
  and confirmed to resolve. Nothing was guessed from a naming pattern.

Two things worth recording from that check:
  - The FOEN ARA-DB is in the federal catalogue as
    ch.bafu.gewaesserschutz-klaeranlagen, updated 2026-03-19, even though its
    download is on request only. Its data reference date is still 2014-01-01.
  - 11 of 27 listed cantons and principalities were publishing the ARA topic
    through geodienste, with the rest announced between October 2026 and April
    2028. That is the situation this watch exists to track.
