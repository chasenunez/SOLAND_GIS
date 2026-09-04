"""Adapters that ask each data provider "has this changed?" and answer in one
common shape.

Three kinds of source, because Swiss geodata is published three different ways:

  geoadmin_stac   data.geo.admin.ch runs a STAC API. Both swisstopo and most FOEN
                  layers live there. It reports an `updated` timestamp per
                  collection and per asset, plus checksums. This is the best case.
  geodienste      geodienste.ch publishes a services API with one record per
                  canton, carrying `updated_at` and the download URL. Cantons
                  update independently, so each canton is watched separately.
  http_headers    Some FOEN datasets are plain zip files with no API at all. All
                  we can do is read the response headers and watch for a change
                  in ETag, Last-Modified or Content-Length.

Everything here uses only the standard library. That is deliberate: this tool
should still run in five years without anyone having to resurrect a dependency.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "eawag-dataset-update-check/1.0 (research data management; contact your data steward)"


class SourceError(Exception):
    """Raised when a source cannot be checked. Carries a readable reason.

    Kept as its own type so the caller can record a per-dataset failure and carry
    on, rather than losing the whole run to one unreachable server.
    """


@dataclass(frozen=True)
class Observation:
    """One checkable thing, as it looks right now.

    `tag` is the only value compared between runs. Everything else is for the
    report. Keeping the comparison down to a single string is what makes the
    change detection easy to reason about and easy to trust.
    """

    target_id: str
    tag: str
    label: str
    download_url: str | None = None
    extra: dict = field(default_factory=dict)

    # No checksum field here on purpose. Checksums are fetched by
    # `fetch_checksum` at download time, not carried on every observation: an
    # earlier version had the field, no adapter ever filled it, and the download
    # path silently reported "no checksum published" for everything while the
    # documentation claimed otherwise. A field nobody populates is worse than no
    # field, because its presence implies a guarantee that is not met.


class Http:
    """Minimal HTTP client with per-host rate limiting.

    The rate limiting matters. geodienste.ch publishes fair-use limits (20 website
    requests per minute, one dataset download per offering per day) and reserves
    the right to restrict access when they are exceeded. A weekly check sits well
    inside that, but only if it paces itself rather than firing 26 cantonal
    queries at once.
    """

    def __init__(self, min_interval: float = 3.0, timeout: float = 30.0) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_at: dict[str, float] = {}

    def _wait_turn(self, url: str) -> None:
        """Sleep if we contacted this host too recently."""
        host = urllib.parse.urlparse(url).netloc
        elapsed = time.monotonic() - self._last_request_at.get(host, 0.0)
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at[host] = time.monotonic()

    def _open(self, url: str, method: str = "GET"):
        self._wait_turn(url)
        request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            raise SourceError(f"HTTP {error.code} for {url}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceError(f"could not reach {url}: {error}") from error

    def get_json(self, url: str) -> dict:
        with self._open(url) as response:
            try:
                return json.loads(response.read().decode("utf-8"))
            except json.JSONDecodeError as error:
                raise SourceError(f"{url} did not return valid JSON: {error}") from error

    def get_headers(self, url: str) -> dict[str, str]:
        """Return response headers, preferring HEAD.

        Some servers mishandle HEAD, so fall back to a ranged GET asking for a
        single byte. That gets the same headers without pulling a 60 MB zip.
        """
        try:
            with self._open(url, method="HEAD") as response:
                return {key.lower(): value for key, value in response.headers.items()}
        except SourceError:
            self._wait_turn(url)
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return {key.lower(): value for key, value in response.headers.items()}
            except Exception as error:  # noqa: BLE001 - reported, not swallowed
                raise SourceError(f"could not read headers for {url}: {error}") from error

    def download(self, url: str, destination: Path) -> int:
        """Stream a URL to disk and return the byte count.

        Streams in chunks rather than reading into memory, because several of
        these files run to hundreds of megabytes.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with self._open(url) as response, destination.open("wb") as handle:
            while chunk := response.read(1 << 20):  # 1 MiB at a time
                handle.write(chunk)
                total += len(chunk)
        return total


# --------------------------------------------------------------------------
# Adapters. Each takes a watchlist entry plus an Http, and returns one or more
# Observations. Parsing is split out from fetching so the parsers can be tested
# against recorded responses without a network.
# --------------------------------------------------------------------------


