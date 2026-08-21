# Mammoth Lakes Campground Occupancy

A lightweight dashboard that tracks **reserved occupancy** of the reservable
campgrounds in the Mammoth Lakes area, using Recreation.gov data. It's built to
be trusted and maintained by a non-engineer: one config file, two short Python
scripts, one static page, and a daily job that needs no credentials.

---

## 1. Architecture

```
campgrounds.py            the frozen roster (edit this to change what's tracked)
snapshot.py               daily: fetch Rec.gov -> snapshots/snapshot_YYYY-MM-DD.csv
build_dashboard_data.py   reduce all snapshots -> docs/dashboard_data.json
docs/index.html           the dashboard (GitHub Pages serves the docs/ folder)
docs/dashboard_data.json  generated data the page reads
snapshots/                append-only history, one CSV per day (never overwritten)
.github/workflows/snapshot.yml   runs the two scripts daily and commits the result
discover_campgrounds.py   one-off roster discovery / validation tool
```

Data flows one way: **Rec.gov → daily snapshot CSV → aggregated JSON → static
page.** No database, no server, no auth. History lives as plain CSVs in git.

## 2. Data sources (exact endpoints)

- **Month availability** (public, no key) — the occupancy signal:
  `https://www.recreation.gov/api/camps/availability/campground/{id}/month?start_date=YYYY-MM-01T00:00:00.000Z`
  Returns one object per campsite with an `availabilities` map of
  `{night -> status}`, plus `type_of_use`, `campsite_type`, `loop`, etc.
- **RIDB API** (documented, free key) — used **only** by `discover_campgrounds.py`
  to find campgrounds near Mammoth and their IDs:
  `https://ridb.recreation.gov/api/v1/facilities`. The daily job does **not**
  use RIDB and needs no key.

## 3. Campground inclusion

Built from data, not assumption (`discover_campgrounds.py`, September 2026 probe).
A campground is included only if it is **fully reservable online**. Excluded:
first-come/first-served campgrounds, **mixed** reservable + first-come
campgrounds, group-only campgrounds, and anything closed/out-of-season (no
sellable inventory). The 15 included campgrounds are listed in `campgrounds.py`.

## 4. Occupancy methodology

Per campground and night:

- **Occupied** = sites with status `Reserved`.
- **Available inventory** = sites with status `Available` or `Reserved` (sellable that night).
- **Occupancy %** = Occupied / Available inventory × 100.
- Statuses `Closed`, `Not Reservable`, `NYR`, `Not Available` are excluded from
  both numerator and denominator — never counted as occupied.

**Destination-wide** number = total occupied ÷ total inventory across all
included campgrounds (not an average of per-campground percentages).

It is **reserved occupancy**: it counts reservations, not whether the holder
physically arrived. Recreation.gov's public data has no check-in/scan feed, so
physical presence is not measured here.

## 5. Data model (each snapshot CSV row)

```
snapshot_date   the day the observation was taken
stay_date       the night being described
campground_id   Recreation.gov facility ID
campground_name display name
site_id         Recreation.gov campsite ID
site_status     raw status string from Rec.gov
occupied_flag   1 if Reserved, else 0
inventory_flag  1 if Available or Reserved, else 0
```

The aggregator uses, for each (campground, night), the **most recent** snapshot
that observed it — a past night's final reserved state, or a future night's
latest booked level.

## 6. Dashboard

`docs/index.html` shows: five summary numbers (tonight; the upcoming **Friday–Sunday**
weekend, 3 nights; the upcoming **10-day** window from Friday through the Sunday
9 days later, 2 weekends; reservable sites; campgrounds — each window also shows its
week-over-week change), one occupancy line chart (previous 14 +
upcoming 30 days, aggregate or a single campground, with a last-year line once
that history exists), a sortable campground table, an upcoming-weekends table,
and a collapsible validation section. Clean, white, mobile-friendly.

## 7. Historical snapshots

`snapshot.py` writes one CSV per day and never overwrites earlier ones, so
occupancy can be tracked over time. Week-over-week needs ~1 week of snapshots;
year-over-year needs ~1 year (aligned by weekday, not calendar date).

## 8. Setup / deployment

1. Create a GitHub repo and add these files.
2. Repo **Settings → Pages** → Deploy from a branch → `main` / `/docs`. That
   publishes the dashboard URL to share with the team.
3. The workflow in `.github/workflows/snapshot.yml` runs daily on its own. To
   populate data immediately, open **Actions → Daily occupancy snapshot → Run
   workflow**.
4. To change the roster, edit `campgrounds.py` and commit. Nothing else changes.

Run locally instead (optional): `pip install requests`, then
`python snapshot.py && python build_dashboard_data.py`, and open `docs/index.html`.

## 9. Validation checklist

- **Site counts**: open the dashboard's validation section (or run
  `discover_campgrounds.py`) and compare *campground → ID → reservable sites*
  against Recreation.gov by hand.
- **Impossible occupancy**: aggregator can't exceed 100% because inventory
  includes every occupied site by construction; the page also caps the axis.
- **Unknown statuses**: `snapshot.py` prints any status string it doesn't
  recognize so the mapping can be reviewed before it silently mis-buckets.
- **Duplicate records**: one row per (campground, site, night) per snapshot;
  the aggregator dedups by taking the latest snapshot per night.
- **Multi-night reservations**: handled naturally — each night is recorded
  independently from the availability map.
- **Cancellations**: reflected on the next snapshot (a freed night flips
  Reserved → Available).
- **Partial / seasonal closures**: closed sites carry non-inventory statuses and
  drop out of both numerator and denominator automatically.
- **Missing dates**: nights the endpoint doesn't return are simply absent, not
  counted as zero.
- **Rate limiting**: `snapshot.py` paces ~1 request/second with exponential
  backoff and retries, so a transient 429 doesn't drop a campground.

## 10. Known limitations

- **No back-history.** Recreation.gov exposes no historical occupancy, so the
  time series only grows from the day snapshots start. There is no way to
  backfill last year.
- **Reserved, not physical.** The metric reflects bookings, not arrivals or
  no-shows.
- **Undocumented endpoint.** The month-availability endpoint is public but not
  officially documented; its shape or statuses could change. The status audit
  is the early-warning system for that.
- **Season-dependent counts.** "Reservable sites" reflects currently sellable
  inventory, so a campground reads lower when a loop closes late in the season.
- **Snapshot timing.** Occupancy is captured once a day; intraday churn between
  snapshots isn't seen.
```
