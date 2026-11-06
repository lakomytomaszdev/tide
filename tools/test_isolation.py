"""Wspólny kontrakt izolacji runnera i bezpośrednich testów usługi (bez importu produktu)."""
import atexit
import json
import os
from pathlib import Path
import shutil
import tempfile

MARKER = "TIDE_TEST_ISOLATED"
STATE_DIR = "TIDE_TEST_STATE_DIR"
ORIGINAL_ROOTS = "TIDE_TEST_ORIGINAL_STATE_ROOTS"


def inherited_state(env):
    """Samo XDG_STATE_HOME nie jest deklaracją testowej własności katalogu."""
    state = env.get(STATE_DIR, "")
    if (env.get(MARKER) == "1" and os.path.isabs(state)
            and env.get("XDG_STATE_HOME") == state and Path(state).is_dir()):
        return state
    return None


def original_roots(env):
    """Korzenie sprzed izolacji dla strażnika; żadnych zapisów ani odczytu stanu."""
    # Fixture może już świadomie zmienić XDG. Nie tracimy wtedy pierwotnych korzeni
    # przy przekazaniu kolejnego katalogu testowemu potomkowi.
    declared = env.get(STATE_DIR, "")
    if (env.get(MARKER) == "1" and os.path.isabs(declared) and Path(declared).is_dir()
            and env.get(ORIGINAL_ROOTS)):
        return json.loads(env[ORIGINAL_ROOTS])
    home = env.get("HOME") or os.path.expanduser("~")
    default = os.path.join(home, ".local", "state")
    return {"xdg_state_home": os.path.abspath(env.get("XDG_STATE_HOME") or default),
            "default_tide_state_home": os.path.abspath(os.path.join(default, "tide"))}


def state_environment(state, env=None):
    """Jawnie przekazuje testowy katalog potomkowi; właściciel nadal odpowiada za jego usunięcie."""
    env = dict(os.environ if env is None else env)
    roots = original_roots(env)
    state = os.path.abspath(state)
    env.update({"XDG_STATE_HOME": state, MARKER: "1", STATE_DIR: state,
                ORIGINAL_ROOTS: json.dumps(roots)})
    return env


def initialize():
    """Przed importem produktu: dziedziczymy jawną izolację albo zakładamy własną do końca procesu."""
    from tools.test_guard import initialize as guard

    inherited = inherited_state(os.environ)
    if inherited:
        os.environ[ORIGINAL_ROOTS] = json.dumps(original_roots(os.environ))
        guard(json.loads(os.environ[ORIGINAL_ROOTS]))
        return inherited
    state = os.path.abspath(tempfile.mkdtemp(prefix="tide-test-state-"))
    env = state_environment(state)
    os.environ.update(env)
    guard(json.loads(env[ORIGINAL_ROOTS]))
    owner = os.getpid()

    def cleanup():
        # Fork nie przenosi własności katalogu na potomka.
        if os.getpid() == owner:
            shutil.rmtree(state, ignore_errors=True)

    atexit.register(cleanup)
    return state
