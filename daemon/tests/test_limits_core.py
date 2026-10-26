"""Limity AI w tide-daemon bez Qt: odczyty z /status, historia i okna, prognoza na syntetycznych
seriach (równe tempo, zrywami, przerwa, przejście przez reset, tydzień), powiadomienia raz na
okno także po restarcie, konfiguracja z palety i klient API na atrapie usługi.

Zegar i strefa są stałe (poniedziałek 26.10.2026, Europe/Warsaw), pliki w katalogu tymczasowym.
"""
import http.server
import json
import os
import secrets
import shutil
import tempfile
import threading
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from tide_daemon.limits import alerts as alerts_mod
from tide_daemon.limits import backend, config, forecast, history, readings
from tide_daemon.limits.readings import Reading
from tide_daemon.limits.tracker import LimitsTracker

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TZ = ZoneInfo("Europe/Warsaw")
CFG = config.from_dict(None)
SESSION = CFG.window("session")
WEEKLY = CFG.window("weekly")
MIN = 60
HOUR = 3600


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ).timestamp()


T0 = at(26, 14)              # poniedziałek 14:00
RESET = at(26, 18, 30)       # reset sesji 18:30


def series(values, start=T0, step=5 * MIN, reset=RESET, window="session", service="claude"):
    return [Reading(service, window, start + i * step, int(v), reset) for i, v in enumerate(values)]


