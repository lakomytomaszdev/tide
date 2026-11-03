"""Zestaw prawdziwych wiadomości agentów do rozpoznawania pytań (PT-111, karta K-33).

Zastępuje skrypt pomiaru z 28.10 (docs/agents/plans/2026-10-28-question-check-probe.py): te same 12 przypadków
wchodzą do zestawu jako `PROBE_CASES`, a oprócz nich ostatnie wiadomości agentów z prawdziwych dzienników.

Użycie (każde polecenie osobno; `extract` czyta dzienniki, więc idzie poza piaskownicą):
    python3 tools/question_corpus.py extract --out KATALOG_POZA_REPO/candidates.json [--candidates NOTKA.json]
                                             [--since RRRR-MM-DD] [--until RRRR-MM-DD] [--per-class 30]
                                             [--claude ~/.claude] [--codex ~/.codex] [--ollama URL] [--model qwen3:8b]
    python3 tools/question_corpus.py review candidates.json --out review.md
    (użytkownik przegląda review.md: „przejrzane: tak”, na każdej wiadomości „czeka”, „usuń”, w razie potrzeby tekst)
    python3 tools/question_corpus.py review --apply review.md --out daemon/tests/fixtures/question-corpus.jsonl
    python3 tools/question_corpus.py bench [daemon/tests/fixtures/question-corpus.jsonl] [--rules-only] [--json]

`extract` bierze z dzienników Claude Code i Codexa ostatnią wiadomość asystenta w każdej skończonej turze (tura
kończąca się wywołaniem narzędzia nie ma wiadomości końcowej — usługa też traktuje ją jako niekończącą), przycina
ją od początku do MAX_CHARS znaków (zostaje koniec) i proponuje oznaczenie: z reguł (`agents/question.py`) oraz — gdy
działa Ollama na tym komputerze — z modelu lokalnego (prompt i końcówka jak w `agents/classify.py`). Model chmurowy
nigdy nie jest wołany: adres Ollamy musi być pętlą zwrotną. Gdy w dziennikach jest za mało wiadomości jednej klasy
(MIN_PER_CLASS) albo w sumie (MIN_TOTAL), `extract` niczego nie zapisuje i kończy się kodem 3 z liczbami i propozycją;
zestawu nie dopełnia się tekstami syntetycznymi.

`extract` i `review` nie zapisują nic w repozytorium (wyjście w repozytorium jest odrzucane); do repozytorium trafia
dopiero `review --apply`, który wymaga „przejrzane: tak”, pomija wiadomości z „usuń: tak” i odmawia, gdy w tekście
zostały ścieżki, adresy e-mail, tokeny, nazwa firma albo treść z projektów klienta (nazwy produktów, tickety,
ślady kroków w tle) — te ostatnie trzeba usunąć razem z wiadomością, nie da się ich zastąpić. Wtedy użytkownik poprawia je w pliku Markdown.

Kody wyjścia: 0 — ok, 1 — błąd danych, 2 — użycie, 3 — za mało wiadomości w dziennikach, 4 — brak Ollamy.
"""
import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "daemon"))
from tide_daemon.agents import classify, question  # noqa: E402
from tide_daemon.agents import claude as claude_log  # noqa: E402
from tide_daemon.agents import codex as codex_log  # noqa: E402

MAX_CHARS = 6000
MIN_TOTAL = 45
MIN_PER_CLASS = 15
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA = "http://localhost:11434"
MODEL_TIMEOUT_S = 120
FINAL_STOPS = ("end_turn", "stop_sequence", "max_tokens", "refusal")

