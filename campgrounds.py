"""
The frozen roster of reservable campgrounds the dashboard tracks.

This is the ONLY file you edit to change what's included. Discovered and
verified with discover_campgrounds.py (September 2026 probe). Every entry is a
fully-reservable, individual-site campground on Recreation.gov. Group-only and
mixed reservable/first-come campgrounds were deliberately left out.

To add or remove a campground: add or delete a line below, keeping the format
    ("recreation_gov_id", "Display Name", "Cluster")
Then the next daily snapshot picks up the change automatically.
"""

CAMPGROUNDS = [
    # Recreation.gov ID, display name, cluster
    ("234329", "Twin Lakes",      "Mammoth Basin & town"),
    ("233860", "New Shady Rest",  "Mammoth Basin & town"),
    ("232271", "Sherwin Creek",   "Mammoth Basin & town"),
    ("234290", "Coldwater",       "Mammoth Basin & town"),
    ("233404", "Lake Mary",       "Mammoth Basin & town"),
    ("233830", "Old Shady Rest",  "Mammoth Basin & town"),
    ("232270", "Pine Glen",       "Mammoth Basin & town"),
    ("232269", "Oh! Ridge",       "June Lake Loop"),
    ("234330", "Silver Lake",     "June Lake Loop"),
    ("232268", "June Lake",       "June Lake Loop"),
    ("233235", "Reversed Creek",  "June Lake Loop"),
    ("10039845", "Gull Lake",     "June Lake Loop"),
    ("234311", "Convict Lake",    "395 corridor"),
    ("232395", "French Camp",     "South 395 / Rock Creek"),
    ("233907", "Rock Creek Lake", "South 395 / Rock Creek"),
]

# Name lookup by ID, for convenience.
NAME_BY_ID = {cid: name for cid, name, _ in CAMPGROUNDS}
CLUSTER_BY_ID = {cid: cluster for cid, _, cluster in CAMPGROUNDS}
