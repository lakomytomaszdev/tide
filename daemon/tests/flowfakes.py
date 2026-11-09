"""Atrapy dla testów przepływu karty (PT-58): `claude -p`, `systemd-run --user --scope` i `systemctl --user`.

- `claude` czyta kartkę ze stdin (polecenie kroku), zapisuje ją z argv w `<katalog wyjściowy>/fake-call.json`, dopisuje
  dziennik sesji w `$HOME/.claude/projects/<katalog>/<id>.jsonl` (jak prawdziwy program) i robi to, co każe linia
  „ATRAPA: …” w opisie karty: `gotowe` (plik, commit, plik wyniku i notka), `bez-wyniku` (commit bez pliku wyniku),
  `czekaj` (plik bez commita i sen — żyje, ale nic nie robi), `powoli N` (N sekund dopisywania, potem jak `gotowe`),
  `pytanie` (commit i wynik „pytanie”), `pytanie-bez-pracy` (pytanie przed zmianą plików i commitem),
  `pytanie-opcje` (pytanie z poprawnymi `options[]`), `pytanie-zle-opcje` (kilka poleconych).
  Ścieżkę pliku wyniku bierze z kartki (linia „Plik wyniku: …”), nie z ustawień.
  Kartkę opiekuna karty (PT-143) rozpoznaje po pierwszej linii „Jesteś opiekunem karty”; co odpowiada, mówi
  „ATRAPA-OPIEKUN: …” w opisie problemu (`escalate` — domyślnie, `powoli` — 6 s pracy i escalate,
  `akcja-<nazwa>`, `bez-wyniku`, `zly-format`, `pytanie`, `blad`; PT-150: `retry` — ponowienie „Kroku problemu”
  z instrukcją, `retry-obcy` — ponowienie kroku spoza problemu, `retry-powoli` — 6 s pracy, potem `retry`;
  PT-147: `akcja-<nazwa>-powoli` — 6 s pracy, potem `akcja-<nazwa>`;
  PT-158: `pisze-sledzony`, `pisze-nowy`,
  `commit` — zapis do worktree albo commit, `karta-sledzony`, `karta-nowy` — zapis do worktree karty; potem
  poprawny `escalate`). Krok ticketu `blad-opiekun` kończy się błędem z powodem z linii „ATRAPA-POWOD: …” (powód
  jest też w dzienniku kroku).
- `systemd-run` zapisuje pid pod nazwą jednostki w `$XDG_RUNTIME_DIR/fake-units/` i robi `exec` programu — jak
  `--scope`, w którym program jest procesem jednostki; proces żyje dalej po końcu usługi, która go uruchomiła.
- `systemctl --user is-active --quiet <j>.scope` (0 — żyje, 3 — nie) i `kill --signal=… <j>.scope` (sygnał do grupy
  procesu jednostki) według tego katalogu.
Prawdziwy menedżer systemd nie jest dotykany.
"""
import os
import stat

