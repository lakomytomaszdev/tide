"""Uruchamiany z kopii w izolowanym katalogu; bez importów z checkoutu."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
from tide_daemon import resources
from tide_daemon.cardflow import handoff
from tide_daemon.tasks import agentcmd

base = Path.cwd()
repo = base / "projekt ze spacjami"
repo.mkdir()
subprocess.run(["git", "init", "-q", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "-c", "user.name=Tester", "-c", "user.email=test@example.com",
                "commit", "--allow-empty", "-qm", "fixture"], check=True)
worktree = base / "krok ze spacjami"
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-qb", "probe", str(worktree)], check=True)
reader = base / "czytnik"
reader.write_text('''#!/usr/bin/python3
import hashlib, json, pathlib, re, sys
text = sys.stdin.read()
standard = pathlib.Path(re.search(r"Standard planu Tide: (.+) \\(wersja", text).group(1))
data = standard.read_bytes()
assert b"tide-plan: 1" in data
out = pathlib.Path(re.findall(r"^Plik wyniku: (.+)$", text, re.M)[-1]).parent
entries = json.loads((out.parent / "in/skills.json").read_text())
skill = pathlib.Path(next(e["path"] for e in entries if e["name"] == "to-spec"))
assert "procedura kontrolna" in skill.read_text()
assert (skill.parent / "references/probe.txt").read_text() == "zasób kontrolny"
print(json.dumps({"standard": str(standard), "sha256": hashlib.sha256(data).hexdigest(),
                  "skill": str(skill), "read": True}))
''')
reader.chmod(0o755)
proofs = []
for tool, folder in (("claude", ".claude"), ("codex", ".agents")):
    skill = Path(os.environ["HOME"]) / folder / "skills/to-spec"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: to-spec\ndescription: Kontrola\n---\nprocedura kontrolna")
    (skill / "references/probe.txt").write_text("zasób kontrolny")
    for resume in (False, True):
        step_dir = base / (tool + ".step")
        prompt = handoff.ticket_card(card_id="K-1", title="Kontrola", description="Kontrola", project="proj",
            ticket="PT-1", branch="ticket/pt-1", card_branch="card/k-1", worktree=str(worktree),
            out_dir=str(step_dir / "out"))
        cmd = agentcmd.background(agentcmd.Step(tool=tool, binary=str(reader), prompt=prompt,
            worktree=str(worktree), repo=str(repo), step_dir=str(step_dir), step_id="probe-" + tool,
            tmp_root=str(base / "agent-tmp"), base_env=dict(os.environ), codex_mcp=("private-server",),
            session_id="11111111-2222-3333-4444-555555555555", resume=resume,
            read_dirs=(str(resources.resolve("plan-standard").parent),)))
        cmd.prepare()
        if tool == "claude":
            assert "--setting-sources=" in cmd.argv and "--strict-mcp-config" in cmd.argv
            plugin = Path(cmd.argv[cmd.argv.index("--plugin-dir") + 1])
            assert str(skill / "SKILL.md") in (plugin / "skills/to-spec/SKILL.md").read_text()
        else:
            assert "mcp_servers.private-server.enabled=false" in cmd.argv
            assert not any(str(skill) in arg for arg in cmd.argv)
        read = subprocess.run(cmd.argv, input=cmd.stdin, cwd=cmd.cwd, env=cmd.env,
                              text=True, capture_output=True, check=True)
        proof = json.loads(read.stdout)
        proof.update(tool=tool, resume=resume)
        proofs.append(proof)
print(json.dumps(proofs, ensure_ascii=False))
