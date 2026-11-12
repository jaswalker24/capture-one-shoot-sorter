#!/bin/bash
# usage:
#   ~/localai/tag <folder> --profile motorsport|gig|kyoto [--sample N] [--apply]      (tagger)
#   ~/localai/tag index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person|schedule|scene|camera ...   (features)
HOME_LOCAL="${LOCALAI_HOME:-$HOME/localai}"
"$HOME_LOCAL/start-ollama.sh" >/dev/null
case "$1" in
  index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person|schedule|scene|camera)
    exec nice -n 10 "$HOME_LOCAL/venv/bin/python" "$HOME_LOCAL/features.py" "$@" ;;
  *)
    exec nice -n 10 "$HOME_LOCAL/venv/bin/python" "$HOME_LOCAL/tagger.py" "$@" ;;
esac