def parse_geoadmin_stac(payload: dict, entry: dict) -> list[Observation]:
    """Read a data.geo.admin.ch STAC collection response.

    The collection-level `updated` timestamp changes whenever any asset in the
    collection is republished, which is exactly the signal we want. Item-level
    detail is left out on purpose: it multiplies requests without changing the
    answer to whether the collection needs revisiting.
    """
    updated = payload.get("updated")
    if not updated:
        raise SourceError(f"no 'updated' field in STAC collection {entry['collection']}")

    interval = payload.get("extent", {}).get("temporal", {}).get("interval", [[None, None]])
    data_end = interval[0][1] if interval and len(interval[0]) > 1 else None

    return [
        Observation(
            target_id=entry["id"],
            tag=updated,
            label="STAC collection updated",
            download_url=f"https://data.geo.admin.ch/browser/index.html#/collections/{entry['collection']}",
            extra={
                "title": payload.get("title", ""),
                "data_reference_date": data_end or "",
            },
        )
    ]


def check_geoadmin_stac(entry: dict, http: Http) -> list[Observation]:
    url = f"https://data.geo.admin.ch/api/stac/v1/collections/{entry['collection']}"
    return parse_geoadmin_stac(http.get_json(url), entry)


def parse_geodienste(payload: dict, entry: dict) -> list[Observation]:
    """Read a geodienste.ch services response, one Observation per canton.

    Cantons publish independently, so a single timestamp for the whole topic
    would hide exactly the change you care about. Cantons that are listed but not
    yet publishing produce an Observation with an empty timestamp, which reads in
    the report as "still in preparation" rather than silently vanishing.
    """
    services = payload.get("services")
    if services is None:
        raise SourceError(f"unexpected geodienste response for {entry['base_topic']}")

    # A canton normally appears once per base topic, so `<id>/<canton>` is the
    # readable target id. It is not guaranteed though: cantons migrate between
    # model versions independently, and during a migration one could publish both
    # 2.0 and 3.0. Two rows would then collapse onto one target id and the second
    # would silently overwrite the first in the state file, which is the worst
    # kind of bug here because the report would look entirely normal.
    #
    # So the version is appended only where a canton appears more than
    # once. Normal runs stay uncluttered, and the ambiguous case is impossible
    # rather than merely unlikely.
    seen: dict[str, int] = {}
    for service in services:
        canton = service.get("canton", "??")
        seen[canton] = seen.get(canton, 0) + 1
    ambiguous = {canton for canton, count in seen.items() if count > 1}
    if ambiguous:
        log.info(
            "cantons publishing more than one model version of %s: %s. "
            "Their target ids carry the version to keep them distinct.",
            entry.get("base_topic") or entry.get("topic"), sorted(ambiguous),
        )

    observations = []
    for service in services:
        canton = service.get("canton", "??")
        updated_at = service.get("updated_at") or ""
        target_id = f"{entry['id']}/{canton}"
        if canton in ambiguous:
            target_id = f"{target_id}/v{service.get('version', 'unknown')}"
        observations.append(
            Observation(
                target_id=target_id,
                tag=updated_at or "not-published",
                label="geodienste updated_at",
                download_url=service.get("dataset_url"),
                extra={
                    "canton": canton,
                    "topic": service.get("topic", ""),
                    "update_cycle": service.get("data_update_cycle", ""),
                    "publication": service.get("publication_data", ""),
                    "terms": service.get("opendata_terms_data", ""),
                    "stac_item_url": service.get("stac_item_url", ""),
                },
            )
        )

    return sorted(_ensure_unique_ids(observations), key=lambda obs: obs.target_id)


def _ensure_unique_ids(observations: list[Observation]) -> list[Observation]:
    """Guarantee that no two observations share a target id.

    The version suffix above handles the case we can foresee. This is the
    backstop for the one we cannot: two rows identical in both canton and
    version would still collide, and a collision means one silently overwrites
    the other in the state file. Better a target id with an ugly counter on it,
    and a warning, than a watch that quietly stops working.
    """
    counts: dict[str, int] = {}
    result = []
    for observation in observations:
        seen_before = counts.get(observation.target_id, 0)
        counts[observation.target_id] = seen_before + 1
        if seen_before:
            log.warning(
                "duplicate target id %s from the provider; disambiguating as %s#%d",
                observation.target_id, observation.target_id, seen_before + 1,
            )
            observation = replace(
                observation, target_id=f"{observation.target_id}#{seen_before + 1}"
            )
        result.append(observation)
    return result


