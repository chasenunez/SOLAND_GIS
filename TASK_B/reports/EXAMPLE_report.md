> Example only. Generated from the recorded fixtures so you can see the shape
> of a weekly report before the first live run. Not real current data.

# Dataset update report, 2026-08-10

Run at 2026-08-10T07:00:00Z. Checked 11 targets.

| Status | Count |
|---|---|
| changed | 3 |
| error | 3 |
| manual | 2 |
| unchanged | 3 |

## Action needed

These have changed since the last run.

### Flussordnungszahlen (FLOZ) für das digitale Gewässernetz (bafu-flussordnungszahlen)

- was: `etag="0000000000000000"; last-modified=Thu, 25 Apr 2024 09:12:44 GMT; content-length=66573824`
- now: `etag="3f7a1c9d8b2e4056"; last-modified=Thu, 25 Apr 2024 09:12:44 GMT; content-length=66573824`
- HTTP headers
- source: https://www.bafu.admin.ch/dam/en/sd-web/beh3IJKG3bLy/flussordnungszahlenflozfuerdasdigitalegewaessernetzderschweiz.zip

### Landwirtschaftliche Nutzungsflächen (LN) (ln-nutzungsflaechen/ZH)

- was: `2025-01-14T10:02:11`
- now: `2026-01-09T21:03:35`
- geodienste updated_at, canton=ZH, update_cycle=Jährlich, publication=Frei erhältlich
- source: https://geodienste.ch/downloads/interlis/lwb_nutzungsflaechen/ZH/lwb_nutzungsflaechen_v2_0_ZH_lv95.zip

### swissTLM3D (swisstlm3d)

- was: `2025-03-11T09:12:00.000000Z`
- now: `2026-02-23T15:40:03.296813Z`
- STAC collection updated, data_reference_date=2026-02-24T00:00:00Z
- source: https://data.geo.admin.ch/browser/index.html#/collections/ch.swisstopo.swisstlm3d

## Could not be checked

- **Abwasserreinigungsanlagen (ARA) ohne Finanzkennzahlen** (`ara-klaeranlagen`): no fixture for https://geodienste.ch/info/services.json?base_topics=klaeranlagen_ohne_finanzkennzahlen
- **Topographische Einzugsgebiete Schweizer Gewässer** (`bafu-einzugsgebiete`): no fixture for https://data.geo.admin.ch/api/stac/v1/collections/ch.bafu.wasser-einzugsgebietsgliederung
- **swissBOUNDARIES3D** (`swissboundaries3d`): no fixture for https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissboundaries3d

## Manual check required

- **ARA-DB full download (on request)** (`bafu-ara-db-download`): The FOEN water geodata page lists the ARA-DB download as available only on request to the Water Division, so there is nothing to poll. The catalogue entry is watched separately as "bafu-ara-db"; when that reports a change, that is the moment to ask FOEN for a fresh extract.
- **Karst groundwater extent, FOEN** (`bafu-karst-groundwater`): The version is embedded in the URL path (v2026-03-17), so watching a fixed URL would never see a new release: a new version appears at a different address entirely. No documented listing endpoint for data.bafu.admin.ch was found, and an endpoint inferred from a naming pattern would fail silently. Check the FOEN water geodata page a few times a year, or ask FOEN whether an index exists.

## Unchanged

3 targets were unchanged. Listed in the CSV.
