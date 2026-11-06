#!/usr/bin/env python3
"""PT-186: odczyt rzeczywistego StackReport i stanu wejściowego repozytorium, bez jego zmiany.

Nie zatwierdza profilu ani nie uruchamia restore. Wynik zapisuje wyłącznie do wskazanego katalogu kroku.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "daemon"))

from tide_daemon.box import profile as bp
from tide_daemon.tasks import stack


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    report = stack.detect(str(repo))
    proposal = stack.propose_profile(report, "infracore")
    items = proposal.pop("items")
    candidate = bp.parse((ROOT / "docs/box-profiles/infracore.yaml").read_text(encoding="utf-8"))
    data = {"repo": str(repo), "head": git(repo, "rev-parse", "HEAD").decode().strip(),
            "status": git(repo, "status", "--porcelain=v1").decode(),
            "trackedDiffSha256": hashlib.sha256(git(repo, "diff", "--binary", "HEAD")).hexdigest(),
            "indexDiffSha256": hashlib.sha256(git(repo, "diff", "--cached", "--binary")).hexdigest(),
            "report": report.to_dict(), "proposal": bp.validate(proposal), "items": items,
            "candidate": candidate,
            "detectedSdkOutsideCandidate": [v for v in report.dotnet.get("sdk", [])
                                            if not any(p.startswith(v + ".") for p in candidate["dotnet"]["sdk"])]}
    # Nie tworzymy katalogów poza kontraktem kroku; rodzic musi już istnieć.
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "stacks": report.stacks,
                      "sdk": report.dotnet.get("sdk", []), "images": report.testcontainers_images,
                      "localFeeds": [s["url"] for s in report.nuget_sources if s.get("local")],
                      "detectedSdkOutsideCandidate": data["detectedSdkOutsideCandidate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