def check_geodienste(entry: dict, http: Http) -> list[Observation]:
    """Query the geodienste services API for one topic.

    Note the two parameter names: `base_topics` takes an unversioned topic such
    as `lwb_nutzungsflaechen`, `topics` takes a versioned one such as
    `lwb_nutzungsflaechen_v2_0`. Passing an unversioned name to `topics` returns
    an empty list rather than an error, which is a easy way to conclude wrongly
    that a topic does not exist.
    """
    if entry.get("topic"):
        query = urllib.parse.urlencode({"topics": entry["topic"]})
    else:
        query = urllib.parse.urlencode({"base_topics": entry["base_topic"]})
    return parse_geodienste(http.get_json(f"https://geodienste.ch/info/services.json?{query}"), entry)


def parse_http_headers(headers: dict[str, str], entry: dict) -> list[Observation]:
    """Build a change tag from whatever the server is willing to tell us.

    Preference order is deliberate. ETag is the server's own opinion about
    whether the body changed, so it is the most reliable. Last-Modified is next.
    Content-Length is the weakest, because an edit that preserves file size would
    slip past it, but it is better than nothing and these files are zips where
    that is unlikely.
    """
    parts = []
    for header in ("etag", "last-modified", "content-length"):
        value = headers.get(header)
        if value:
            parts.append(f"{header}={value.strip()}")

    if not parts:
        raise SourceError(
            f"{entry['url']} returned no ETag, Last-Modified or Content-Length, "
            "so changes to it cannot be detected from headers alone"
        )

    return [
        Observation(
            target_id=entry["id"],
            tag="; ".join(parts),
            label="HTTP headers",
            download_url=entry["url"],
            extra={"weak_signal": "etag" not in headers and "last-modified" not in headers},
        )
    ]


def check_http_headers(entry: dict, http: Http) -> list[Observation]:
    return parse_http_headers(http.get_headers(entry["url"]), entry)


def parse_geodienste_checksum(item: dict, download_url: str) -> tuple[str | None, str | None]:
    """Pull the md5 for one download out of a geodienste STAC item.

    Assets are matched on href rather than on their key, because the key names
    the format ("interlis", "geopackage_zip") while the services API hands us a
    URL. Matching on the URL means we verify the file we actually fetched.

    The md5 field is used rather than the STAC-standard `file:checksum`. On the
    ZH item, `file:checksum` reads "d51039386239386663366636386430303163", whose
    hex decodes to the ASCII text "98b98fc6f68d001c", which is the first sixteen
    characters of the md5 next to it. So it is a truncated hex-of-hex encoding
    rather than a multihash, and comparing against it would fail every time.
    """
    assets = item.get("assets", {})
    for asset in assets.values():
        if asset.get("href") == download_url:
            digest = asset.get("md5")
            return (digest, "md5") if digest else (None, None)
    return None, None


def fetch_checksum(observation: Observation, http: Http) -> tuple[str | None, str | None]:
    """Look up a published checksum for an observation, just before downloading.

    Deliberately not done during the regular check. Every watched target would
    cost an extra request, which for a topic spanning 26 cantons would push a
    weekly run towards geodienste's fair-use limit for no benefit: knowing the
    checksum tells you nothing about whether the file changed, since the
    timestamp already did.

    Returns (None, None) where no checksum is published, which is the correct
    answer for the geo.admin entries (notify only, so nothing is fetched) and
    for the plain FOEN zips (no checksum at all).
    """
    item_url = observation.extra.get("stac_item_url")
    if not item_url or not observation.download_url:
        return None, None

    try:
        return parse_geodienste_checksum(http.get_json(item_url), observation.download_url)
    except SourceError as error:
        # An unreachable checksum must not abort a download that is otherwise
        # fine. The caller reports "no checksum published" and carries on.
        log.warning("could not read checksum from %s: %s", item_url, error)
        return None, None


ADAPTERS = {
    "geoadmin_stac": check_geoadmin_stac,
    "geodienste": check_geodienste,
    "http_headers": check_http_headers,
}


def observe(entry: dict, http: Http) -> list[Observation]:
    """Dispatch a watchlist entry to its adapter."""
    adapter = ADAPTERS.get(entry.get("source", ""))
    if adapter is None:
        raise SourceError(
            f"unknown source type {entry.get('source')!r}; expected one of {sorted(ADAPTERS)}"
        )
    return adapter(entry, http)
