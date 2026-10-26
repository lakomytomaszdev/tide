// Tide: globalny skrót Meta+Alt+A — okno „Tide: nowe zadanie” (repozytorium, Claude Code albo Codex,
// polecenie, nowy worktree; agent startuje w karcie Konsoli).
//
// Meta+Shift+A, o który prosiło zgłoszenie (#22), ma w Plasmie „Przełączaj między aktywnościami
// (odwrotnie)”, więc skrót to Meta+Alt+A — ta sama litera co Meta+A (skok do czekającego agenta).
// Skrót rejestruje KWin (registerShortcut), jak przy Meta+A: działa na Waylandzie i można go zmienić
// w Ustawieniach systemowych → Skróty → KWin. Okno otwiera tide-daemon (ShowDialog); drugie
// naciśnięcie przy otwartym oknie tylko je pokazuje. Jeśli usługa nie działa, D-Bus ją uruchomi.
registerShortcut(
    "TideNewAgentTask",
    "Tide: nowe zadanie dla agenta",
    "Meta+Alt+A",
    function () {
        callDBus("org.tide.Daemon", "/Tasks", "org.tide.Daemon.Tasks", "ShowDialog");
    }
);
