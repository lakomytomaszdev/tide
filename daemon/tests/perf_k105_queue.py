#!/usr/bin/python3
"""Skrypt kolejki koordynatora do pomiaru 10 okien CPU w reproduktorze (K-105, PT-458). Każde podpolecenie to
**jedno polecenie `outside`**.

    /usr/bin/python3 daemon/tests/perf_k105_queue.py build-data [--cards 46] [--journal-mb 60]
    /usr/bin/python3 daemon/tests/perf_k105_queue.py measure <before|after> <false|true> [--base-sha SHA]

`build-data` — buduje dane (46 kart, ~60 MB dzienników, generator PT-410) raz do `build/k105-data/` w worktree
(ignorowany przez git) z manifestem `manifest.json` (parametry, SHA generatora, rozmiar na dysku, czas budowy).
Ponowne wywołanie przy zgodnym manifeście nic nie robi.

`measure <strona> <box.enabled>` — **jedna konfiguracja i jedna seria 10 okien na polecenie**: kopiuje dane do świeżego
katalogu tymczasowego; strona `before` uruchamia kod usługi z przypiętego SHA bazy karty (`--base-sha` albo
`K105_BASE_SHA`) z własnego `git worktree add --detach` w katalogu tymczasowym i usuwa go po pomiarze
(`git worktree remove` tylko tego katalogu, bez `prune`); `after` — kod bieżącego worktree. Wynik w
`build/k105-measure/<strona>-box-<false|true>.json` z listą prób, zapis przyrostowy po każdym oknie.

Limit i wznawianie: próba ma wewnętrzny limit czasu (`--limit-min`, domyślnie 13) i po jego przekroczeniu jest
nieważna. Ponowne uruchomienie **tego samego polecenia** pomija konfigurację z ważnym wynikiem albo z wyczerpanymi
3 próbami i liczy próby dalej; próba zapisana jako „w toku” (polecenie ubite, np. kod 143) liczy się jako nieważna.
Jeden przebieg naraz (blokada pliku), najwyżej 3 próby na konfigurację, rozstrzyga pierwsza ważna.

Kody wyjścia: 0 — wynik ważny (także gdy któreś okno przekracza próg: to ocena, nie błąd) albo konfiguracja już
rozstrzygnięta; 1 — wyczerpane 3 próby bez ważnej; 2 — ta próba nieważna (uruchom to samo polecenie ponownie);
3 — inny przebieg trwa (blokada); 4 — brak danych (`build-data`) albo zły SHA bazy.

Wyłącznie dane syntetyczne w `build/` worktree i katalogu tymczasowym; kodu usługi nie zmienia.
"""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    from . import perf_cpu_windows as cpuwin
    from . import perf_idle_cpu as perf
except ImportError:
    import perf_cpu_windows as cpuwin
    import perf_idle_cpu as perf

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BUILD = os.path.join(REPO, "build")
DATA_DIR = os.path.join(BUILD, "k105-data")
MEASURE_DIR = os.path.join(BUILD, "k105-measure")
MAX_ATTEMPTS = 3
DEFAULT_CARDS = 46
DEFAULT_JOURNAL_MB = 60.0
DEFAULT_LIMIT_MIN = 13.0
GENERATOR_FILES = ("perf_idle_scale.py", "perf_board_refresh.py", "perf_idle_cpu.py")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class Busy(Exception):
    """Inny przebieg kolejki trwa."""


class Lock:
    """Jeden przebieg naraz: `flock` na pliku w katalogu wyników; zwalniany przy zamknięciu (także po ubiciu)."""

    def __init__(self, path):
        self.path = path
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise Busy(self.path) from None
        return self

    def __exit__(self, *exc):
        self.fh.close()


# --- build-data -------------------------------------------------------------------------------------

def generator_hash(tests_dir=HERE):
    digest = hashlib.sha256()
    for name in GENERATOR_FILES:
        with open(os.path.join(tests_dir, name), "rb") as fh:
            digest.update(name.encode() + b"\0" + fh.read() + b"\0")
    return digest.hexdigest()


def manifest_matches(manifest, params, generator):
    return bool(manifest) and manifest.get("params") == params and manifest.get("generatorSha256") == generator


