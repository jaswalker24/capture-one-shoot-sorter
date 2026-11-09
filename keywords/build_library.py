#!/usr/bin/env python3
"""Builds trip-keyword-library.txt (tab-indented, Lightroom/Capture One import format) + CSV of full paths."""
import csv
L = lambda *a: list(a)
tree = {
 "Motorsport": {
  "Car Colour": L("Black","Blue","Green","Grey","Orange","Red","White","Yellow","Silver","Purple","Pink","Gold","Multi"),
  "Car Number": [str(i) for i in range(1000)],
  "Night": [],
  "Shot": L("Panning","Head-on","Rear","Pit Lane","Paddock","Podium","Grid","Crash-Incident","Detail","Driver Portrait"),
  "Series": L("Britcar","CTCC","Tiedeman","BTCC","GT","Formula","Touring","Endurance"),
  "Session": L("Practice","Qualifying","Race 1","Race 2","Race 3"),
  "Circuit": {"Brands Hatch": {"Corner": L("Paddock Hill Bend","Druids","Graham Hill Bend","Surtees","Clearways","Hawthorns","Westfield","Sheene","Stirlings")},
              "Silverstone":[], "Donington":[], "Snetterton":[], "Oulton Park":[], "Thruxton":[], "Goodwood":[],
              "Fuji Speedway":[], "Suzuka":[], "Motegi":[]},
 },
 "Gig": {
  "Crowd":[], "Stage":[], "Portrait":[],
  "Framing": L("Wide","Close-up"),
  "Performer": L("Band","Solo","Guitar","Drums","Bass","Vocals","Keys","DJ"),
  "Lighting": L("Silhouette","Pyro","Backlit","Strobe"),
  "Backstage":[],
  "Underground Idol": L("Cheki","Dance","Group"),
  "Venue Type": L("Live House","Club","Arena","Festival"),
 },
 "Kyoto": L("Temple","Street","Nature","People","Shrine Gate","Night","Rain","Landscape","Transit"),
 "Japan": {
  "City": {"Tokyo": L("Shibuya","Shinjuku","Akihabara","Asakusa","Harajuku"),
           "Kyoto": L("Gion","Arashiyama","Fushimi Inari"),
           "Osaka": L("Dotonbori","Namba","Umeda")},
  "Subject": L("Shrine","Temple","Torii","Street","Neon","Food","Train","Station","Market","Garden",
               "Autumn Leaves","Skyline","Night","Rain","Transit","Landscape","Mt Fuji"),
 },
 "Aviation": {"Subject": L("Airliner","Military","Helicopter","Light Aircraft","Vintage","Display Team"),
              "Shot": L("Takeoff","Landing","Banking","Head-on","Ground","Airshow","Spotting","Cockpit","Detail")},
 "Wildlife": {"Subject": L("Bird","Bird in Flight","Mammal","Big Cat","Insect","Reptile","Pet","Dog","Cat","Zoo","Safari Park"),
              "Shot": L("Portrait","Action","Environmental","Close-up")},
 "Static Vehicles": {"Type": L("Car","Motorbike","Classic","Supercar","JDM","Truck","Military Vehicle"),
                     "Event": L("Car Show","Meet","Museum","Track Day Paddock"),
                     "Shot": L("Front Three-Quarter","Rear Three-Quarter","Side Profile","Interior","Engine Bay","Badge","Wheel","Detail")},
 "Film": {"Format": L("35mm","120","Instant"), "Process": L("Colour Negative","Black and White","Slide"),
          "Stage": L("Developed","Scanned")},
 "General": {
  "Light": L("Golden Hour","Blue Hour","Night","Backlit"),
  "Weather": L("Rain","Fog","Snow","Sun"),
  "Quality": L("Sharp","Motion Blur","Soft Focus","Noise"),
  "Use": L("Portfolio","Social","Client","Archive","Reject Candidate"),
  "Orientation": L("Portrait","Landscape","Square"),
 },
}

# Feature + confirmed Japan Nov 2026 schedule keywords
_extra = {
 "General": {
  "Burst Best": [],
  "Score": ["Below 5", "5", "6", "7", "8+"],
  "Rank": ["Best", "Good"],
 },
 "Gig": {
  "Cheki": [],
  "Meet & Greet": [],
  "Changeover": [],
  "Event": [
    "Sample Festival vol.1",
    "Evening Lights Showcase",
    "Example Hall Live",
    "Headline Show",
    "Afternoon Bill",
    "Example Tour 2026",
    "Birthday Showcase",
  ],
  "Venue": [
    "Example Cyclone",
    "City Plaza",
    "Example Hall",
    "Example Arena",
    "Example SOMA",
    "Example Stadium",
    "Example REX",
  ],
  "Act": [
    "Red Harbor", "Steel Orchard'", "Amber Parade",
    "メロディうさぎ", "HALO",
    "Headline Act",
    "Support Act", "No Photo Act",
    "Main Act",
  ],
 },
}

def _merge_extra(dst, extra):
    for k, v in extra.items():
        if k not in dst:
            dst[k] = v if isinstance(v, dict) else {x: [] for x in v}
            continue
        node = dst[k]
        if not isinstance(node, dict):
            continue
        if isinstance(v, dict):
            for ck, cv in v.items():
                if ck not in node:
                    node[ck] = cv if isinstance(cv, dict) else ({x: [] for x in cv} if isinstance(cv, list) else [])
                elif isinstance(cv, list) and isinstance(node[ck], dict):
                    for x in cv:
                        node[ck].setdefault(x, [])
                elif isinstance(cv, dict) and isinstance(node[ck], dict):
                    _merge_extra({ck: node[ck]}, {ck: cv})
                    node[ck] = node[ck]
        elif isinstance(v, list):
            for x in v:
                node.setdefault(x, [])

_merge_extra(tree, _extra)

lines, rows = [], []
def walk(node, depth, path):
    items = node.items() if isinstance(node, dict) else [(k, []) for k in node]
    for k, v in items:
        lines.append("\t" * depth + k)
        p = path + [k]
        rows.append(("|".join(p), "/".join(p), depth + 1, p[0]))
        walk(v, depth + 1, p)
walk(tree, 0, [])
with open(__import__("pathlib").Path(__file__).with_name("trip-keyword-library.txt"), "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines) + "\n")
with open(__import__("pathlib").Path(__file__).with_name("trip-keyword-library.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["path_pipe", "path_slash", "level", "top_level"]); w.writerows(rows)
from collections import Counter
print(len(rows), Counter(r[3] for r in rows))
