"""Identyfikatory ticketów w opisach podagentów, gałęziach i poleceniach.

Konwencja z docs/agent-plan-standard.md: opis podagenta zaczyna się od ID ticketu
(„TOM-140: implement guard”), gałąź zawiera ID małymi literami („tom-140-guard”), pytanie do
użytkownika wymienia ID. W praktyce bywa różnie: „Review LA-035 code part”, „la029_adapter”
(nazwa zadania Codexa, bez myślnika), „New agent task launcher (#22)”, a czasem bez ID
(„Implement ticket 01 log value”).

- Z prefiksem z planu (`ticket-prefix`) szukamy `PREFIX-123`, `prefix123`, `prefix_123`
  bez względu na wielkość liter — także w gałęziach i nazwach zadań.
- Bez prefiksu: ogólny wzorzec `[A-Z]{2,6}-\\d+` (tylko wielkie litery, bez znanych skrótów
  w rodzaju UTF-8, SHA-256, ADR-0016 — decyzja architektoniczna, INV-001 — niezmiennik) i `#\\d+`,
  ale tylko w opisie podagenta, nazwie zadania i gałęzi — nie w treści polecenia, która cytuje
  decyzje, niezmienniki i wymagania (board.py).
- Klucz ticketu to `PREFIX-<liczba>` bez zer wiodących (LA-029 i la29 to ten sam ticket),
  a pokazujemy zapis z planu albo pierwszy napotkany.
- Mapa migracji (docs/agents/youtrack-migration/*.csv w repozytorium) zamienia stare klucze
  na nowe, np. TOM-10 → LOG-128 (Linear → YouTrack) — tickety grupują się po nowym kluczu.
"""
import csv
import os
import re

GENERIC_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z]{1,5})-(\d{1,6})(?![A-Za-z0-9])")
HASH_RE = re.compile(r"(?<![\w&])#(\d{1,6})(?!\d)")
# Skróty techniczne, które wyglądają jak klucz ticketu.
NOT_TICKETS = {"UTF", "SHA", "ISO", "GPT", "RFC", "CVE", "HTTP", "HTTPS", "MD", "TLS", "SSL", "IPV", "CP", "PEP",
               "ES", "WCAG", "AES", "RSA", "TCP", "UDP", "X", "WIN", "COM", "LPT", "AM", "PM", "UTC", "GMT", "CET",
               "CEST", "TS", "ECMA", "NET", "QT", "KF", "API", "SQL", "PDF", "PNG", "JPG", "SVG", "OK", "VS", "CS",
               "ARM", "EAN", "PIN", "VIN",
               # Dokumenty i zapisy w specyfikacjach, nie tickety: decyzje architektoniczne, niezmienniki,
               # wymagania (także niefunkcjonalne) i kryteria akceptacji.
               "ADR", "INV", "REQ", "NFR", "AC"}
TASK_NAME_RE = re.compile(r"^([a-z]{2,5})[-_ ]?(\d{1,5})(?:[-_ ]|$)")
# Słowa, po których stoi liczba, a które nie są prefiksem ticketu („phase 1”, „step3_…”).
NOT_PREFIX_WORDS = {"phase", "step", "round", "part", "krok", "etap", "faza", "wave", "batch", "ticket", "item",
                    "task", "slice", "stage", "level", "page", "test", "run", "fix", "v", "vol", "issue", "pr", "mr"}
MIGRATION_DIR = os.path.join("docs", "agents", "youtrack-migration")


def key_of(prefix: str, number: str) -> str:
    """Klucz porównania: wielkie litery i liczba bez zer wiodących."""
    return f"{prefix.upper()}-{int(number)}"


def prefix_pattern(prefix: str) -> re.Pattern:
    """`TOM-140`, `tom-140`, `tom_140`, `tom140`, `TOM 140` — dla znanego prefiksu."""
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(prefix)}[-_ ]?(\d{{1,6}})(?![0-9])", re.I)