def steady(start_used, per_hour, minutes, step_min=5):
    """Równe tempo: zużycie zaokrąglone do całych procentów, jak na stronie."""
    return [start_used + round(per_hour * i * step_min / 60) for i in range(minutes // step_min + 1)]


def fc(window, now=None, cfg=SESSION):
    return forecast.forecast(window, window[-1].t + 60 if now is None else now, cfg, TZ)


class ReadingsTest(unittest.TestCase):
    STATUS = {
        "chatgpt": {"session": {"remaining": None, "reset": None},
                    "weekly": {"remaining": 91, "reset": "~poniedziałek 11:41"},
                    "lastRead": "2026-10-26T16:41+01:00", "state": "ready"},
        "claude": {"session": {"remaining": 38, "reset": "poniedziałek 18:30"},
                   "weekly": {"remaining": 71, "reset": "czwartek 11:00"},
                   "lastRead": "2026-10-26T16:42+01:00", "state": "ready"},
        "paused": False, "busy": False,
    }

    def test_status_gives_used_percent_and_reset_moments(self):
        rows = readings.from_status(self.STATUS)
        self.assertEqual([(r.key, r.used) for r in rows],
                         [("chatgpt/weekly", 9), ("claude/session", 62), ("claude/weekly", 29)])
        self.assertEqual(rows[0].t, at(26, 16, 41))
        # „~poniedziałek 11:41” odczytane w poniedziałek 16:41 to następny poniedziałek.
        self.assertEqual(rows[0].reset, datetime(2026, 11, 2, 11, 41, tzinfo=TZ).timestamp())
        self.assertEqual(rows[1].reset, RESET)
        self.assertEqual(rows[2].reset, at(29, 11))

    def test_missing_read_time_or_values_are_skipped(self):
        status = json.loads(json.dumps(self.STATUS))
        status["claude"]["lastRead"] = None
        status["chatgpt"]["weekly"]["remaining"] = "91"
        self.assertEqual(readings.from_status(status), [])
        self.assertEqual(readings.from_status({"chatgpt": {"state": "login", "session": {}, "weekly": {},
                                                           "lastRead": None}}), [])
        self.assertEqual(readings.from_status("nie słownik"), [])

    def test_reset_labels(self):
        read = at(26, 14)
        self.assertEqual(readings.parse_reset("18:30", read), RESET)
        self.assertEqual(readings.parse_reset("13:00", read), at(27, 13))  # bez dnia: najbliższa taka godzina
        self.assertEqual(readings.parse_reset("~środa 09:05", read), at(28, 9, 5))
        self.assertEqual(readings.parse_reset("ŚRODA 9:05", read), at(28, 9, 5))
        for bad in (None, "", "jutro 10:00", "25:00", "reset za chwilę"):
            self.assertIsNone(readings.parse_reset(bad, read), bad)

    def test_reset_label_across_daylight_saving_change(self):
        # Sobota 24.10 (czas letni) → niedziela 25.10 12:00 już w czasie zimowym: godzina z etykiety zostaje.
        read = datetime(2026, 10, 24, 20, 0, tzinfo=TZ).timestamp()
        self.assertEqual(readings.parse_reset("niedziela 12:00", read),
                         datetime(2026, 10, 25, 12, 0, tzinfo=TZ).timestamp())


class ForecastTest(unittest.TestCase):
    def test_steady_pace_runs_out_before_reset(self):
        window = series(steady(30, 20, 90))           # 14:00–15:30, 20 pkt/h, 60% zużyte
        result = fc(window)
        self.assertEqual(result["state"], forecast.RUNS_OUT)
        self.assertAlmostEqual(result["ratePerHour"], 20, delta=0.5)
        # 40 pkt przy 20 pkt/h = 2 h od 15:30 → 17:30, reset 18:30.
        self.assertEqual(result["text"], "wyczerpie się ok. 17:30")
        self.assertEqual(result["short"], "koniec ok. 17:30")
        self.assertAlmostEqual(result["marginS"], HOUR, delta=5 * MIN)
        self.assertEqual((result["used"], result["remaining"], result["resetTs"]), (60, 40, int(RESET)))

    def test_steady_slow_pace_lasts(self):
        result = fc(series(steady(30, 6, 90)))        # 39% po 15:30, 6 pkt/h → starczy na 10 h
        self.assertEqual(result["state"], forecast.LASTS)
        self.assertEqual((result["text"], result["short"]), ("starczy do resetu", "starczy do resetu"))
        self.assertGreater(result["exhaustTs"], RESET)

    def test_bursty_pace_is_averaged(self):
        # Zrywy: +6 pkt w jednym odczycie, potem 15 min bez zmian (≈ 18 pkt/h).
        values, used = [], 30
        for i in range(19):
            if i and i % 4 == 0:
                used += 6
            values.append(used)
        result = fc(series(values))
        self.assertEqual(result["state"], forecast.RUNS_OUT)
        self.assertGreater(result["ratePerHour"], 15)
        self.assertLess(result["ratePerHour"], 22)

    def test_idle_gap_does_not_dilute_session_pace(self):
        # 40 min pracy (20 pkt/h), 80 min przerwy, 30 min pracy — przerwa liczy się najwyżej jako 15 min.
        values = steady(20, 20, 40) + [33] * 16 + [33 + round(20 * i * 5 / 60) for i in range(1, 7)]
        window = series(values)
        capped = fc(window)
        uncapped = forecast.forecast(window, window[-1].t + 60, config.from_dict(
            {"session": {"idleGapMin": 0}}).window("session"), TZ)
        self.assertGreater(capped["ratePerHour"], 14)
        self.assertLess(uncapped["ratePerHour"], capped["ratePerHour"] * 0.6)
        self.assertEqual(capped["state"], uncapped["state"])  # obie: 43% o 16:30, starczy do 18:30
        # Ta sama przerwa przy 78% zużycia o 16:30: z rozcieńczonym tempem byłoby „starczy”, a nie jest.
        busy = series([v + 35 for v in values])
        self.assertEqual(fc(busy)["text"], "wyczerpie się ok. 18:00")
        self.assertEqual(forecast.forecast(busy, busy[-1].t + 60, config.from_dict(
            {"session": {"idleGapMin": 0}}).window("session"), TZ)["state"], forecast.LASTS)

    def test_long_break_without_use_lasts(self):
        # Rano trochę pracy, potem 90 min stoi w miejscu (tempo z okna pracy to płaski odcinek).
        values = [10] * 25
        result = fc(series(values))
        self.assertEqual(result["state"], forecast.LASTS)
        self.assertEqual(result["ratePerHour"], 0)

    def test_too_little_data_gives_no_text(self):
        for values in ([30], [30, 32], [30, 32, 34]):  # trzy odczyty to dopiero 10 min
            result = fc(series(values))
            self.assertEqual((result["state"], result["reason"], result["text"]),
                             (forecast.UNKNOWN, "tooLittleData", ""), values)
        self.assertEqual(forecast.forecast([], T0, SESSION)["reason"], "noData")

    def test_stale_unknown_reset_and_passed_reset_give_no_text(self):
        window = series(steady(30, 20, 60))
        self.assertEqual(fc(window, now=window[-1].t + 31 * MIN)["reason"], "stale")
        self.assertEqual(fc(series(steady(30, 20, 60), reset=None))["reason"], "noReset")
        self.assertEqual(fc(window, now=RESET + 1)["reason"], "resetPassed")
        for reason_case in (fc(window, now=window[-1].t + 31 * MIN), fc(window, now=RESET + 1)):
            self.assertEqual(reason_case["text"], "")

    def test_exhausted_window(self):
        result = fc(series([96, 98, 99, 100]))
        self.assertEqual((result["state"], result["text"], result["remaining"]), (forecast.EXHAUSTED, "", 0))

    def test_reset_crossing_uses_only_the_new_window(self):
        # Do 18:30 ostre zużycie (85→97%), potem reset i spokojna nowa sesja do 23:30.
        before = series([85, 88, 91, 94, 97], start=at(26, 18, 5), reset=RESET)
        new_reset = at(26, 23, 30)
        after = series([0, 1, 1, 2, 2, 3, 3, 4], start=at(26, 18, 35), reset=new_reset)
        rows = before + after
        window = history.current_window(rows, SESSION)
        self.assertEqual(window, after)
        result = fc(window)
        self.assertEqual(result["windowStartTs"], int(at(26, 18, 35)))
        self.assertEqual(result["state"], forecast.LASTS)
        self.assertEqual(result["resetTs"], int(new_reset))
        # Pierwsze odczyty nowego okna: za mało danych, mimo bogatej historii sprzed resetu.
        self.assertEqual(fc(history.current_window(before + after[:2], SESSION))["reason"], "tooLittleData")

    def test_weekly_pace_includes_nights(self):
        # Doba: 8 h pracy po ok. 2 pkt/h, 16 h bez zmian; odczyt co 30 min. Reset w czwartek 11:00.
        values, used = [], 60
        for half_hour in range(49):            # od poniedziałku 8:00 do wtorku 8:00
            hour = 8 + half_hour / 2
            if 8 < hour <= 16 or 32 < hour:
                used += 1
            values.append(used)
        window = series(values, start=at(26, 8), step=30 * MIN, reset=at(29, 11), window="weekly")
        result = fc(window, cfg=WEEKLY)
        # Tempo dobowe ≈ 16 pkt / 24 h, a nie 2 pkt/h z godzin pracy.
        self.assertGreater(result["ratePerHour"], 0.5)
        self.assertLess(result["ratePerHour"], 1.0)
        self.assertEqual(result["state"], forecast.RUNS_OUT)
        self.assertTrue(result["text"].startswith("wyczerpie się ok. "), result["text"])
        self.assertRegex(result["whenText"], r"^(środa|czwartek) \d\d:(00|30)$")

    def test_when_text(self):
        now = at(26, 14)
        self.assertEqual(forecast.when_text(at(26, 17, 40), now, TZ), "17:40")
        self.assertEqual(forecast.when_text(at(27, 9, 5), now, TZ), "jutro 09:05")
        self.assertEqual(forecast.when_text(at(29, 11), now, TZ), "czwartek 11:00")


class WindowsTest(unittest.TestCase):
    def test_breaks(self):
        a = Reading("claude", "session", T0, 40, RESET)
        same = Reading("claude", "session", T0 + 300, 41, RESET + 60)        # „~” drga o minutę
        after_reset = Reading("claude", "session", RESET + 60, 2, None)
        jumped = Reading("claude", "session", T0 + 600, 41, RESET + 5 * HOUR)
        dropped = Reading("claude", "session", T0 + 600, 30, RESET)
        late = Reading("claude", "session", T0 + 6 * HOUR, 41, None)
        no_reset = Reading("claude", "session", T0, 40, None)
        self.assertFalse(history.is_break(a, same, SESSION))
        self.assertTrue(history.is_break(a, after_reset, SESSION))
        self.assertTrue(history.is_break(a, jumped, SESSION))
        self.assertTrue(history.is_break(a, dropped, SESSION))
        self.assertTrue(history.is_break(no_reset, late, SESSION))
        self.assertEqual([len(w) for w in history.split_windows([a, same, after_reset], SESSION)], [2, 1])


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-history-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "state", "tide", "limits-history.jsonl")
        self.now = T0 + 2 * HOUR

    def store(self, days=14):
        h = history.History(self.path, days=days, clock=lambda: self.now)
        h.load()
        return h

    def lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_appends_only_new_readings_and_survives_restart(self):
        h = self.store()
        rows = series(steady(30, 20, 30))
        self.assertTrue(all(h.add(r) for r in rows))
        self.assertFalse(h.add(rows[-1]))          # ten sam lastRead drugi raz
        self.assertFalse(h.add(rows[0]))           # starszy niż ostatni
        h.flush()
        self.assertEqual(len(self.lines()), len(rows))
        self.assertEqual(self.lines()[0], {"t": int(T0), "s": "claude", "w": "session", "u": 30, "r": int(RESET)})
        again = self.store()
        self.assertEqual(again.readings("claude/session"), rows)
        self.assertEqual(again.window("claude/session", SESSION), rows)

    def test_keeps_only_history_days_and_skips_broken_lines(self):
        os.makedirs(os.path.dirname(self.path))
        old = Reading("claude", "weekly", self.now - 15 * 86400, 10, None)
        recent = Reading("claude", "weekly", self.now - 86400, 20, None)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(history.encode(old) + "\n" + "{zepsute\n" + '{"t":1,"s":"x","w":"inne","u":5}\n'
                     + history.encode(recent) + "\n")
        h = self.store()
        self.assertEqual(h.readings("claude/weekly"), [recent])
        h.flush()                                   # pierwszy zapis przepisuje plik bez starych linii
        self.assertEqual(self.lines(), [json.loads(history.encode(recent))])
        # Później starsze niż 14 dni wypadają przy dopisywaniu (najwyżej raz na godzinę).
        self.now += 14 * 86400
        h.add(Reading("claude", "weekly", self.now, 30, None))
        h.flush()
        self.assertEqual([row["u"] for row in self.lines()], [30])

    def test_file_holds_only_numbers(self):
        h = self.store()
        h.add(Reading("chatgpt", "weekly", T0, 9, None))
        h.flush()
        with open(self.path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(set(json.loads(text)), {"t", "s", "w", "u", "r"})
        self.assertNotIn("reset", text)


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-alerts-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.path = os.path.join(self.tmp, "tide", "limits-alerts.json")

    def book(self, cfg=CFG):
        return alerts_mod.AlertBook(cfg, self.path, tz=TZ)

    def thresholds_only(self):
        """Księga bez alertu o wyczerpaniu (margines nieosiągalny) — same progi zużycia."""
        return self.book(config.from_dict({"alertRunoutMarginMin": 100000}))

    def run_series(self, book, values, reset=RESET, start=T0):
        """Kolejne odczyty jak w usłudze: prognoza z okna do bieżącego odczytu i sprawdzenie alertów."""
        rows = series(values, start=start, reset=reset)
        sent = []
        for i in range(1, len(rows) + 1):
            window = rows[:i]
            sent += book.check("claude", "session", fc(window), window[-1].t + 60)
        return sent

    def test_each_threshold_once_per_window_and_not_after_restart(self):
        # Wolne tempo (bez prognozy wyczerpania): 76 → 84 → 96%.
        values = [76, 76, 77, 78, 80, 81, 81, 82]
        sent = self.run_series(self.thresholds_only(), values)
        self.assertEqual([a["summary"] for a in sent], ["Claude: zużyto 80% limitu sesji"])
        self.assertEqual(sent[0]["body"], "Zostało 20% · reset 18:30")
        self.assertEqual(sent[0]["id"], "claude/session")
        # Restart usługi: nowy AlertBook z tym samym plikiem — bez powtórki 80%, za to 95% raz.
        restarted = self.thresholds_only()
        sent = self.run_series(restarted, values + [90, 95, 96, 97])
        self.assertEqual([a["summary"] for a in sent], ["Claude: zużyto 95% limitu sesji"])
        self.assertEqual(self.run_series(self.thresholds_only(), values + [90, 95, 96, 97, 98]), [])

    def test_jump_over_several_thresholds_gives_one_alert(self):
        sent = self.run_series(self.thresholds_only(), [70, 70, 96])
        self.assertEqual([a["summary"] for a in sent], ["Claude: zużyto 96% limitu sesji"])
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["claude/session"]["fired"], ["used80", "used95"])

    def test_new_window_alerts_again(self):
        book = self.thresholds_only()
        self.assertEqual(len(self.run_series(book, [79, 80, 81])), 1)
        later = self.run_series(book, [79, 80, 81], reset=RESET + 5 * HOUR, start=RESET + 10 * MIN)
        self.assertEqual(len(later), 1)

    def test_runout_once_with_enough_margin(self):
        # 20 pkt/h od 30%: wyczerpie się ok. 17:30, reset 18:30 — godzina zapasu > 30 min.
        sent = self.run_series(self.book(), steady(30, 20, 90))
        self.assertEqual([a["summary"] for a in sent], ["Claude: limit sesji skończy się przed resetem"])
        self.assertIn("wyczerpie się ok. ", sent[0]["body"])
        self.assertIn("a reset dopiero 18:30", sent[0]["body"])
        self.assertEqual(self.run_series(self.book(), steady(30, 20, 90) + [61, 62]), [])

    def test_runout_close_to_reset_is_not_announced(self):
        # Wyczerpanie ok. 18:00 przy resecie 18:15 — za mało zapasu, także przy szumie pierwszych odczytów.
        sent = self.run_series(self.book(), steady(20, 20, 90), reset=at(26, 18, 15))
        self.assertEqual(sent, [])

    def test_threshold_and_runout_in_one_reading_are_one_notification(self):
        # Pierwsza prognoza (po 15 min) przypada na odczyt, w którym zużycie przekracza 80%.
        sent = self.run_series(self.book(), [62, 68, 74, 81])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["summary"], "Claude: zużyto 81% limitu sesji")
        self.assertIn("Przy obecnym tempie wyczerpie się ok.", sent[0]["body"])
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("runout", json.load(fh)["claude/session"]["fired"])

    def test_disabled_or_broken_forecast_sends_nothing(self):
        self.assertEqual(self.book().check("claude", "session", forecast.forecast([], T0, SESSION), T0), [])

    def test_reading_from_an_already_reset_window_is_not_announced(self):
        # Stary odczyt (np. QuotaApp wstrzymana przy pełnym ekranie) z 90% — reset już minął.
        window = series([88, 89, 90])
        self.assertEqual(self.book().check("claude", "session", fc(window, now=RESET + 60), RESET + 60), [])


