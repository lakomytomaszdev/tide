"""Syntetyczne raporty w kształcie /Reports Days() i Day(doba) (report.json z reports/data.py) — układ
jak na zatwierdzonym mocku ReportsView: sześć dni, pięć projektów (dwa z czymś, co czeka), tagi
z tablicy, „Poza projektami”. Nazwy i treść są wymyślone, bez prawdziwej pracy.

Zegar: wt. 27.10.2026 07:00 w Warszawie (TZ=Europe/Warsaw w testach)."""

TZ = "Europe/Warsaw"
T0 = 1793080800          # 2026-10-27 07:00 CET
H = 3600
DAY = 86400
TODAY = "2026-10-27"
INJECTION = '<b>pogrubione</b> <a href="file:///etc/passwd">link</a> <img src=x onerror=alert(1)>'


def item(text, ref="", about="", next_=""):
    out = {"ref": ref, "text": text}
    if about:
        out["about"] = about
    if next_:
        out["next"] = next_
    return out


def note(ref, title, text, summary, manual=False, checklist=(), hours=1):
    return {"id": f"claude:k|{ref}", "ref": ref, "title": title, "text": text, "summary": summary, "manual": manual,
            "checklist": list(checklist), "ts": T0 + hours * H}


def project(name, slug, headline, done=(), doing=(), waiting=(), next_=(), chores=(), theme="", tools=("claude",),
            finished=(), attention=()):
    groups = {"done": list(done), "doing": list(doing), "waiting": list(waiting), "next": list(next_),
              "attention": list(attention), "chores": list(chores)}
    counts = {k: len(v) for k, v in groups.items()}
    counts["waiting"] = len([i for i in waiting if not i.get("stale")])
    counts["finished"] = len(finished)
    lines = ["Na daily · wtorek, 27 października 2026", "", name]
    if theme:
        lines.append(f"Na czym polega: {theme}")
    if finished:
        lines.append("Skończone dziś:")
        lines += [f"- {n['ref']} {n['text']}" for n in finished]
    for key, label in (("done", "Zrobione"), ("doing", "W toku"), ("waiting", "Czeka na mnie"), ("attention", "Uwaga"),
                       ("next", "Dalej"), ("chores", "Porządki")):
        fresh = [i for i in groups[key] if not i.get("stale")]
        if fresh:
            lines.append(f"{label}:")
            for i in fresh:
                line = f"- {i['ref']} {i['text']}" if i["ref"] else f"- {i['text']}"
                if i.get("about"):
                    line += f" — {i['about']}"
                if i.get("next"):
                    line += f" (dalej: {i['next']})"
                lines.append(line)
    return dict({"name": name, "slug": slug, "page": f"{slug}.html", "path": f"/home/ja/DevWorkspace/{name}",
                 "tools": list(tools), "headline": headline, "theme": theme, "counts": counts,
                 "hot": bool(counts["waiting"]), "choresOnly": False, "commits": 3, "sessions": 2,
                 "waitingSessions": 0, "lastTs": T0 - 2 * H, "plans": [], "copyText": "\n".join(lines) + "\n",
                 "finished": list(finished)},
                **groups)