# 12 przypadków ze skryptu pomiaru z 28.10 (PT-42): (tekst, czeka).
PROBE_CASES = [
    ("Na stronę ustala się wynik i nazwy graczy. Do tego dochodzi przycisk „Zamień strony”.\n\n"
     "Czy taki podział Wydarzenie → Seria → wynik pasuje? Czy bywa u Ciebie castowanie bez wydarzenia, np. luźne "
     "mecze znajomych? Wtedy Wydarzenie byłoby opcjonalne, a płytka pokazywałaby tylko graczy i wynik.", True),
    ("Wszystkie sześć zadań jest gotowe.\n\n**Mogę scalić i zainstalować?**\n\nPrzy sprzątaniu worktree czeka jeszcze "
     "plik testowy w starym worktree PT-16. Mogę go przywrócić razem z resztą, jeśli napiszesz „przywróć”.", True),
    ("Gotowe. Testy przechodzą: 1071 OK. Scalone do mastera i zainstalowane.", False),
    ("Co się zmieniło? Dodałem pole daty. Testy przechodzą, review APPROVE.", False),
    ("Czy to było trudne? Trochę. Ale ostatecznie wszystkie testy przechodzą i jest scalone.", False),
    ("Pytanie 12 — model observera. Proponuję trzy pojęcia: Wydarzenie, Seria, Nakładka wyniku. "
     "Który podział wolisz: jeden ekran czy dwa?", True),
    ("Gotowe. Jeśli chcesz, mogę też dodać testy.", False),
    ("Jasne. Do tej pory baza była tylko czytana.\n\n**Krok 1:** uruchom ten select i wklej mi wynik:\n\n"
     "```sql\nSELECT * FROM messages WHERE id = 5;\n```", True),
    ("Zrobiłem migrację. Dwie rzeczy wymagają twojej decyzji: 1) czy usuwamy starą kolumnę, 2) czy wypuszczamy dziś. "
     "Daj znać.", True),
    ("## Co dalej?\n\nNic, wszystko scalone i zainstalowane. Kafelek pokaże zmianę po restarcie Plasmy.", False),
    ("Sprawdziłem logi. Błąd był w parsowaniu dat, poprawiłem i dodałem test. Czy warto by jeszcze przejrzeć inne "
     "parsery? Może kiedyś, na razie nie ma potrzeby.", False),
    ("Agent PT-33 pracuje w tle. Dam znać, jak skończy.", False),
]
PROBE_SOURCE = "probe 2026-10-28"

