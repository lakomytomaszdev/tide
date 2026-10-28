"""Wtyczka KRunnera na D-Bus: interfejs org.kde.krunner1 (API „DBus” KRunnera 6).

Nazwa usługi:  org.tide.Daemon.KRunner
Obiekt:        /KRunner
Interfejs:     org.kde.krunner1
- Match(s query) -> a(sssida{sv})   dopasowania: (id, tekst, ikona, ważność kategorii, trafność,
                                     właściwości: subtext, category, actions),
- Actions() -> a(sss)               akcje dodatkowe: (id, tekst, ikona),
- Run(s matchId, s actionId)        uruchomienie dopasowania (actionId pusty — akcja domyślna).

Dlaczego dbus-python, a nie QtDBus jak reszta usługi: PyQt6 nie umie zbudować tablicy struktur
(a(sssida{sv})) — QDBusArgument.beginArray wymaga typu zarejestrowanego w C++
(qDBusRegisterMetaType), a próba obejścia kończy się przerwaniem libdbus. dbus-python
(python3-dbus) ma pełne typowanie i działa na pętli GLib; Qt na Linuksie używa tej samej
domyślnej pętli GLib (QEventDispatcherGlib), więc obie biblioteki obsługuje jeden wątek
główny. Gdy Qt działa bez GLib (QT_NO_GLIB), pętlę GLib popychamy timerem.

Osobne połączenie z magistralą oznacza osobną nazwę: org.tide.Daemon należy do połączenia
QtDBus, a KRunner dostaje org.tide.Daemon.KRunner (plik .desktop wtyczki wskazuje tę nazwę,
a plik aktywacji D-Bus uruchamia tę samą usługę systemd).

Wyszukiwanie po znaczeniu (PT-19): Match odpowiada od razu wynikami słownymi, chyba że zapytanie nadaje
się na wyszukiwanie po znaczeniu (KRunnerCore.meaning_query) — wtedy wektor zapytania liczy wątek
(semantic.py: najwyżej ok. 0,3 s ciszy i 1,5 s na model), a odpowiedź przychodzi z dopisanymi sesjami
podobnymi znaczeniowo albo z samymi słowami, gdy modelu nie ma albo nie zdążył.
"""
import threading

SERVICE = "org.tide.Daemon.KRunner"
PATH = "/KRunner"
IFACE = "org.kde.krunner1"
GLIB_PUMP_MS = 50


def available() -> bool:
    try:
        import dbus.mainloop.glib  # noqa: F401
        from gi.repository import GLib  # noqa: F401
    except ImportError:
        return False
    return True


def register(core, lazy_index_factory, app=None, address: str | None = None):
    """Rejestruje obiekt /KRunner i nazwę usługi. Zwraca obiekt, który trzeba trzymać przy życiu.

    lazy_index_factory(post) → LazyIndex korzystający z post (przeniesienie do wątku głównego).
    """
    import dbus
    import dbus.bus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib

    def post(fn):
        def once():
            fn()
            return False  # jednorazowo
        GLib.idle_add(once)

    loop = dbus.mainloop.glib.DBusGMainLoop()
    bus = dbus.bus.BusConnection(address or dbus.bus.BUS_SESSION, mainloop=loop)
    lazy = lazy_index_factory(post)

    class KRunnerObject(dbus.service.Object):
        @dbus.service.method(IFACE, in_signature="s", out_signature="a(sssida{sv})",
                             async_callbacks=("reply", "error"))
        def Match(self, query, reply, error):
            def send(matches):
                try:
                    reply(dbus.Array([_struct(m) for m in matches], signature="(sssida{sv})"))
                except Exception as exc:  # błąd w jednym zapytaniu nie może położyć usługi
                    print(f"tide-daemon: krunner: Match: {exc!r}", flush=True)
                    error(exc)

            def respond():
                text = str(query)
                try:
                    matches = core.match(text)
                    meaning = core.meaning_query(text)
                except Exception as exc:
                    print(f"tide-daemon: krunner: Match: {exc!r}", flush=True)
                    error(exc)
                    return
                if not meaning:
                    send(matches)
                    return

                # Po znaczeniu (PT-19): wektor zapytania w wątku (najwyżej ok. 2 s, potem same słowa),
                # łączenie wyników znowu w wątku głównym — tam, gdzie reszta wtyczki.
                def work():
                    try:
                        vector = core.semantic.query_vector(meaning)
                    except Exception as exc:
                        print(f"tide-daemon: krunner: po znaczeniu: {exc!r}", flush=True)
                        vector = None
                    post(lambda: send(core.with_meaning(text, matches, vector)))

                threading.Thread(target=work, name="tide-krunner-meaning", daemon=True).start()
            lazy.request(respond)

        @dbus.service.method(IFACE, in_signature="", out_signature="a(sss)")
        def Actions(self):
            return dbus.Array([dbus.Struct(a, signature="sss") for a in core.actions()], signature="(sss)")

        @dbus.service.method(IFACE, in_signature="ss", out_signature="")
        def Run(self, match_id, action_id):
            try:
                if not core.run(str(match_id), str(action_id)):
                    print(f"tide-daemon: krunner: nie udało się uruchomić {str(match_id).split(':')[0]}", flush=True)
            except Exception as exc:
                print(f"tide-daemon: krunner: Run: {exc!r}", flush=True)

    holder = KRunnerObject(bus, PATH)
    holder.bus = bus
    holder.lazy = lazy
    holder.name = dbus.service.BusName(SERVICE, bus, do_not_queue=True)
    holder.pump = _pump_glib_if_needed(app, GLib)
    return holder


def _struct(match: dict):
    import dbus
    props = match["properties"]
    properties = dbus.Dictionary({
        "subtext": dbus.String(props.get("subtext", "")),
        "category": dbus.String(props.get("category", "")),
        "actions": dbus.Array(props.get("actions", []), signature="s"),
    }, signature="sv")
    return dbus.Struct((match["id"], match["text"], match["icon"], dbus.Int32(match["categoryRelevance"]),
                        dbus.Double(match["relevance"]), properties), signature="sssida{sv}")


def _pump_glib_if_needed(app, GLib):
    """Gdy pętla Qt nie jest pętlą GLib, obsługujemy zdarzenia GLib co GLIB_PUMP_MS."""
    if app is None:
        return None
    dispatcher = app.eventDispatcher()
    if dispatcher is not None and dispatcher.metaObject().className() == "QEventDispatcherGlib":
        return None
    from PyQt6.QtCore import QTimer
    context = GLib.MainContext.default()

    def pump():
        while context.pending():
            context.iteration(False)

    timer = QTimer(interval=GLIB_PUMP_MS)
    timer.timeout.connect(pump)
    timer.start()
    return timer
