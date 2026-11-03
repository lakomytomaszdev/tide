"""Uwagi recenzenta planu (K-35, PT-127): format `findings[{severity, text, tickets}]` i zgodność ze starym tekstem.

Nowy `plan-review.json`: `{"verdict": "APPROVE"|"REQUEST_CHANGES", "findings": [{"severity": "blocker"|"major"|"nit",
"text": "...", "tickets": ["#5", "PT-42"]}]}`. Stary format (karty w toku) to jeden napis w `findings`; staje się
jedną uwagą bez wagi (`severity: ""`) bez powiązanych ticketów. Wszystko, co opuszcza usługę (stan karty, decyzje),
ma już postać listy.
"""
SEVERITIES = ("blocker", "major", "nit")
MAX_TEXT = 32000
MAX_TICKETS = 50
LABELS = {"blocker": "blokująca", "major": "ważna", "nit": "drobna", "": "bez wagi"}


def normalize(raw, stored: bool = False) -> list[dict]:
    """Lista uwag `{severity, text, tickets}` z nowego formatu albo ze starego napisu.

    Rzuca `ValueError` z czytelnym powodem, gdy format jest błędny. `stored=True` (zapisane zdarzenia) dopuszcza
    też uwagę bez wagi (`severity: ""`), jaką daje stary napis; z pliku recenzenta waga jest wymagana."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [{"severity": "", "text": raw.strip(), "tickets": []}] if raw.strip() else []
    if not isinstance(raw, list):
        raise ValueError("findings musi być listą uwag albo napisem")
    out = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"uwaga {n}: oczekiwano obiektu {{severity, text, tickets}}")
        severity = item.get("severity", "")
        if severity not in SEVERITIES and not (stored and severity == ""):
            raise ValueError(f"uwaga {n}: severity musi być jedną z {', '.join(SEVERITIES)}")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"uwaga {n}: text musi być niepustym napisem")
        if len(text) > MAX_TEXT:
            raise ValueError(f"uwaga {n}: text jest za długi (do {MAX_TEXT} znaków)")
        tickets = item.get("tickets", [])
        if tickets is None:
            tickets = []
        if not isinstance(tickets, list) or len(tickets) > MAX_TICKETS or not all(
                isinstance(t, str) and t.strip() for t in tickets):
            raise ValueError(f"uwaga {n}: tickets musi być listą numerów ticketów (napisów)")
        out.append({"severity": severity, "text": text.strip(), "tickets": [t.strip() for t in tickets]})
    return out


def lenient(raw) -> list[dict]:
    """To samo co `normalize`, ale dla już zapisanych zdarzeń: błędne wpisy nie psują stanu karty."""
    try:
        return normalize(raw, stored=True)
    except ValueError:
        return []


def default_selected(finding: dict) -> bool:
    """Domyślnie odsyłamy `blocker` i `major` oraz uwagi bez wagi (stary napis); `nit` nie."""
    return finding.get("severity") != "nit"


def selection(findings: list[dict]) -> list[dict]:
    """Uwagi odsyłane domyślnie; gdy są same drobne — wszystkie (inaczej poprawa nie miałaby treści)."""
    chosen = [f for f in findings if default_selected(f)]
    return chosen or list(findings)


def render(findings: list[dict], own: str = "") -> str:
    """Tekst uwag dla planującego: tylko podane uwagi i własne słowa użytkownika."""
    lines = []
    for f in findings:
        label = f"[{f['severity']}] " if f.get("severity") else ""
        tickets = f" (tickety: {', '.join(f['tickets'])})" if f.get("tickets") else ""
        lines.append(f"- {label}{f['text']}{tickets}")
    if own and own.strip():
        lines.append(("Uwagi użytkownika:\n" if lines else "") + own.strip())
    return "\n".join(lines)


def counts(findings: list[dict]) -> dict:
    return {key: sum(1 for f in findings if f.get("severity") == key) for key in SEVERITIES}


def review_view(event: dict | None) -> dict:
    """`plan.review{verdict, reviewer, findings[]}` dla stanu karty z zapisanego `plan.reviewed`.

    Bez werdyktu (nieznany lub brak) pole `verdict` jest puste. Brak review → pusty słownik."""
    if not event:
        return {}
    verdict = event.get("verdict")
    return {**{k: v for k, v in event.items() if k not in ("verdict", "findings")},
            "verdict": verdict if verdict in ("APPROVE", "REQUEST_CHANGES") else "",
            "findings": lenient(event.get("findings"))}