def today_report() -> dict:
    projects = [
        project("log-explorer", "log-explorer",
                "Poprawka wyszukiwania bez duplikatów przeszła review i czeka na scalenie. Warunki na kolumnach "
                "przebiegu są w połowie.",
                done=[item("Wyszukiwanie fraz nie zwraca już duplikatów; poprawka przeszła review bez uwag."),
                      item("Test liczby rekordów nie zależy już od czasu wykonania.")],
                doing=[item("Wartość w logu: 5 z 9 ticketów, teraz warunki na kolumnach przebiegu.", "K-13",
                            next_="warunki w zapisanych zapytaniach")],
                waiting=[item("czy warunek ma działać też na archiwalnych logach?", "TOM-146"),
                         item("poprawka gotowa do scalenia z master.", "K-12")],
                next_=[item("warunki w zapisanych zapytaniach.", "TOM-147")],
                theme="Filtrowanie logów po wartościach: warunki na kolumnach i zapisane zapytania.",
                finished=[note("K-14", "Eksport do CSV",
                               "Wyniki wyszukiwania da się pobrać jako plik CSV. Zespół otworzy je w arkuszu bez kopiowania.",
                               "Wyniki wyszukiwania da się pobrać jako CSV", manual=True,
                               checklist=["pobierz CSV i otwórz w arkuszu", "polskie znaki w nagłówkach"])]),
        project("mobile-app", "mobile-app",
                "Bez zmian w kodzie. Specyfikacja historii diagnoz jest gotowa do przeczytania.",
                done=[item("Specyfikacja historii diagnoz z filtrem po numerze pojazdu.")],
                waiting=[item("filtr po dacie w pierwszej wersji czy później?", "K-19"),
                         dict(item("Wydanie: agent pyta: „Pushować?”"), stale=True, kind="blocked",
                              sessionId="claude:m", since=T0 - 3 * H)]),
        project("plasma-tide", "plasma-tide",
                "Karty w Tide Sesje opisane po polsku, ze szczegółami ticketu w sekcjach. Wrócili koordynatorzy "
                "Codexa uruchomieni przed oknem dnia.",
                done=[item("Karty na tablicy Tide Sesje mówią po polsku, czym jest zadanie i co się dzieje teraz."),
                      item("Szczegóły ticketu mają sekcje „Co to jest”, „Co zrobiono” i „Teraz / review”."),
                      item("Koordynatorzy Codexa uruchomieni przed oknem dnia znów są na liście.")],
                doing=[item("Tablica pracy: plan z 10 ticketami, zaczęte okno tablicy.", "K-15")],
                next_=[item("kafelek tablicy w docku.", "TIDE-3"), item("moduł Docker w usłudze.", "TIDE-8")],
                chores=[item("Plan pracy agentów przeniesiony do docs/agents/plans.")],
                tools=("claude", "codex")),
        project("team-v2", "team-v2", "Zatwierdzanie bazowej wersji z otwartym pytaniem przeszło review. Trwa LA-036.",
                done=[item("bazową wersję można zatwierdzić, zostawiając otwarte pytanie.", "LA-035")],
                doing=[item("w trakcie, implementuje Codex.", "LA-036")],
                next_=[item("LA-037, LA-038 z planu.")], tools=("codex",)),
        project("cookapp", "cookapp", "Kontenery działają od wczoraj; dokumentacja API opisuje stronicowanie.",
                done=[item("Dokumentacja API opisuje stronicowanie listy przepisów.")],
                next_=[item("import przepisu z linku.", "K-17")],
                attention=[dict(item("Konflikt numerów: K-17 ma w dwóch planach różne tytuły."), kind="attention")]),
    ]
    return {
        "version": 1, "day": TODAY, "title": "Wtorek, 27 października 2026", "generatedAt": T0,
        "start": T0 - DAY, "end": T0, "period": "pon. 26 paź 07:00 – wt. 27 paź 07:00 · 1 dzień",
        "since": "Od poniedziałku 07:00", "source": "opis: Claude Sonnet",
        "counts": {"projects": 7, "waiting": 3, "waitingSessions": 1, "commits": 15, "sessions": 10, "outside": 2,
                   "outsideWaiting": 1, "quiet": 4, "finished": 1, "waitingNow": 1},
        "brief": ["log-explorer: wyszukiwanie fraz nie zwraca już duplikatów, a wyniki da się pobrać jako CSV.",
                  "plasma-tide: karty na tablicy mówią po polsku, czym jest zadanie i co się dzieje teraz.",
                  "team-v2: bazową wersję można zatwierdzić z otwartym pytaniem; trwa kolejny krok wydania.",
                  "W pozostałych projektach drobne poprawki dokumentacji i specyfikacja historii diagnoz."],
        "briefSource": "model",
        "now": [{"id": "claude:a", "project": "log-explorer", "tool": "claude", "title": "Warunki na wartościach",
                 "text": "czy warunek ma działać też na archiwalnych logach?", "reason": "question", "since": T0 - H}],
        "nowText": "Teraz czeka na ciebie: log-explorer — agent pyta: „czy warunek ma działać też na archiwalnych logach?”.",
        "live": True,
        "copyAll": "Na daily · wtorek, 27 października 2026\n",
        "projects": projects,
        "choreProjects": [
            {"name": "QuotaApp", "slug": "quotaapp", "page": "quotaapp.html", "path": "/home/ja/DevWorkspace/QuotaApp",
             "chores": [item("Instrukcje dla agentów uporządkowane.")]},
            {"name": "dev-dashboard", "slug": "dev-dashboard", "page": "dev-dashboard.html", "path": "/home/ja/DevWorkspace/dev-dashboard",
             "chores": [item("Zadania przeniesione do nowego trackera.")]},
        ],
        "outside": [
            {"project": "DevWorkspace", "path": "/home/ja/DevWorkspace", "tool": "claude", "state": "waiting",
             "word": "czeka na odpowiedź", "title": "Konfiguracja KDE", "task": "Jak ustawić skrót dla okna?",
             "said": "Który skrót wolisz: Meta+T czy Meta+Shift+T?", "lastTs": T0 - H},
            {"project": "tmp", "path": "/tmp/proba", "tool": "codex", "state": "done", "word": "skończył",
             "title": "Próba czcionek", "task": "", "said": "Geist jest zainstalowany.", "lastTs": T0 - 5 * H},
        ],
    }


