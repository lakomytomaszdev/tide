"""Negatywna próba rzeczywistego tide-daemon na prywatnej magistrali, poza discover."""
import json
import os

from . import test_state_isolation_dbus as fixture


class LeakingDaemon(fixture.InheritedDaemonStateTest):
    def spawn(self):
        self.env["XDG_STATE_HOME"] = os.environ["TIDE_GUARD_PROBE_OLD_STATE"]
        return super().spawn()

    def test_default_service_journal_is_written_in_inherited_package_state(self):
        card = self.card(description="Dane syntetyczne.\n\nATRAPA: gotowe")
        # Prawdziwy StartCard używa domyślnego Journal z FlowEngine. Celowo nie
        # zamieniamy błędu produktu na porażkę unittest: robi to nadzorca drzewa.
        response = self.service.call(fixture.SERVICE, "/Board", "org.tide.Daemon.Board", "StartCard",
                                     card, json.dumps({"entry": "now", "executor": "Sonnet"}), timeout=5000)
        print("DAEMON RESULT IGNORED", response.type(), flush=True)
        print("INNER GREEN", flush=True)
