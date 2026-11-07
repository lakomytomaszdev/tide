"""Rozmowa z opiekunem (PT-149): trwałe pliki akcji, wspólna walidacja i wykonanie przez silnik."""
from __future__ import annotations

import os
import re
import signal
import time

from ..board.model import BoardError, card_num
from ..tasks import agentcmd, launch, stepstop
from . import caretaker, caretakerctx, execution, guard
from .state import fold, ticket_of


class CaretakerChat:
    def talk_to_caretaker(self, card_id: str, request: dict) -> dict:
        from .engine import FlowError
        try:
            card = f"K-{card_num(card_id)}"
        except BoardError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if not isinstance(request, dict) or set(request) - {"problem"}:
            raise FlowError("bad-request", "oczekiwano opcjonalnego id problemu")
        st = fold(self.journal.read(card))
        if not st.started or st.card_finished:
            raise FlowError("no-step", "karta nie ma trwającego przepływu")
        if any(s.get("kind") == "caretaker-chat" and not s.get("consoleClosed")
               for s in st.caretakers.values()):
            raise FlowError("console-open", "rozmowa z opiekunem jest już otwarta")
        if self.terminal is None or not self.terminal.available():
            raise FlowError("no-terminal", "nie znaleziono Konsoli")
        ident = request.get("problem", "")
        if not isinstance(ident, str):
            raise FlowError("bad-request", "problem musi być napisem")
        problems = caretaker.problems(st)
        problem = next((p for p in problems if p["id"] == ident), None) if ident else next(iter(problems), None)
        if ident and problem is None:
            raise FlowError("no-step", "problem już nie występuje")
        problem = problem or {"id": f"chat:{st.last_seq}", "trigger": "chat", "step": "", "root": "",
                              "ticket": "", "decision": "", "options": []}
        cfg = self.models_config() if self.models_config else None
        catalog = cfg.catalog if cfg is not None else self.catalog() if self.catalog else None
        conf = self._caretaker_setup(st, catalog)
        if conf["reason"] or not conf["executor"]:
            raise FlowError("no-executor", conf["reason"] or "brak wykonawcy opiekuna")
        try:
            selected = execution.resolve(conf["executor"], catalog, config=cfg,
                                         probe=self.model_probe, which=self.which)
        except execution.ExecutionError as exc:
            raise FlowError(exc.code, str(exc)) from None
        if caretaker.local_only(st) and not selected.local:
            raise FlowError("local-only", "karta wymaga lokalnego wykonawcy opiekuna")
        item, project = self._project(card)
        info = ticket_of(st)
        made = {"worktree": info["cardWorktree"], "branch": info["cardBranch"]}
        if not made["worktree"] or not os.path.isdir(made["worktree"]):
            raise FlowError("no-worktree", "brak katalogu roboczego karty")
        name = f"{card}-chat-{st.last_seq + 1}"

        def prepare(step_dir):
            events = self.journal.read(card)
            now = fold(events)
            context_dir = os.path.join(step_dir, "in", caretakerctx.CONTEXT_DIR)
            files = caretakerctx.build(problem, now, events, card=card, worktree=made["worktree"],
                                       context_dir=context_dir,
                                       card_dir=os.path.join(self.journal.root, self.journal.rel_dir(card)), step=name,
                                       include_text=selected.tool != "claude")
            prompt = caretaker.brief(problem, now, card=card, out_dir=os.path.join(step_dir, "out"),
                                     worktree=made["worktree"], commands=selected.tool == "claude",
                                     context=files, context_dir=context_dir, chat=True)
            bg = agentcmd.caretaker_interactive(agentcmd.Step(
                tool=selected.tool, binary=selected.binary, prompt=prompt, worktree=made["worktree"],
                repo=project.repo, step_dir=step_dir, step_id=name, model=selected.model,
                effort=selected.effort, local=selected.route, tmp_root=self.tmp_root,
                enforce_command_policy=True, permission_mode="acceptEdits" if selected.tool == "claude" else "",
                codex_mcp_skip=tuple(self._settings_data().get("codexMcpSkip") or ())))
            bg.prepare()
            self.journal.append(card, "caretaker.chat", step=name, problem=problem["id"],
                                target=problem.get("step") or "", root=problem.get("root") or "",
                                ticket=problem.get("ticket") or "", trigger=problem["trigger"],
                                decision=problem.get("decision") or "", watermark=now.last_seq, mode="chat")
            self._guard_baseline(card, name, project.repo, [(made["worktree"], bg.git_dir)], [made["branch"]])
            return bg

        try:
            self._launch_full_planning(item, project, selected, made, name, [], [], "", include_context=False,
                                       kind="caretaker-chat", prepare=prepare)
        except (agentcmd.StepError, OSError) as exc:
            raise FlowError("step", f"rozmowa nie wystartowała: {exc}") from None
        return {"ok": True, "step": name}

    def _poll_caretaker_chat(self, card: str, step: dict) -> None:
        name = step["step"]
        st = fold(self.journal.read(card))
        if name in st.finished:
            return
        wrote = self._guard_verdict(card, name)
        if wrote:
            self._finish_caretaker_chat(card, step, guard.WROTE, wrote)
            return
        out = os.path.join(step["stepDir"], "out")
        # Najpierw dokończenie utrwalonych akcji, również gdy plik zniknął albo został podmieniony po restarcie.
        for run in list(st.caretaker_chat_actions.values()):
            if run.get("chat") == name and run["step"] not in st.caretaker_outcomes:
                self._caretaker_apply(card, fold(self.journal.read(card)), run, {"outcome": run["outcome"]})
        entries = sorted((int(m[1]), entry) for entry in os.listdir(out)
                         if (m := re.fullmatch(r"action-([1-9][0-9]{0,8})\.json", entry)))
        for _number, entry in entries:
            st = fold(self.journal.read(card))
            key = f"{name}-{entry[:-5]}"
            if key in st.caretaker_chat_actions:
                continue
            wrote = self._guard_verdict(card, name)
            if wrote:
                self._finish_caretaker_chat(card, step, guard.WROTE, wrote)
                return
            run = st.caretaker_chats[name]
            problem = self._caretaker_problem(st, run)
            path = os.path.join(out, entry)
            raw = self._raw_result(path)
            if raw == "" and not os.path.islink(path):
                # Write może zapisywać plik w chwili odpytywania. Błędny JSON odrzucamy dopiero, gdy
                # metadane nie zmieniły się od poprzedniego przebiegu (również po restarcie).
                try:
                    info = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                signature = [info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
                if (st.caretaker_chat_files.get(key) or {}).get("signature") != signature:
                    self.journal.append(card, "caretaker.chat-file", step=key, chat=name, signature=signature)
                    continue
            outcome = caretaker.parse_result(raw, problem, st)
            run = self.journal.append(card, "caretaker.chat-action",
                                      **{k: v for k, v in run.items() if k not in ("seq", "ts", "type", "step")},
                                      step=key, chat=name, file=entry, outcome=outcome)
            self._caretaker_apply(card, fold(self.journal.read(card)), run, {"outcome": outcome})
        if step.get("consoleClosed") or self._console_ended(step):
            self._finish_caretaker_chat(card, step)
        else:
            task = step.get("taskFile") or ""
            try:
                expired = bool(task) and self.clock() - os.stat(task).st_mtime > 30
            except OSError:
                expired = False
            if expired and self.task_files.cancel(task):
                self._finish_caretaker_chat(card, step, "interactive-closed")

    def _finish_caretaker_chat(self, card: str, step: dict, reason="", detail="") -> None:
        name = step["step"]
        if name in fold(self.journal.read(card)).finished:
            return
        if reason == guard.WROTE:
            st = fold(self.journal.read(card))
            base = st.caretaker_guards.get(name)
            if base and not st.caretaker_wrote.get(name):
                self.journal.append(card, guard.EVENT, step=name, cause="chat-check", wrote=[detail or reason],
                                    **{k: base.get(k) for k in guard.FIELDS})
            # Brak potwierdzenia zatrzymania oznacza dalszą blokadę, nigdy zgodę na kolejne akcje.
            if not self._stop_caretaker_chat(step):
                return
            run = fold(self.journal.read(card)).caretaker_chats.get(name) or {}
            self._caretaker_fail(card, {**run, "step": name}, reason, detail or reason)
        self.task_files.cancel(step.get("taskFile") or "")
        self.journal.append(card, "step.console_closed", step=name)
        self.journal.append(card, "step.finished", step=name, ticket="", result="error" if reason else "done",
                            reason=reason, mode="chat")
        self._release_local(name)

    def _stop_caretaker_chat(self, step: dict) -> bool:
        """Anulowanie atomowo przed przejęciem, a po przejęciu potwierdzony koniec launchera i całej jednostki.
        Nieczynna jednostka przed startem scope nie jest dowodem końca launchera (PT-67)."""
        task = step.get("taskFile") or ""
        if task and (self.task_files.cancel(task) or os.path.isfile(task + ".cancelled")):
            return True
        path, unit = step.get("processFile") or "", step.get("consoleUnit") or ""
        if not path or not unit or launch.process_alive(path) is None:
            return False
        launch.signal_flow_launcher(path, signal.SIGTERM)
        if not stepstop.terminate(None, unit, self.grace):
            return False
        deadline = time.monotonic() + max(1.0, self.grace)
        while not self._console_ended(step) and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._console_ended(step):
            launch.signal_flow_launcher(path, signal.SIGKILL)
            return False
        return True