def older_report(day: str, title: str, names: list[str]) -> dict:
    report = today_report()
    report.update(day=day, title=title, since="Od soboty 09:40", generatedAt=T0 - DAY, outside=[], now=[],
                  nowText="", live=False, choreProjects=[], brief=report["brief"][:2])
    report["projects"] = [p for p in report["projects"] if p["name"] in names]
    for p in report["projects"]:
        p["waiting"], p["hot"], p["finished"] = [], False, []
        p["counts"] = dict(p["counts"], waiting=0, finished=0)
    report["counts"] = dict(report["counts"], projects=len(report["projects"]), waiting=0, outside=0)
    return report


def sample(unread: bool = True) -> dict:
    """Dane widoku dla --data: {"days", "reports", "board"}."""
    days = [
        {"day": "2026-10-27", "generatedAt": T0, "projects": 7, "waiting": 3, "data": True},
        {"day": "2026-10-26", "generatedAt": T0 - DAY, "projects": 2, "waiting": 0, "data": True},
        {"day": "2026-10-25", "generatedAt": T0 - 2 * DAY + 3 * H + 12 * 60, "projects": 1, "waiting": 0, "data": True},
        {"day": "2026-10-24", "generatedAt": T0 - 3 * DAY + 2 * H + 40 * 60, "projects": 2, "waiting": None,
         "data": False},
        {"day": "2026-10-23", "generatedAt": T0 - 4 * DAY, "projects": 7, "waiting": 1, "data": True},
        {"day": "2026-10-22", "generatedAt": T0 - 5 * DAY, "projects": 5, "waiting": 0, "data": True},
    ]
    reports = {
        "2026-10-27": today_report(),
        "2026-10-26": older_report("2026-10-26", "Poniedziałek, 26 października 2026", ["plasma-tide", "cookapp"]),
        "2026-10-25": older_report("2026-10-25", "Niedziela, 25 października 2026", ["cookapp"]),
        "2026-10-24": {"legacy": True, "title": "Sobota, 24 października 2026", "generatedAt": days[3]["generatedAt"],
                       "counts": {"projects": 2}, "outside": [],
                       "projects": [{"name": "log-explorer", "slug": "log-explorer", "page": "log-explorer.html"},
                                    {"name": "cookapp", "slug": "cookapp", "page": "cookapp.html"}]},
    }
    board = {
        "tags": [{"name": "praca", "color": "sky"}, {"name": "własne", "color": "lavender"}],
        "projects": [
            {"slug": "log-explorer", "name": "log-explorer", "repo": "/home/ja/DevWorkspace/log-explorer",
             "tags": ["praca"]},
            {"slug": "mobile-app", "name": "Mobile", "repo": "/home/ja/DevWorkspace/mobile-app", "tags": ["praca"]},
            {"slug": "plasma-tide", "name": "plasma-tide", "repo": "", "tags": ["własne"]},
            {"slug": "team-v2", "name": "team-v2", "repo": "/home/ja/DevWorkspace/team-v2", "tags": ["własne"]},
            {"slug": "cookapp", "name": "cookapp", "repo": "/home/ja/DevWorkspace/cookapp", "tags": ["własne"]},
        ],
    }
    return {"days": {"root": "/home/ja/.local/share/tide/reports", "today": TODAY, "keepDays": 30,
                     "latest": TODAY, "unread": unread, "days": days},
            "reports": reports, "board": board}
