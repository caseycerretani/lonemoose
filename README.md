# dcpermits

An agent that harvests **publicly available building-permit data nationwide**
and surfaces **data-center construction activity** from it.

There is no national building-permit database. Permits are issued by
thousands of independent cities and counties, each publishing (or not
publishing) on its own schedule, in its own format, with its own column
names. This agent assembles that into one queryable, deduplicated dataset
and scores every record for data-center relevance.

```bash
dcpermits discover                 # find and validate permit sources nationwide
dcpermits harvest                  # fetch, classify and store
dcpermits report                   # growth analysis
```

## What it produced on a live run

A sweep of 55 auto-discovered sources (46 reachable) returned **277
data-center candidate permits** — before the precision fixes described
below, which removed the Google Fiber and Apple-retail false positives, so
a current run returns a smaller and cleaner set. Genuine hyperscale
projects it surfaced:

| Valuation | Market | Type | Description |
|---|---|---|---|
| $569.5M | Mesa, AZ | expansion | Tenant improvement in existing 868,431 sf 3-story addition for a data center |
| $294.9M | Mesa, AZ | fit-out | 490,782 SF shell space data center — modifications into 3 data halls |
| $190.9M | TX | new build | — |
| $171.8M | Mesa, AZ | expansion | New 868,431 sf 3-story addition for a data center |

Mesa AZ, Northern Virginia, Central Texas and Chicago are among the largest
data-center markets in the country, so this is the activity you would want
a nationwide sweep to find.

## How it works

```
discover → probe → harvest → map → classify → store → report
```

### 1. Discovery: finding the data at all

A hardcoded list of endpoints cannot cover the country and rots as agencies
republish their layers. So discovery is part of the agent's job, driven by
the two catalogs that index public permit data at national scale:

- **Socrata Discovery API** — every dataset on every public Socrata portal
- **ArcGIS Hub** — ~10,000 permit-ish feature layers published by counties
  and cities

Catalog hits are only *candidates*. Each is probed — schema introspected,
fields mapped — and admitted only if it genuinely looks like a permit table
with an identity column, a date, and searchable text. That probe step is
what keeps the registry free of inspection logs, sign permits and
right-of-way layers that merely contain the word "permit".

The validated registry is cached in `data/sources.json`, so routine
harvests cost nothing extra. A curated `seed_sources.json` can pin sources
verified by hand.

### 2. Schema mapping: the actual hard part

No two jurisdictions name their columns alike. A description lives under
`DESCRIPTION`, `work_description`, `PROJ_DESC`, `scope_of_work` or
`Field14`. Rather than a per-jurisdiction lookup table, `schema_map.py`
scores every provider field against patterns for each canonical field and
assigns greedily by descending score.

Mapping mistakes found and fixed against live data, each now a regression
test:

- `total_fee` mapped to `valuation` — understates a project ~1000×
- Socrata's `location` (a GeoJSON dict) mapped to `address` — yields
  `"{'type': 'Point', ...}"` as a street address
- `contact_10_zipcode` mapped to `zipcode` — that's the applicant's mailing
  ZIP, not the job site
- `street_direction` mapped to `address` — puts a bare `"N"` in the field.
  Chicago has no single address column, so components are detected and
  assembled instead (`100 N MICHIGAN AVE`)

`dcpermits inspect <endpoint>` shows the derived mapping for any endpoint,
which is how you debug a source returning nonsense.

### 3. Classification: precision over recall

Keyword grep performs badly here in both directions, so classification is a
weighted signal model over four groups:

| Group | Examples | Role |
|---|---|---|
| **direct** | "data center", "data hall", "hyperscale", "colocation" | enough on their own |
| **operator** | owner/applicant matches the operator registry | strong lead |
| **supporting** | generators, switchgear, CRAH units, raised floor, "8 MW", N+1 | only in combination |
| **scale** | valuation and square-footage tiers | amplifier only, never a trigger |

Confidence tiers are gated on the *kind* of evidence, not just the score:

- **confirmed** requires an explicit topical term
- **probable** requires a topical term, or an operator match corroborated by
  a supporting signal or real scale
- **possible** is everything else above the floor — a lead to check

