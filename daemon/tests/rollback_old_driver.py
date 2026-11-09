"""Stary silnik przepływu kart na zachowanej historii (PT-293): uruchamiany przez rollback_trial.py jako
`python3 -I rollback_old_driver.py <config.json>`, nigdy importowany przez testy nowego kodu.

Konfiguracja: `oldTree` (katalog z `daemon/` z `git archive`), `board`, `claudeProjects`, `systemdRun`, `tmpRoot`,
`settings`, `fakes` ({nazwa: ścieżka}), `ticks`, `actions` ([{op: answer|resume, card, step?, answer?}]), `report`.
Sterownik importuje wyłącznie `tide_daemon` ze starego drzewa i zapisuje raport JSON: fold każdej karty (bez wyjątku
czy z wyjątkiem), wynik akcji użytkownika i wyjątki ticków. Skutki (starty, systemctl, dziennik) ocenia test.
"""
import json
import os
import sys
import traceback


def main(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    sys.path.insert(0, os.path.join(cfg["oldTree"], "daemon"))
    import tide_daemon
    report = {"module": os.path.realpath(tide_daemon.__file__), "fold": {}, "ticks": [], "actions": []}
    from tide_daemon.board.store import BoardStore
    from tide_daemon.cardflow.engine import FlowEngine
    from tide_daemon.cardflow.state import fold
    from tide_daemon.models import executors
    fakes = cfg["fakes"]
    engine = FlowEngine(BoardStore(cfg["board"]), claude_projects=cfg["claudeProjects"],
                        systemd_run=cfg["systemdRun"], catalog=executors.default_catalog,
                        tmp_root=cfg["tmpRoot"], settings=cfg["settings"], which=lambda name: fakes.get(name),
                        grace=0.5)
    for card in engine.journal.cards():
        events = engine.journal.read(card)
        try:
            st = fold(events)
            report["fold"][card] = {"ok": True, "events": len(events), "paused": bool(st.paused),
                                    "running": sorted(s.get("step") or "" for s in st.running()),
                                    "boxFieldsKnown": hasattr(st, "box_wait")}
        except Exception as exc:  # fold bez wyjątku jest tu obserwacją, nie dowodem zgodności
            report["fold"][card] = {"ok": False, "error": repr(exc)}

    def tick(label):
        try:
            engine.tick()
            report["ticks"].append({"label": label, "ok": True})
        except Exception as exc:
            report["ticks"].append({"label": label, "ok": False, "error": repr(exc),
                                    "trace": traceback.format_exc()[-2000:]})

    for n in range(int(cfg.get("ticks") or 0)):
        tick(f"before-{n}")
    for action in cfg.get("actions") or []:
        try:
            if action["op"] == "resume":
                out = engine.set_paused(action["card"], False)
            elif action["op"] == "answer":
                out = engine.resolve_step(action["card"], {"choice": "answer", "step": action["step"],
                                                           "answer": action.get("answer") or "ISO 8601"})
            else:
                raise ValueError(action["op"])
            report["actions"].append({**action, "ok": True, "reply": out})
        except Exception as exc:
            report["actions"].append({**action, "ok": False, "error": repr(exc)})
        for n in range(int(cfg.get("ticksAfterAction") or 0)):
            tick(f"after-{action['op']}-{action['card']}-{n}")
    report["procs"] = sorted(engine.procs)
    with open(cfg["report"], "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
