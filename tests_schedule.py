"""Synthetic-timestamp unit tests for the Japan schedule (no Japan photos exist yet)."""
from datetime import datetime, timezone, timedelta
from schedule_tag import load_schedule, match
from pathlib import Path as _P
S = load_schedule(str(_P(__file__).resolve().parent / "schedules" / "example.json")); TZ = S[1]
def jst(s): return datetime.fromisoformat(s + "+09:00")
def gmt(s): return datetime.fromisoformat(s + "+00:00")   # camera left on London time
cases = [
  ("Red Harbor mid-set", jst("2026-11-21T13:25:00"), ["Gig/Act/Red Harbor"], False),
  ("Red Harbor->MCL changeover", jst("2026-11-21T13:36:00"), ["Gig/Changeover"], False),
  ("Sample Festival cheki", jst("2026-11-21T14:30:00"), ["Gig/Cheki", "Gig/Act/Red Harbor", "Gig/Act/Steel Orchard'"], False),
  ("Sample Festival unknown slot", jst("2026-11-21T16:00:00"), ["Gig/Event/Sample Festival vol.1"], False),
  ("Miku lights preferred (beats festival window)", jst("2026-11-21T18:35:00"), ["Gig/Event/Evening Lights Showcase"], False),
  ("camera on GMT -> Merodi set", gmt("2026-11-22T04:40:00"), ["Gig/Act/メロディうさぎ"], False),
  ("Merodi meet window overlaps HALO set", jst("2026-11-22T14:00:00") + timedelta(minutes=10), ["Gig/Meet & Greet"], True),
  ("Headline Act mid-show", jst("2026-11-22T18:00:00"), ["Gig/Act/Headline Act"], False),
  ("No Photo Act no-photo slot", jst("2026-11-23T15:45:00"), ["Gig/Event/Afternoon Bill"], True),
  ("NPC set", jst("2026-11-23T17:10:00"), ["Gig/Act/Support Act"], False),
  ("SOMA 10-min drift buffer", jst("2026-11-23T16:58:00"), ["Gig/Changeover"], False),
  ("Main Act: event/venue only", jst("2026-11-24T20:00:00"), ["Gig/Venue/Example Stadium"], False),
  ("Eclipse REX TBA acts", jst("2026-11-25T18:00:00"), ["Gig/Event/Birthday Showcase"], False),
  ("no event (sightseeing)", jst("2026-11-20T15:00:00"), [], False),
  ("dropped unconfirmed show 26 Nov", jst("2026-11-26T19:00:00"), [], False),
]
ok = 0
for name, t, expect, review in cases:
    m = match(t, S, TZ)
    good = all(k in m["keywords"] for k in expect) and (bool(m["review"]) == review) and bool(expect or not m["keywords"])
    if name.startswith("Iron"): good = good and not any(k.startswith("Gig/Act/") for k in m["keywords"])
    ok += good
    print(("PASS" if good else "FAIL"), name, "|", m["local_time"][11:16], m["keywords"], "tent:", m["tentative"], "rev:", m["review"])
print(f"{ok}/{len(cases)} passed")