def dir_bytes(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def build_data(cards=DEFAULT_CARDS, journal_mb=DEFAULT_JOURNAL_MB, data_dir=DATA_DIR, src=REPO, tests_dir=HERE,
               prepare=None, clock=time.monotonic, out=print, box_file=True, shape=None):
    """Buduje dane raz; zgodny manifest → brak pracy. Zwraca (zbudowano, manifest). `box_file` False — tylko test
    dymny w piaskownicy bez przestrzeni nazw (jak w `perf_board_refresh.Service`); `measure` i tak nadpisuje `box.enabled`.

    `shape` (K-125 PT-486): plik kształtu produkcji (`perf_prod_shape.py`) — liczba kart, role i rozmiary dzienników
    z produkcji (dane nadal syntetyczne); `cards` i `journal_mb` są wtedy ignorowane, a manifest trzyma skrót pliku."""
    prepare = prepare or perf.prepare_board
    params = {"cards": cards, "journalMb": journal_mb, "seed": 105}
    if shape:
        with open(shape, "rb") as fh:
            params = {"shape": hashlib.sha256(fh.read()).hexdigest(), "seed": 105}
        cards = len(read_json(shape)["cards"])
    generator = generator_hash(tests_dir)
    manifest_path = os.path.join(data_dir, "manifest.json")
    existing = read_json(manifest_path)
    if manifest_matches(existing, params, generator) and os.path.isdir(os.path.join(data_dir, "base")):
        out(f"dane już zbudowane (zgodny manifest {manifest_path}): nic do roboty")
        return False, existing
    staging = data_dir + ".tmp"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(os.path.join(staging, "base"))
    base = os.path.realpath(os.path.join(staging, "base"))
    with open(os.path.join(base, "gitconfig"), "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Perf\n\temail = perf@example.invalid\n[init]\n\tdefaultBranch = master\n")
    started = clock()
    stats = prepare(base, src, box_file, cards, journal_mb, **({"shape": os.path.realpath(shape)} if shape else {}))
    build_s = round(clock() - started, 1)
    manifest = {"params": params, "generatorSha256": generator, "generatorFiles": list(GENERATOR_FILES),
                "repoHead": perf.git_sha(src), "basePath": base, "buildSeconds": build_s,
                "diskBytes": dir_bytes(os.path.join(staging, "base")), "builtAt": now_iso(), "scale": stats}
    write_json(os.path.join(staging, "manifest.json"), manifest)
    shutil.rmtree(data_dir, ignore_errors=True)
    os.rename(staging, data_dir)
    manifest["basePath"] = os.path.join(os.path.realpath(data_dir), "base")
    # Ścieżka bazowa po przeniesieniu katalogu: `copy_data` podmienia stary prefiks, więc relokujemy dane na miejscu.
    perf.relocate(base, manifest["basePath"])
    write_json(os.path.join(data_dir, "manifest.json"), manifest)
    out(f"dane zbudowane w {build_s} s: {manifest['diskBytes'] / 1048576:.1f} MB na dysku ({data_dir})")
    return True, manifest


# --- plik wyników konfiguracji ----------------------------------------------------------------------

def result_path(side, box, directory=MEASURE_DIR):
    return os.path.join(directory, f"{side}-box-{'true' if box else 'false'}.json")


def new_document(side, box, base_sha):
    return {"side": side, "boxEnabled": box, "baseSha": base_sha, "attempts": [], "decisive": None}


def settle(document):
    """Stan konfiguracji po wczytaniu pliku: niedokończone próby (`running`) stają się nieważne (ubite polecenie).

    Zwraca `valid` (jest ważna próba), `exhausted` (3 próby bez ważnej) albo `run` (kolejna próba)."""
    for attempt in document["attempts"]:
        if attempt["status"] == "running":
            attempt["status"] = "invalid"
            attempt["reason"] = "próba przerwana (polecenie ubite albo limit czasu poza skryptem)"
            attempt["finishedAt"] = now_iso()
    decisive = next((a["n"] for a in document["attempts"] if a["status"] == "valid"), None)
    document["decisive"] = decisive
    if decisive is not None:
        return "valid"
    return "exhausted" if len(document["attempts"]) >= MAX_ATTEMPTS else "run"


def classify(result):
    """(status, powód) próby z wyniku: ważna, gdy ocena 10 okien nie jest `invalid` (pass i fail są ważne)."""
    evaluation = cpuwin.evaluate(result)
    if evaluation["valid"]:
        return "valid", ""
    return "invalid", "; ".join(evaluation["invalidReasons"])


def run_attempt(document, path, runner, limit_min):
    """Jedna próba: wpis `running` → okna zapisywane przyrostowo → ocena. `runner(on_window, deadline)` zwraca wynik."""
    attempt = {"n": len(document["attempts"]) + 1, "status": "running", "startedAt": now_iso(),
               "limitMin": limit_min, "windows": []}
    document["attempts"].append(attempt)
    write_json(path, document)

    def on_window(block):
        attempt["windows"] = list(block["windows"])
        write_json(path, document)

    deadline = time.monotonic() + limit_min * 60.0
    try:
        result = runner(on_window, deadline)
    except Exception as exc:                                  # błąd narzędzia = próba nieważna, nie utrata pliku
        attempt.update(status="invalid", reason=f"błąd próby: {type(exc).__name__}: {exc}", finishedAt=now_iso())
        write_json(path, document)
        return attempt
    attempt["result"] = result
    attempt["evaluation"] = cpuwin.evaluate(result)
    attempt["windows"] = (result.get("cpuWindows") or {}).get("windows") or attempt["windows"]
    attempt["status"], attempt["reason"] = classify(result)
    attempt["finishedAt"] = now_iso()
    if attempt["status"] == "valid":
        document["decisive"] = attempt["n"]
    write_json(path, document)
    return attempt


# --- strona „before”: własny worktree z przypiętego SHA ---------------------------------------------

def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


class BeforeCode:
    """Kod usługi z przypiętego SHA w `git worktree add --detach` w katalogu tymczasowym; sprzątanie usuwa tylko ten
    worktree (`git worktree remove`), nie dotyka gałęzi karty ani ticketu i nie woła `git worktree prune`."""

    def __init__(self, repo, sha):
        self.repo, self.sha = repo, sha
        self.root = self.path = None

    def __enter__(self):
        self.root = tempfile.mkdtemp(prefix="tide-k105-before-")
        self.path = os.path.join(self.root, "code")
        git(self.repo, "worktree", "add", "--detach", self.path, self.sha)
        return self.path

    def __exit__(self, *exc):
        if self.path and os.path.isdir(self.path):
            subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force", self.path],
                           capture_output=True, text=True)
        if self.root:
            shutil.rmtree(self.root, ignore_errors=True)


