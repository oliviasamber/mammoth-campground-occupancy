"""
build_dashboard_data.py  —  turn the snapshot history into one JSON the
dashboard reads. Pure data reduction, no network calls.

Key rules (see README for rationale):
  * For each (campground, night), use the MOST RECENT snapshot that observed
    that night. For a past night that's its final reserved state; for a future
    night it's the latest booked level.
  * Destination-wide occupancy = total occupied / total inventory across
    included campgrounds — NOT an average of per-campground percentages.
  * Week-over-week = occupancy for the same upcoming nights now vs. as recorded
    ~7 days ago. Null until a ~7-day-old snapshot exists.
  * Year-over-year plumbing aligns by weekday (not calendar date) and stays
    empty until snapshots from ~1 year prior exist.
"""

import csv
import datetime as dt
import glob
import json
import os
from collections import defaultdict

from campgrounds import CAMPGROUNDS, NAME_BY_ID, CLUSTER_BY_ID

SNAPSHOT_DIR = "snapshots"
OUT_PATH = os.path.join("docs", "dashboard_data.json")

CHART_DAYS_BACK = 14
CHART_DAYS_FWD = 30
INCLUDED_IDS = [cid for cid, _, _ in CAMPGROUNDS]


def load_snapshots():
    """
    Returns obs[(campground_id, stay_date)] = {
        'snapshot_date': latest snapshot that saw this night,
        'occupied': int, 'inventory': int, 'sites': int
    }
    and by_snap[snapshot_date][(cg, stay_date)] = (occupied, inventory) for the
    week-over-week lookup.
    """
    latest = {}                                   # (cg, stay) -> row dict
    by_snap = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for path in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "snapshot_*.csv"))):
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                cid = r["campground_id"]
                if cid not in NAME_BY_ID:
                    continue  # ignore campgrounds no longer in the roster
                stay = r["stay_date"]
                snap = r["snapshot_date"]
                occ = int(r["occupied_flag"])
                inv = int(r["inventory_flag"])

                cell = by_snap[snap][(cid, stay)]
                cell[0] += occ
                cell[1] += inv

                key = (cid, stay)
                cur = latest.get(key)
                if cur is None or snap > cur["snapshot_date"]:
                    latest[key] = {"snapshot_date": snap, "occupied": 0,
                                   "inventory": 0, "sites": 0}
                    cur = latest[key]
                if snap == cur["snapshot_date"]:
                    cur["occupied"] += occ
                    cur["inventory"] += inv
                    cur["sites"] += 1
    return latest, by_snap


def pct(occupied, inventory):
    return round(100 * occupied / inventory, 1) if inventory else None


def agg_over(latest, ids, dates):
    """Total occupied / total inventory across ids over dates (correct aggregate)."""
    occ = inv = 0
    for cid in ids:
        for d in dates:
            cell = latest.get((cid, d))
            if cell:
                occ += cell["occupied"]
                inv += cell["inventory"]
    return occ, inv


def daterange(start, n):
    return [(start + dt.timedelta(days=i)).isoformat() for i in range(n)]


def upcoming_friday(today):
    """The next Friday on or after `today`."""
    d = today
    while d.weekday() != 4:      # Friday = weekday 4
        d += dt.timedelta(days=1)
    return d


def next_weekends(today, count=6):
    """Upcoming Friday–Sunday weekends as (label, [fri, sat, sun]) night lists."""
    d = upcoming_friday(today)
    out = []
    for _ in range(count):
        nights = [(d + dt.timedelta(days=i)).isoformat() for i in range(3)]  # Fri,Sat,Sun
        sun = d + dt.timedelta(days=2)
        out.append((f"{d:%a %b %-d}\u2013{sun:%a %b %-d}", nights))
        d += dt.timedelta(days=7)
    return out


