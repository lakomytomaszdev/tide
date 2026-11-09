#!/bin/bash
# PT-192: pełna weryfikacja poza piaskownicą — próba Incusa i make test, także gdy jedna z nich zawiedzie.
# Uruchomienie: bash scripts/box/stacks-acceptance.sh. Próba może wyświetlić systemowe okno hasła sudo.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
trial_code=0
tests_code=0

bash "$ROOT/scripts/box/stacks-trial.sh" || trial_code=$?
make -C "$ROOT" test || tests_code=$?
printf '\nWYNIK pełnej weryfikacji: próba Incusa=%s, make test=%s\n' "$trial_code" "$tests_code"
if (( trial_code != 0 )); then
    exit "$trial_code"
fi
exit "$tests_code"
