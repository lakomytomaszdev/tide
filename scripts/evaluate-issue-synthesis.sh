#!/usr/bin/env bash
# Jawny przebieg PT-224 poza piaskownicą: operacje CLI, potem płatna/sieciowa synteza.
set -euo pipefail
python3 -m unittest daemon.tests.test_issues_adapters -v
output_dir="${1:-build/issue-synthesis/pt-224}"
python3 cli/agents/evaluate.py --output "$output_dir" --claude-model sonnet --codex-model gpt-6.1-sol