# Prywatność: to, co nie może trafić do pliku w repozytorium.
PRIVATE = [
    ("ścieżka", re.compile(r"(?<![\w.:])(?:/(?:home|Users|srv|mnt|opt|var|tmp|etc|usr|root|media|run)/|~/)"
                         r"[^\s`\"'<>)\],;]*[^\s`\"'<>)\],;.]")),
    ("adres e-mail", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("token", re.compile(r"\b(?:sk-|ghp_|gho_|ghs_|github_pat_|xox[bpas]-|AKIA|glpt-|perm:)[A-Za-z0-9_\-:.]{8,}|"
                         r"\basst_[A-Za-z0-9]{10,}|\beyJ[A-Za-z0-9_\-]{10,}|\b[A-Fa-f0-9]{32,}\b")),
    ("firma", re.compile(r"acme[\s_-]?corp[\w-]*", re.I)),
    # treść z projektów klienta (nazwy produktów, tickety, repozytoria) i ślady kroków w tle — nie do zastąpienia
    # automatycznego: usuń wiadomość albo zastąp ją wiadomością z własnych projektów
    # (same `out/note.md` i `out/result.json` nic nie zdradzają — kroki w tle własnych projektów piszą o nich stale)
    # (`APP4`/`APP5` także wewnątrz identyfikatorów: `BUTTON_SCRIPT_DP4`, `LogViewer` — `_` to znak słowa, więc
    # `\b` tu nie wystarcza)
    ("projekt klienta", re.compile(r"coretools\w*|\bDPT-\d+|(?<![A-Za-z])DP[45](?![0-9])|[\w-]+\.step\b|"
                                   r"LogViewer|NecAdapters|IsActiveOnDevice|\bTST\d-[0-9X]{3,}", re.I)),
    ("telefon / PESEL / IBAN", re.compile(r"(?<!\d)(?:\+48[\s-]?)?\d{3}[\s-]\d{3}[\s-]\d{3}(?!\d)|(?<!\d)\d{11}(?!\d)|"
                                          r"\bPL\d{2}(?:[\s]?\d{4}){6}\b")),
]
# Automatyczne zastąpienia (`review --apply --redact-paths`): rodzaj z PRIVATE → tekst zastępczy.
REDACTIONS = {"ścieżka": "<ścieżka>", "firma": "<firma>"}


class UsageError(Exception):
    pass


# --- wiadomości z dzienników -------------------------------------------------------------------------------------

def clip_head(text: str, limit: int = MAX_CHARS) -> str:
    """Przycięcie od początku: zostaje ostatnie `limit` znaków (o pytaniu decyduje koniec wiadomości)."""
    text = text.strip()
    return text if len(text) <= limit else text[-limit:]


def _day(value) -> str:
    return str(value or "")[:10]


def read_jsonl(path: str):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def claude_messages(path: str) -> list:
    """Ostatnie wiadomości asystenta skończonych tur jednego dziennika Claude Code: [{text, date}]."""
    out, last = [], None   # last: (tekst, data) ostatniego tekstu asystenta w turze, jeśli po nim nie było narzędzia

    def flush():
        if last:
            out.append({"text": clip_head(last[0]), "date": last[1]})

    for record in read_jsonl(path):
        kind = record.get("type")
        if record.get("isSidechain") or kind not in ("user", "assistant"):
            continue
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        if kind == "assistant":
            if record.get("isApiErrorMessage") or str(message.get("model") or "").startswith("<"):
                continue
            content = claude_log._content(message)
            if any(c.get("type") == "tool_use" for c in content):
                last = None   # tura idzie dalej przez narzędzie — wcześniejszy tekst nie jest końcowy
                continue
            text = "\n".join(str(c.get("text") or "") for c in content if c.get("type") == "text").strip()
            if isinstance(message.get("content"), str):
                text = message["content"].strip()
            final = message.get("stop_reason") in FINAL_STOPS
            last = (text, _day(record.get("timestamp"))) if text and final else None
            continue
        if claude_log.synthetic_user(record):
            continue
        content = message.get("content")
        if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            continue
        text = claude_log._text(content)
        if text.startswith(claude_log.INJECTED_PREFIXES + claude_log.LOCAL_COMMAND_PREFIXES) or \
                text.startswith((claude_log.LOCAL_COMMAND_CAVEAT, claude_log.COMMAND_ECHO)):
            continue
        if text.startswith("[Request interrupted by user"):
            last = None   # przerwana tura nie ma wiadomości końcowej
            continue
        flush()
        last = None
    flush()
    return out


def codex_messages(path: str) -> list:
    """To samo dla dziennika Codexa: wiadomość z task_complete (last_agent_message) albo ostatni tekst po narzędziu."""
    out, last, in_turn = [], None, False
    for record in read_jsonl(path):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        kind, event = record.get("type"), payload.get("type")
        day = _day(record.get("timestamp"))
        if kind == "session_meta":
            source = payload.get("source")
            if isinstance(source, dict) and "subagent" in source:
                return []   # podagent albo recenzent zgód — nie osobna rozmowa z użytkownikiem
        elif kind == "event_msg" and event == "task_started":
            in_turn, last = True, None
        elif kind == "event_msg" and event == "turn_aborted":
            in_turn, last = False, None
        elif kind == "event_msg" and event == "task_complete":
            text = payload.get("last_agent_message")
            text = text.strip() if isinstance(text, str) and text.strip() else (last[0] if last else "")
            if in_turn and text:
                out.append({"text": clip_head(text), "date": day})
            in_turn, last = False, None
        elif kind == "response_item" and event in codex_log.CALLS:
            last = None
        elif kind == "response_item" and event == "message" and payload.get("role") == "assistant":
            text = "\n".join(str(p.get("text") or "") for p in payload.get("content") or []
                             if isinstance(p, dict) and p.get("type") in ("output_text", "text")).strip()
            last = (text, day) if text else None
    return out


def log_files(root: str, kind: str, since: str):
    """Dzienniki z katalogu (Claude Code: projects/*/*.jsonl bez podagentów; Codex: sessions/**/rollout-*.jsonl)."""
    cutoff = datetime.datetime.strptime(since, "%Y-%m-%d").timestamp() if since else 0.0
    base = os.path.join(root, "projects" if kind == "claude" else "sessions")
    found = []
    for dirpath, dirnames, names in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "subagents"]
        for name in names:
            if name.endswith(".jsonl") and (kind == "claude" or name.startswith("rollout-")):
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getmtime(path) >= cutoff:
                        found.append(path)
                except OSError:
                    pass
    return sorted(found)


