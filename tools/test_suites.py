"""Jedyna lista nazw zestawów, mapy zmian i wyjątków od równoległości."""

# Ścieżki są względem repozytorium. Wartości to pary (katalog, wzorzec discover).
SUITES = {
    "kwin": [("kwin/tests", "test*.py")],
    "colors": [("theme/colors/tests", "test*.py")],
    "scripts": [("scripts/tests", "test*.py")],
    "board": [("apps/board/tests", "test*.py")],
    "daemon": [("daemon/tests", "test*.py")],
    "issues": [("daemon/tests", "test_issues*.py"), ("apps/issues/tests", "test*.py")],
    "decoration": [("theme/decoration/tests", "test*.py")],
    "docker": [("apps/docker/tests", "test*.py")],
    "icons": [("theme/icons/tests", "test*.py")],
    "konsole": [("theme/konsole/tests", "test*.py")],
    "logo": [("theme/logo/tests", "test*.py")],
    "lookandfeel": [("theme/lookandfeel/tests", "test*.py")],
    "lockscreen": [("theme/lockscreen/tests", "test*.py")],
    "models": [("apps/models/tests", "test*.py")],
    "newtask": [("apps/newtask/tests", "test*.py")],
    "palette": [("theme/palette/tests", "test*.py")],
    "plasma": [("theme/plasma/tests", "test*.py")],
    "plymouth": [("theme/plymouth/tests", "test*.py")],
    "reports": [("apps/reports/tests", "test*.py")],
    "sddm": [("theme/sddm/tests", "test*.py")],
    "sessions": [("apps/sessions/tests", "test*.py")],
    "terminal": [("theme/terminal/tests", "test*.py")],
    "tide": [("apps/tide/tests", "test*.py")],
    "tray": [("tray/tests", "test*.py")],
    "vivaldi": [("theme/vivaldi/tests", "test*.py")],
    "wallpaper": [("theme/wallpaper/tests", "test*.py")],
    "widgets": [("widgets/tests", "test*.py")],
    "worktrees": [("apps/worktrees/tests", "test*.py")],
    "cardflow": [
        ("daemon/tests", "test_cardflow*.py"),
        ("daemon/tests", "test_base_suggestions.py"),
        ("daemon/tests", "test_hostingcli_real.py"),
    ],
}

# Najdłuższy pasujący prefiks wygrywa. Nieznana ścieżka oznacza pełny przebieg.
CHANGE_MAP = {
    directory.removesuffix("tests"): name
    for name, entries in SUITES.items() if name not in {"cardflow", "issues"}
    for directory, _ in entries
}
CHANGE_MAP["daemon/tide_daemon/cardflow/"] = "cardflow"
CHANGE_MAP["daemon/tests/test_cardflow"] = "cardflow"
CHANGE_MAP["daemon/tide_daemon/issues.py"] = "issues"
CHANGE_MAP["daemon/tests/test_issues"] = "issues"
CHANGE_MAP["cli/"] = "issues"
CHANGE_MAP["mk/cli.mk"] = "issues"
CHANGE_MAP["apps/issues/"] = "issues"

# Narzędzia użytkowe ładowane po ścieżce lub uruchamiane jako proces nie tworzą
# zwykłej krawędzi importu. Jawny kontrakt obejmuje wszystkich znanych odbiorców.
# Pozostałe tools/, zwłaszcza test_*.py, są infrastrukturą: pełny przebieg.
FILE_TEST_MODULES = {
    "tools/board_compat_check.py": ("daemon/tests/test_board_compat_check.py",),
    "tools/question_corpus.py": ("daemon/tests/test_question_corpus.py",),
    "tools/push_guard.py": ("daemon/tests/test_push_guard.py",),
    "tools/push_guard.json": ("daemon/tests/test_push_guard.py",),
    "tools/next_id.py": ("daemon/tests/test_boardplans.py", "daemon/tests/test_coordination_numbering.py"),
    "tools/check_plans.py": ("daemon/tests/test_boardplans.py", "daemon/tests/test_coordination_numbering.py"),
    "tools/sessions_preview.py": ("apps/sessions/tests/test_window.py",),
}

# Tylko ten kod ma stabilną przestrzeń nazw importów. Pozostałe komponenty
# (QML, generatory ładowane po ścieżce) zachowują bezpieczny dobór katalogowy.
IMPORT_ROOTS = {"daemon/tide_daemon/": "tide_daemon"}

# Uruchomienie osobnego procesu usługi nie tworzy krawędzi importu do jej kodu.
# Te dwa małe moduły sprawdzają start/Agents oraz drogę /Board od ticketu do review.
SERVICE_CONTRACT_MODULES = (
    "daemon/tests/test_dbus_contract.py",
    "daemon/tests/test_service_smoke.py",
)
SERVICE_PREFIX = "daemon/"

# Zmiana wspólnej infrastruktury testów wymaga pełnego przebiegu, także gdy
# pomocnik mieszka w module testowym (FlowCase/FlowInProcess).
SHARED_TEST_MODULES = (
    "daemon/tests/test_cardflow.py",
    "daemon/tests/test_cardflow_dbus.py",
)

# Klucz to ścieżka modułu; wartość musi wyjaśniać wspólny zasób. Takie moduły
# uruchamiamy jeden po drugim dopiero po zakończeniu wszystkich równoległych.
# Prywatne magistrale D-Bus, porty 0 i TemporaryDirectory nie wymagają wyjątków.
SERIAL_MODULES = {
    "theme/terminal/tests/test_statusline.py": "pomiar budżetu 150 ms czasu ściennego bez konkurencji pozostałych modułów o CPU",
    "daemon/tests/test_cardflow_review.py":
        "Qt: tick uruchamia domknięcie przed callbackiem decyzji; test liczy step.started przed następnym tickiem. "
        "Próba PT-215: 0/3 porażek osobno, 1/5 przy 14 procesach; osobno do czasu synchronizacji scenariusza.",
    "daemon/tests/test_cardflow_dbus.py":
        "D-Bus: test czyta Snapshot natychmiast po odtworzeniu pliku dziennika, przed publikacją ostrzeżenia. "
        "Próba PT-215: 0/3 porażek osobno, 2/4 przy 14 procesach; osobno do czasu synchronizacji scenariusza.",
}
