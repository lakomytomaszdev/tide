"""Wspólny opis wejść Runnerów; HostRunner dostaje tylko wybór tego kroku.

Brak adaptera kopii K-55/K-60 nie oznacza dostępu do hostowego oryginału.
Tylko hostowy silnik może utworzyć Selection. Pole nie jest częścią IPC/profilu.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
import json

from ..box import exposure, packs
from . import agentcmd


@dataclass(frozen=True)
class Selection:
    project: str
    version: str
    snapshot_ids: tuple[str, ...]


@contextmanager
def selected(step, runner):
    selection = step.exposure_inputs
    if selection is None:
        yield step, ()
        return
    if (type(selection) is not Selection or runner not in ("host", "box") or
            type(selection.snapshot_ids) is not tuple or
            len(set(selection.snapshot_ids)) != len(selection.snapshot_ids)):
        raise agentcmd.StepError("wejścia: nieznana wersja wyboru kopii")
    try:
        with packs.snapshots_access(selection.project, selection.snapshot_ids) as copies:
            with exposure.approved_access(selection.project, selection.version) as manifest:
                if step.repo not in manifest["repositories"]:
                    raise exposure.ExposureError("wejścia: repozytorium kroku nie należy do projektu")
                entries = {i["snapshot"]["id"]: i for i in manifest["inputs"]}
                chosen = []
                for copy in copies:
                    entry = entries.get(copy.id)
                    if not entry or entry["snapshot"] != json.loads(json.dumps(copy.to_dict())):
                        raise exposure.ExposureError("wejścia: kopia nie jest częścią tej zgody")
                    chosen.append(entry)
                directories = tuple(i["mount"]["source" if runner == "host" else "target"] for i in chosen)
                # W hostowym kroku nie rozszerzamy zapisu ani odczytu o inne karty,
                # ich worktree, wspólny home czy wszystkie wejścia projektu.
                adapted = replace(step, read_dirs=tuple(dict.fromkeys((*step.read_dirs, *directories))))
                yield adapted, tuple(chosen)
    except (exposure.ExposureError, packs.PackError, OSError) as exc:
        raise agentcmd.StepError(str(exc)) from exc