def collect(claude_root: str, codex_root: str, since: str, until: str) -> list:
    """Wszystkie wiadomości końcowe z dzienników w zakresie dat: [{text, source}], bez powtórzeń, najnowsze pierwsze."""
    found, seen = [], set()
    for kind, root, reader in (("claude", claude_root, claude_messages), ("codex", codex_root, codex_messages)):
        for path in log_files(root, kind, since) if root else ():
            for item in reader(path):
                if not item["text"] or not (since <= item["date"] <= until) or item["text"] in seen:
                    continue
                seen.add(item["text"])
                found.append({"text": item["text"], "source": f"{kind} {item['date']}"})
    found.sort(key=lambda m: m["source"].split()[1], reverse=True)
    return found


# --- oznaczenia: reguły i model lokalny --------------------------------------------------------------------------

def rules_waits(text: str) -> bool:
    """Oznaczenie obecnych reguł: pierwszy stopień rozpoznawania (agents/question.py)."""
    return bool(question.ending_question(text))


def combined_waits(text: str, model_waits):
    """Jak w usłudze: pytanie widziane przez reguły liczy się od razu; wątpliwą końcówkę rozstrzyga model."""
    if question.ending_question(text):
        return True
    if question.doubtful(text) and model_waits is not None:
        return model_waits
    return False


def local_url(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise UsageError(f"adres Ollamy {url} nie jest lokalny — wiadomości nie wychodzą poza ten komputer")
    return url.rstrip("/")


class Ollama:
    """Model lokalny: ten sam prompt i schemat co usługa (agents/classify.py), wariant „końcówka” albo „całość”."""

    def __init__(self, url: str = DEFAULT_OLLAMA, model: str = DEFAULT_MODEL, timeout: float = MODEL_TIMEOUT_S):
        self.url, self.model, self.timeout = local_url(url), model, timeout

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        request = urllib.request.Request(self.url + path, json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def available(self) -> str:
        """"" gdy serwer odpowiada i ma model, inaczej powód po polsku."""
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=5) as response:
                names = [m.get("name", "") for m in json.load(response).get("models", [])]
        except (OSError, ValueError) as exc:
            return f"Ollama nie odpowiada pod {self.url} ({type(exc).__name__})"
        if not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return f"Ollama nie ma modelu {self.model} (`ollama pull {self.model}`)"
        return ""

    def judge(self, text: str, full: bool = False):
        """(czeka | None przy błędzie, czas w sekundach)."""
        body = {"model": self.model, "prompt": classify.PROMPT + (text if full else classify.tail_of(text)),
                "format": classify.SCHEMA, "stream": False, "think": False, "options": {"temperature": 0}}
        started = time.monotonic()
        try:
            waits = json.loads(self._post("/api/generate", body, self.timeout)["response"]).get("waits")
        except (OSError, ValueError, KeyError, AttributeError):
            waits = None
        return (waits if isinstance(waits, bool) else None), time.monotonic() - started


# --- extract ------------------------------------------------------------------------------------------------------

def message_id(text: str) -> str:
    return "m" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def classes(items: list) -> dict:
    waits = sum(1 for i in items if i["proposal"])
    return {"waits": waits, "idle": len(items) - waits, "total": len(items)}


def shortage(items: list) -> str:
    """Pytanie z liczbami, gdy zestaw nie spełnia progów (""), inaczej pusty napis."""
    count = classes(items)
    if count["total"] >= MIN_TOTAL and count["waits"] >= MIN_PER_CLASS and count["idle"] >= MIN_PER_CLASS:
        return ""
    return (f"Za mało wiadomości: w sumie {count['total']} (potrzeba {MIN_TOTAL}), „czeka” {count['waits']}, "
            f"„nie czeka” {count['idle']} (potrzeba po {MIN_PER_CLASS}). Zestawu nie dopełniam tekstami syntetycznymi. "
            "Propozycja: szerszy zakres dat (--since wcześniejsza data) albo mniejszy zestaw (obniżenie progów w karcie).")


def build_candidates(logs: list, extra: list, model: Ollama | None, per_class: int):
    """Kandydaci do przeglądu: przypadki ze skryptu z 28.10, kandydaci z notki #2 i wiadomości z dzienników
    (najnowsze, najwyżej `per_class` na klasę proponowaną, bez prawie identycznych). Model jest pytany tylko do
    zapełnienia limitów. Zwraca (lista, powód braku zestawu albo "")."""
    fixed = [{"text": t, "source": PROBE_SOURCE, "waits": w} for t, w in PROBE_CASES] + extra
    quota = {True: per_class, False: per_class}
    seen, shapes, kept = set(), [], []
    judged = 0
    for entry in fixed + logs:
        text = entry["text"].strip()
        known = "waits" in entry
        if not text or text in seen:
            continue
        if not known and not quota[True] and not quota[False]:
            break   # limity obu klas pełne — model nie jest pytany o resztę
        shape = shape_of(text)
        if not known and any(near_duplicate(shape, other) for other in shapes):
            continue   # prawie to samo co już wybrana wiadomość (np. różni się numerem kroku)
        rules = rules_waits(text)
        model_waits = None
        if model:
            model_waits = model.judge(text)[0]
            judged += 1
            if judged % 10 == 0:
                print(f"  model lokalny: {judged} wiadomości ocenionych…", file=sys.stderr)
        proposal = entry["waits"] if known else (model_waits if model_waits is not None else rules)
        if not known:   # z dziennika: limit na klasę (przypadki stałe wchodzą zawsze)
            if quota[proposal] <= 0:
                continue
            quota[proposal] -= 1
        seen.add(text)
        shapes.append(shape)
        kept.append({"id": message_id(text), "text": text, "source": entry["source"], "rules": rules,
                     "model": model_waits, "proposal": proposal, "known": known})
    problem = shortage(kept)
    if problem and not all(quota.values()):
        problem += f" Liczba w klasie, która dobiła do limitu --per-class {per_class}, może być w dziennikach większa."
    return kept, problem


def shape_of(text: str) -> str:
    """Tekst bez liczb, skrótów commitów i identyfikatorów kroków — do wykrywania prawie identycznych wiadomości."""
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "", text.lower())
    return re.sub(r"[\d\s]+", " ", text).strip()[-1500:]