CLAUDE = r'''#!/usr/bin/python3
import json, os, re, subprocess, sys, time, tempfile
args = sys.argv[1:]
if args[:3] == ["mcp", "list", "--json"]:
    print("[]")
    sys.exit(0)
def opt(name):
    return args[args.index(name) + 1] if name in args else ""
sid = opt("--session-id") or opt("--resume")
prompt = sys.stdin.read()
cwd = os.getcwd()
if prompt.startswith("Jesteś opiekunem karty"):
    # Kontekst opiekuna może cytować kartki wcześniejszych kroków. Obowiązuje końcowa linia jego własnego wyniku.
    results = re.findall(r"^Plik wyniku: (\S+)$", prompt, re.MULTILINE)
    result = results[-1] if results else ""
else:
    # Planowanie podaje ścieżkę po zdaniu w tej samej linii; pozostałe kroki zachowują dawny odczyt.
    result = re.search(r"Plik wyniku: (\S+)", prompt)
    result = result.group(1) if result else ""
out = os.path.dirname(result) if result else os.path.join(os.path.dirname(cwd), "missing-out")
os.makedirs(out, exist_ok=True)
fd, call_tmp = tempfile.mkstemp(prefix=".fake-call-", suffix=".tmp", dir=out)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"argv": args, "prompt": prompt, "cwd": cwd, "pid": os.getpid(), "env": dict(os.environ)}, fh)
    # Testy czekają na istnienie tego pliku: publikacja musi oznaczać kompletny JSON.
    os.replace(call_tmp, os.path.join(out, "fake-call.json"))
finally:
    if os.path.exists(call_tmp):
        os.unlink(call_tmp)
logs = os.path.join(os.environ["HOME"], ".claude", "projects", re.sub(r"[/.]", "-", cwd))
os.makedirs(logs, exist_ok=True)
with open(os.path.join(logs, sid + ".jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-10-28T12:00:00Z",
                         "message": {"role": "user", "content": "kartka"}}) + "\n")
    fh.write(json.dumps({"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": "2026-10-28T12:00:01Z",
                         "message": {"role": "assistant", "content": [{"type": "text", "text": "Robię ticket."}]}}) + "\n")
mode = re.search(r"ATRAPA: ([a-z-]+)(?: (\d+))?", prompt)
word, count = (mode.group(1), int(mode.group(2) or 0)) if mode else ("gotowe", 0)
file_match = re.search(r"ATRAPA-PLIK: ([a-zA-Z0-9_-]+)", prompt)
work = os.path.join(cwd, "ATRAPA-" + file_match.group(1) + ".md" if file_match else "ATRAPA.md")

# TIDE_FAKE_AGENT_READY: instrumentacja po inicjalizacji, przed obsługą kroku.
if prompt.startswith("Jesteś opiekunem karty"):
    # Krok opiekuna (PT-143): co zrobić mówi „ATRAPA-OPIEKUN: …” w opisie problemu (powód błędu kroku).
    care = re.search(r"ATRAPA-OPIEKUN: ([a-z-]+)", prompt)
    if not care:
        # PT-146: przy poprawie planu polecenie jest w odrzuconym pliku plan.md z kontekstu (druga nieudana poprawka).
        failed_plan = re.search(r"(\S*plan-fix/failed-plan\.md)", prompt)
        if failed_plan and os.path.isfile(failed_plan.group(1)):
            with open(failed_plan.group(1), encoding="utf-8") as fh:
                care = re.search(r"ATRAPA-OPIEKUN: ([a-z-]+)", fh.read())
    care = care.group(1) if care else "escalate"
    if care == "powoli":            # opiekun pracuje kilka sekund (widok „W toku”, restart usługi), potem escalate
        time.sleep(6)
        care = "escalate"
    if care == "retry-powoli":      # PT-150: kilka sekund pracy (decyzja użytkownika w tym czasie), potem retry
        time.sleep(6)
        care = "retry"
    if care.endswith("-powoli"):    # PT-147: `akcja-<nazwa>-powoli` — kilka sekund pracy, potem ta akcja
        time.sleep(6)
        care = care[:-len("-powoli")]
    # PT-158: opiekun, który pisze poza `out` (zmienia plik śledzony, dodaje nieśledzony, robi commit), a potem
    # zapisuje poprawny wynik `escalate` — strażnik ma go odrzucić przed odczytem wyniku.
    tree = re.search(r"Worktree kroku \(wyłącznie do odczytu\): (\S+)", prompt).group(1)
    if care == "pisze-sledzony":
        tracked = subprocess.check_output(["git", "-C", tree, "ls-files"], text=True).split()
        with open(os.path.join(tree, tracked[0]), "a", encoding="utf-8") as fh:
            fh.write("zmiana opiekuna\n")
    elif care == "pisze-nowy":
        with open(os.path.join(tree, "opiekun-byl-tu.txt"), "w", encoding="utf-8") as fh:
            fh.write("nowy plik\n")
    elif care == "commit":
        subprocess.run(["git", "-C", tree, "commit", "-q", "--allow-empty", "-m", "opiekun commituje"], check=True)
    elif care in ("karta-sledzony", "karta-nowy"):
        # Review M2 (PT-158): zapis w worktree karty (nie ticketu) bez commita.
        listed = subprocess.check_output(["git", "-C", tree, "worktree", "list", "--porcelain"], text=True)
        num = re.match(r"Jesteś opiekunem karty K-(\d+)", prompt).group(1)
        card_tree = next(block.split("\n")[0][len("worktree "):] for block in listed.split("\n\n")
                         if f"branch refs/heads/card/k-{num}-" in block)
        if care == "karta-sledzony":
            tracked = subprocess.check_output(["git", "-C", card_tree, "ls-files"], text=True).split()
            with open(os.path.join(card_tree, tracked[0]), "a", encoding="utf-8") as fh:
                fh.write("zmiana opiekuna w karcie\n")
        else:
            with open(os.path.join(card_tree, "opiekun-w-karcie.txt"), "w", encoding="utf-8") as fh:
                fh.write("nowy plik\n")
    if care in ("pisze-sledzony", "pisze-nowy", "commit", "karta-sledzony", "karta-nowy"):
        care = "escalate"
    data = None
    if care == "zly-format":
        with open(result, "w", encoding="utf-8") as fh:
            fh.write("to nie jest JSON")
    elif care == "pytanie":
        data = {"result": "question", "question": "Czy ponowić krok z inną instrukcją?"}
    elif care == "blad":
        data = {"result": "error", "reason": "opiekun nie wie, co zrobić"}
    elif care.startswith("akcja-"):
        data = {"result": "done", "action": care[len("akcja-"):], "summary": "Sprawdziłem.", "recommendation": "Zrób to."}
    elif care == "escalate":
        data = {"result": "done", "action": "escalate", "summary": "Krok dwa razy padł na tym samym teście.",
                "recommendation": "Zmień wykonawcę na mocniejszy model.", "optionId": "change-executor"}
    elif care in ("retry", "retry-obcy"):
        # PT-150: ponowienie kroku problemu („Krok problemu: …” w kartce) albo kroku spoza problemu.
        target = re.search(r"Krok problemu: (\S+)", prompt).group(1) if care == "retry" else "K-999-obcy-1"
        data = {"result": "done", "action": "retry", "step": target,
                "instruction": "Najpierw uruchom test eksportu i popraw format daty na ISO 8601.",
                "reason": "Dziennik kroku pokazuje błąd formatu daty."}
    elif care == "return-plan-gap":
        # PT-155: luka wynika z decyzji A, a rozpoczęte tickety mają pozostać nietykalne.
        data = {"result": "done", "action": "return-plan",
                "feedback": "Dodaj ticket na migrację formatu, wynika z decyzji A; tickety, które ruszyły, "
                            "przepisz bez zmian.", "reason": "W planie brakuje migracji z decyzji A."}
    elif care in ("return-plan", "return-plan-obce", "approve-plan"):
        # PT-146: odesłanie planu z precyzyjną uwagą (wiersz, bloker, błąd standardu); `-obce` — numer uwagi spoza
        # listy; `approve-plan` — akcja, której opiekun nie ma.
        data = {"result": "done", "action": care if care == "approve-plan" else "return-plan",
                "feedback": "Wiersz #1: kryterium nie jest mierzalne (bloker: ticket nie może ruszyć); błąd "
                            "standardu 1.2: brak sekcji Dla recenzenta. Odrocz tę uwagę do ticketu #2.",
                "reason": "Poprzednia poprawka nie przeszła sprawdzenia formatu."}
        if care == "return-plan-obce":
            data["findings"] = "9"
    # PT-156: odpowiedź zadana przez test okablowania, bez udawania diagnozy modelu.
    scripted = re.search(r"^ATRAPA-WYNIK: (.+)$", prompt, re.MULTILINE)
    if scripted:
        data = json.loads(scripted.group(1))
        if data.get("step") == "$target":
            data["step"] = re.search(r"Krok problemu: (\S+)", prompt).group(1)
    if data is not None:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": sid}))
    sys.exit(0)

if prompt.startswith("Popraw propozycję planu karty"):
    if "ATRAPA: amend-question" in prompt and "Odpowiedź użytkownika:" not in prompt:
        with open(result, "w") as fh:
            json.dump({"result": "question", "question": "Który wariant poprawki?"}, fh)
        sys.exit(0)
    proposal = json.loads(re.search(r"Poprzednia propozycja: (.+)", prompt).group(1))
    if "ATRAPA: invalid-amend" in prompt:
        with open(os.path.join(out, "plan.md"), "w") as fh:
            fh.write("błędny plan")
        with open(result, "w") as fh:
            json.dump({"result": "done", "notes": []}, fh)
        sys.exit(0)
    rows = proposal["rows"]
    broken_structure = "ATRAPA: amend-bad-structure" in prompt
    if broken_structure:
        rows[0]["blockers"] = ["#99"]
    if "Dodaj ticket" in prompt:
        rows.append({**rows[-1], "workId": "#3", "title": "Dodatkowy ticket", "blockers": []})
        if "Dodaj ticket na migrację formatu" in prompt:
            rows[-1]["body"] = "# Migracja\nATRAPA: migracja\nATRAPA-PLIK: migracja\n- [ ] Format zmigrowany.\n"
    os.makedirs(os.path.join(out, "tickets"), exist_ok=True)
    with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ntide-plan: 1\ntitle: Poprawiony\ncard: " + re.search(r"karty (K-[0-9]+)", prompt).group(1) +
                 "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n"
                 "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for row in rows:
            fh.write("| " + " | ".join([row["workId"], row["title"], "todo", "", row["executor"], row["note"], row["class"], row["reviewer"], ", ".join(row["blockers"])]) + " |\n")
    for row in rows:
        with open(os.path.join(out, "tickets", row["workId"].lstrip("#") + ".md"), "w", encoding="utf-8") as fh:
            # Dodanie ticketu nie zmienia istniejących kartek, także przy numerach z YouTracka.
            keep = not row["workId"].startswith("#") and ("Dodaj ticket" in prompt or row is rows[0])
            fh.write(row["body"] + ("" if keep else "\n## Dla recenzenta\nSprawdź wymagany format.\n"))
    if broken_structure:
        with open(os.path.join(out, "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("# Eksport\nCo zbudować: eksport danych.\n## Dla recenzenta\nSprawdź format.\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": ["plan.md"]}, fh)
    sys.exit(0)

if prompt.startswith("Review propozycji planu"):
    review = {"verdict": "REQUEST_CHANGES" if "ATRAPA: plan-review-changes" in prompt else "APPROVE", "findings": "Uzupełnij Dla recenzenta."}
    if "ATRAPA: plan-review-structured" in prompt:  # nowy format uwag (PT-127)
        review = {"verdict": "REQUEST_CHANGES", "findings": [
            {"severity": "blocker", "text": "Pierwszy plasterek nie ma mierzalnego kryterium.", "tickets": ["#1"]},
            {"severity": "major", "text": "Rozdziel pierwszy plasterek.", "tickets": ["#1"]},
            {"severity": "nit", "text": "Drobiazg w nazwie drugiego.", "tickets": ["#2"]}]}
    if "ATRAPA: plan-review-malformed" in prompt:
        review = {"verdict": "REQUEST_CHANGES", "findings": [{"severity": "krytyczna", "text": "x", "tickets": []}]}
    with open(os.path.join(out, "plan-review.json"), "w", encoding="utf-8") as fh:
        json.dump(review, fh)
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": []}, fh)
    sys.exit(0)

if prompt.startswith("Uzupełnij istniejący plan"):
    card = re.search(r"karty (K-[0-9]+)", prompt).group(1)
    rows = json.loads(re.search(r"Oryginalna tabela: (.+)", prompt).group(1))
    os.makedirs(os.path.join(out, "tickets"), exist_ok=True)
    with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ntide-plan: 1\ntitle: Przejęty plan\ncard: " + card +
                 "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n"
                 "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for row in rows:
            fh.write("| " + " | ".join([row["id"], row["title"], row["state"], "",
                     row["agent"] or "Sonnet", row["note"] or "przekazanie", "standard", "Opus",
                     ", ".join(row["blockers"])]) + " |\n")
            with open(os.path.join(out, "tickets", row["id"].lstrip("#") + ".md"), "w", encoding="utf-8") as ticket:
                ticket.write("# " + row["title"] + "\n\nATRAPA: gotowe\n\n- [ ] Wynik.\n\n## Dla recenzenta\nSprawdź wynik.\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": ["plan.md"]}, fh)
    sys.exit(0)

if prompt.startswith("Krok planowania Lekkiego"):
    card = re.search(r"Krok planowania Lekkiego karty (K-[0-9]+)", prompt).group(1)
    if "ATRAPA: plan-question" in prompt and "Odpowiedź użytkownika:" not in prompt:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "question", "question": "Który wariant wybrać?"}, fh)
        sys.exit(0)
    if "ATRAPA: plan-error" in prompt:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "error", "reason": "awaria planującego"}, fh)
        sys.exit(0)
    os.makedirs(os.path.join(out, "tickets"), exist_ok=True)
    if "ATRAPA: plan-multi" in prompt:
        # Projekty zlecenia z polecenia: #1 w drugim projekcie, #3 w projekcie karty czeka na #1.
        other = re.findall(r"(?m)^- (\S+) — .*\(repozytorium tylko do odczytu", prompt)[0]
        with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Grupa\ncard: " + card +
                     "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery | Projekt |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | API w drugim projekcie | todo | | Sonnet | api | standard | Opus | | " + other + " |\n"
                     "| #2 | Niezależny widok | todo | | Sonnet | widok | standard | Opus | | |\n"
                     "| #3 | Klient API | todo | | Sonnet | klient | standard | Opus | #1 | |\n")
        for number in ("1", "2", "3"):
            with open(os.path.join(out, "tickets", number + ".md"), "w", encoding="utf-8") as fh:
                fh.write("# #" + number + "\n- [ ] Działa.\n## Dla recenzenta\nSprawdź.\n")
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "notes": ["plan.md"]}, fh)
        sys.exit(0)
    bad = "ATRAPA: plan-bad" in prompt
    repair = "ATRAPA: plan-repair" in prompt and ".plan-fix-" not in out
    marker = re.search(r"ATRAPA-OPIEKUN: [a-z-]+", prompt)     # PT-146: polecenie dla opiekuna w odrzuconym planie
    with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("nie plan" + (" " + marker.group(0) if marker else "") if bad or repair else
                 "---\ntide-plan: 1\ntitle: Eksport\ncard: " + card +
                 "\ntracker: local\nticket-prefix: PT\ncoordinator: claude\n---\n"
                 "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                 "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                 "| #1 | Eksport | todo | | Sonnet | standardowa praca | standard | Opus | |\n")
    with open(os.path.join(out, "tickets", "1.md"), "w", encoding="utf-8") as fh:
        fh.write("# Eksport\n**Co zbudować:** eksport\n- [ ] Eksport działa.\n## Dla recenzenta\nSprawdź format.\n")
    with open(os.path.join(out, "spec.md"), "w", encoding="utf-8") as fh:
        fh.write("# Specyfikacja\nEksport działa.\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": ["plan.md", "spec.md"]}, fh)
    sys.exit(0)

if prompt.startswith("Krok review "):
    if "ATRAPA: czekaj-review" in prompt and "Notka techniczna przekazania" not in prompt:
        time.sleep(3600)
    round_match = re.search(r"runda (\d+)", prompt)
    round_no = int(round_match.group(1)) if round_match else 1
    if "ATRAPA: review-error" in prompt and round_no == 1 and "Użytkownik wybrał innego wykonawcę" not in prompt:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "error", "reason": "awaria review"}, fh)
        sys.exit(0)
    requests = re.search(r"ATRAPA: review-requests (\d+)", prompt)
    bad = requests and round_no <= int(requests.group(1))
    if "ATRAPA: review-modifies" in prompt:
        with open(os.path.join(cwd, "review-modified.txt"), "w", encoding="utf-8") as fh:
            fh.write("nieautoryzowana zmiana\n")
    with open(os.path.join(out, "review.md"), "w", encoding="utf-8") as fh:
        fh.write("# Review\n\n" + ("MAJOR: popraw ATRAPA.md\n" if bad else "APPROVE\n"))
        if "ATRAPA: pulpit" in prompt:
            fh.write("\n## Do sprawdzenia na pulpicie\n\n- otwórz  widżet na drugim monitorze\n"
                     "- Sprawdź skróty klawiszowe\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "verdict": "REQUEST_CHANGES" if bad else "APPROVE",
                   "blockers": 0, "majors": 1 if bad else 0, "nits": 0, "notes": ["review.md"]}, fh)
    sys.exit(0)

if prompt.startswith("Domknij kartę "):
    manual = "Dodaj sekcję „Co przetestować ręcznie i jak”" in prompt
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("# Podsumowanie karty\n\nTickety zakończone.\n")
        if manual:
            fh.write("\n## Co przetestować ręcznie i jak\n\n1. Otwórz widok i sprawdź wynik.\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": ["summary.md"]}, fh)
    sys.exit(0)

if prompt.startswith("Rozwiąż konflikt scalenia "):
    if "ATRAPA: conflict-error" in prompt:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "error", "reason": "konflikt wymaga decyzji"}, fh)
        sys.exit(0)
    branch = re.search(r"Zatwierdzony ticket jest na gałęzi ([^;\s]+)", prompt).group(1)
    merged = subprocess.run(["git", "merge", "--no-ff", "--no-edit", branch], capture_output=True, text=True)
    if merged.returncode:
        paths = subprocess.check_output(["git", "diff", "--name-only", "--diff-filter=U"], text=True).splitlines()
        for path in paths:
            with open(os.path.join(cwd, path), "w", encoding="utf-8") as fh:
                fh.write("Konflikt rozwiązany w karcie.\n")
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "Merge ticket into card"], check=True)
    with open(os.path.join(out, "note.md"), "w", encoding="utf-8") as fh:
        fh.write("# Scalenie\n\nKonflikt rozwiązany i sprawdzony.\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "done", "notes": ["note.md"]}, fh)
    sys.exit(0)

def write(text):
    with open(work, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")

def commit():
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: atrapa robi ticket"], check=True)

def finish(kind, **extra):
    with open(os.path.join(out, "note.md"), "w", encoding="utf-8") as fh:
        fh.write("# Notka wyniku\n\nAtrapa zrobiła ticket.\n")
        if "ATRAPA: pulpit" in prompt:
            fh.write("\n## Do sprawdzenia na pulpicie\n\n- Otwórz widżet na drugim monitorze\n"
                     "- Przeciągnij okno\n  i sprawdź ramkę\n")
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": kind, "notes": ["note.md"], **extra}, fh)

if word == "pytanie-bez-pracy" and "Odpowiedź użytkownika:" not in prompt:
    finish("question", question="Który format daty?")
    sys.exit(0)

if word == "poza-uwagi":
    if "Wznowienie tego samego kroku" not in prompt:
        until = time.monotonic() + 30
        while not os.path.isfile(os.path.join(cwd, "release-notes")) and time.monotonic() < until:
            time.sleep(0.05)
        os.unlink(os.path.join(cwd, "release-notes"))
    word = "poza"

write("start")
if word in ("na-sygnal", "blad-local"):
    until = time.monotonic() + 120
    while not os.path.isfile(os.path.join(out, "release-agent")):
        if time.monotonic() >= until:
            finish("error", reason="Test nie zwolnił atrapy sygnałem release-agent")
            sys.exit(1)
        time.sleep(0.05)
if word == "czekaj" or (word == "czekaj-pierwszy" and "Notka techniczna przekazania" not in prompt):
    time.sleep(3600)
elif word == "limit" and "Wznowienie tego samego kroku" not in prompt:
    print(json.dumps({"type": "error", "message": "You've hit your limit"}), flush=True)
    time.sleep(3600)
elif word == "powoli":
    for i in range(count * 4):
        write(f"krok {i}")
        time.sleep(0.25)
if word == "login" and "Użytkownik polecił ponowić" not in prompt:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}), flush=True)
    sys.exit(1)
commit()
if word == "cli-zgoda":
    print(json.dumps({"type": "result", "subtype": "success", "permission_denials": [
        {"tool_name": "Bash", "tool_input": {"command": "git fetch origin"}}]}))
    sys.exit(0)
if word == "wynik-worktree" and "Instrukcja od opiekuna karty" not in prompt:
    with open(os.path.join(cwd, "result.json"), "w", encoding="utf-8") as fh:
        json.dump({"result": "done"}, fh)
    commit()
    print("Zapisano wynik: " + os.path.join(cwd, "result.json"), flush=True)
    print("GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", flush=True)
    sys.exit(0)
if word == "bez-wyniku":
    sys.exit(0)
if word == "odmowa-zapisu" and "Użytkownik polecił ponowić" not in prompt and "Instrukcja od opiekuna karty" not in prompt:
    # PT-125: piaskownica odmawia zapisu pliku wyniku; w dzienniku kroku token i zmienne środowiskowe.
    print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash", "input": {
            "command": "GITHUB_TOKEN=ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8 gh api user"}}]}}), flush=True)
    print("export TIDE_FAKE_SECRET_TOKEN=atrapa-sekret-z-env-42 HOME=" + os.environ["HOME"], flush=True)
    print("wartość bez nazwy: atrapa-sekret-z-env-42", flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Nie zapisałem wyniku.",
                      "permission_denials": [{"tool_name": "Write", "tool_input": {"file_path": result}}]}), flush=True)
    sys.exit(0)
if word == "pytanie":
    if "Odpowiedź użytkownika:" in prompt:
        finish("done")
    else:
        finish("question", question="Który format daty?")
elif word == "pytanie-zawsze":
    # PT-280: pytanie w każdej próbie — kolejne wznowienia tego samego kroku (zgody liczone przy każdym).
    finish("question", question="Który format daty?")
elif word in ("pytanie-opcje", "pytanie-zle-opcje"):
    if "Odpowiedź użytkownika:" in prompt:
        finish("done")
    elif word == "pytanie-opcje":
        finish("question", question="Który format daty?", options=[
            {"title": "ISO 8601", "effect": "Daty w UTC, bez stref.", "recommended": True},
            {"title": "Lokalny", "effect": "Daty jak w interfejsie."}])
    else:
        finish("question", question="Który format daty?", options=[
            {"title": "A", "recommended": True}, {"title": "B", "recommended": True}])
elif word == "odmowa-opiekun":
    finish("denied", command="make install", reason="ATRAPA-OPIEKUN: retry")
elif word == "zgoda":
    if "- git fetch origin\n" in prompt or "Użytkownik odmówił zgody" in prompt:
        finish("done")
    else:
        finish("denied", command="git fetch origin")
elif word in ("zgoda-zabroniona", "zgoda-zabroniona-cytat", "zgoda-zabroniona-ukosnik", "zgoda-zabroniona-potok"):
    if "Użytkownik odmówił zgody" in prompt:
        finish("done")
    else:
        command = {"zgoda-zabroniona": "make install", "zgoda-zabroniona-cytat": 'make "install"',
                   "zgoda-zabroniona-ukosnik": r"make \install",
                   "zgoda-zabroniona-potok": "pwd |& make install"}[word]
        finish("denied", command=command)
elif word == "siec":
    # PT-172: odmowa proxy pudełka jest wynikiem denied z poleceniem „network: <domena>”.
    if "dopisał tę domenę do profilu karty" in prompt or "nie dopisał domeny" in prompt:
        finish("done")
    else:
        finish("denied", command="network: api.sejm.gov.pl")
elif word == "zgoda-lancuch":
    if "Wznowienie tego samego kroku" in prompt:
        finish("done")
    else:
        step_dir = os.path.dirname(out)
        finish("denied", command=f"ls -la {step_dir}/in {step_dir}/out; git fetch origin && git push origin | head; "
                                 "echo $(make install)")
elif word == "zgoda-katalog-kroku":
    if "Wznowienie tego samego kroku" in prompt:
        finish("done")
    else:
        step_dir = os.path.dirname(out)
        finish("denied", command=f"ls -la {step_dir}/in {step_dir}/out")
elif word == "zgoda-dluga":
    if "Użytkownik odmówił zgody" in prompt:
        finish("done")
    else:
        finish("denied", command="git fetch origin " + "x" * 300)
elif word in ("czeka-na", "czeka-na-blad"):
    # 06.11: krok potrzebuje pracy innego ticketu karty. `czeka-na` zgłasza `waitFor` w wyniku; `czeka-na-blad` pisze
    # tylko błąd z nazwą ticketu (jak PT-191) — o czekaniu decyduje użytkownik w oknie.
    target = re.search(r"ATRAPA-CZEKA: (\S+)", prompt).group(1)
    if "jest scalony do gałęzi karty" in prompt:
        # PT-260: dowód pobrania pracy blokera, a nie samo rozpoznanie zdania w kartce wznowienia.
        branch = re.search(r"git merge --no-edit ([^`]+)`", prompt).group(1)
        subprocess.run(["git", "merge", "--no-edit", branch], check=True)
        with open(os.path.join(cwd, "ATRAPA-1.md"), encoding="utf-8") as fh:
            dependency = fh.read()
        with open(os.path.join(out, "dependency-read.txt"), "w", encoding="utf-8") as fh:
            fh.write(dependency)
        finish("done")
    elif word == "czeka-na":
        finish("error", reason=f"brak modułu z {target}", waitFor=target)
    else:
        finish("error", reason=f"zatwierdzony bloker {target} nadal niespełniony; nie ponawiać przed integracją {target}")
elif word == "blad-raz":
    finish("done" if "Krok ponowiony automatycznie" in prompt else "error", reason="awaria testowa")
elif word in ("blad-dwa", "blad-zmien"):
    if prompt.startswith("Krok poprawek") or "Użytkownik polecił ponowić" in prompt or "Użytkownik wybrał innego wykonawcę" in prompt:
        finish("done")
    else:
        finish("error", reason="awaria testowa")
elif word == "blad-opiekun":
    # PT-143: błąd po każdej próbie z powodem z linii „ATRAPA-POWOD: …” (tam też polecenie dla atrapy opiekuna).
    if "Użytkownik rozstrzygnął poprawę planu po odesłaniu przez opiekuna" in prompt:
        # PT-155: wznowiony autor rzeczywiście korzysta z nowej pracy dostępnej w gałęzi karty.
        branch = re.search(r"Uwzględnij zmiany z gałęzi karty (\S+) w bieżącym worktree", prompt).group(1)
        migrated = subprocess.check_output(["git", "show", branch + ":MIGRACJA.txt"], text=True)
        with open(os.path.join(cwd, "MIGRACJA.txt"), "w", encoding="utf-8") as fh:
            fh.write(migrated)
        write("eksport po migracji: " + migrated.strip())
        commit()
        finish("done")
    elif "Użytkownik polecił ponowić" in prompt or "Użytkownik wybrał innego wykonawcę" in prompt:
        finish("done")
    else:
        reason = re.search(r"ATRAPA-POWOD: (.+)", prompt)
        reason = reason.group(1).strip() if reason else "awaria testowa"
        # PT-153: powód (z sekretami z testu) trafia też do dziennika kroku — kontekst opiekuna musi go zamaskować.
        print(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Test pada: " + reason}]}}), flush=True)
        finish("error", reason=reason)
elif word == "migracja":
    with open(os.path.join(cwd, "MIGRACJA.txt"), "w", encoding="utf-8") as fh:
        fh.write("format-v2\n")
    commit()
    finish("done")
elif word == "srodowisko":
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "question",
                   "question": "Czy możesz uruchomić testy z Dockerem/Testcontainers poza piaskownicą?"}, fh)
elif word == "poza-kolejne":
    # Każde wznowienie ponownie prosi o to samo polecenie: test granic zgody ticketu.
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "outside", "command": "echo zewnetrzne-ok",
                   "reason": "kolejna prośba ticketu"}, fh)
elif word == "poza":
    if "uruchomił poza piaskownicą" in prompt and "zewnetrzne-ok" in prompt:
        finish("done")
    elif "nie zgodził się uruchomić poza piaskownicą" in prompt:
        finish("done")
    else:
        with open(result, "w", encoding="utf-8") as fh:
            json.dump({"result": "outside", "command": "echo zewnetrzne-ok",
                       "reason": "potrzebna sieć do instalacji"}, fh)
elif word == "smieci":
    junk = os.path.join(cwd, "debug_tmp.py")
    if "worktree ticketu ma niezacommitowane" in prompt:
        if os.path.exists(junk):
            os.remove(junk)
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "chore: atrapa sprząta"], check=True)
    elif not prompt.startswith("Krok review"):
        with open(junk, "w") as fh:
            fh.write("# diagnostyka\n")
    finish("done")
elif word == "dane-zostaja":
    # Autor zostawia nieśledzone dane wejściowe i po prośbie o porządek ich nie usuwa (04.11, K-43/BO-9).
    if not prompt.startswith("Krok review"):
        with open(os.path.join(cwd, "dane-wejsciowe.tar"), "w") as fh:
            fh.write("x\n")
    finish("done")
elif word == "blad-local":
    finish("error", reason="awaria testowa")
else:
    finish("done")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": sid}))
'''

