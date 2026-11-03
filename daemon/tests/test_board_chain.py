"""Łańcuch poleceń rozłożony na pojedyncze polecenia do zgody (PT-124): podział, flagi i powód blokady."""
import unittest

from tide_daemon.board import chain, commands

DENIED = ["make install"]
STEP = "/home/user/DevWorkspace/plasma-tide-worktrees/k-33-tide-work-closing-mvp.plan-review-K-33-plan-review-58.step"
LONG_TAIL = " ".join(f"docs/plik-{i}.md" for i in range(40))

# Podział: (łańcuch, oczekiwane polecenia w kolejności wykonania; None — nie da się rozłożyć).
SPLITS = [
    ("git status && make test", ["git status", "make test"]),
    ("git fetch || git status", ["git fetch", "git status"]),
    ("ls docs | head -50", ["ls docs", "head -50"]),
    ("pwd |& make install", ["pwd", "make install"]),
    ("pwd; ls; wc -l a", ["pwd", "ls", "wc -l a"]),
    ("sleep 1 & make install", ["sleep 1", "make install"]),
    ("pwd\nmake install", ["pwd", "make install"]),
    ("echo $(make install)", ["make install", "echo $(make install)"]),
    ("echo $(cat $(make install))", ["make install", "cat $(make install)", "echo $(cat $(make install))"]),
    ("echo `make install`", ["make install", "echo `make install`"]),
    ("diff <(make install) x", ["make install", "diff <(make install) x"]),
    ('echo "x $(make install) y"', ["make install", 'echo "x $(make install) y"']),
    ('echo "a; b && c"', ['echo "a; b && c"']),
    ("echo '$(make install); x'", ["echo '$(make install); x'"]),
    ("git status && \"make\" 'install'", ["git status", "\"make\" 'install'"]),
    (r"git status && make \install", ["git status", r"make \install"]),
    ("bash -c \"pwd; make install\"", ["bash -c \"pwd; make install\"", "pwd", "make install"]),
    ("sh -c 'make install'", ["sh -c 'make install'", "make install"]),
    ("LC_ALL=C bash -lc 'make install'", ["LC_ALL=C bash -lc 'make install'", "make install"]),
    ('eval "make install"', ['eval "make install"', "make install"]),
    ("(cd x && make install)", ["cd x", "make install"]),
    ("{ ls; make install; }", ["ls", "make install"]),
    ("if make install; then echo ok; fi", ["make install", "echo ok"]),
    ("! make install", ["make install"]),
    ("ls 2>&1 | head", ["ls 2>&1", "head"]),
    ("ls &>/dev/null; pwd", ["ls &>/dev/null", "pwd"]),
    ("echo ${X:-$(make install)}", ["make install", "echo ${X:-$(make install)}"]),
    ("git log --oneline -3 -- docs/agent-plan-standard.md", ["git log --oneline -3 -- docs/agent-plan-standard.md"]),
    ("rg -n x " + LONG_TAIL + " && make install", ["rg -n x " + LONG_TAIL, "make install"]),
    ('echo "bez pary', None),
    ("echo 'bez pary", None),
    ("echo $(make install", None),
    ("ls )", None),
    ("echo `make install", None),
]

A1 = (f"ls -la {STEP}/in {STEP}/out; ls docs | head -50; git log --oneline -3 -- docs/agent-plan-standard.md; "
      "git branch --list 'codex/k28*' 'claude/k-29*' 'fix/plan-ticket*'")


class SplitTest(unittest.TestCase):
    def test_table(self):
        for command, expected in SPLITS:
            with self.subTest(command=command[:60]):
                self.assertEqual(chain.split(command), expected)

    def test_long_chain_over_command_limit_still_splits(self):
        self.assertGreater(len(A1), commands.MAX_COMMAND)
        self.assertEqual(len(chain.split(A1)), 5)

    def test_too_many_commands_cannot_be_split(self):
        self.assertIsNone(chain.split("; ".join(["pwd"] * (chain.MAX_PIECES + 1))))