def near_duplicate(shape: str, other: str, threshold: float = 0.85) -> bool:
    if shape == other:
        return True
    matcher = difflib.SequenceMatcher(None, shape, other, autojunk=False)
    return matcher.real_quick_ratio() >= threshold and matcher.quick_ratio() >= threshold and matcher.ratio() >= threshold


def load_extra(path: str) -> list:
    """Kandydaci z notki #2: JSON (lista) albo JSONL z polami `text`, opcjonalnie `waits` i `source`."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    out = []
    for entry in data if isinstance(data, list) else data.get("candidates", []):
        if isinstance(entry, str):
            entry = {"text": entry}
        entry = {"text": clip_head(str(entry.get("text") or "")), "source": str(entry.get("source") or "notka PT-110"),
                 **({"waits": entry["waits"]} if isinstance(entry.get("waits"), bool) else {})}
        if entry["text"]:
            out.append(entry)
    return out


def inside_repo(path: str) -> bool:
    real, root = os.path.realpath(path), os.path.realpath(ROOT)
    return real == root or real.startswith(root + os.sep)


def cmd_extract(args) -> int:
    if inside_repo(args.out):
        raise UsageError("--out wskazuje na repozytorium; kandydaci (prawdziwe wiadomości) nie mogą tam trafić przed przeglądem")
    today = datetime.date.today()
    since = args.since or (today - datetime.timedelta(days=30)).isoformat()
    until = args.until or today.isoformat()
    model = Ollama(args.ollama, args.model)
    problem = model.available()
    if problem:
        print(f"Uwaga: {problem}; proponuję oznaczenia tylko z reguł.", file=sys.stderr)
        model = None
    logs = collect(os.path.expanduser(args.claude), os.path.expanduser(args.codex), since, until)
    extra = load_extra(args.candidates) if args.candidates else []
    items, problem = build_candidates(logs, extra, model, args.per_class)
    count = classes(items)
    print(f"Dzienniki {since}…{until}: {len(logs)} wiadomości końcowych; do przeglądu {count['total']} "
          f"(czeka {count['waits']}, nie czeka {count['idle']}).", file=sys.stderr)
    if problem:
        print(problem, file=sys.stderr)
        return 3
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"model": args.model if model else "", "since": since, "until": until, "items": items}, fh,
                  ensure_ascii=False, indent=1)
    print(f"Zapisano {args.out}. Następny krok: review {args.out} --out review.md", file=sys.stderr)
    return 0


# --- review -------------------------------------------------------------------------------------------------------

YES = ("tak", "yes", "t", "y")
HEADER = re.compile(r"^## (m[0-9a-f]{8}) — (.+?)\s*$")
FIELD = re.compile(r"^- ([^:]+):\s*(.*?)\s*$")


def privacy_problems(text: str) -> list:
    """[(numer linii od 1, rodzaj)] — miejsca, które nie mogą trafić do repozytorium."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in PRIVATE:
            if pattern.search(line):
                found.append((number, label))
    return found


