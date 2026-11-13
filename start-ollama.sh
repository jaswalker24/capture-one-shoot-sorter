#!/bin/bash
# Starts the local Ollama server (localhost only) with models stored under the runtime folder.
if [ -n "${LOCALAI_HOME:-}" ]; then
  HOME_LOCAL="$LOCALAI_HOME"
elif [ -n "${SHOOT_SORTER_C1_HOME:-}" ]; then
  HOME_LOCAL="$SHOOT_SORTER_C1_HOME"
elif [ -d "$HOME/localai" ]; then
  HOME_LOCAL="$HOME/localai"
else
  HOME_LOCAL="$HOME/shoot-sorter-c1"
fi
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME_LOCAL/models}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_KEEP_ALIVE=5m
export OLLAMA_MAX_LOADED_MODELS=1
mkdir -p "$HOME_LOCAL/logs"
if curl -s "http://${OLLAMA_HOST}/api/version" >/dev/null; then echo "ollama already running"; exit 0; fi
nohup "$HOME_LOCAL/ollama/ollama" serve >> "$HOME_LOCAL/logs/ollama.log" 2>&1 &
echo $! > "$HOME_LOCAL/logs/ollama.pid"
sleep 3; curl -s "http://${OLLAMA_HOST}/api/version"; echo