Three false-positive classes drove that design, all found in live data:

1. **Low-voltage "data" work.** Permit text is saturated with "voice and
   data cabling", "data outlets", "data drops". Hard-vetoed — unless a
   direct term also fires, because "data cabling in the new data center"
   *is* a data-center permit.
2. **Large institutional buildings.** A hospital central plant has
   chillers, cooling towers, emergency generators and switchgear — exactly
   like a data hall. A $420M hospital scored 62 ("probable") before scale
   amplifiers were restricted to named anchors and a building-type discount
   was added. It now scores 0.
3. **Hyperscaler names on unrelated work.** Live results attributed 11
   permits to Google that were all *Google Fiber* fiber-hut and vault
   installs, and 5 to Apple that were retail and sign permits. Fixed by
   vetoing telecom outside-plant work and by requiring corroboration before
   an operator match can reach "probable".

Every classification carries the signals that fired, so any number in a
report traces back to the words that produced it:

```bash
$ dcpermits classify "NEW 250,000 SF HYPERSCALE DATA CENTER WITH 12 DIESEL GENERATORS" --valuation 380000000
{
  "score": 127.5,
  "tier": "confirmed",
  "role": "new_build",
  "signals": ["hyperscale", "data_center", "generator_bank", "switchgear",
              "generators", "valuation>=250_000_000", "sqft>=200_000"]
}
```

### 4. Reporting: measuring growth, not permit counts

Raw permit counts mislead badly in this domain. One campus generates dozens
of permits (shell, each building, electrical, mechanical, generator yard)
while another jurisdiction issues one permit for the same scope — so a
market can appear to triple because its clerk started splitting trades onto
separate records.

The reports therefore lead with measures that survive that noise:

- **new-build and expansion counts**, separated from fit-out and equipment
  work, since only the former adds footprint
- **declared valuation**, which tracks scope better than record counts
- **quarter-over-quarter deltas** per market, with a minimum-volume floor so
  a one-permit blip doesn't drown the real movers
- **operator attribution**, so activity traces to who is building

An optional capacity estimate converts new-build valuation to megawatts at
a configurable $/MW. It is a crude order-of-magnitude heuristic, labelled as
such wherever it appears — permit valuations are self-declared, exclude IT
equipment, and are missing entirely in many jurisdictions. It is not a
substitute for utility interconnection filings.

## Install

Runtime is **stdlib-only**, so it runs in a bare container or cron job with
no install step.

```bash
pip install -e .            # optional, for the `dcpermits` entry point
pip install -e '.[dev]'     # plus pytest
```

Or run it directly: `python -m dcpermits.cli --help`

## Usage

```bash
# Build the source registry (start here)
dcpermits discover --per-query 45 --max-probes 110

# Harvest the last year, nationwide
dcpermits harvest

# Narrow to specific markets, with more history
dcpermits harvest --state VA --state AZ --state TX --lookback-days 1095

# Higher recall: skip server-side keyword filtering (much slower)
dcpermits harvest --full-scan --max-sources 5

# Reports
dcpermits report                                   # markdown to stdout
dcpermits report --format json --out report.json
dcpermits report --format csv --out candidates.csv
dcpermits report --format geojson --out map.geojson
dcpermits report --level state --min-tier confirmed
dcpermits report --operator "Amazon Web Services"
dcpermits report --role new_build --min-valuation 50000000

# Diagnostics
dcpermits sources                       # what's in the registry
dcpermits inspect <endpoint> --sample 5 # debug a source's field mapping
dcpermits classify "<permit text>"      # explain a scoring decision
dcpermits status                        # store stats and recent runs
```

Running on a schedule is the intended mode — the store accumulates across
runs so trends become measurable:

```cron
0 6 * * * cd /srv/dcpermits && python -m dcpermits.cli harvest --lookback-days 45
0 7 * * 1 cd /srv/dcpermits && python -m dcpermits.cli discover --max-probes 150
```

## Design notes

**Resilience.** One broken county endpoint must never abort a nationwide
run. Per-source failures are caught, recorded on the run row, and reported
at the end — a run that reaches 90% of sources and names the other 10% is
far more useful than one that raises on the first timeout. A live run hit 9
failures out of 55 sources (one host dropping connections) and still
returned 277 candidates.

