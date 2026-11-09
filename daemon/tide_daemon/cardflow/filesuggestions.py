"""Ograniczona heurystyka lokalnych drzew i historii; bez odczytu wskazanych plików z dysku."""
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache

MAX_PATHS = 20
MAX_BRANCHES = 100
MAX_COMMITS = 200
MAX_SECONDS = 5


def suggest_files(git, repo: str, description: str, text: str) -> dict:
    deadline = time.monotonic() + MAX_SECONDS
    warnings = []
    partial = False
    candidates = []
    trees = []
    paths = []

    def warn(message):
        if message not in warnings and len(warnings) < 20:
            warnings.append(message)

    # Gałęzie często wskazują ten sam commit. Cztery ostatnie odczyty mieszczą
    # drzewo i historię bez trzymania wyników wszystkich 100 drzew w pamięci.
    @lru_cache(maxsize=4)
    def read(*args):
        result = git.run(repo, "--no-replace-objects", "--literal-pathspecs", *args,
                         timeout=max(.001, deadline - time.monotonic()))
        if not result.ok:
            if time.monotonic() >= deadline:
                raise TimeoutError
            raise OSError
        return result.out

    def run(*args):
        if time.monotonic() >= deadline:
            raise TimeoutError
        return read(*args)

    try:
        # Cytaty zachowują spacje i metaznaki jako dane. Nie interpretujemy powłoki ani globów.
        tokens = re.compile(r"`([^`\n]+)`|'([^'\n]+)'|\"([^\"\n]+)\"|([^\s`'\"<>(),;]+)")
        for match in tokens.finditer(description + "\n" + text):
            if time.monotonic() >= deadline:
                raise TimeoutError
            token = next(value for value in match.groups() if value is not None)
            if match.group(4) is not None:
                token = token.rstrip(".:!")
            if not ("/" in token or "." in token or match.group(4) is None):
                continue
            if (len(token) > 4096 or "\x00" in token or any(c in token for c in "*?[]")
                    or ".." in token.split("/") or "://" in token):
                warn("Pominięto niebezpieczne lub wzorcowe wskazanie ścieżki.")
                continue
            if os.path.isabs(token):
                root = os.path.abspath(repo)
                if os.path.commonpath((root, token)) != root:
                    warn("Pominięto bezwzględną ścieżkę spoza repozytorium.")
                    continue
                token = os.path.relpath(token, root)
                if "/" not in token:
                    token = "./" + token
            if token.startswith("./") and "/" in token[2:]:
                token = token[2:]
            if token in ("", ".") or token in paths:
                continue
            if len(paths) == MAX_PATHS:
                partial = True
                warn("Ograniczono analizę do 20 ścieżek.")
                break
            paths.append(token)
        if paths:
            listed = run("for-each-ref", "--sort=refname", "--sort=-committerdate",
                         f"--count={MAX_BRANCHES + 1}", "--format=%(refname)%00%(objectname)%00%(symref)%00%(objecttype)",
                         "refs/heads/", "refs/remotes/")
            branches = []
            for line in listed.splitlines():
                ref, sha, symref, kind = line.split("\x00")
                if symref or kind != "commit":
                    continue
                branches.append((ref, sha))
            if len(listed.splitlines()) > MAX_BRANCHES:
                partial = True
                warn("Ograniczono analizę do 100 gałęzi o najnowszych commitach.")
            direct_paths = {p.removeprefix("./") for p in paths if "/" in p}
            basenames = {p: set() for p in paths if "/" not in p}
            for ref, sha in branches[:MAX_BRANCHES]:
                tree = run("ls-tree", "-r", "-z", "--name-only", sha, "--")
                relevant = set()
                for path in tree.split("\x00"):
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    if path in direct_paths:
                        relevant.add(path)
                    name = path.rsplit("/", 1)[-1]
                    if name in basenames:
                        # Dwa różne dopasowania już dowodzą niejednoznaczności; nie budujemy indeksu drzewa.
                        matches = basenames[name]
                        if len(matches) < 2:
                            matches.add(path)
                        if path in matches:
                            relevant.add(path)
                trees.append((ref, sha, relevant))
            # Skrót musi być jednoznaczny w całym rozpatrywanym zbiorze, nie tylko na jednej gałęzi.
            resolved = []
            for path in paths:
                if "/" in path:
                    resolved.append(path.removeprefix("./"))
                    continue
                matches = basenames[path]
                if len(matches) == 1:
                    resolved.extend(matches)
                elif len(matches) > 1:
                    warn("Niejednoznaczna nazwa pliku: " + path[:256] + ". Podaj ścieżkę od katalogu repozytorium.")
            resolved = set(resolved)
            for ref, sha, tree in trees:
                wanted = resolved & tree
                if not wanted:
                    continue
                # Limit dotyczy wszystkich commitów gałęzi, a nie 200 trafień po filtrze ścieżek.
                commits = run("rev-list", f"--max-count={MAX_COMMITS}", sha, "--").splitlines()
                if len(commits) >= MAX_COMMITS:
                    partial = True
                    warn("Ograniczono historię do 200 commitów na gałąź; starsze dotknięcia mogą być niewidoczne.")
                # Bez dalszego przechodzenia historii; git zwraca zmiany tylko wskazanych plików.
                history = run("log", "--no-color", "--no-show-signature", "--no-ext-diff", "--no-textconv",
                              "--no-renames", "--root", "-m", "--no-walk=unsorted", "-z", "--name-only",
                              "--format=%x00%x00%H%x00%ct%x00", *commits, "--", *sorted(wanted))
                headers = list(re.finditer(r"\x00\x00([0-9a-f]{40,64})\x00([0-9]+)\x00", history))
                evidence = {}
                for index, header in enumerate(headers):
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    block = history[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(history)]
                    changed = set(block.removeprefix("\x00").removeprefix("\n").split("\x00"))
                    stamp = int(header[2])
                    for path in wanted & changed:
                        if path not in evidence or stamp > evidence[path]["timestamp"]:
                            evidence[path] = {"path": path, "commit": header[1], "timestamp": stamp,
                                              "time": datetime.fromtimestamp(stamp, timezone.utc).isoformat()}
                if not evidence:
                    continue
                remote = ref.startswith("refs/remotes/")
                name = ref[len("refs/remotes/"):] if remote else ref[len("refs/heads/"):]
                candidates.append({"ref": ref, "name": name, "commit": sha,
                                   "kind": "remote" if remote else "local",
                                   "remote": name.partition("/")[0] if remote else "",
                                   "selectable": True, "reason": "", "source": "files", "strength": "heuristic",
                                   "evidence": [evidence[p] for p in sorted(evidence)],
                                   "matchedPaths": len(evidence),
                                   "lastTouch": max(e["timestamp"] for e in evidence.values())})
    except TimeoutError:
        partial = True
        warn("Przekroczono 5 sekund analizy. Wynik jest częściowy; brak wskazania nie wyklucza gałęzi.")
    except (OSError, ValueError):
        partial = True
        warn("Nie można odczytać części lokalnej historii. Wynik nie wyklucza innych gałęzi.")
    candidates.sort(key=lambda c: (-c["matchedPaths"], -c["lastTouch"], c["ref"]))
    previous = None
    rank = 0
    for index, candidate in enumerate(candidates):
        score = (candidate["matchedPaths"], candidate["lastTouch"])
        if score != previous:
            rank = index + 1
        candidate["rank"] = rank
        previous = score
    if partial:
        warnings.append("Wynik częściowy: brak wskazania nie wyklucza gałęzi. Możesz wybrać bazę ręcznie.")
    warnings.append("Heurystyka lokalna: do 20 ścieżek, 100 najnowszych gałęzi, 200 commitów na gałąź, 5 sekund. "
                    "To podpowiedź, nie pewne ustalenie bazy; jednakowa pozycja oznacza remis.")
    return {"candidates": candidates, "warnings": warnings,
            "analysis": {"partial": partial, "paths": len(paths), "branches": len(trees),
                         "limits": {"paths": MAX_PATHS, "branches": MAX_BRANCHES,
                                    "commitsPerBranch": MAX_COMMITS, "seconds": MAX_SECONDS}}}