def label(value) -> str:
    return "?" if value is None else ("tak" if value else "nie")


def render_review(data: dict) -> str:
    items = data["items"]
    out = ["# Przegląd zestawu wiadomości do rozpoznawania pytań", "",
           "Dla każdej wiadomości ustaw „czeka” (tak: agent nie może iść dalej bez użytkownika; nie: raport, propozycja",
           "opcjonalna, pytanie retoryczne) i ewentualnie „usuń: tak”. Tekst (linie z „>”) można poprawić — np. wyciąć",
           "nazwy klientów i ścieżki; `review --apply` odmówi, dopóki w tekście są ścieżki, e-maile, tokeny i „firma”.",
           "Gdy skończysz, zmień „przejrzane” na „tak”.", "",
           "- przejrzane: nie", f"- model lokalny: {data.get('model') or 'brak (tylko reguły)'}", ""]
    for item in items:
        flags = sorted({kind for _, kind in privacy_problems(item["text"])})
        out += [f"## {item['id']} — {item['source']}", "",
                f"- czeka: {label(item['proposal'])}",
                "- usuń: nie",
                f"- propozycja: reguły {label(item['rules'])}, model {label(item['model'])}"
                + (" (przypadek ze skryptu z 28.10 — znane oznaczenie)" if item.get("known") else "")
                + (f" · UWAGA prywatność: {', '.join(flags)}" if flags else ""), ""]
        out += [("> " + line).rstrip() for line in item["text"].splitlines()] + [""]
    return "\n".join(out)


def parse_review(markdown: str):
    """(przejrzane, [{id, source, waits, remove, text}]) z pliku Markdown po przeglądzie."""
    reviewed, items, current = False, [], None
    for line in markdown.splitlines():
        header = HEADER.match(line)
        if header:
            current = {"id": header.group(1), "source": header.group(2), "waits": None, "remove": False, "lines": []}
            items.append(current)
        elif current is None:
            field = FIELD.match(line)
            if field and field.group(1).strip().lower() == "przejrzane":
                reviewed = field.group(2).lower() in YES
        elif line.startswith(">"):
            current["lines"].append(line[2:] if line.startswith("> ") else line[1:])
        else:
            field = FIELD.match(line)
            if field and field.group(1).strip().lower() == "czeka":
                current["waits"] = {"tak": True, "nie": False}.get(field.group(2).lower())
            elif field and field.group(1).strip().lower() == "usuń":
                current["remove"] = field.group(2).lower() in YES
    for item in items:
        item["text"] = "\n".join(item.pop("lines")).strip()
    return reviewed, items


def redact(text: str) -> str:
    """Ścieżki → <ścieżka>, nazwa firmy w każdej pisowni → <firma>. E-maile, tokeny i numery nie są zastępowane
    (blokują zapis, dopóki ktoś ich nie usunie)."""
    for kind, pattern in PRIVATE:
        if kind in REDACTIONS:
            text = pattern.sub(REDACTIONS[kind], text)
    return text


