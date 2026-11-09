#!/bin/bash
D="$HOME/Library/Scripts/Capture One Scripts/Trip Local AI"
t() { # script name, frames...
  local s="$1"; shift
  local log=$(osascript "$D/$s.scpt" "$@" 2>&1)
  echo "== $s ($*) -> $log"
  for i in $(seq 1 300); do grep -qE '^(DONE|ERROR)' "$log" 2>/dev/null && break; sleep 2; done
  grep -E '^(DONE|ERROR)|APPLIED|failures' "$log"
}
t "Tag with Local AI" _A3A5122 _A3A5182
t "Find Bursts in Selection" _A3A3806 _A3A3807 _A3A3808
t "Check Sharpness" _A3A5540 _A3A5600 _A3A5660
t "Read Text in Frame" _A3A3807 _A3A5182
t "Tag by Schedule" _A3A3807