class ConfigTest(unittest.TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual(CFG.alert_used, (80, 95))
        self.assertEqual(CFG.poll_s, 120)
        self.assertEqual(SESSION.lookback_s, 90 * MIN)
        self.assertEqual(WEEKLY.idle_gap_s, 0)
        cfg = config.from_dict({"alertUsedPercent": [90, 70], "session": {"lookbackMin": 60, "nieznany": 1},
                                "pollS": -5, "weekly": "zły typ"})
        self.assertEqual(cfg.alert_used, (70, 90))
        self.assertEqual(cfg.window("session").lookback_s, HOUR)
        self.assertEqual(cfg.poll_s, 120)
        self.assertEqual(cfg.window("weekly"), WEEKLY)
        self.assertEqual(config.from_dict({"alertUsedPercent": [0, 150]}).alert_used, (80, 95))

    def test_palette_values_match_defaults(self):
        path = os.path.join(REPO, "theme", "palette", "mist.json")
        self.assertEqual(config.load([os.path.join(REPO, "brak.json"), path]), CFG)
        with open(path, encoding="utf-8") as fh:
            section = json.load(fh)["limits"]
        self.assertEqual(section, config.DEFAULTS)


class FakeQuotaPeek(http.server.ThreadingHTTPServer):
    """Atrapa API QuotaApp: GET /status z tokenem, kolejne odpowiedzi z listy."""

    def __init__(self, token, statuses):
        super().__init__(("127.0.0.1", 0), FakeQuotaPeekHandler)
        self.token = token
        self.statuses = list(statuses)
        self.requests = []

    def next_status(self):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


class FakeQuotaPeekHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.requests.append((self.command, self.path, self.headers.get("X-Limity-Token")))
        if self.headers.get("X-Limity-Token") != self.server.token:
            code, value = 403, {"error": "forbidden"}
        elif self.path != "/status":
            code, value = 404, {"error": "not found"}
        else:
            code, value = 200, self.server.next_status()
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.requests.append((self.command, self.path, self.headers.get("X-Limity-Token")))
        self.send_response(405)
        self.end_headers()


def start_fake(test, token, statuses, cls=FakeQuotaPeek):
    server = cls(token, statuses)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def write_config(folder, token, port):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"token": token, "port": port}, fh)
    os.chmod(path, 0o600)
    return path


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-backend-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.token = "test-" + secrets.token_hex(12)

    def test_reads_status_with_token_from_config_only_get(self):
        server = start_fake(self, self.token, [ReadingsTest.STATUS])
        path = write_config(self.tmp, self.token, server.server_address[1])
        self.assertEqual(backend.fetch_status(path), ReadingsTest.STATUS)
        self.assertEqual(server.requests, [("GET", "/status", self.token)])

    def test_errors_never_contain_the_token(self):
        server = start_fake(self, "inny", [ReadingsTest.STATUS])
        path = write_config(self.tmp, self.token, server.server_address[1])
        with self.assertRaises(backend.BackendError) as caught:
            backend.fetch_status(path)
        self.assertIn("403", str(caught.exception))
        self.assertNotIn(self.token, str(caught.exception))
        with self.assertRaises(backend.BackendError):
            backend.fetch_status(os.path.join(self.tmp, "brak.json"))
        port = server.server_address[1]
        server.shutdown()
        server.server_close()
        with self.assertRaises(backend.BackendError) as caught:
            backend.fetch_status(write_config(os.path.join(self.tmp, "b"), self.token, port))
        self.assertNotIn(self.token, str(caught.exception))

    def test_config_path_follows_environment(self):
        old = {k: os.environ.get(k) for k in ("LIMITY_CONFIG", "XDG_CONFIG_HOME")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])
        os.environ.pop("LIMITY_CONFIG", None)
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.assertEqual(backend.config_path(), os.path.join(self.tmp, "limity-ai-widget", "config.json"))
        os.environ["LIMITY_CONFIG"] = "/x/config.json"
        self.assertEqual(backend.config_path(), "/x/config.json")