# --- measure ----------------------------------------------------------------------------------------

def measure_command(args, repo=REPO, data_dir=DATA_DIR, directory=MEASURE_DIR, run_measure=None, out=print):
    side, box = args.side, args.box == "true"
    run_measure = run_measure or perf.measure
    if read_json(os.path.join(data_dir, "manifest.json")) is None:
        out("brak danych: najpierw `perf_k105_queue.py build-data`")
        return 4
    base_sha = ""
    if side == "before":
        base_sha = args.base_sha or os.environ.get("K105_BASE_SHA", "")
        try:
            base_sha = git(repo, "rev-parse", "--verify", base_sha + "^{commit}") if base_sha else ""
        except subprocess.CalledProcessError:
            base_sha = ""
        if not base_sha:
            out("strona before wymaga --base-sha (albo K105_BASE_SHA) z istniejącym commitem")
            return 4
    path = result_path(side, box, directory)
    try:
        lock = Lock(os.path.join(directory, ".lock")).__enter__()
    except Busy:
        out("inny przebieg kolejki trwa (blokada pliku) — nic nie robię")
        return 3
    try:
        document = read_json(path) or new_document(side, box, base_sha)
        if side == "before" and document.get("baseSha") not in ("", base_sha):
            out(f"plik wyniku jest dla innego SHA bazy ({document.get('baseSha')}); usuń go świadomie")
            return 4
        document["baseSha"] = base_sha
        state = settle(document)
        write_json(path, document)
        if state == "valid":
            out(f"{side} box={box}: ważny wynik już zapisany (próba {document['decisive']}) — pomijam")
            return 0
        if state == "exhausted":
            out(f"{side} box={box}: wyczerpane {MAX_ATTEMPTS} próby bez ważnej — pomijam")
            return 1

        def runner(on_window, deadline):
            def go(src):
                return run_measure(src, base_sha if side == "before" else "", box, args.sessions, args.warmup,
                                   args.window, cards=args.cards, journal_mb=args.journal_mb,
                                   windows=args.windows, data_dir=data_dir, on_window=on_window, deadline=deadline)
            if side == "before":
                with BeforeCode(repo, base_sha) as code:
                    return go(code)
            return go(repo)

        attempt = run_attempt(document, path, runner, args.limit_min)
        evaluation = attempt.get("evaluation")
        out(f"{side} box={box} próba {attempt['n']}: {attempt['status']}" +
            (f" ({attempt['reason']})" if attempt.get("reason") else ""))
        if evaluation:
            out(cpuwin.evaluation_text(evaluation))
        return 0 if attempt["status"] == "valid" else 2
    finally:
        lock.__exit__()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    bd = sub.add_parser("build-data", help="zbuduj dane raz do build/k105-data/")
    bd.add_argument("--cards", type=int, default=DEFAULT_CARDS)
    bd.add_argument("--journal-mb", type=float, default=DEFAULT_JOURNAL_MB)
    bd.add_argument("--shape", default=None,
                    help="kształt produkcji z `perf_prod_shape.py` (liczba kart, role, rozmiary dzienników)")
    ms =sub.add_parser("measure", help="jedna konfiguracja, jedna seria 10 okien")
    ms.add_argument("side", choices=("before", "after"))
    ms.add_argument("box", choices=("false", "true"))
    ms.add_argument("--base-sha", default="", help="przypięty SHA bazy karty (strona before)")
    ms.add_argument("--limit-min", type=float, default=DEFAULT_LIMIT_MIN, help="limit czasu próby w minutach")
    ms.add_argument("--warmup", type=float, default=60.0, help="rozgrzewka w s (bez żądań do usługi)")
    ms.add_argument("--window", type=float, default=cpuwin.WINDOW_S)
    ms.add_argument("--windows", type=int, default=cpuwin.WINDOWS)
    ms.add_argument("--sessions", type=int, default=200)
    ms.add_argument("--cards", type=int, default=DEFAULT_CARDS)
    ms.add_argument("--journal-mb", type=float, default=DEFAULT_JOURNAL_MB)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "build-data":
        build_data(args.cards, args.journal_mb, shape=args.shape)
        return 0
    return measure_command(args)


if __name__ == "__main__":
    sys.exit(main())
