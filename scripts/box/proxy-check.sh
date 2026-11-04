#!/bin/bash
# Ręczna próba proxy usługi pudełka (PT-168) z kontenera prób Incusa — opis w scripts/box/proxy_check.py.
# Bez sudo i bez zmian nftables: proxy usługi słucha na adresie mostka na porcie z reguł PT-166 (domyślnie 18080).
set -Eeuo pipefail
exec /usr/bin/python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/proxy_check.py" "$@"
