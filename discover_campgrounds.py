#!/usr/bin/env python3
"""
discover_campgrounds.py
-----------------------
Builds the authoritative list of RESERVABLE campgrounds in the Mammoth Lakes
area, straight from Recreation.gov. Run it once to lock your roster and to
verify counts by eye against the website.

Inclusion rule (agreed):
  * Include a campground ONLY if it is fully reservable online.
  * EXCLUDE first-come/first-served campgrounds AND "mixed" campgrounds that
    offer reservable + first-come sites side by side (e.g. Convict Lake,
    McGee Creek). No partial denominators.
  * Closed / out-of-season campgrounds fall out automatically (no inventory) —
    this is how the Reds Meadow corridor drops for the 2026 season.

It answers, per campground:
    Campground name  ->  Recreation.gov ID  ->  # reservable overnight sites
                     ->  suggested INCLUDE / EXCLUDE

Two Recreation.gov sources, each for one job:
  1. RIDB API (needs a free key) — DISCOVERY only: which campgrounds exist near
     Mammoth, and their IDs + names.  https://ridb.recreation.gov/
  2. The public month-availability endpoint (no key) — RESERVABILITY: whether a
     campground actually sells sites online, how many, and whether any of its
     sites are first-come. Source of truth for the include/exclude call.

Why a peak month? Seasonal campgrounds show zero sellable sites in winter, so we
probe a summer month to see the full reservable roster (change PROBE_MONTH).

SETUP (2 minutes):
  1. Get a free RIDB key: log in at https://www.recreation.gov/, open Account
     Settings, and enable/copy your API key (a.k.a. "developer" key).
  2. In a terminal:
        export RIDB_API_KEY="your-key-here"
        pip install requests
        python discover_campgrounds.py
"""

import os
import sys
import time
import datetime as dt

import requests

# ── CONFIG ─────────────────────────────────────────────────────────────────
# Town of Mammoth Lakes. Widen RADIUS_MILES to include June Lake / the 395
# corridor; shrink it to stay in the immediate basin.
CENTER_LAT = 37.6485
CENTER_LON = -118.9721
RADIUS_MILES = 40          # generous on purpose — you'll trim the list after

# Probe a month that is BOTH already released for booking and in-season.
# Recreation.gov releases sites on a ~6-month rolling window; anything further
# out comes back "NYR" (Not Yet Released) and would read as falsely empty.
# Next month is always inside that window. (If you run this in deep winter, set
# PROBE_MONTH manually to an upcoming summer month so seasonal campgrounds show
# their real roster rather than reading as closed.)
_first_next = (dt.date.today().replace(day=1) + dt.timedelta(days=32)).replace(day=1)
PROBE_MONTH = (_first_next.year, _first_next.month)

REQUEST_PAUSE_SEC = 1.1     # availability endpoint asks for ~1 req/sec
# ───────────────────────────────────────────────────────────────────────────

RIDB_KEY = os.environ.get("RIDB_API_KEY", "").strip()
UA = "MammothOccupancyDashboard/1.0 (Visit Mammoth research; contact: data@visitmammoth.com)"

# Site is part of sellable ONLINE inventory if it ever shows one of these.
RESERVABLE_STATUSES = {"Available", "Reserved"}
# Explicit first-come / non-reservable markers → makes a campground "mixed".
FCFS_STATUSES = {"Not Reservable", "Not Reservable Management"}
# Everything else (Closed, NYR, Not Available, Open) is transient/blocked and
# is neither counted as reservable nor treated as proof of first-come.
KNOWN_STATUSES = RESERVABLE_STATUSES | FCFS_STATUSES | {
    "Not Available", "Closed", "NYR", "Open", "Lottery",
}