SYSTEMD_RUN = r'''#!/usr/bin/python3
import os, sys
args = sys.argv[1:]
unit = next((a.split("=", 1)[1] for a in args if a.startswith("--unit=")), "")
argv = args[args.index("--") + 1:]
if unit:
    folder = os.path.join(os.environ["XDG_RUNTIME_DIR"], "fake-units")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, unit + ".pid"), "w") as fh:
        fh.write(str(os.getpid()))
os.execvp(argv[0], argv)
'''

SYSTEMCTL = r'''#!/usr/bin/python3
import os, signal, sys
args = [a for a in sys.argv[1:] if a not in ("--user", "--quiet") and not a.startswith("--kill-whom")]
unit = args[-1][:-len(".scope")] if args[-1].endswith(".scope") else args[-1]
path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/nonexistent"), "fake-units", unit + ".pid")
try:
    pid = int(open(path).read())
except (OSError, ValueError):
    pid = 0

def alive():
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False

if args[0] == "is-active":
    sys.exit(0 if pid and alive() else 3)
if args[0] == "kill":
    name = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--signal=")), "SIGTERM")
    if pid and alive():
        try:
            os.killpg(pid, getattr(signal, name))
        except ProcessLookupError:
            pass
    sys.exit(0)
sys.exit(0)
'''

KONSOLE = """#!/bin/bash
echo "konsole $*" >> "$TIDE_FAKE_LOG"
if [ -n "$TIDE_FAKE_KONSOLE_SERVICE" ]; then
    exec /usr/bin/python3 -s "$TIDE_FAKE_KONSOLE" --record-task "$TIDE_FAKE_KONSOLE_SERVICE" "${@: -1}"
fi
"""


def install(folder: str) -> dict:
    """Atrapy w `folder`; zwraca {nazwa: ścieżka}."""
    os.makedirs(folder, exist_ok=True)
    out = {}
    for name, body in (("claude", CLAUDE), ("codex", CLAUDE), ("systemd-run", SYSTEMD_RUN), ("systemctl", SYSTEMCTL),
                       ("konsole", KONSOLE)):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        out[name] = path
    return out


def unit_pid(runtime_dir: str, unit: str) -> int:
    try:
        with open(os.path.join(runtime_dir, "fake-units", unit + ".pid"), encoding="utf-8") as fh:
            return int(fh.read())
    except (OSError, ValueError):
        return 0


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return False
