#!/usr/bin/env bash
# Instaluje Ollamę jako usługę systemd z ustawieniami Tide (PT-13).
# Użycie: sudo bash scripts/ollama/install.sh [--yes]
#
# 1. Sprawdza warunki: działający sterownik NVIDIA (inaczej oficjalny instalator doinstalowałby
#    sterowniki przez apt, z pominięciem symulacji), zstd, curl.
# 2. Pobiera oficjalny instalator do katalogu tymczasowego, pokazuje ścieżkę i sumę SHA-256
#    i czeka na potwierdzenie (można go wtedy przejrzeć w drugim terminalu). --yes pomija pytanie.
# 3. Uruchamia go: /usr/local/bin/ollama, /usr/local/lib/ollama, użytkownik ollama, usługa ollama.
# 4. Dopisuje do grupy ollama użytkownika, który wywołał sudo (instalator dopisałby roota).
# 5. Zakłada katalog modeli na /home (na / jest za mało miejsca) i nakładkę usługi
#    /etc/systemd/system/ollama.service.d/tide.conf, restartuje usługę i sprawdza, czy widzi kartę.
#
# Konkretną wersję wybiera OLLAMA_VERSION (np. OLLAMA_VERSION=0.12.6), domyślnie najnowsza.
# Cofnięcie: sudo bash scripts/ollama/uninstall.sh
set -euo pipefail

MODELS_DIR="${TIDE_OLLAMA_MODELS:-/home/ollama-models}"
DROPIN_DIR=/etc/systemd/system/ollama.service.d
DROPIN="$DROPIN_DIR/tide.conf"
API=http://127.0.0.1:11434

yes=0
[[ "${1:-}" == "--yes" ]] && yes=1

if [[ $EUID -ne 0 ]]; then
    echo "Uruchom przez sudo: sudo bash $0" >&2
    exit 1
fi
user="${SUDO_USER:-}"
if [[ -z "$user" || "$user" == root ]]; then
    echo "Brak SUDO_USER — uruchom jako zwykły użytkownik przez sudo, nie z konta roota." >&2
    exit 1
fi

# --- 1. warunki -----------------------------------------------------------------------------------

# Najpierw całe wyjście, potem grep: `nvidia-smi | grep -q` z pipefail bywa fałszywie nieudane
# (grep kończy się po trafieniu, nvidia-smi dostaje SIGPIPE).
smi=$(nvidia-smi 2>&1) || true
if [[ "$smi" != *"CUDA Version: "[0-9]* ]]; then
    echo "nvidia-smi nie pokazuje wersji CUDA. Oficjalny instalator próbowałby wtedy instalować" >&2
    echo "sterowniki przez apt — przerywam. Wyjście nvidia-smi:" >&2
    echo "${smi:-(puste; PATH=$PATH)}" | head -5 >&2
    exit 1
fi
for tool in curl zstd sha256sum systemctl; do
    if ! command -v "$tool" >/dev/null; then
        echo "Brak programu: $tool" >&2
        exit 1
    fi
done
echo "Sterownik NVIDIA: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"

# --- 2. oficjalny instalator ----------------------------------------------------------------------

work=$(mktemp -d /tmp/tide-ollama-XXXXXX)
trap 'rm -rf "$work"' EXIT
curl -fsSL https://ollama.com/install.sh -o "$work/install.sh"
echo
echo "Oficjalny instalator: $work/install.sh ($(wc -l <"$work/install.sh") linii)"
echo "SHA-256: $(sha256sum "$work/install.sh" | cut -d' ' -f1)"
if [[ $yes -eq 0 ]]; then
    read -r -p "Uruchomić go? [t/N] " answer
    if [[ "$answer" != [tTyY] ]]; then
        echo "Przerwano, nic nie zmieniono."
        exit 1
    fi
fi

# --- 3. instalacja --------------------------------------------------------------------------------

sh "$work/install.sh"

# --- 4. grupa -------------------------------------------------------------------------------------

usermod -a -G ollama "$user"

# --- 5. modele i nakładka usługi ------------------------------------------------------------------

install -d -o ollama -g ollama -m 755 "$MODELS_DIR"
install -d -m 755 "$DROPIN_DIR"
cat >"$DROPIN" <<EOF
# Ustawienia Tide dla Ollamy (scripts/ollama/install.sh, PT-13). Usunięcie: scripts/ollama/uninstall.sh
[Service]
# Tylko lokalnie; dostęp z sieci (Mac Studio) to osobna decyzja (PT-21).
Environment=OLLAMA_HOST=127.0.0.1:11434
# Modele na /home — na / jest za mało miejsca.
Environment=OLLAMA_MODELS=$MODELS_DIR
# Kontekst pod „Na daily” (do ok. 16 tys. znaków faktów na projekt) i opisy kart.
Environment=OLLAMA_CONTEXT_LENGTH=16384
# Mniej pamięci karty na kontekst (cache KV w 8 bitach wymaga flash attention).
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
# 8 GB VRAM: jeden model i jedno zapytanie naraz; po 10 min bez użycia model zwalnia pamięć.
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_KEEP_ALIVE=10m
EOF
chmod 644 "$DROPIN"

systemctl daemon-reload
systemctl restart ollama

for _ in $(seq 30); do
    curl -fsS "$API/api/version" >/dev/null 2>&1 && break
    sleep 1
done
if ! version=$(curl -fsS "$API/api/version"); then
    echo "Usługa nie odpowiada na $API. Dziennik: journalctl -u ollama -n 50" >&2
    exit 1
fi

echo
echo "Ollama działa: $version"
echo "Modele: $MODELS_DIR (wolne: $(df -h --output=avail "$MODELS_DIR" | tail -1 | tr -d ' '))"
log=$(journalctl -u ollama --since "-2 min" --no-pager 2>&1) || true
if [[ "${log,,}" == *cuda* ]]; then
    echo "Karta: $(grep -i 'inference compute' <<<"$log" | tail -1 | sed 's/.*msg=//')"
else
    echo "UWAGA: w dzienniku nie widać CUDA — modele mogą liczyć się na procesorze. journalctl -u ollama -n 50" >&2
fi
echo
echo "Dalej (bez sudo): bash scripts/ollama/pull-models.sh, potem python3 scripts/ollama/check.py"
echo "Grupa ollama dla $user działa po ponownym zalogowaniu (do samego API nie jest potrzebna)."
