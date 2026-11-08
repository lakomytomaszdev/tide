"""Wspólne dane testów polityki testów kartek (PT-279): drzewa worktree i wycinanie fragmentów polityki.

Wzorcowe fragmenty w `fixtures/test-policy/master-fragments.json` wygenerowano publicznymi funkcjami kartek
z master `04a23c16` na tych drzewach (`tree`) i tymi wycięciami (`fragments`). Nie zmieniaj drzew ani wycięć
bez ponownego wygenerowania wzorca z master — inaczej porównanie przestaje znaczyć „identyczne z master”.
"""
import os
import tempfile

BASE = "0123456789abcdef0123456789abcdef01234567"
MASTER = "04a23c16c74fd991e325c4ddb601f84d9f84845d"

# plasma-tide: `test` w Makefile, `test-changed` w mk/tests.mk, uruchamiacz z `--modules`.
CHANGED = {
    "Makefile": ".PHONY: help test\ninclude mk/*.mk\n\ntest:\n\t@/usr/bin/python3 tools/test_runner.py\n",
    "mk/tests.mk": ".PHONY: test-changed\n\ntest-changed:\n\t@/usr/bin/python3 tools/test_runner.py --changed\n",
    "tools/test_runner.py": 'parser.add_argument("--modules", nargs="+")\n',
}
# Starsza gałąź karty (K-37/K-38 sprzed K-54): tylko `test`, uruchamiacz bez `--modules`.
LEGACY = {
    "Makefile": ".PHONY: test\ninclude mk/*.mk\n\ntest:\n\t@/usr/bin/python3 tools/test_runner.py\n",
    "mk/tests.mk": "test-daemon:\n\t@true\n",
    "tools/test_runner.py": "def execute():\n    pass\n",
}
VARIANTS = {"changed": CHANGED, "legacy": LEGACY}


def tree(case, files: dict) -> str:
    """Katalog tymczasowy z plikami (`ścieżka względna → treść`), sprzątany po teście."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    for rel, text in files.items():
        path = os.path.join(tmp.name, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return tmp.name


def cards(handoff, worktree: str, **extra) -> dict:
    """Kartki ticketu, poprawek, review i domknięcia dla plasma-tide (`extra` — np. `policy` po PT-279)."""
    common = dict(card_id="K-69", title="Kontrakt testów", description="Opis karty.", ticket="PT-279",
                  worktree=worktree, out_dir="/o")
    ticket_extra = {k: v for k, v in extra.items() if k != "close_policy"}
    close_extra = {"policy": extra["close_policy"]} if "close_policy" in extra else {}
    return {
        "ticket": handoff.ticket_card(**common, project="plasma-tide", branch="ticket/pt-279",
                                      card_branch="card/k-69", test_base=BASE, **ticket_extra),
        "fix": handoff.fix_card(**common, round_no=1, branch="ticket/pt-279", review="MAJOR: popraw",
                                test_base=BASE, **ticket_extra),
        "review": handoff.review_card(**common, round_no=1, card_branch="card/k-69",
                                      ticket_branch="ticket/pt-279", diff="diff", note="notka", rules="zasady",
                                      test_base=BASE, **ticket_extra),
        "close": handoff.close_card(card_id="K-69", title="Kontrakt testów", description="Opis karty.",
                                    worktree=worktree, out_dir="/o", tickets=[], manual=False, **close_extra),
    }


def _between(text: str, start: str, end: str, *, include_start=True, include_end=True) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i if include_start else i + len(start):j + len(end) if include_end else j]


TESTS_START = "Testy ticketu (także po poprawkach)"
TESTS_END = "właściciel.\n"


def fragments(texts: dict) -> dict:
    """Fragmenty polityki testów z kartek: testy ticketu, punkty recenzenta, testy domknięcia."""
    out = {}
    for kind in ("ticket", "fix", "review"):
        out[f"{kind}-tests"] = _between(texts[kind], TESTS_START, TESTS_END)
    out["review-bullets"] = _between(texts["review"], "- sprawdź zgodność z opisem karty i zasadami projektu,\n",
                                     "- podaj konkretne", include_start=False, include_end=False)
    out["close-tests"] = _between(texts["close"], "Przed napisaniem podsumowania", "bez nowych zmian.\n")
    return out