def apply_review(markdown: str, redact_text: bool = False, drop_flagged: bool = False, dropped: list | None = None):
    """(rekordy zestawu, błędy). Rekordy: {id, text, waits, source}.

    redact_text  — przed kontrolą prywatności zastępuje ścieżki i nazwę firmy (`redact`),
    drop_flagged — wiadomości, w których po tym nadal jest cokolwiek z PRIVATE, pomija (nigdy ich nie zachowuje);
                   ich id trafiają do `dropped`, a progi zestawu liczą się po pominięciu.
    Plik musi być oznaczony „- przejrzane: tak” przez użytkownika; innego obejścia nie ma."""
    reviewed, items = parse_review(markdown)
    errors = []
    if not reviewed:
        errors.append("plik nie jest oznaczony jako przejrzany („- przejrzane: tak” na początku)")
    records = []
    for item in items:
        if item["remove"]:
            continue
        if redact_text:
            item["text"] = redact(item["text"])
        if drop_flagged and privacy_problems(item["text"]):
            if dropped is not None:
                dropped.append(item["id"])
            continue
        if item["waits"] is None:
            errors.append(f"{item['id']}: pole „czeka” musi mieć wartość tak albo nie")
        for number, kind in privacy_problems(item["text"]):
            errors.append(f"{item['id']}, linia tekstu {number}: {kind} — usuń albo zastąp w pliku Markdown")
        if not item["text"]:
            errors.append(f"{item['id']}: pusty tekst")
        records.append({"id": item["id"], "text": item["text"], "waits": bool(item["waits"]), "source": item["source"]})
    count = {True: sum(r["waits"] for r in records)}
    count[False] = len(records) - count[True]
    if len(records) < MIN_TOTAL or min(count.values()) < MIN_PER_CLASS:
        errors.append(f"zestaw po przeglądzie ma {len(records)} wiadomości (czeka {count[True]}, nie czeka {count[False]}); "
                      f"potrzeba co najmniej {MIN_TOTAL}, w tym po {MIN_PER_CLASS} w każdej klasie")
    return records, errors


