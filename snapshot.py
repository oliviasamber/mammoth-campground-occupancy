"""
snapshot.py  —  take one daily snapshot of reserved occupancy.

For every campground in campgrounds.py, this fetches Recreation.gov's public
month-availability data and records, for each reservable site and each night in
the window, whether that night is RESERVED (occupied) and whether it is part of
sellable INVENTORY. One CSV is written per run, named by the snapshot date, and
prior snapshots are never touched — that's how history accumulates.

Data source (documented):
  * Public month-availability endpoint, no key required:
      https://www.recreation.gov/api/camps/availability/campground/{id}/month
      ?start_date=YYYY-MM-01T00:00:00.000Z
    Returns one object per campsite with an `availabilities` map of
    {night -> status}, plus type_of_use, campsite_type, etc.

Occupancy definitions (see README for the full rationale):
  * occupied_flag  = 1 if status == "Reserved"          (a booking exists)
  * inventory_flag = 1 if status in {Available, Reserved}(sellable that night)
  * Everything else (Closed, Not Reservable, NYR, Not Available) is neither —
    it is not sellable inventory and is never counted as occupied.

Because the endpoint reports reservations, not check-ins, the metric is
"reserved occupancy", not physical presence.
"""

import csv
import datetime as dt
import os
import sys
import time

import requests

from campgrounds import CAMPGROUNDS

# ── Window: how many nights back and forward to record each run ──────────────
DAYS_BACK = 14      # supports the "previous 14 days" chart view
DAYS_FWD = 45       # supports "upcoming 30 days" + a week of look-ahead headroom

SNAPSHOT_DIR = "snapshots"
UA = "MammothOccupancyDashboard/1.0 (Visit Mammoth research)"
REQUEST_PAUSE_SEC = 1.1
MAX_RETRIES = 4

RESERVABLE_STATUSES = {"Available", "Reserved"}
OCCUPIED_STATUSES = {"Reserved"}
# Statuses we recognize but never count as inventory or occupancy.
KNOWN_NON_INVENTORY = {"Not Available", "Not Reservable", "Not Reservable Management",
                       "Closed", "NYR", "Open", "Lottery"}


def months_in_window(start, end):
    """First-of-month dates covering [start, end]."""
    months, cur = [], start.replace(day=1)
    while cur <= end:
        months.append(cur)
        cur = (cur + dt.timedelta(days=32)).replace(day=1)
    return months


def fetch_month(campground_id, first_of_month):
    """One month of availability for one campground, with retry/backoff."""
    url = (f"https://www.recreation.gov/api/camps/availability/campground/"
           f"{campground_id}/month")
    params = {"start_date": first_of_month.strftime("%Y-%m-%dT00:00:00.000Z")}
    for attempt in range(MAX_RETRIES):
        r = requests.get(url, headers={"User-Agent": UA}, params=params, timeout=30)
        if r.status_code == 404:
            return {}  # not a reservable campground that month
        if r.status_code in (429, 500, 502, 503, 504):
            wait = REQUEST_PAUSE_SEC * (2 ** attempt)  # exponential backoff
            print(f"  {campground_id} {first_of_month:%Y-%m}: HTTP {r.status_code}, "
                  f"retrying in {wait:.1f}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json().get("campsites", {})
    print(f"  !! {campground_id} {first_of_month:%Y-%m}: gave up after retries")
    return {}


def main():
    today = dt.date.today()
    start = today - dt.timedelta(days=DAYS_BACK)
    end = today + dt.timedelta(days=DAYS_FWD)
    window = {(start + dt.timedelta(days=i)).isoformat()
              for i in range((end - start).days + 1)}

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    out_path = os.path.join(SNAPSHOT_DIR, f"snapshot_{today.isoformat()}.csv")

    rows = 0
    unknown_statuses = {}
    with open(out_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["snapshot_date", "stay_date", "campground_id",
                         "campground_name", "site_id", "site_status",
                         "occupied_flag", "inventory_flag"])

        for cid, name, _cluster in CAMPGROUNDS:
            merged = {}  # campsite_id -> availabilities map, merged across months
            for m in months_in_window(start, end):
                for site_id, site in fetch_month(cid, m).items():
                    if site.get("type_of_use") != "Overnight":
                        continue  # skip day-use / non-overnight inventory
                    merged.setdefault(site_id, {}).update(
                        site.get("availabilities", {}))
                time.sleep(REQUEST_PAUSE_SEC)

            for site_id, avail in merged.items():
                for night_iso, status in avail.items():
                    stay_date = night_iso[:10]  # 'YYYY-MM-DDT00:00:00Z' -> date
                    if stay_date not in window:
                        continue
                    if (status not in RESERVABLE_STATUSES
                            and status not in KNOWN_NON_INVENTORY):
                        unknown_statuses[status] = unknown_statuses.get(status, 0) + 1
                    occ = 1 if status in OCCUPIED_STATUSES else 0
                    inv = 1 if status in RESERVABLE_STATUSES else 0
                    writer.writerow([today.isoformat(), stay_date, cid, name,
                                     site_id, status, occ, inv])
                    rows += 1
            print(f"  {name}: {len(merged)} sites")

    print(f"\nWrote {rows} rows -> {out_path}")
    if unknown_statuses:
        print(f"!! UNRECOGNIZED statuses (treated as non-inventory): "
              f"{unknown_statuses}")
        # Not fatal, but surfaces so the mapping can be reviewed.
    return out_path


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
