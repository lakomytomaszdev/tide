"""Zgodność kart Tablicy po integracji (PT-109, karta K-33): te same pola kart liczone kodem sprzed i po.

Użycie:
    /usr/bin/python3 tools/board_compat_check.py KOPIA_TABLICY [--before-ref REF | --before-tree KATALOG]
                                                 [--after-tree KATALOG] [--json]

KOPIA_TABLICY to kopia katalogu Tablicy (nigdy prawdziwa Tablica, nigdy w gicie). Narzędzie jej nie zapisuje:
odczyt idzie z `GIT_OPTIONAL_LOCKS=0`, bez bajtkodu i z wyłączonym zakładaniem katalogów. Kod „przed” to drzewo
z `--before-tree` albo wyciąg `git archive` z `--before-ref` (domyślnie wspólny przodek `master` i bieżącej gałęzi,
czyli `master` sprzed integracji K-28 i K-29); kod „po” to `--after-tree` (domyślnie ten katalog Tide).

Dla każdej karty oba kody liczą: kolumnę, znacznik (notatka z kolorem), stan karty z panelu (`derived`: postęp,
następny krok, pytanie, stan agenta, sprawdzenie, przepływ) oraz podgląd zlecenia — listę wejść, wejście domyślne,
projekt i repozytorium, projekty dodatkowe, `check` i komunikat blokady. Wyjście: liczba kart w klasach, liczba
podglądów zlecenia kart z Backlogu bez błędu i tabela różnic (karta, pole, przed, po) albo „0 różnic”.
Kod wyjścia: 0 — wszystkie karty wczytane (różnice są wynikiem, nie błędem), 1 — błąd wczytania tablicy albo
podglądu zlecenia karty z Backlogu, 2 — użycie.
Stan z żywych sesji agentów nie jest liczony (tablica z kopii nie ma sesji): karty dawnego przepływu liczą się
z planu w repozytorium i z gałęzi, karty przepływu K-28 — z dziennika.
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile

CLASSES = ("archiwum", "przepływ K-28", "spec sprzed PT-77", "projekty dodatkowe", "dawny przepływ", "inne")
PANEL_KEYS = ("done", "total", "nextStep", "question", "agentState", "check", "link", "tool")
PANEL_FLOW_KEYS = ("entry", "action", "stepKind", "result", "reason")
PAYLOAD = ("daemon/tide_daemon", "apps/board/qml/board.js")
VALUE_LIMIT = 80
LEGACY_SPEC = re.compile(r"^mode:\s*spec\s*$", re.M | re.I)

COMPAT_JS = r"""
function __compat(S) {
  function safe(fn) { try { return fn(); } catch (e) { return "błąd: " + e; } }
  var entries = safe(function () { return LAUNCH_ENTRIES.map(function (e) { return e.id; }); });
  var out = {};
  S.cards.forEach(function (card) {
    var work = safe(function () { return launchProject(S, card); });
    var shown = safe(function () { return projectBySlug(S, card.project); });
    var o = { entries: entries };
    o.column = safe(function () { return displayColumn(card); });
    o.marker = safe(function () { var t = cardNote(card); return t + " [" + noteTint(card, t) + "]"; });
    o.entry = safe(function () { return launchDefaults(typeof work === "string" ? null : work).entry; });
    o.project = typeof work === "string" ? work : (work ? work.slug : "");
    o.repo = typeof shown === "string" ? shown : (shown ? shown.repo || "" : "");
    o.projects = card.projects || [];
    o.check = card.check || "auto";
    o.blocker = safe(function () { return launchBlocker(S, card); });
    out[card.id] = o;
  });
  return JSON.stringify(out);
}
"""


# --- pracownik: liczy pola jednym kodem (osobny proces, własne drzewo w sys.path) ---------------------------------

def worker(tree: str, board: str, out_path: str) -> int:
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.join(tree, "daemon"))
    os.makedirs = lambda *args, **kwargs: None   # render_launch zakłada katalog dokumentów — kopii nie ruszamy
    from tide_daemon.board.derive import CardDeriver
    from tide_daemon.board.store import BoardStore
    from tide_daemon.cardflow.journal import Journal
    from tide_daemon.cardflow.state import derived as flow_derived, fold

    workdir = os.getcwd()
    store = BoardStore(board)
    journal = Journal(board, mirror=os.path.join(workdir, "no-mirror"))  # bez kopii usługi: dziennik z tablicy
    deriver = CardDeriver(clock=lambda: 0.0)

    def derive(card):
        if journal.has(card["id"]):
            return flow_derived(fold(journal.read(card["id"])), card)
        return deriver(card)

    try:
        snapshot = store.snapshot(repos=[], derive=derive)
    finally:
        deriver.stop()
    problems = [e.get("message", "") for e in snapshot.get("errors") or []]
    cards = {}
    for card in snapshot.get("cards") or []:
        shown = card.get("derived") or {}
        panel = {key: shown.get(key) for key in PANEL_KEYS}
        panel["flow"] = {key: (shown.get("flow") or {}).get(key) for key in PANEL_FLOW_KEYS} if shown.get("flow") else None
        render = ""
        try:
            store.render_launch(card["id"])
        except Exception as exc:  # błąd podglądu zlecenia to wynik, nie awaria narzędzia
            render = f"{type(exc).__name__}: {exc}"[:200]
        cards[card["id"]] = {"class": classify(card, journal), "stored": card.get("column"), "render": render,
                             "panel": panel, "projects": card.get("projects") or []}
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841 — musi żyć do końca liczenia
    engine = QJSEngine()
    with open(os.path.join(tree, "apps", "board", "qml", "board.js"), encoding="utf-8") as fh:
        loaded = engine.evaluate(fh.read().replace(".pragma library", ""))
    if loaded.isError():
        raise RuntimeError(f"board.js: {loaded.toString()}")
    engine.globalObject().setProperty("S", engine.toScriptValue(json.dumps(snapshot)))
    engine.evaluate("S = JSON.parse(S)")
    loaded = engine.evaluate(COMPAT_JS)
    if loaded.isError():
        raise RuntimeError(f"podgląd zlecenia: {loaded.toString()}")
    result = engine.evaluate("__compat(S)")
    if result.isError():
        raise RuntimeError(f"podgląd zlecenia: {result.toString()}")
    for card_id, fields in json.loads(result.toString()).items():
        cards[card_id].update(fields)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"cards": cards, "problems": problems}, fh, ensure_ascii=False)
    return 0


def classify(card: dict, journal) -> str:
    if card.get("column") == "archived":
        return "archiwum"
    if journal.has(card["id"]):
        return "przepływ K-28"
    try:
        with open(card.get("path") or "", encoding="utf-8", errors="replace") as fh:
            if LEGACY_SPEC.search(fh.read(8192)):
                return "spec sprzed PT-77"
    except OSError:
        pass
    if card.get("projects"):
        return "projekty dodatkowe"
    if card.get("session") or card.get("planPath"):
        return "dawny przepływ"
    return "inne"


# --- narzędzie ----------------------------------------------------------------------------------------------

def default_tree() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_ref(tree: str, ref: str, target: str) -> None:
    """Wyciąg kodu z gita (`git archive`) do katalogu `target`; bez zapisu w repozytorium."""
    done = subprocess.run(["git", "-C", tree, "archive", "--format=tar", ref, *PAYLOAD], capture_output=True)
    if done.returncode:
        raise SystemExit(f"git archive {ref}: {done.stderr.decode('utf-8', 'replace').strip()}")
    root = os.path.realpath(target)
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        for member in archive.getmembers():
            path = os.path.realpath(os.path.join(root, member.name))
            if not (path == root or path.startswith(root + os.sep)) or not (member.isreg() or member.isdir()):
                continue
            if member.isdir():
                os.makedirs(path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(archive.extractfile(member).read())


def merge_base(tree: str) -> str:
    done = subprocess.run(["git", "-C", tree, "merge-base", "master", "HEAD"], capture_output=True, text=True)
    if done.returncode or not done.stdout.strip():
        raise SystemExit("nie ma wspólnego przodka master i HEAD — podaj --before-ref albo --before-tree")
    return done.stdout.strip()


def compute(tree: str, board: str, scratch: str, name: str) -> dict:
    out = os.path.join(scratch, name + ".json")
    work = os.path.join(scratch, name + "-work")
    os.makedirs(work)
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "PYTHONDONTWRITEBYTECODE": "1", "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--worker", tree, board, out],
                          cwd=work, env=env, capture_output=True, text=True)
    if done.returncode or not os.path.isfile(out):
        raise SystemExit(f"liczenie kodem „{name}” nie powiodło się (kod {done.returncode}):\n"
                         f"{(done.stderr.strip() or done.stdout.strip())[-1500:]}")
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


def shorten(value) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= VALUE_LIMIT else text[:VALUE_LIMIT - 1] + "…"


def card_number(card_id: str) -> int:
    return int(card_id.rsplit("-", 1)[-1]) if card_id.rsplit("-", 1)[-1].isdigit() else 0


def compare(before: dict, after: dict) -> list[tuple]:
    """Różnice (karta, pole, przed, po); karta obecna tylko po jednej stronie to pole „karta”."""
    rows = []
    fields = ("column", "marker", "panel", "entries", "entry", "project", "repo", "projects", "check", "blocker")
    for card_id in sorted(set(before["cards"]) | set(after["cards"]), key=card_number):
        old, new = before["cards"].get(card_id), after["cards"].get(card_id)
        if old is None or new is None:
            rows.append((card_id, "karta", "jest" if old else "brak", "jest" if new else "brak"))
            continue
        for field in fields:
            if old.get(field) != new.get(field):
                rows.append((card_id, field, old.get(field), new.get(field)))
    return rows


def report(before: dict, after: dict) -> dict:
    """Wynik porównania do wypisania i do testów: klasy, podglądy z Backlogu, błędy wczytania, różnice."""
    counts = {name: 0 for name in CLASSES}
    for card in after["cards"].values():
        counts[card["class"]] += 1
    backlog = {cid: c for cid, c in after["cards"].items() if c.get("stored") == "backlog"}
    broken = {cid: c["render"] for cid, c in after["cards"].items() if c.get("render")}
    loading = [*before["problems"], *after["problems"]]
    return {"cards": len(after["cards"]), "classes": counts, "backlog": len(backlog),
            "backlogBroken": sorted(cid for cid in backlog if backlog[cid]["render"]),
            "renderErrors": broken, "loadErrors": loading, "differences": compare(before, after)}


def render_text(result: dict) -> str:
    lines = [f"Karty: {result['cards']}; klasy: " + ", ".join(f"{k} {v}" for k, v in result["classes"].items()),
             f"Podgląd zlecenia kart z Backlogu: {result['backlog'] - len(result['backlogBroken'])} z "
             f"{result['backlog']} bez błędu"]
    if result["renderErrors"]:
        lines.append("Podgląd zlecenia z błędem: " + ", ".join(sorted(result["renderErrors"], key=card_number)))
    if result["loadErrors"]:
        lines.append(f"Błędy wczytania tablicy: {len(result['loadErrors'])}")
    diffs = result["differences"]
    if not diffs:
        lines.append("0 różnic")
        return "\n".join(lines)
    lines.append(f"Różnice: {len(diffs)}")
    lines.append("| karta | pole | przed | po |")
    lines.append("| --- | --- | --- | --- |")
    for card_id, field, old, new in diffs:
        lines.append(f"| {card_id} | {field} | {shorten(old).replace('|', '/')} | {shorten(new).replace('|', '/')} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--worker"]:
        return worker(argv[1], argv[2], argv[3])
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("board", help="kopia katalogu Tablicy")
    parser.add_argument("--before-ref")
    parser.add_argument("--before-tree")
    parser.add_argument("--after-tree", default=default_tree())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    board = os.path.abspath(args.board)
    if not os.path.isfile(os.path.join(board, "board.md")):
        print(f"{board}: to nie katalog Tablicy (brak board.md)", file=sys.stderr)
        return 2
    after_tree = os.path.abspath(args.after_tree)
    with tempfile.TemporaryDirectory(prefix="tide-compat-") as scratch:
        before_tree = args.before_tree
        if not before_tree:
            before_tree = os.path.join(scratch, "before-tree")
            os.makedirs(before_tree)
            extract_ref(after_tree, args.before_ref or merge_base(after_tree), before_tree)
        result = report(compute(os.path.abspath(before_tree), board, scratch, "before"),
                        compute(after_tree, board, scratch, "after"))
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render_text(result))
    return 1 if result["loadErrors"] or result["backlogBroken"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
