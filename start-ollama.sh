#!/bin/bash
# Starts the local Ollama server (localhost only) with models stored under ~/localai/models
export OLLAMA_MODELS="$HOME/localai/models"
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_KEEP_ALIVE=5m
export OLLAMA_MAX_LOADED_MODELS=1
if curl -s http://127.0.0.1:11434/api/version >/dev/null; then echo "ollama already running"; exit 0; fi
nohup "$HOME/localai/ollama/ollama" serve >> "$HOME/localai/logs/ollama.log" 2>&1 &
echo $! > "$HOME/localai/logs/ollama.pid"
sleep 3; curl -s http://127.0.0.1:11434/api/version; echo
