#!/usr/bin/env bash
# Usuwa Ollamę zainstalowaną przez scripts/ollama/install.sh.
# Użycie: sudo bash scripts/ollama/uninstall.sh [--models]
#   --models  usuwa też pobrane modele (domyślnie zostają, to dziesiątki GB do ponownego pobrania)
set -euo pipefail

MODELS_DIR="${TIDE_OLLAMA_MODELS:-/home/ollama-models}"

if [[ $EUID -ne 0 ]]; then
    echo "Uruchom przez sudo: sudo bash $0" >&2
    exit 1
fi
models=0
[[ "${1:-}" == "--models" ]] && models=1

echo "Zostanie usunięte: usługa ollama, /usr/local/bin/ollama, /usr/local/lib/ollama,"
echo "nakładka Tide, użytkownik i grupa ollama, /usr/share/ollama."
[[ $models -eq 1 ]] && echo "Także modele: $MODELS_DIR ($(du -sh "$MODELS_DIR" 2>/dev/null | cut -f1))"
read -r -p "Kontynuować? [t/N] " answer
if [[ "$answer" != [tTyY] ]]; then
    echo "Przerwano, nic nie zmieniono."
    exit 1
fi

systemctl disable --now ollama 2>/dev/null || true
rm -f /etc/systemd/system/ollama.service
rm -rf /etc/systemd/system/ollama.service.d
systemctl daemon-reload
rm -f /usr/local/bin/ollama
rm -rf /usr/local/lib/ollama
userdel ollama 2>/dev/null || true
groupdel ollama 2>/dev/null || true
rm -rf /usr/share/ollama
if [[ $models -eq 1 ]]; then
    rm -rf "$MODELS_DIR"
else
    echo "Modele zostały w $MODELS_DIR (usunięcie: --models)."
fi
echo "Ollama usunięta."
