"""Zasady przydziału w repozytorium tablicy: wspólne i nadpisanie projektu."""
import json

DEFAULT_RULES = """lekki: drobne, mechaniczne zmiany z jasnym kryterium.
standard: typowa funkcja lub poprawka z testami.
mocny: decyzje domenowe, architektura, bezpieczeństwo i trudna diagnoza.
lokalny: praca wymagająca pozostawienia danych lokalnie.
Recenzent: zawsze inny model; przy dostawcy mieszanym także inny dostawca.
Przy każdym przydziale zapisz jedno zdanie uzasadnienia."""
KEY = "assignment-rules"


def rules(board, project=None):
    if project is not None and project.assignment_rules:
        return project.assignment_rules
    if board.meta:
        try:
            value = json.loads(board.meta.get(KEY) or '""')
            if isinstance(value, str) and value.strip():
                return value
        except ValueError:
            pass
    return DEFAULT_RULES
