#!/bin/bash
# usage:
#   ~/shoot-sorter-c1/tag <folder> --profile motorsport|gig|kyoto [--sample N] [--apply]      (tagger)
#   ~/shoot-sorter-c1/tag index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person|schedule|scene|camera ...   (features)
if [ -n "${LOCALAI_HOME:-}" ]; then
  HOME_LOCAL="$LOCALAI_HOME"
elif [ -n "${SHOOT_SORTER_C1_HOME:-}" ]; then
  HOME_LOCAL="$SHOOT_SORTER_C1_HOME"
elif [ -d "$HOME/localai" ]; then
  HOME_LOCAL="$HOME/localai"
else
  HOME_LOCAL="$HOME/shoot-sorter-c1"
fi
"$HOME_LOCAL/start-ollama.sh" >/dev/null
case "$1" in
  index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person|schedule|scene|camera)
    exec nice -n 10 "$HOME_LOCAL/venv/bin/python" "$HOME_LOCAL/features.py" "$@" ;;
  *)
    exec nice -n 10 "$HOME_LOCAL/venv/bin/python" "$HOME_LOCAL/tagger.py" "$@" ;;
esac
