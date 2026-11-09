#!/bin/bash
# usage:
#   ~/localai/tag <folder> --profile motorsport|gig|kyoto [--sample N] [--apply]      (tagger)
#   ~/localai/tag index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person ...   (features)
"$HOME/localai/start-ollama.sh" >/dev/null
case "$1" in
  index|search|ocr|sharpness|bursts|rank|captions|journal|ask|faces|name-person|schedule)
    exec nice -n 10 "$HOME/localai/venv/bin/python" "$HOME/localai/features.py" "$@" ;;
  *)
    exec nice -n 10 "$HOME/localai/venv/bin/python" "$HOME/localai/tagger.py" "$@" ;;
esac
