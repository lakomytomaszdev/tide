#!/usr/bin/env bash
# Pobiera modele testowe PT-13 (bez sudo). Łącznie ok. 46 GB.
# Użycie: bash scripts/ollama/pull-models.sh [model ...]   (bez argumentów: wszystkie z listy)
set -euo pipefail

# model  przybliżony rozmiar  do czego
MODELS=(
    "qwen3:8b|5 GB|opisy kart i „Na daily”, w całości na karcie"
    "hf.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M|7 GB|polski model, porównanie polszczyzny raportu"
    "gpt-oss:20b|14 GB|lepsze rozumowanie, częściowo na procesorze"
    "qwen3-coder:30b|19 GB|tylko test połączenia agenta (Codex, Claude Code)"
    "bge-m3|1 GB|embeddingi pod wyszukiwanie w KRunnerze (PT-19)"
)

if ! command -v ollama >/dev/null || ! curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Ollama nie działa. Najpierw: sudo bash scripts/ollama/install.sh" >&2
    exit 1
fi

wanted=("$@")
for entry in "${MODELS[@]}"; do
    IFS='|' read -r name size why <<<"$entry"
    if [[ ${#wanted[@]} -gt 0 && ! " ${wanted[*]} " == *" $name "* ]]; then
        continue
    fi
    echo
    echo "== $name ($size) — $why"
    ollama pull "$name"
done
echo
ollama list
