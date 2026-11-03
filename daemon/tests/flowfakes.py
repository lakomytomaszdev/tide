"""Atrapy dla testów przepływu karty (PT-58): `claude -p`, `systemd-run --user --scope` i `systemctl --user`.

- `claude` czyta kartkę ze stdin (polecenie kroku), zapisuje ją z argv w `<katalog wyjściowy>/fake-call.json`, dopisuje
  dziennik sesji w `$HOME/.claude/projects/<katalog>/<id>.jsonl` (jak prawdziwy program) i robi to, co każe linia
  „ATRAPA: …” w opisie karty: `gotowe` (plik, commit, plik wyniku i notka), `bez-wyniku` (commit bez pliku wyniku),
  `czekaj` (plik bez commita i sen — żyje, ale nic nie robi), `powoli N` (N sekund dopisywania, potem jak `gotowe`),
  `pytanie` (wynik „pytanie”), `pytanie-opcje` (pytanie z poprawnymi `options[]`), `pytanie-zle-opcje` (kilka poleconych). Ścieżkę pliku wyniku bierze z kartki (linia „Plik wyniku: …”), nie z ustawień.
- `systemd-run` zapisuje pid pod nazwą jednostki w `$XDG_RUNTIME_DIR/fake-units/` i robi `exec` programu — jak
  `--scope`, w którym program jest procesem jednostki; proces żyje dalej po końcu usługi, która go uruchomiła.
- `systemctl --user is-active --quiet <j>.scope` (0 — żyje, 3 — nie) i `kill --signal=… <j>.scope` (sygnał do grupy
  procesu jednostki) według tego katalogu.
Prawdziwy menedżer systemd nie jest dotykany.
"""
import os
import stat

CLAUDE = r'''#!/usr/bin/python3
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
if args[:3] == ["mcp", "list", "--json"]:
    print("[]")
    sys.exit(0)
def opt(name):
    return args[args.index(name) + 1] if name in args else ""
sid = opt("--session-id") or opt("--resume")
prompt = sys.stdin.read()
cwd = os.getcwd()
result = re.search(r"Plik wyniku: (\S+)", prompt)
result = result.group(1) if result else ""
out = os.path.dirname(result) if result else os.path.join(os.path.dirname(cwd), "missing-out")
os.makedirs(out, exist_ok=True)
with open(os.path.join(out, "fake-call.json"), "w", encoding="utf-8") as fh:
    json.dump({"argv": args, "prompt": prompt, "cwd": cwd, "pid": os.getpid(), "env": dict(os.environ)}, fh)
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
    if "Dodaj ticket" in prompt:
        rows.append({**rows[-1], "workId": "#3", "title": "Dodatkowy ticket", "blockers": []})
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
            fh.write(row["body"] + ("" if row is rows[0] and row["workId"].startswith("PT-") else "\n## Dla recenzenta\nSprawdź wymagany format.\n"))
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
    bad = "ATRAPA: plan-bad" in prompt
    repair = "ATRAPA: plan-repair" in prompt and ".plan-fix-" not in out
    with open(os.path.join(out, "plan.md"), "w", encoding="utf-8") as fh:
        fh.write("nie plan" if bad or repair else "---\ntide-plan: 1\ntitle: Eksport\ncard: " + card +
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

write("start")
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
if word == "bez-wyniku":
    sys.exit(0)
if word == "odmowa-zapisu" and "Użytkownik polecił ponowić" not in prompt:
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
elif word == "blad-raz":
    finish("done" if "Krok ponowiony automatycznie" in prompt else "error", reason="awaria testowa")
elif word in ("blad-dwa", "blad-zmien"):
    if prompt.startswith("Krok poprawek") or "Użytkownik polecił ponowić" in prompt or "Użytkownik wybrał innego wykonawcę" in prompt:
        finish("done")
    else:
        finish("error", reason="awaria testowa")
elif word == "srodowisko":
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"result": "question",
                   "question": "Czy możesz uruchomić testy z Dockerem/Testcontainers poza piaskownicą?"}, fh)
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
elif word == "blad-local":
    time.sleep(0.5)
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