def ridb_campgrounds_near():
    """Discover CAMPING facilities near the center point via RIDB."""
    if not RIDB_KEY:
        sys.exit("ERROR: set RIDB_API_KEY first (see SETUP in the file header).")
    found, offset = {}, 0
    while True:
        r = requests.get(
            "https://ridb.recreation.gov/api/v1/facilities",
            headers={"apikey": RIDB_KEY, "accept": "application/json"},
            params={
                # No activity filter here — RIDB's activity param is finicky
                # (expects numeric IDs). We instead pull everything in radius and
                # keep only Campgrounds below, which is more reliable.
                "latitude": CENTER_LAT,
                "longitude": CENTER_LON,
                "radius": RADIUS_MILES,
                "full": "true",
                "limit": 50,
                "offset": offset,
            },
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json().get("RECDATA", [])
        for f in batch:
            if f.get("FacilityTypeDescription") == "Campground":
                fid = str(f.get("FacilityID"))
                found[fid] = f.get("FacilityName", "").strip()
        if len(batch) < 50:
            break
        offset += 50
        time.sleep(0.3)
    return found


def availability(facility_id, year, month):
    """Raw month-availability JSON for one campground (public, no key)."""
    start = f"{year:04d}-{month:02d}-01T00:00:00.000Z"
    url = f"https://www.recreation.gov/api/camps/availability/campground/{facility_id}/month"
    r = requests.get(url, headers={"User-Agent": UA}, params={"start_date": start}, timeout=30)
    if r.status_code == 404:
        return None            # not a reservable campground on rec.gov
    r.raise_for_status()
    return r.json().get("campsites", {})


def summarize(campsites):
    """Reduce one campground's month to reservable vs first-come overnight sites."""
    reservable_overnight = 0
    fcfs_overnight = 0
    statuses_seen = {}
    for site in campsites.values():
        for s in site.get("availabilities", {}).values():
            statuses_seen[s] = statuses_seen.get(s, 0) + 1
        if site.get("type_of_use") != "Overnight":
            continue
        month_statuses = set(site.get("availabilities", {}).values())
        if month_statuses & RESERVABLE_STATUSES:
            reservable_overnight += 1
        elif month_statuses & FCFS_STATUSES:
            fcfs_overnight += 1
    return reservable_overnight, fcfs_overnight, statuses_seen


def verdict(reservable, fcfs):
    if reservable == 0:
        return "EXCLUDE", "no reservable inventory (closed/seasonal or FCFS)"
    if fcfs > 0:
        return "EXCLUDE", f"MIXED — {fcfs} first-come site(s) alongside reservable"
    return "INCLUDE", ""


def main():
    year, month = PROBE_MONTH
    print(f"Probing {year}-{month:02d} within {RADIUS_MILES} mi of "
          f"({CENTER_LAT}, {CENTER_LON})\n")

    campgrounds = ridb_campgrounds_near()
    rows, all_statuses = [], {}

    for fid, name in sorted(campgrounds.items(), key=lambda kv: kv[1].lower()):
        try:
            camps = availability(fid, year, month)
        except requests.HTTPError as e:
            rows.append((name, fid, "ERR", "", "?", f"HTTP {e.response.status_code}"))
            time.sleep(REQUEST_PAUSE_SEC)
            continue

        if camps is None:
            rows.append((name, fid, 0, 0, "EXCLUDE", "not reservable on rec.gov"))
            time.sleep(REQUEST_PAUSE_SEC)
            continue

        resv, fcfs, statuses = summarize(camps)
        for s, n in statuses.items():
            all_statuses[s] = all_statuses.get(s, 0) + n
        v, why = verdict(resv, fcfs)
        rows.append((name, fid, resv, fcfs, v, why))
        time.sleep(REQUEST_PAUSE_SEC)

    # ── Roster table ─────────────────────────────────────────────────────
    print(f"{'Campground':34} {'Rec.gov ID':>10} {'Resv':>5} {'FCFS':>5} "
          f"{'Verdict':>8}  Why")
    print("-" * 104)
    total = 0
    for name, fid, resv, fcfs, v, why in rows:
        if v == "INCLUDE" and isinstance(resv, int):
            total += resv
        print(f"{name[:34]:34} {fid:>10} {str(resv):>5} {str(fcfs):>5} "
              f"{v:>8}  {why}")
    print("-" * 104)
    print(f"{'TOTAL reservable sites (INCLUDE only)':34} "
          f"{'':>10} {total:>5}\n")

    # ── Status audit — confirm we recognize every status Rec.gov emits ────
    print("All statuses observed (site-nights) across the probed month:")
    for s, n in sorted(all_statuses.items(), key=lambda kv: -kv[1]):
        tag = ""
        if s in RESERVABLE_STATUSES:
            tag = "  <-- reservable"
        elif s in FCFS_STATUSES:
            tag = "  <-- first-come"
        print(f"   {s:26} {n:>8}{tag}")
    unknown = set(all_statuses) - KNOWN_STATUSES
    if unknown:
        print(f"\n!! UNRECOGNIZED statuses — decide how to bucket these: {unknown}")


if __name__ == "__main__":
    main()