def build():
    latest, by_snap = load_snapshots()
    today = dt.date.today()

    chart_start = today - dt.timedelta(days=CHART_DAYS_BACK)
    chart_dates = daterange(chart_start, CHART_DAYS_BACK + CHART_DAYS_FWD + 1)

    # ── main series: aggregate + per-campground occupancy by night ──────────
    aggregate = []
    for d in chart_dates:
        occ, inv = agg_over(latest, INCLUDED_IDS, [d])
        aggregate.append(pct(occ, inv))

    by_campground = {}
    for cid in INCLUDED_IDS:
        by_campground[cid] = [pct(*agg_over(latest, [cid], [d])) for d in chart_dates]

    # ── summary metrics ─────────────────────────────────────────────────────
    tonight = today.isoformat()
    tonight_occ, tonight_inv = agg_over(latest, INCLUDED_IDS, [tonight])

    # Two headline windows, both aligned to the upcoming Friday:
    #   Fri–Sun    = Fri, Sat, Sun (3 nights)
    #   10-day     = Fri through the Sunday 9 days later (10 nights, two weekends)
    fri = upcoming_friday(today)
    frisun_nights = [(fri + dt.timedelta(days=i)).isoformat() for i in range(3)]
    tenday_nights = [(fri + dt.timedelta(days=i)).isoformat() for i in range(10)]
    fso, fsi = agg_over(latest, INCLUDED_IDS, frisun_nights)
    tdo, tdi = agg_over(latest, INCLUDED_IDS, tenday_nights)
    sun1 = fri + dt.timedelta(days=2)
    sun2 = fri + dt.timedelta(days=9)

    week7 = daterange(today, 7)   # baseline for week-over-week + site-count fallback
    weekends = next_weekends(today, count=6)

    # reservable sites currently in the roster = distinct sellable sites tonight
    # (falls back to the max sites seen across the next week if tonight is thin)
    reservable_sites = 0
    for cid in INCLUDED_IDS:
        best = 0
        for d in [tonight] + week7:
            cell = latest.get((cid, d))
            if cell:
                best = max(best, cell["sites"])
        reservable_sites += best

    # ── week-over-week deltas (same nights, observed ~7 days apart) ──────────
    prior_snap = pick_snapshot_near(by_snap, today - dt.timedelta(days=7))

    def window_wow(ids, nights):
        """Change in occupancy for `nights` now vs. the snapshot ~7 days ago."""
        now = pct(*agg_over(latest, ids, nights))
        if prior_snap is None or now is None:
            return None
        po = pi = 0
        for cid in ids:
            for d in nights:
                cell = by_snap[prior_snap].get((cid, d))
                if cell:
                    po += cell[0]; pi += cell[1]
        then = pct(po, pi)
        return None if then is None else round(now - then, 1)

    frisun_delta = window_wow(INCLUDED_IDS, frisun_nights)
    tenday_delta = window_wow(INCLUDED_IDS, tenday_nights)

    # ── campground table ────────────────────────────────────────────────────
    table = []
    for cid in INCLUDED_IDS:
        o, i = agg_over(latest, [cid], [tonight])
        table.append({
            "id": cid, "name": NAME_BY_ID[cid], "cluster": CLUSTER_BY_ID[cid],
            "occupied": o, "inventory": i, "pct": pct(o, i),
            "frisun_delta": window_wow([cid], frisun_nights),
            "tenday_delta": window_wow([cid], tenday_nights),
        })

    # ── upcoming weekends ───────────────────────────────────────────────────
    weekend_rows = []
    for label, nights in weekends:
        o, i = agg_over(latest, INCLUDED_IDS, nights)
        weekend_rows.append({
            "label": label, "occupancy_pct": pct(o, i),
            "sites_remaining": (i - o) if i else None,  # open site-nights (Fri+Sat)
        })

    # ── year-over-year plumbing (weekday-aligned; empty until history exists) ─
    last_year = year_over_year(latest, chart_dates)

    # ── developer/debug: campground -> id -> reservable sites tonight ───────
    debug = [{"name": t["name"], "id": t["id"], "reservable_sites":
              (latest.get((t["id"], tonight)) or {}).get("sites", 0)}
             for t in table]

    data = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"),
        "snapshot_count": len(by_snap),
        "latest_snapshot": max(by_snap) if by_snap else None,
        "summary": {
            "tonight_pct": pct(tonight_occ, tonight_inv),
            "frisun_pct": pct(fso, fsi),
            "frisun_label": f"{fri:%a %b %-d}\u2013{sun1:%a %b %-d}",
            "frisun_delta": frisun_delta,
            "tenday_pct": pct(tdo, tdi),
            "tenday_label": f"{fri:%b %-d}\u2013{sun2:%b %-d}",
            "tenday_delta": tenday_delta,
            "reservable_sites": reservable_sites,
            "campgrounds": len(INCLUDED_IDS),
        },
        "series": {
            "dates": chart_dates,
            "aggregate": aggregate,
            "by_campground": by_campground,
            "last_year_aggregate": last_year,
        },
        "campgrounds": table,
        "weekends": weekend_rows,
        "roster_debug": debug,
    }
    os.makedirs("docs", exist_ok=True)
    with open(OUT_PATH, "w") as fh:
        json.dump(data, fh, indent=2)
    print(f"Wrote {OUT_PATH}  (snapshots: {data['snapshot_count']}, "
          f"tonight: {data['summary']['tonight_pct']}%)")
    return data


def pick_snapshot_near(by_snap, target):
    """Snapshot_date closest to `target` (a date), or None."""
    if not by_snap:
        return None
    best, best_gap = None, None
    for snap in by_snap:
        gap = abs((dt.date.fromisoformat(snap) - target).days)
        if best_gap is None or gap < best_gap:
            best, best_gap = snap, gap
    # Only use it if it's genuinely ~a week old (within 3 days of target).
    return best if best_gap is not None and best_gap <= 3 else None


def year_over_year(latest, chart_dates):
    """
    For each chart night, find last year's WEEKDAY-aligned equivalent (same
    weekday, ~52 weeks earlier) and return its aggregate occupancy if a snapshot
    ever recorded it. Empty list of None until a year of history exists.
    """
    out = []
    have_any = False
    for d in chart_dates:
        cur = dt.date.fromisoformat(d)
        ly = cur - dt.timedelta(weeks=52)  # same weekday, one year back
        o, i = agg_over(latest, INCLUDED_IDS, [ly.isoformat()])
        v = pct(o, i)
        out.append(v)
        have_any = have_any or v is not None
    return out if have_any else None


if __name__ == "__main__":
    build()