class TicketMatcher:
    """Wyszukiwanie ID ticketów dla jednego koordynatora (prefiksy z planu i nauczone z opisów)."""

    def __init__(self, prefixes=(), aliases: dict | None = None, ignore=()):
        self.prefixes = [p.upper() for p in prefixes if p]
        self.aliases = aliases or {}         # klucz → klucz (mapa migracji)
        self.ignore = {k.upper() for k in ignore if k}  # np. ID samego koordynatora (TOM-20 z gałęzi RC)
        self.display: dict[str, str] = {}    # klucz → zapis do pokazania

    def learn(self, texts) -> None:
        """Bez prefiksu z planu: prefiks, który najczęściej pojawia się w opisach podagentów."""
        if self.prefixes:
            return
        counts: dict[str, int] = {}
        for text in texts:
            for match in GENERIC_RE.finditer(text or ""):
                if match.group(1) not in NOT_TICKETS:
                    counts[match.group(1)] = counts.get(match.group(1), 0) + 1
        if not counts:
            # Nazwy zadań Codexa i gałęzie małymi literami: „tom142_implementation”, „la029-adapter”.
            for text in texts:
                match = TASK_NAME_RE.match((text or "").strip())
                if match and match.group(1).lower() not in NOT_PREFIX_WORDS:
                    prefix = match.group(1).upper()
                    if prefix not in NOT_TICKETS:
                        counts[prefix] = counts.get(prefix, 0) + 1
        if counts:
            best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            self.prefixes = [best[0]]

    def find(self, text: str, generic: bool = True) -> str:
        """Pierwszy klucz ticketu w tekście albo pusty napis."""
        text = text or ""
        found = []
        for prefix in self.prefixes:
            for match in prefix_pattern(prefix).finditer(text):
                found.append((match.start(), prefix, match.group(1)))
        if not found and generic:
            for match in GENERIC_RE.finditer(text):
                if match.group(1) not in NOT_TICKETS:
                    found.append((match.start(), match.group(1), match.group(2)))
            for match in HASH_RE.finditer(text):
                found.append((match.start(), "#", match.group(1)))
        for _pos, prefix, number in sorted(found):
            key = f"#{int(number)}" if prefix == "#" else key_of(prefix, number)
            key = self.aliases.get(key, key)
            if key in self.ignore:
                continue
            if key not in self.display:
                self.display[key] = key if prefix == "#" or key != key_of(prefix, number) \
                    else f"{prefix.upper()}-{number}"
            return key
        return ""

    def strip(self, text: str) -> str:
        """Tekst bez pierwszego ID ticketu i separatora: „LA-028: tax tails” → „tax tails”,
        „tom142_implementation” → „implementation”."""
        text = text or ""
        patterns = [prefix_pattern(p) for p in self.prefixes] + [GENERIC_RE, HASH_RE]
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                out = (text[:match.start()] + " " + text[match.end():]).strip()
                out = re.sub(r"^[\s:·\-–—_()]+|[\s:·\-–—_(]+$", "", out)
                out = re.sub(r"\(\s*\)", "", out).strip()
                return out or text
        return text

    def show(self, key: str) -> str:
        return self.display.get(key, key)


def load_aliases(roots) -> dict:
    """Mapa migracji z docs/agents/youtrack-migration/*.csv pierwszego repozytorium, które ją ma.

    Kolumny: źródło (`source_key` z „linear:TOM-10” albo `source_id`) i `youtrack_id`; wiersze
    z `kind` innym niż „ticket” (np. tematy LA-E…) pomijamy. Zwraca {„TOM-10”: „LOG-128”}.
    """
    for root in roots:
        folder = os.path.join(root or "", MIGRATION_DIR)
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith(".csv"))
        except OSError:
            continue
        out = {}
        for name in names:
            try:
                with open(os.path.join(folder, name), encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        source = (row.get("source_key") or row.get("source_id") or "").split(":")[-1].strip()
                        target = (row.get("youtrack_id") or "").strip()
                        if row.get("kind") and row.get("kind") != "ticket":
                            continue
                        s, t = _split(source), _split(target)
                        if s and t and s != t:
                            out[s] = t
            except (OSError, csv.Error, UnicodeDecodeError):
                continue
        if out:
            return out
    return {}


def _split(value: str) -> str:
    match = re.fullmatch(r"([A-Za-z]{1,8})-(\d{1,6})", value or "")
    return key_of(match.group(1), match.group(2)) if match else ""


def strip_ticket(text: str, display: str) -> str:
    """Opis bez ID ticketu na początku: „TOM-140: implement guard” → „implement guard”."""
    text = (text or "").strip()
    if not display:
        return text
    pattern = re.compile(rf"^\s*(?:{re.escape(display)}|{re.escape(display.replace('-', ''))})\s*[:·\-–—]?\s*", re.I)
    return pattern.sub("", text, count=1) or text