def status_at(read_ts, claude_session, claude_weekly=None, gpt_weekly=None,
              session_reset="poniedziałek 18:30", weekly_reset="czwartek 11:00"):
    """Odpowiedź /status w kształcie QuotaApp z podanym zużyciem (procent zużyty)."""
    read = datetime.fromtimestamp(read_ts, TZ).isoformat(timespec="minutes")

    def metric(used, reset):
        return {"remaining": None if used is None else 100 - used, "reset": reset if used is not None else None}

    return {
        "chatgpt": {"session": metric(None, None), "weekly": metric(gpt_weekly, "~poniedziałek 11:41"),
                    "lastRead": read if gpt_weekly is not None else None,
                    "state": "ready" if gpt_weekly is not None else "checking"},
        "claude": {"session": metric(claude_session, session_reset), "weekly": metric(claude_weekly, weekly_reset),
                   "lastRead": read, "state": "ready"},
        "paused": False, "fullscreenKnown": True, "browserOpen": False, "busy": False,
    }


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-tracker-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.now = T0

    def tracker(self):
        state = os.path.join(self.tmp, "state", "tide")
        return LimitsTracker(CFG, history=history.History(os.path.join(state, "limits-history.jsonl"),
                                                          clock=lambda: self.now),
                             alerts=alerts_mod.AlertBook(CFG, os.path.join(state, "limits-alerts.json"), tz=TZ),
                             clock=lambda: self.now, tz=TZ)

    def feed(self, tracker, values, weekly=20):
        sent = []
        for i, used in enumerate(values):
            self.now = T0 + i * 5 * MIN + 30
            sent += tracker.ingest(status_at(T0 + i * 5 * MIN, used, weekly + i // 6))
            sent += tracker.ingest(status_at(T0 + i * 5 * MIN, used, weekly + i // 6))  # ten sam odczyt = nic
        return sent

    def test_end_to_end(self):
        tracker = self.tracker()
        sent = self.feed(tracker, steady(30, 20, 90))
        self.assertEqual([a["summary"] for a in sent], ["Claude: limit sesji skończy się przed resetem"])
        snap = tracker.snapshot()
        self.assertTrue(snap["available"])
        self.assertEqual(list(snap["services"]), ["claude"])
        session = snap["services"]["claude"]["session"]
        self.assertEqual((session["state"], session["text"]), ("runs-out", "wyczerpie się ok. 17:30"))
        self.assertEqual(snap["services"]["claude"]["weekly"]["state"], "unknown")  # 90 min to za mało na tydzień
        # Restart: ta sama historia i pamięć powiadomień — prognoza ta sama, powiadomień brak.
        again = self.tracker()
        self.assertEqual(again.snapshot()["services"], snap["services"])
        self.assertEqual(self.feed(again, steady(30, 20, 90) + [61]), [])

    def test_unavailable_service_keeps_forecast_from_history(self):
        tracker = self.tracker()
        self.feed(tracker, steady(30, 20, 30))
        tracker.failed("usługa Limity AI nie odpowiada (URLError)")
        snap = json.loads(tracker.json())
        self.assertFalse(snap["available"])
        self.assertEqual(snap["error"], "usługa Limity AI nie odpowiada (URLError)")
        self.assertIn("claude", snap["services"])


class PrintLimitsTest(unittest.TestCase):
    """--print-limits: historia z dysku i jeden GET /status, bez zapisu czegokolwiek."""

    def test_prints_forecast_without_writing(self):
        import subprocess
        import sys
        tmp = tempfile.mkdtemp(prefix="tide-print-limits-")
        self.addCleanup(shutil.rmtree, tmp)
        token = "test-" + secrets.token_hex(12)
        state = os.path.join(tmp, "state")
        h = history.History(os.path.join(state, "tide", "limits-history.jsonl"), clock=lambda: T0 + 2 * HOUR)
        for r in series(steady(50, 20, 85)):
            h.add(r)
        h.flush()
        before = os.listdir(os.path.join(state, "tide"))
        server = start_fake(self, token, [status_at(T0 + 90 * MIN, 80, 20)])
        config_path = write_config(os.path.join(tmp, "limity"), token, server.server_address[1])
        env = dict(os.environ, LIMITY_CONFIG=config_path, XDG_STATE_HOME=state, TZ="Europe/Warsaw")
        out = subprocess.run([sys.executable, os.path.join(REPO, "daemon", "tide-daemon.py"), "--print-limits",
                              "--now", "2026-10-26T14:31:00Z"], env=env, capture_output=True, text=True,
                             check=True, timeout=30).stdout
        result = json.loads(out)
        self.assertTrue(result["available"])
        self.assertEqual(result["services"]["claude"]["session"]["text"], "wyczerpie się ok. 16:30")
        self.assertNotIn(token, out)
        self.assertEqual(os.listdir(os.path.join(state, "tide")), before)  # bez nowych plików
        with open(h.path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 18)                     # i bez dopisywania


if __name__ == "__main__":
    unittest.main()
