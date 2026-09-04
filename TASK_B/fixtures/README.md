# Test fixtures

Responses used by `test_check_updates.py` so the parsers can be tested without a
network. Some are real captures, some were written by hand to exercise a code
path that the live services do not currently produce. Which is which matters, so
it is recorded here rather than left to be assumed.

## Real captures

Taken from the live services on 2026-08-10. Trimmed to the fields the parsers
read, but every value below is as the service returned it.

| File | Source |
|---|---|
| `geoadmin_swisstlm3d.json` | `https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swisstlm3d` |
| `geoadmin_bafu_klaeranlagen.json` | `https://data.geo.admin.ch/api/stac/v1/collections/ch.bafu.gewaesserschutz-klaeranlagen` |
| `geodienste_stac_item_zh.json` | `https://geodienste.ch/stac/collections/lwb_nutzungsflaechen/items/lwb_nutzungsflaechen_v2_0-ZH` |

Re-capture with, for example:

```bash
curl -s "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swisstlm3d" \
  > geoadmin_swisstlm3d.json
```

## Constructed

Written by hand. Realistic in shape, but the values are invented and should not
be quoted as evidence about any canton or file.

**`geodienste_lwb_nutzungsflaechen_zh.json`** is a mixture, which is why it needs
spelling out:

- the **ZH** entry is a real capture from 2026-08-10;
- the **AG** entry was constructed to give the tests a canton on model version
  3.0 alongside ZH on 2.0. Its timestamp is invented. On 2026-08-10 the live API
  actually returned AG on version 2.0 with `updated_at` 2025-12-02;
- the **GL** entry was constructed to give the tests a canton that is listed but
  not yet publishing, so that `updated_at: null` and the "canton comes online"
  transition are covered. The live API did not return GL for this topic at all.

**`http_headers_bafu_zip.json`** is entirely constructed. No HEAD request was ever
made against the FOEN download. The four header combinations (ETag present, ETag
absent, length only, nothing usable) exist to exercise the preference order in
`parse_http_headers`, and the byte count and modification date are plausible
rather than observed.

## Why the distinction is written down

Inventing a fixture is normal and often the only way to reach a branch. Calling
an invented one a live capture is not, because the next person to read it has no
way to tell that the AG timestamp is fiction, and might reasonably cite it. An
earlier version of this file claimed everything here was captured verbatim, which
was wrong.
