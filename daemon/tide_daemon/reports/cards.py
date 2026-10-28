"""Tablica pracy w raporcie: nazwy projektów i karty, do których należy praca (czytelność po PT-24).

Raport dostaje migawkę tablicy (/Board Snapshot(), board/store.py) albo None — wtedy wszystko jak
dawniej (nazwa katalogu, tytuł z nazwy gałęzi). Z migawki bierzemy:

- nazwę projektu z tablicy dla repozytorium (`repo` projektu → `name`): „LegalApp” zamiast
  „agent-team” na kartach, w tekście do schowka, w „Dziś w skrócie” i na wejściu modelu,
- kartę, do której należy jednostka pracy (gałąź, sesja): `branch` karty, `session` karty, klucz
  `card:` planu, a na końcu token `k-12` w nazwie gałęzi — ten tylko w repozytorium projektu karty
  (główny albo dodatkowy, PT-28), żeby obca gałąź „k-1-…” nie udawała karty,
- projekty karty (główny i dodatkowe) — praca nad jedną kartą w kilku repozytoriach jest opisana raz,
  w projekcie głównym („pracuje też w: …”), a w pozostałych krótką wzmianką,
- karty w toku („W toku”, „Czeka na mnie”, „Do sprawdzenia”) — stan prac na dzień w projekcie głównym karty,
  także gdy w okresie nie było ruchu (collect.assign_cards, standup.card_items).

Bez I/O: tylko słowniki z migawki.
"""
import os
import re

from ..board.model import CARD_PREFIX

# Kolumny tablicy, w których karta to praca w toku (stan prac na dzień): „W toku”, „Czeka na mnie”, „Do sprawdzenia”.
OPEN_COLUMNS = ("progress", "waiting", "review")
CARD_TOKEN = re.compile(r"(?i)(?<![a-z0-9])" + re.escape(CARD_PREFIX) + r"(\d{1,6})(?![a-z0-9])")


def _real(path) -> str:
    return os.path.realpath(os.path.expanduser(path)) if isinstance(path, str) and path else ""


def _session_key(value) -> str:
    """„claude:abc” i „abc” → „abc” (karta trzyma id sesji z narzędziem albo bez)."""
    text = str(value or "").strip()
    return text.split(":", 1)[1] if ":" in text else text


class BoardInfo:
    """Migawka tablicy przygotowana do raportu. Pusta (None, zły słownik) — nic nie wie."""

    def __init__(self, snapshot=None):
        self.names: dict[str, str] = {}      # ścieżka repozytorium (realpath) → nazwa projektu z tablicy
        self.slugs: dict[str, dict] = {}     # slug projektu → {"name", "repo"}
        self.cards: dict[str, dict] = {}     # „K-12” → karta z migawki
        if not isinstance(snapshot, dict):
            return
        for project in snapshot.get("projects") or []:
            if not isinstance(project, dict):
                continue
            name = str(project.get("name") or project.get("slug") or "").strip()
            repo = _real(project.get("repo"))
            slug = str(project.get("slug") or "")
            if slug:
                self.slugs[slug] = {"name": name, "repo": repo}
            if repo and name:
                self.names.setdefault(repo, name)
        for card in snapshot.get("cards") or []:
            if isinstance(card, dict) and isinstance(card.get("id"), str) and card["id"]:
                self.cards[card["id"].upper()] = card

    def __bool__(self) -> bool:
        return bool(self.names or self.cards)

    def label(self, path: str) -> str:
        """Nazwa projektu z tablicy dla repozytorium albo pusty napis."""
        return self.names.get(_real(path), "")

    def open_cards(self) -> list[dict]:
        """Karty w toku — kolumna do wyświetlenia (`boardColumn`, z pracy agentów) w OPEN_COLUMNS."""
        return [c for c in self.cards.values() if column_of(c) in OPEN_COLUMNS]

    def card_repos(self, card: dict) -> list[str]:
        """Repozytoria projektów karty (realpath): najpierw główny, potem dodatkowe (PT-28)."""
        out = []
        main = self.slugs.get(str(card.get("project") or "")) or {}
        if main.get("repo"):
            out.append(main["repo"])
        for slug in card.get("projects") or []:
            repo = (self.slugs.get(str(slug)) or {}).get("repo") or ""
            if repo and repo not in out:
                out.append(repo)
        for extra in card.get("extraProjects") or []:
            repo = _real((extra or {}).get("repo")) if isinstance(extra, dict) else ""
            if repo and repo not in out:
                out.append(repo)
        return out

    def card_for(self, *, branch: str = "", sessions=(), plan_card: str = "", repo: str = "") -> dict | None:
        """Karta jednostki pracy: `branch` karty równy gałęzi, `session` karty wśród sesji jednostki,
        klucz `card:` planu, token `k-12` w nazwie gałęzi (tylko w repozytorium projektu karty)."""
        if not self.cards:
            return None
        branch = (branch or "").strip()
        if branch and branch != "HEAD":
            hit = next((c for c in self.cards.values() if str(c.get("branch") or "").strip() == branch), None)
            if hit is not None:
                return hit
        keys = {_session_key(s) for s in sessions or ()} - {""}
        if keys:
            hit = next((c for c in self.cards.values() if _session_key(c.get("session")) in keys), None)
            if hit is not None:
                return hit
        if plan_card and plan_card.strip().upper() in self.cards:
            return self.cards[plan_card.strip().upper()]
        if branch:
            real = _real(repo)
            for match in CARD_TOKEN.finditer(branch):
                card = self.cards.get(f"{CARD_PREFIX}{int(match.group(1))}".upper())
                if card is None:
                    continue
                repos = self.card_repos(card)
                if not repos or (real and real in repos):
                    return card
        return None


def column_of(card: dict) -> str:
    """Kolumna karty tak jak na tablicy: wyprowadzona z pracy agentów albo zapisana ręcznie."""
    derived = card.get("derived") if isinstance(card.get("derived"), dict) else {}
    return str(derived.get("column") or card.get("boardColumn") or card.get("column") or "")


def card_label(card: dict) -> str:
    """„K-1 Data ważności wiadomości” — numer i tytuł karty, tak jak na tablicy."""
    ident = str(card.get("id") or "").strip()
    title = " ".join(str(card.get("title") or "").split())
    return f"{ident} {title}".strip()