class DescribeTest(unittest.TestCase):
    def test_forbidden_comes_only_from_is_forbidden_for_every_piece(self):
        for command, expected in SPLITS:
            for item in chain.describe(command, denied=DENIED, step_dir=STEP):
                with self.subTest(command=command[:60], piece=item["text"][:60]):
                    self.assertEqual(item["forbidden"], commands.is_forbidden(item["text"], DENIED))
                    if not item["canAllow"]:
                        self.assertTrue(item["reason"], "zablokowane polecenie zawsze ma powód")
                    if item["forbidden"]:
                        self.assertFalse(item["canAllow"])

    def test_forbidden_hidden_in_subshell_or_quoting_is_marked_with_project_rule(self):
        for command in ("echo $(make install)", 'echo "$(make install)"', "echo `make install`",
                        "bash -c \"pwd; make install\"", "sh -c 'make install'", "pwd |& make install",
                        "git status && \"make\" 'install'", r"git status && make \install",
                        "(cd x && make install)", "if make install; then echo ok; fi",
                        "diff <(make install) x", "pwd; make install PREFIX=/tmp/x"):
            with self.subTest(command=command):
                items = chain.describe(command, denied=DENIED, step_dir=STEP)
                ruled = [i for i in items if i["reason"] == "Zabronione w projekcie regułą „make install”."]
                self.assertTrue(ruled, items)
                self.assertTrue(all(i["forbidden"] and not i["canAllow"] for i in ruled))

    def test_no_builtin_rules_without_project_denials(self):
        items = chain.describe("pwd && make install", denied=[], step_dir=STEP)
        self.assertEqual([(i["text"], i["forbidden"], i["canAllow"]) for i in items],
                         [("pwd", False, True), ("make install", False, True)])

    def test_scenario_a1_flags_and_own_step_reads(self):
        items = chain.describe(A1, denied=DENIED, step_dir=STEP)
        self.assertEqual([i["text"] for i in items],
                         ["ls docs", "head -50", "git log --oneline -3 -- docs/agent-plan-standard.md",
                          "git branch --list 'codex/k28*' 'claude/k-29*' 'fix/plan-ticket*'"],
                         "odczyt własnego <krok>/in i <krok>/out nie jest poleceniem do zgody")
        self.assertTrue(all(i["readOnly"] and i["canAllow"] and i["preselected"] and not i["forbidden"]
                            and i["reason"] == "" for i in items))

    def test_own_step_reads_only_inside_in_and_out(self):
        for command, own in ((f"ls {STEP}/in", True), (f"cat {STEP}/out/note.md", True),
                             (f"ls -la {STEP}/in {STEP}/out", True), (f"head -n 5 {STEP}/in/prompt.txt", True),
                             (f"cat {STEP}/in/../../secret", False), (f"cat {STEP}/inne/x", False),
                             (f"cat {STEP}/out/x /etc/passwd", False), (f"rm {STEP}/out/x", False),
                             (f"cat {STEP}/out/$(make install)", False), ("ls", False), ("cat in/prompt.txt", False)):
            with self.subTest(command=command):
                self.assertEqual(chain.own_step_read(command, STEP), own)

    def test_step_dir_and_read_only_flags(self):
        items = {i["text"]: i for i in chain.describe(
            f"mkdir -p {STEP}/out/x; rm -rf build; git branch -D old; sort -o out.txt a; cat /etc/hosts",
            denied=DENIED, step_dir=STEP)}
        self.assertEqual({t: (i["readOnly"], i["stepDir"], i["preselected"]) for t, i in items.items()}, {
            f"mkdir -p {STEP}/out/x": (False, True, True),
            "rm -rf build": (False, False, False),
            "git branch -D old": (False, False, False),
            "sort -o out.txt a": (False, False, False),
            "cat /etc/hosts": (True, False, True),
        })

    def test_step_dir_needs_every_argument_inside_the_step(self):
        """Review PT-124 r1 MAJOR-1: jedna ścieżka w kroku obok argumentu spoza niego nie daje „katalog kroku"."""
        cases = {
            f"rm -rf {STEP}/out/a build": False,
            f"cp {STEP}/out/a secrets.txt": False,
            f"sort -o result.txt {STEP}/out/a": False,
            f"sort -oresult.txt {STEP}/out/a": False,
            f"cp --target-directory=build {STEP}/out/a": False,
            f"cp --target-directory build {STEP}/out/a": False,
            f"cp {STEP}/out/a ../poza": False,
            f"cp {STEP}/out/a {STEP}/../poza": False,
            f"cp {STEP}/out/a ~/x": False,
            f"tee {STEP}/out/a -": False,
            f"rm -r {STEP}/out/a {STEP}/out/b": True,
            f"cp -- {STEP}/out/a {STEP}/out/b": True,
            f"sort -o {STEP}/out/r {STEP}/out/a": True,
            f"cp --target-directory={STEP}/out {STEP}/in/a": True,
            "mkdir -p": False,
        }
        for command, in_step in cases.items():
            with self.subTest(command=command.replace(STEP, "<krok>")):
                item = chain.describe(f"pwd && {command}", denied=DENIED, step_dir=STEP)[1]
                self.assertEqual(item["stepDir"], in_step)
                self.assertEqual(item["preselected"], in_step, "bez „katalog kroku” i „tylko czyta” — bez zaznaczenia")

    def test_read_only_excludes_write_and_exec_flags_of_always_allowed_tools(self):
        """Review PT-124 r1 MAJOR-2: narzędzia z ALWAYS_ALLOWED z flagą zapisu/uruchamiania nie są „tylko czyta"."""
        cases = {
            "rg --pre ./run.sh x": False, "rg --pre=./run.sh x": False, "rg --hostname-bin=./h x": False,
            "rg -n x src": True,
            "git diff --output=patch.txt": False, "git log --output patch.txt": False,
            "git show --out=patch.txt HEAD": False, "git diff --ext-diff": False, "git log -p --textconv": False,
            "git log --oneline -3": True, "git diff --stat HEAD~1": True,
            "git log --output-indicator-new=+ -p": True,
            "tree -o out.txt": False, "tree -ao out.txt": False, "tree -R -H x": False, "tree -L 2": True,
            "file -C -m magic": False, "file -m magic x": False, "file --compile": False, "file -b x": True,
            "sort -o out.txt a": False, "sort -ro out.txt a": False, "sort --out=out.txt a": False,
            "sort --compress-program=gzip a": False, "sort -n a": True,
            "git branch nowa": False, "git branch -D old": False, "git branch --track x origin/x": False,
            "git branch -a": True, "git branch": True, "git branch --list 'fix/*'": True,
            "git branch -l 'fix/*'": True, "git branch --show-current": True,
            "uniq a b": False, "uniq -c a": True,
            "cat /etc/hosts": True, "make test": False,
        }
        for command, read_only in cases.items():
            with self.subTest(command=command):
                item = chain.describe(f"pwd && {command}", denied=DENIED, step_dir=STEP)[1]
                self.assertEqual(item["text"], command)
                self.assertEqual(item["readOnly"], read_only)
                self.assertEqual(item["preselected"], read_only)

    def test_own_step_read_with_write_flag_is_listed(self):
        items = chain.describe(f"pwd && tree -o {STEP}/out/t {STEP}/in", denied=DENIED, step_dir=STEP)
        self.assertEqual([i["text"] for i in items], ["pwd", f"tree -o {STEP}/out/t {STEP}/in"])
        self.assertFalse(items[1]["readOnly"])

    def test_wrapped_forbidden_command_is_shown_and_outer_cannot_be_allowed(self):
        """Review PT-124 r1 NIT-1: `nohup make install` nie omija zakazu projektu `make install`."""
        for command in ("nohup make install", "command make install", "nice -n 5 make install", "nice -10 make install",
                        "timeout 5 make install", "timeout -s KILL 5m make install", "env FOO=1 make install",
                        "env -S 'make install'", "sudo -u root make install", "sudo --user root make install",
                        "xargs make install", "xargs -I {} make install", "stdbuf -oL make install",
                        "setsid nohup make install", "LC_ALL=C nohup make install"):
            with self.subTest(command=command):
                items = {i["text"]: i for i in chain.describe(f"pwd && {command}", denied=DENIED, step_dir=STEP)}
                outer = items[command]
                self.assertFalse(outer["canAllow"])
                self.assertFalse(outer["preselected"])
                self.assertTrue(outer["reason"])
                self.assertTrue(any(i["forbidden"] and "„make install”" in i["reason"] for i in items.values()),
                                items)
        items = chain.describe("pwd && nohup make test", denied=DENIED, step_dir=STEP)
        self.assertEqual([(i["text"], i["canAllow"]) for i in items],
                         [("pwd", True), ("nohup make test", True), ("make test", True)])
        hidden = chain.describe("pwd && timeout 5 sh -c pwd", denied=DENIED, step_dir=STEP)[1]
        self.assertFalse(hidden["canAllow"])
        self.assertIn("nie jest pojedynczym prostym poleceniem", hidden["reason"])

    def test_long_piece_and_unsplittable_chain_have_reasons(self):
        long_piece = "git fetch origin " + "x" * 300
        items = chain.describe(f"pwd && {long_piece}", denied=DENIED, step_dir=STEP)
        self.assertEqual([(i["text"], i["canAllow"], i["forbidden"]) for i in items],
                         [("pwd", True, False), (long_piece, False, False)])
        self.assertIn("300 znaków", items[1]["reason"])
        broken = chain.describe('pwd && echo "bez pary', denied=DENIED, step_dir=STEP)
        self.assertEqual(len(broken), 1)
        self.assertFalse(broken[0]["canAllow"])
        self.assertIn("nie da się rozłożyć", broken[0]["reason"])

    def test_shell_constructs_are_not_allowable(self):
        items = chain.describe("for f in a b; do cat $f; done", denied=[], step_dir=STEP)
        self.assertEqual(items[0]["text"], "for f in a b")
        self.assertFalse(items[0]["canAllow"])
        self.assertIn("konstrukcja powłoki", items[0]["reason"])

    def test_trailing_star_cannot_widen_the_grant(self):
        items = chain.describe("pwd && rm -rf *", denied=[], step_dir=STEP)
        self.assertEqual([(i["text"], i["canAllow"]) for i in items], [("pwd", True), ("rm -rf *", False)])
        self.assertIn("dowolne argumenty", items[1]["reason"])

    def test_duplicates_listed_once(self):
        self.assertEqual([i["text"] for i in chain.describe("pwd; pwd; ls", denied=[], step_dir=STEP)], ["pwd", "ls"])


class StepReadGrantsTest(unittest.TestCase):
    def test_claude_gets_exact_rules_for_own_step_dirs(self):
        allowed, _ = commands.claude_tools([], DENIED, step_dir=STEP)
        for rule in (f"Bash(ls {STEP}/in)", f"Bash(ls -la {STEP}/in {STEP}/out)", f"Bash(cat {STEP}/out/*)"):
            self.assertIn(rule, allowed)
        self.assertFalse(any(STEP in r for r in commands.claude_tools([], DENIED)[0]))
        self.assertEqual(commands.step_reads("/w/z spacją.step"), ())
        self.assertEqual(commands.step_reads("wzgledny.step"), ())


if __name__ == "__main__":
    unittest.main()