def cmd_review(args) -> int:
    if args.apply:
        with open(args.apply, encoding="utf-8") as fh:
            dropped = []
            records, errors = apply_review(fh.read(), args.redact_paths, args.drop_flagged, dropped)
        if dropped:
            print(f"Pominięto {len(dropped)} wiadomości z danymi prywatnymi (--drop-flagged): {', '.join(dropped)}",
                  file=sys.stderr)
        if errors:
            print("Zestaw nie został zapisany:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"Zapisano {len(records)} wiadomości do {args.out}.", file=sys.stderr)
        return 0
    if not args.candidates:
        raise UsageError("podaj plik kandydatów z `extract` (albo --apply PLIK.md)")
    if inside_repo(args.out):
        raise UsageError("--out wskazuje na repozytorium; plik do przeglądu zawiera prawdziwe wiadomości")
    with open(args.candidates, encoding="utf-8") as fh:
        data = json.load(fh)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(render_review(data))
    print(f"Zapisano {args.out} ({len(data['items'])} wiadomości). Przejrzyj go i uruchom review --apply.", file=sys.stderr)
    return 0


# --- bench --------------------------------------------------------------------------------------------------------

DEFAULT_CORPUS = os.path.join(ROOT, "daemon", "tests", "fixtures", "question-corpus.jsonl")


def load_corpus(path: str = DEFAULT_CORPUS) -> list:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def score(corpus: list, predictions: list, times: list | None = None) -> dict:
    """Trafność i błędy: false_waits — model/reguły mówią „czeka”, a nie czeka; false_idle — odwrotnie.
    Brak odpowiedzi (None) liczy się jako błąd osobno i jako nietrafienie."""
    right = sum(1 for m, p in zip(corpus, predictions) if p is m["waits"])
    result = {"n": len(corpus), "right": right,
              "accuracy": round(100.0 * right / len(corpus), 1) if corpus else 0.0,
              "false_waits": sum(1 for m, p in zip(corpus, predictions) if p is True and not m["waits"]),
              "false_idle": sum(1 for m, p in zip(corpus, predictions) if p is False and m["waits"]),
              "errors": sum(1 for p in predictions if p is None)}
    if times:
        result["mean_s"], result["max_s"] = round(sum(times) / len(times), 2), round(max(times), 2)
    return result


def rules_accuracy(corpus: list) -> float:
    """Trafność obecnych reguł w pełnych punktach procentowych, zaokrąglona w dół (próg w teście `make test`)."""
    # z dokładnego ilorazu, nie z `accuracy` (ta jest już zaokrąglona do 0,1: 31/47 = 65,96 → 66,0 → 66 zamiast 65)
    result = score(corpus, [rules_waits(m["text"]) for m in corpus])
    return 100 * result["right"] // result["n"] if result["n"] else 0


def bench(corpus: list, model: Ollama | None) -> dict:
    result = {"n": len(corpus), "rules": score(corpus, [rules_waits(m["text"]) for m in corpus])}
    if model is None:
        return result
    model.judge("test")   # rozgrzewka: pierwsze wywołanie ładuje model
    for variant, full in (("tail", False), ("full", True)):
        answers = [model.judge(m["text"], full=full) for m in corpus]
        verdicts = [a[0] for a in answers]
        result[f"model_{variant}"] = score(corpus, verdicts, [a[1] for a in answers])
        result[f"combined_{variant}"] = score(corpus, [combined_waits(m["text"], v) for m, v in zip(corpus, verdicts)])
    return result


def format_bench(result: dict, model_name: str) -> str:
    rows = [("reguły", "rules"), (f"model {model_name}, końcówka", "model_tail"), (f"model {model_name}, cała wiadomość", "model_full"),
            ("reguły + model, końcówka", "combined_tail"), ("reguły + model, cała wiadomość", "combined_full")]
    lines = [f"Zestaw: {result['n']} wiadomości",
             f"{'wariant':40} {'trafność':>9} {'fałszywe czeka':>15} {'fałszywe nie czeka':>19} {'błędy':>6} {'średni czas':>12} {'najdłuższy':>11}"]
    for title, key in rows:
        if key not in result:
            continue
        r = result[key]
        mean = f"{r['mean_s']:.2f} s" if "mean_s" in r else "—"
        longest = f"{r['max_s']:.2f} s" if "max_s" in r else "—"
        lines.append(f"{title:40} {r['accuracy']:8.1f}% {r['false_waits']:15d} {r['false_idle']:19d} {r['errors']:6d} "
                     f"{mean:>12} {longest:>11}")
    return "\n".join(lines)


def cmd_bench(args) -> int:
    corpus = load_corpus(args.corpus)
    model = None
    if not args.rules_only:
        model = Ollama(args.ollama, args.model)
        problem = model.available()
        if problem:
            print(f"Bench modelu niemożliwy: {problem}. Reguły bez modelu: --rules-only.", file=sys.stderr)
            return 4
    result = bench(corpus, model)
    print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else format_bench(result, args.model))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("extract", help="wiadomości końcowe z dzienników → plik kandydatów (poza repozytorium)")
    e.add_argument("--out", required=True)
    e.add_argument("--claude", default="~/.claude")
    e.add_argument("--codex", default="~/.codex")
    e.add_argument("--since")
    e.add_argument("--until")
    e.add_argument("--per-class", type=int, default=30)
    e.add_argument("--candidates", help="kandydaci z notki #2 (JSON/JSONL: text, waits, source)")
    e.add_argument("--ollama", default=DEFAULT_OLLAMA)
    e.add_argument("--model", default=DEFAULT_MODEL)
    r = sub.add_parser("review", help="plik Markdown do przeglądu albo (--apply) zatwierdzony zestaw JSONL")
    r.add_argument("candidates", nargs="?")
    r.add_argument("--apply", metavar="REVIEW.md")
    r.add_argument("--redact-paths", action="store_true",
                   help="z --apply: ścieżki → <ścieżka>, nazwa firmy (firma) → <firma>")
    r.add_argument("--drop-flagged", action="store_true",
                   help="z --apply: pomiń wiadomości, w których po zastąpieniach nadal są dane prywatne (wypisuje tylko id)")
    r.add_argument("--out", required=True)
    b = sub.add_parser("bench", help="trafność reguł i modelu lokalnego na zestawie")
    b.add_argument("corpus", nargs="?", default=DEFAULT_CORPUS)
    b.add_argument("--rules-only", action="store_true")
    b.add_argument("--json", action="store_true")
    b.add_argument("--ollama", default=DEFAULT_OLLAMA)
    b.add_argument("--model", default=DEFAULT_MODEL)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return {"extract": cmd_extract, "review": cmd_review, "bench": cmd_bench}[args.command](args)
    except UsageError as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