**Graceful query degradation.** Connectors try the most selective query
first — server-side keyword filter plus date filter — then relax to
date-only, then to a full scan with local filtering. Portals run a wide
range of server versions, and "this query should work" is not a safe
assumption. ArcGIS in particular reports query errors with HTTP 200 and an
error body, which is checked explicitly.

**Idempotent storage.** Permits are upserted on a stable `uid` keyed on
source + permit number, so re-runs and overlapping sources don't duplicate.
`first_seen` is preserved while the rest of the row refreshes, so status and
valuation revisions are captured without losing the original detection date.

**Run time is dominated by politeness, not compute.** Per-host rate
limiting means many layers published on one host serialize, and the
keyword set is pushed server-side in batches to stay inside URL limits, so
a source costs several requests. A full nationwide sweep is a
tens-of-minutes job, not seconds — which is why it is designed to run on a
schedule against an accumulating store rather than on demand. Use
`--state`, `--max-sources` or `--connector` to scope an interactive run,
and raise `--delay` (never lower it much) if a publisher asks you to.

**Conservative attribution.** A wrong state silently moves permits into the
wrong market, which is worse than no state at all. State inference uses
`.xx.us` domains, full state names, a portal-keyword map, and bare
two-letter codes *only* when capitalised as a state code would be — so
"Building Permits in 2024" does not become Indiana, and `brampton.ca`
(Ontario) does not become California.

## Operating politely

This agent queries government open-data APIs, which exist to be queried.
It is still built to stay a good citizen:

- **per-host** rate limiting (default 1 req/sec/host), not a global sleep
- `robots.txt` consulted once per host and cached; a blocked host is skipped
- bounded retries with exponential backoff and jitter, only for transient
  failures — a 404 is never retried
- an identifiable User-Agent with a contact URL
- server-side filtering wherever possible, so a nationwide sweep transfers
  a tiny fraction of what a full scrape would
- hard response-size caps

`--ignore-robots` exists but should only be used where you have confirmed
the data policy permits it. Note also that permit records can contain
personal names and addresses of individual property owners; the classifier
discards residential work, but if you retain the store, treat it as
containing personal data.

Coverage is a floor, not a ceiling: many jurisdictions publish permits only
as PDFs, behind interactive portals (Accela, Tyler EnerGov) with no public
API, or not at all. Absence of permits from a market means absence of
*published, machine-readable* permits, not absence of construction.

## Layout

| Path | Purpose |
|---|---|
| `dcpermits/discovery.py` | nationwide source discovery, probing, registry |
| `dcpermits/schema_map.py` | heuristic field mapping |
| `dcpermits/classify.py` | data-center signal scoring |
| `dcpermits/connectors/` | Socrata, ArcGIS, CSV/JSON |
| `dcpermits/pipeline.py` | the agent loop |
| `dcpermits/store.py` | SQLite persistence, dedup, run history |
| `dcpermits/report.py` | growth analysis and exports |
| `dcpermits/httpclient.py` | rate-limited, robots-aware HTTP |
| `dcpermits/data/operators.json` | operator/shell-entity registry (editable) |

## Extending it

**Add an operator.** Edit `dcpermits/data/operators.json`. Nothing in the
code is hardcoded to those names. Shell-entity matching is explicitly
heuristic — entities get renamed and reused, and press reports are
sometimes wrong — so shells score below direct names and always report the
matched string for human checking.

**Add a source the catalogs miss.** Add it to
`dcpermits/data/seed_sources.json`; hand-curated sources override
discovered ones.

**Tune classification.** `ClassifierConfig` carries the thresholds and
scale tiers. Verify changes with `dcpermits classify` and the test suite,
which encodes the false-positive classes above as regression tests.

## Tests

```bash
python -m pytest        # 266 tests, fully offline
```

Connector tests use a fake HTTP client with canned responses, so the suite
never depends on a county web server being up. Several fixtures are real
schemas and real permit descriptions captured from live endpoints — the
bugs in this codebase were found by real data, not invented inputs.
