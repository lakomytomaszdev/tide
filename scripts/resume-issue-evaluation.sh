#!/usr/bin/env bash
# Tylko nowy test konfiguracji D-Bus oraz odłożona ocena Codexa; bez powtórzenia 16 udanych testów.
set -euo pipefail
python3 -m unittest daemon.tests.test_issues_adapters.EvaluatorBusTest -v
python3 cli/agents/evaluate.py --skip-claude --output build/issue-synthesis/pt-224-codex-3 --claude-model sonnet --codex-model gpt-6.1-sol --codex-bin /usr/lib/chatgpt/resources/codex
