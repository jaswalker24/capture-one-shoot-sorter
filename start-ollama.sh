#!/bin/bash
# Starts the local Ollama server (localhost only) with models stored under $LOCALAI_HOME/models
HOME_LOCAL="${LOCALAI_HOME:-$HOME/localai-lr}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME_LOCAL/models}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_KEEP_ALIVE=5m
export OLLAMA_MAX_LOADED_MODELS=1
mkdir -p "$HOME_LOCAL/logs"
if curl -s "http://${OLLAMA_HOST}/api/version" >/dev/null; then echo "ollama already running"; exit 0; fi
nohup "$HOME_LOCAL/ollama/ollama" serve >> "$HOME_LOCAL/logs/ollama.log" 2>&1 &
echo $! > "$HOME_LOCAL/logs/ollama.pid"
sleep 3; curl -s "http://${OLLAMA_HOST}/api/version"; echo
