// Tide: globalny skrót Meta+A — przejście do okna sesji agenta, która czeka na ciebie najdłużej.
//
// Skrót rejestruje KWin (registerShortcut), więc działa na Waylandzie bez dodatkowych uprawnień
// i można go zmienić w Ustawieniach systemowych → Skróty → KWin. Wybór sesji i przełączenie
// okna (także karty Konsoli) robi tide-daemon: ActivateLongestWaiting zwraca false i nic
// nie robi, gdy nikt nie czeka. Jeśli usługa nie działa, D-Bus uruchomi ją na żądanie.
// „Czeka” to tylko prawdziwa blokada (zgoda, pytanie) — sesja, która skończyła turę („done”),
// nie jest celem skrótu, nawet gdy nikt inny nie czeka (docs/tide-daemon.md).
registerShortcut(
    "TideJumpToWaitingAgent",
    "Tide: przejdź do agenta, który czeka najdłużej",
    "Meta+A",
    function () {
        callDBus("org.tide.Daemon", "/Agents", "org.tide.Daemon.Agents", "ActivateLongestWaiting");
    }
);
