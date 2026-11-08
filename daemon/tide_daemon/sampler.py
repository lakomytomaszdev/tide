"""Próbnik stosów usługi na żądanie (08.11: usługa zużywa ~1 rdzeń na postoju, a py-spy nie może się podpiąć —
`kernel.yama.ptrace_scope=1` bez sudo).

Lekki wątek sprawdza co kilka sekund plik żądania `$XDG_RUNTIME_DIR/tide-daemon-profile`. Gdy plik się pojawi,
przez zadany czas (treść pliku: liczba sekund, domyślnie 60) co 10 ms zbiera `sys._current_frames()` ze wszystkich
wątków i zapisuje raport `$XDG_STATE_HOME/tide/profile-<czas>.txt`: najczęstsze stosy oraz funkcje według
czasu własnego i łącznego (w próbkach). Wątki czekające (sleep, select, wait) liczą się osobno, żeby nie zasłaniały
pracy procesora. Plik żądania jest usuwany na starcie pomiaru — kolejne żądanie to nowy plik.
"""
import os
import sys
import threading
import time
from collections import Counter

REQUEST = "tide-daemon-profile"
INTERVAL = 0.01
DEFAULT_SECONDS = 60
MAX_SECONDS = 600
# Ramki, w których wątek nie zużywa procesora — liczone osobno jako „czekanie”.
IDLE = ("wait", "sleep", "select", "poll", "acquire", "accept", "recv", "read", "readline", "_wait_for_tstate_lock")


def request_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(runtime, REQUEST)


def report_dir() -> str:
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "tide")


def _frames(frame):
    stack = []
    while frame is not None:
        code = frame.f_code
        stack.append(f"{os.path.basename(code.co_filename)}:{code.co_name}:{frame.f_lineno}")
        frame = frame.f_back
    return list(reversed(stack))


def sample(seconds: float, interval: float = INTERVAL, skip: int | None = None) -> dict:
    """Próbki stosów wszystkich wątków (poza wątkiem próbnika `skip`)."""
    names = {}
    stacks, own, total, idle = Counter(), Counter(), Counter(), Counter()
    count = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        names = {t.ident: t.name for t in threading.enumerate()}
        for ident, frame in sys._current_frames().items():
            if ident == skip:
                continue
            frames = _frames(frame)
            if not frames:
                continue
            leaf = frames[-1].split(":")[1]
            thread = names.get(ident, str(ident))
            if leaf in IDLE:
                idle[(thread, frames[-1])] += 1
                continue
            stacks[(thread, " > ".join(f.rsplit(":", 1)[0] for f in frames[-12:]))] += 1
            own[frames[-1].rsplit(":", 1)[0]] += 1
            for name in {f.rsplit(":", 1)[0] for f in frames}:
                total[name] += 1
        count += 1
        time.sleep(interval)
    return {"samples": count, "stacks": stacks, "own": own, "total": total, "idle": idle}


def write_report(result: dict, seconds: float, folder: str | None = None) -> str:
    folder = folder or report_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, time.strftime("profile-%Y%m%d-%H%M%S.txt"))
    busy = sum(result["own"].values())
    lines = [f"Próbki: {result['samples']} w {seconds:.0f} s (co {INTERVAL * 1000:.0f} ms); "
             f"próbek pracy (poza czekaniem): {busy}", "", "## Funkcje — czas własny (próbki)"]
    lines += [f"{n:7d}  {name}" for name, n in result["own"].most_common(40)]
    lines += ["", "## Funkcje — czas łączny (próbki)"]
    lines += [f"{n:7d}  {name}" for name, n in result["total"].most_common(60)]
    lines += ["", "## Najczęstsze stosy (wątek: ostatnie 12 ramek)"]
    for (thread, stack), n in result["stacks"].most_common(25):
        lines += [f"{n:7d}  [{thread}] {stack}"]
    lines += ["", "## Czekanie (wątek, ramka)"]
    lines += [f"{n:7d}  [{thread}] {frame}" for (thread, frame), n in result["idle"].most_common(15)]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return path


def _requested_seconds(path: str) -> float | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        os.unlink(path)
    except FileNotFoundError:
        return None
    except OSError:
        return DEFAULT_SECONDS
    try:
        return max(1.0, min(float(text), MAX_SECONDS)) if text else DEFAULT_SECONDS
    except ValueError:
        return DEFAULT_SECONDS


def watch(check_every: float = 3.0, stop: threading.Event | None = None, folder: str | None = None) -> None:
    stop = stop or threading.Event()
    path = request_path()
    while not stop.wait(check_every):
        seconds = _requested_seconds(path)
        if seconds is None:
            continue
        try:
            write_report(sample(seconds, skip=threading.get_ident()), seconds, folder)
        except Exception as exc:          # próbnik nie może zatrzymać usługi
            print(f"tide-daemon: próbnik stosów: {exc}", file=sys.stderr)


def start(**kwargs) -> threading.Thread:
    thread = threading.Thread(target=watch, kwargs=kwargs, name="tide-sampler", daemon=True)
    thread.start()
    return thread
