"""Tests for the update checker. Run with: python test_check_updates.py

Uses only unittest from the standard library, and recorded API responses from
fixtures/, so the whole suite runs offline. That matters for a tool whose job is
to talk to the network: if the tests needed the network they would fail for
reasons that have nothing to do with the code, and would soon stop being run.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import check_updates
import sources

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestGeoadminStac(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = {"id": "swisstlm3d", "collection": "ch.swisstopo.swisstlm3d"}

    def test_reads_the_collection_updated_timestamp(self) -> None:
        observations = sources.parse_geoadmin_stac(
            load_fixture("geoadmin_swisstlm3d.json"), self.entry
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].tag, "2026-02-23T15:40:03.296813Z")
        self.assertEqual(observations[0].target_id, "swisstlm3d")

    def test_captures_the_data_reference_date_separately(self) -> None:
        # The catalogue entry and the data itself have different dates, and
        # conflating them is how you conclude that 2014 data is current.
        observations = sources.parse_geoadmin_stac(
            load_fixture("geoadmin_bafu_klaeranlagen.json"),
            {"id": "bafu-ara-db", "collection": "ch.bafu.gewaesserschutz-klaeranlagen"},
        )
        self.assertEqual(observations[0].tag, "2026-03-19T09:42:47.639018Z")
        self.assertEqual(observations[0].extra["data_reference_date"], "2014-01-01T00:00:00Z")

    def test_missing_updated_field_is_an_error_not_a_silent_pass(self) -> None:
        with self.assertRaises(sources.SourceError):
            sources.parse_geoadmin_stac({"id": "x"}, self.entry)


class TestGeodienste(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = load_fixture("geodienste_lwb_nutzungsflaechen_zh.json")
        self.entry = {"id": "ln-nutzungsflaechen", "base_topic": "lwb_nutzungsflaechen"}

    def test_one_observation_per_canton(self) -> None:
        observations = sources.parse_geodienste(self.payload, self.entry)
        self.assertEqual(
            [obs.target_id for obs in observations],
            ["ln-nutzungsflaechen/AG", "ln-nutzungsflaechen/GL", "ln-nutzungsflaechen/ZH"],
        )

    def test_uses_each_cantons_own_timestamp(self) -> None:
        by_id = {obs.target_id: obs for obs in sources.parse_geodienste(self.payload, self.entry)}
        self.assertEqual(by_id["ln-nutzungsflaechen/ZH"].tag, "2026-01-09T21:03:35")
        self.assertEqual(by_id["ln-nutzungsflaechen/AG"].tag, "2026-03-02T04:15:10")

    def test_cantons_not_yet_publishing_are_kept_not_dropped(self) -> None:
        # A canton coming online is the single most interesting event this watch
        # can detect, so a canton with no data still needs a stable target id.
        by_id = {obs.target_id: obs for obs in sources.parse_geodienste(self.payload, self.entry)}
        self.assertEqual(by_id["ln-nutzungsflaechen/GL"].tag, "not-published")
        self.assertIsNone(by_id["ln-nutzungsflaechen/GL"].download_url)

    def test_a_canton_coming_online_registers_as_a_change(self) -> None:
        before = sources.parse_geodienste(self.payload, self.entry)
        stored = {obs.target_id: {"tag": obs.tag} for obs in before}

        later = json.loads(json.dumps(self.payload))
        later["services"][2]["updated_at"] = "2026-10-01T08:00:00"
        after = {obs.target_id: obs for obs in sources.parse_geodienste(later, self.entry)}

        status, previous = check_updates.compare(
            after["ln-nutzungsflaechen/GL"], stored["ln-nutzungsflaechen/GL"]
        )
        self.assertEqual(status, "changed")
        self.assertEqual(previous, "not-published")

    def test_malformed_response_is_an_error(self) -> None:
        with self.assertRaises(sources.SourceError):
            sources.parse_geodienste({"unexpected": True}, self.entry)

    def test_a_canton_on_two_model_versions_gets_two_distinct_targets(self) -> None:
        # Cantons migrate between model versions independently, so one could
        # publish both 2.0 and 3.0 at once. Without disambiguation the two rows
        # would collapse onto one target id and the second would overwrite the
        # first in the state file, with the report looking entirely normal.
        payload = json.loads(json.dumps(self.payload))
        zurich = next(s for s in payload["services"] if s["canton"] == "ZH")
        migrating = json.loads(json.dumps(zurich))
        migrating["topic"] = "lwb_nutzungsflaechen_v3_0"
        migrating["version"] = "3.0"
        migrating["updated_at"] = "2026-06-01T09:00:00"
        payload["services"].append(migrating)

        observations = sources.parse_geodienste(payload, self.entry)
        ids = [obs.target_id for obs in observations]
        self.assertEqual(len(ids), len(set(ids)), f"target ids collided: {ids}")
        self.assertIn("ln-nutzungsflaechen/ZH/v2.0", ids)
        self.assertIn("ln-nutzungsflaechen/ZH/v3.0", ids)

    def test_the_ordinary_case_keeps_the_plain_readable_id(self) -> None:
        # No version suffix when a canton appears once, which is every canton
        # in the live response as of 2026-08-10.
        ids = [obs.target_id for obs in sources.parse_geodienste(self.payload, self.entry)]
        self.assertNotIn("ln-nutzungsflaechen/ZH/v2.0", ids)
        self.assertIn("ln-nutzungsflaechen/ZH", ids)


class TestHttpHeaders(unittest.TestCase):
    def setUp(self) -> None:
        self.headers = load_fixture("http_headers_bafu_zip.json")
        self.entry = {"id": "bafu-floz", "url": "https://example.invalid/f.zip"}

    def test_prefers_etag_but_keeps_everything_available(self) -> None:
        observation = sources.parse_http_headers(self.headers["with_etag"], self.entry)[0]
        self.assertIn("etag=", observation.tag)
        self.assertIn("last-modified=", observation.tag)
        self.assertFalse(observation.extra["weak_signal"])

    def test_falls_back_to_last_modified(self) -> None:
        observation = sources.parse_http_headers(self.headers["without_etag"], self.entry)[0]
        self.assertIn("last-modified=", observation.tag)
        self.assertFalse(observation.extra["weak_signal"])

    def test_content_length_alone_is_flagged_as_weak(self) -> None:
        observation = sources.parse_http_headers(self.headers["length_only"], self.entry)[0]
        self.assertTrue(observation.extra["weak_signal"])

    def test_no_usable_header_is_an_error_rather_than_a_false_all_clear(self) -> None:
        with self.assertRaises(sources.SourceError):
            sources.parse_http_headers(self.headers["nothing_useful"], self.entry)

    def test_a_changed_etag_is_detected(self) -> None:
        first = sources.parse_http_headers(self.headers["with_etag"], self.entry)[0]
        moved = dict(self.headers["with_etag"], etag='"0000000000000000"')
        second = sources.parse_http_headers(moved, self.entry)[0]
        status, _ = check_updates.compare(second, {"tag": first.tag})
        self.assertEqual(status, "changed")


class TestChecksumLookup(unittest.TestCase):
    """The checksum path, which for a long while existed only on paper.

    An earlier version of this tool had a `checksum` field on Observation that no
    adapter ever populated, so `verify_download` always reported "no checksum
    published" while the documentation claimed md5s were being checked. These
    tests exist to keep the lookup wired to something real.
    """

    def setUp(self) -> None:
        self.item = load_fixture("geodienste_stac_item_zh.json")
        self.download = (
            "https://geodienste.ch/downloads/interlis/lwb_nutzungsflaechen/ZH/"
            "lwb_nutzungsflaechen_v2_0_ZH_lv95.zip"
        )

    def test_finds_the_md5_for_the_file_we_are_downloading(self) -> None:
        digest, algorithm = sources.parse_geodienste_checksum(self.item, self.download)
        self.assertEqual(digest, "98b98fc6f68d001cf1666be6074cbf59")
        self.assertEqual(algorithm, "md5")

    def test_matches_on_url_not_on_asset_key(self) -> None:
        # The services API hands us a URL; the asset keys name formats. Matching
        # on the URL is what guarantees we verify the file we actually fetched.
        gpkg = (
            "https://geodienste.ch/downloads/geopackage/lwb_nutzungsflaechen/ZH/deu/"
            "lwb_nutzungsflaechen_v2_0_ZH_gpkg_lv95.zip"
        )
        digest, _ = sources.parse_geodienste_checksum(self.item, gpkg)
        self.assertEqual(digest, "722b01c9fff93b880dc2d5ecb39f3add")

    def test_unknown_url_yields_nothing_rather_than_a_wrong_digest(self) -> None:
        digest, algorithm = sources.parse_geodienste_checksum(self.item, "https://example.invalid/x.zip")
        self.assertIsNone(digest)
        self.assertIsNone(algorithm)

    def test_the_stac_file_checksum_field_is_deliberately_not_used(self) -> None:
        # geodienste's file:checksum is a truncated hex-of-hex encoding, not a
        # multihash: "d5103938...31 63" decodes to the ASCII text
        # "98b98fc6f68d001c", the first 16 characters of the md5 beside it.
        # Comparing against it would fail on every single download.
        raw = self.item["assets"]["interlis"]["file:checksum"]
        decoded = bytes.fromhex(raw[4:]).decode("ascii")
        self.assertEqual(decoded, "98b98fc6f68d001c")
        self.assertTrue(self.item["assets"]["interlis"]["md5"].startswith(decoded))

    def test_observation_without_a_stac_item_gets_no_checksum(self) -> None:
        # geo.admin entries are notify-only and the plain FOEN zips publish
        # nothing, so (None, None) is the correct answer rather than a failure.
        observation = sources.Observation(
            target_id="bafu-floz", tag="etag=x", label="HTTP headers",
            download_url="https://example.invalid/f.zip",
        )
        self.assertEqual(sources.fetch_checksum(observation, http=None), (None, None))

    def test_a_verified_download_reports_ok(self) -> None:
        # End to end over the two halves: look the digest up, then check a file
        # against it.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.zip"
            path.write_bytes(b"eawag")
            digest = check_updates.md5_of(path)

            item = {"assets": {"interlis": {"href": "https://x.invalid/a.zip", "md5": digest}}}
            found, algorithm = sources.parse_geodienste_checksum(item, "https://x.invalid/a.zip")
            self.assertEqual(check_updates.verify_download(path, found, algorithm), "checksum ok")


class TestComparison(unittest.TestCase):
    def make(self, tag: str) -> sources.Observation:
        return sources.Observation(target_id="t", tag=tag, label="test")

    def test_first_sighting_is_new_not_changed(self) -> None:
        # Otherwise the first run reports every dataset as updated, and a report
        # that cries wolf once gets ignored from then on.
        status, previous = check_updates.compare(self.make("a"), None)
        self.assertEqual(status, "new")
        self.assertEqual(previous, "")

    def test_same_tag_is_unchanged(self) -> None:
        status, _ = check_updates.compare(self.make("a"), {"tag": "a"})
        self.assertEqual(status, "unchanged")

    def test_different_tag_is_changed(self) -> None:
        status, previous = check_updates.compare(self.make("b"), {"tag": "a"})
        self.assertEqual(status, "changed")
        self.assertEqual(previous, "a")


class TestChecksumVerification(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "sample.bin"
        self.path.write_bytes(b"eawag")
        self.addCleanup(self.directory.cleanup)

    def test_sha256_match(self) -> None:
        digest = check_updates.sha256_of(self.path)
        self.assertEqual(check_updates.verify_download(self.path, digest, "sha256"), "checksum ok")

    def test_geoadmin_multihash_prefix_is_stripped(self) -> None:
        # data.geo.admin.ch publishes multihash: '1220' marks sha2-256 with a
        # 32-byte digest. Comparing without stripping it fails every time.
        digest = check_updates.sha256_of(self.path)
        self.assertEqual(
            check_updates.verify_download(self.path, "1220" + digest, "sha256"), "checksum ok"
        )

    def test_md5_match(self) -> None:
        digest = check_updates.md5_of(self.path)
        self.assertEqual(check_updates.verify_download(self.path, digest, "md5"), "checksum ok")

    def test_mismatch_is_reported_loudly(self) -> None:
        self.assertEqual(
            check_updates.verify_download(self.path, "00" * 32, "sha256"), "CHECKSUM MISMATCH"
        )

    def test_absent_checksum_is_stated_not_assumed_ok(self) -> None:
        self.assertEqual(
            check_updates.verify_download(self.path, None, None), "no checksum published"
        )


class TestStateRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.json"
        self.addCleanup(self.directory.cleanup)

    def test_missing_state_file_starts_empty(self) -> None:
        self.assertEqual(check_updates.load_state(self.path), {})

    def test_survives_a_round_trip(self) -> None:
        state = {"a/ZH": {"tag": "2026-01-09T21:03:35", "title": "LN"}}
        check_updates.save_state(self.path, state)
        self.assertEqual(check_updates.load_state(self.path), state)

    def test_no_temporary_file_is_left_behind(self) -> None:
        check_updates.save_state(self.path, {"a": {"tag": "x"}})
        self.assertFalse(self.path.with_suffix(".tmp").exists())


class TestPolicyHandling(unittest.TestCase):
    def test_manual_entries_are_reported_with_their_reason(self) -> None:
        # Manual entries exist so a source with no API stays visible. Dropping
        # them from the report is how a dataset quietly goes stale for years.
        results = check_updates.process_entry(
            {
                "id": "bafu-karst",
                "title": "Karst groundwater",
                "policy": "manual",
                "source": "manual",
                "manual_reason": "version is embedded in the URL path",
            },
            http=None,
            state={},
            staging_dir=Path("/nonexistent"),
            dry_run=True,
            downloads_left=[0],
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "manual")
        self.assertIn("embedded in the URL", results[0].detail)

    def test_unknown_source_type_is_an_error_not_a_crash(self) -> None:
        results = check_updates.process_entry(
            {"id": "typo", "title": "Typo", "source": "geoadmin_stak", "policy": "notify"},
            http=None,
            state={},
            staging_dir=Path("/nonexistent"),
            dry_run=True,
            downloads_left=[0],
        )
        self.assertEqual(results[0].status, "error")
        self.assertIn("unknown source type", results[0].detail)


class TestReportWriting(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.report_dir = Path(self.directory.name) / "reports"
        self.addCleanup(self.directory.cleanup)

    def test_writes_both_formats_and_highlights_changes(self) -> None:
        results = [
            check_updates.Result(
                target_id="ln/ZH", dataset_id="ln", title="LN", status="changed",
                previous_tag="2025-01-01T00:00:00", current_tag="2026-01-09T21:03:35",
                detail="geodienste updated_at, canton=ZH",
            ),
            check_updates.Result(
                target_id="tlm", dataset_id="tlm", title="swissTLM3D", status="unchanged"
            ),
            check_updates.Result(
                target_id="karst", dataset_id="karst", title="Karst", status="manual",
                detail="version embedded in URL",
            ),
        ]
        markdown_path, csv_path = check_updates.write_reports(
            results, self.report_dir, "2026-08-10T09:00:00Z"
        )
        text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("Action needed", text)
        self.assertIn("2026-01-09T21:03:35", text)
        self.assertIn("Manual check required", text)

        rows = csv_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 4)  # header plus three results
        self.assertTrue(rows[0].startswith("run_at,target_id"))


@unittest.skipIf(check_updates.tomllib is None, "no TOML reader on this interpreter")
class TestRealWatchlist(unittest.TestCase):
    """Check the shipped watchlist, not just a made-up one.

    A config file is code by another name. These catch the mistakes that are easy
    to make and hard to notice, above all a source type that does not exist and a
    fetch policy on something with no download URL.
    """

    def setUp(self) -> None:
        self.config = check_updates.load_config(Path(__file__).parent / "watchlist.toml")
        self.entries = self.config["dataset"]

    def test_every_id_is_unique(self) -> None:
        ids = [entry["id"] for entry in self.entries]
        self.assertEqual(len(ids), len(set(ids)), "duplicate dataset ids would overwrite state")

    def test_every_source_type_is_one_we_implement(self) -> None:
        allowed = set(sources.ADAPTERS) | {"manual"}
        for entry in self.entries:
            self.assertIn(entry["source"], allowed, f"{entry['id']} has an unknown source type")

    def test_each_source_type_has_the_fields_its_adapter_needs(self) -> None:
        required = {
            "geoadmin_stac": "collection",
            "http_headers": "url",
            "manual": "manual_reason",
        }
        for entry in self.entries:
            field = required.get(entry["source"])
            if field:
                self.assertIn(field, entry, f"{entry['id']} is missing {field!r}")
            if entry["source"] == "geodienste":
                self.assertTrue(
                    entry.get("base_topic") or entry.get("topic"),
                    f"{entry['id']} needs base_topic or topic",
                )

    def test_policies_are_recognised(self) -> None:
        for entry in self.entries:
            self.assertIn(entry.get("policy"), {"notify", "fetch", "manual"}, entry["id"])

    def test_manual_entries_explain_themselves(self) -> None:
        for entry in self.entries:
            if entry.get("policy") == "manual":
                self.assertGreater(
                    len(entry.get("manual_reason", "").strip()), 40,
                    f"{entry['id']} needs a stated reason, so it is not rediscovered later",
                )

    def test_download_limit_is_set_and_conservative(self) -> None:
        # geodienste allows one dataset per offering per day. An unbounded fetch
        # policy across 26 cantons would breach that on the first busy week.
        limit = self.config["settings"]["max_downloads_per_run"]
        self.assertGreaterEqual(limit, 1)
        self.assertLessEqual(limit, 5)

    def test_request_spacing_respects_the_published_fair_use_limit(self) -> None:
        # geodienste publish 20 website requests per minute, so 3 seconds apart.
        spacing = self.config["settings"]["min_seconds_between_requests"]
        self.assertGreaterEqual(spacing, 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
