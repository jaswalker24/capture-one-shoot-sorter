"""Synthetic-timestamp unit tests for schedule tagging (uses the example template)."""
from datetime import datetime, timezone, timedelta
from schedule_tag import load_schedule, match
from pathlib import Path as _P
S = load_schedule(str(_P(__file__).resolve().parent / "schedules" / "example.json")); TZ = S[1]
def jst(s): return datetime.fromisoformat(s + "+09:00")
def gmt(s): return datetime.fromisoformat(s + "+00:00")   # camera left on London time
cases = [
  ("Act One mid-set", jst("2026-06-01T13:25:00"), ["Gig/Act/Act One"], False),
  ("Act One->Act Two changeover", jst("2026-06-01T13:36:00"), ["Gig/Changeover"], False),
  ("Sample Festival cheki", jst("2026-06-01T14:30:00"), ["Gig/Cheki", "Gig/Act/Act One", "Gig/Act/Act Two"], False),
  ("Sample Festival unknown slot", jst("2026-06-01T16:00:00"), ["Gig/Event/Sample Festival vol.1"], False),
  ("Evening Lights preferred (beats festival window)", jst("2026-06-01T18:35:00"), ["Gig/Event/Evening Lights Showcase"], False),
  ("camera on GMT -> Act Alpha set", gmt("2026-06-02T04:40:00"), ["Gig/Act/Act Alpha"], False),
  ("Act Alpha meet window overlaps Act Beta set", jst("2026-06-02T14:00:00") + timedelta(minutes=10), ["Gig/Meet & Greet"], True),
  ("Headline Act mid-show", jst("2026-06-02T18:00:00"), ["Gig/Act/Headline Act"], False),
  ("No Photo Act slot", jst("2026-06-03T15:45:00"), ["Gig/Event/Example Tour 2026"], True),
  ("Main Act set", jst("2026-06-03T17:10:00"), ["Gig/Act/Main Act"], False),
  ("SOMA 10-min drift buffer", jst("2026-06-03T16:58:00"), ["Gig/Changeover"], False),
  ("Stadium Headline: event/venue only", jst("2026-06-04T20:00:00"), ["Gig/Venue/Example Stadium"], False),
  ("Birthday Showcase TBA acts", jst("2026-06-05T18:00:00"), ["Gig/Event/Birthday Showcase"], False),
  ("no event (sightseeing)", jst("2026-05-31T15:00:00"), [], False),
  ("dropped unconfirmed show 7 Jun", jst("2026-06-07T19:00:00"), [], False),
]
ok = 0
for name, t, expect, review in cases:
    m = match(t, S, TZ)
    good = all(k in m["keywords"] for k in expect) and (bool(m["review"]) == review) and bool(expect or not m["keywords"])
    if name.startswith("Stadium"): good = good and not any(k.startswith("Gig/Act/") for k in m["keywords"])
    ok += good
    print(("PASS" if good else "FAIL"), name, "|", m["local_time"][11:16], m["keywords"], "tent:", m["tentative"], "rev:", m["review"])
print(f"{ok}/{len(cases)} passed")
raise SystemExit(0 if ok == len(cases) else 1)
