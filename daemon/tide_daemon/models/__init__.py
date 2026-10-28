"""Modele lokalne w usłudze: jedno miejsce rozmowy z serwerem modeli (Ollama teraz, Mac Studio później).

Ustawienia w $XDG_CONFIG_HOME/tide/models.json (punkty dostępu, rodzaj serwera, role), czytane
w chwili wywołania. Wszystko tutaj jest bez Qt:

- config.py    — punkty i role z models.json, wartości domyślne, token z pliku (tylko w pamięci),
- transport.py — jedno zapytanie HTTP z JSON-em, każdy błąd jako ModelError z komunikatem po polsku,
- client.py    — odpowiedź modelu dla roli albo (punkt, model), adapter według rodzaju serwera:
                 `ollama` przez natywne /api/chat (num_ctx z roli), `openai` przez /v1/chat/completions,
- queue.py     — kolejka wywołań całego procesu: jedno naraz na serwer, opisy kart przed raportem,
                 limit czekania osobno od limitu wywołania; chat() zawsze przez nią przechodzi,
- status.py    — stan serwerów: online, wersja, pobrane i załadowane modele (część na karcie), role;
                 próba połączenia bez zapisu (probe),
- settings.py  — zapis ustawień z okna Tide (D-Bus): punkty, tokeny, domyślny punkt, role, tryb raportu,
- pull.py      — pobieranie modelu na serwer Ollamy z postępem (strumień /api/pull).

Tryb lokalny nigdy nie przechodzi do chmury: gdy serwer nie odpowiada, wywołujący dostaje ModelError
i pokazuje informację zamiast opisu. Odpytywanie stanu, zapis i pobieranie: models_monitor.py, D-Bus: obiekt /Models.
Opis i kontrakt D-Bus: docs/tide-daemon.md (sekcja „Modele lokalne”), uzasadnienie: docs/decisions.md.
"""
from . import queue  # noqa: F401
from .client import Answer, TargetChanged, chat  # noqa: F401
from .transport import Cancel, ModelError  # noqa: F401
