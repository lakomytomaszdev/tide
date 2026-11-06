#!/usr/bin/env bash
# Jawny przebieg PT-224 poza piaskownicą: operacje CLI, potem płatna/sieciowa synteza.
set -euo pipefail
python3 -m unittest daemon.tests.test_issues_adapters -v
python3 cli/agents/evaluate.py --output build/issue-synthesis/pt-224 --claude-model sonnet --codex-model gpt-6.1-sol --codex-bin /usr/lib/chatgpt/resources/codex "$@"
