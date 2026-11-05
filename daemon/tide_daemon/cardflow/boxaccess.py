"""Tożsamość sieciowa kroku: dziennik proxy, atrapa K3 i prywatny zapis do odtworzenia po restarcie.

Plik stanu leży poza montowaniami pudełka. Nie zawiera tokenów hosta, tylko losowe identyfikatory kroku.
Nie odtwarzamy ich z `in/env`, które kod projektu może dowolnie zmienić.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import time

from ..box import k3
from ..box.runner import StepDir, worktrees_root

STATE_FILE = "access.json"


def _path(log: str) -> str:
    return os.path.join(os.path.dirname(log), STATE_FILE)


def read(log: str) -> dict:
    fd = os.open(_path(log), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("niepoprawny kontekst sieci kroku")
    return data


def create(proxy, placement, step: str, program: str, log: str, policy) -> dict:
    data = {"step": step, "box": placement.box, "ip": placement.ip, "networkKey": secrets.token_urlsafe(32),
            "domains": list(policy.domains), "ollama": policy.ollama, "full": policy.full}
    if k3.k3_active(program):
        grant = proxy.grants.issue(step, placement.ip, program)
        data["grant"] = {"route": grant.route, "key": grant.key, "expires": time.time() + k3.DEFAULT_TTL}
    fd, tmp = tempfile.mkstemp(prefix=".access-", dir=os.path.dirname(log))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, _path(log))
        proxy.register_step(placement.ip, step, data["networkKey"], log)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return data


def restore(proxy, step: dict, log: str) -> dict:
    data = read(log)
    if (data.get("step"), data.get("box"), data.get("ip")) != (step["step"], step["box"], step["boxIp"]):
        raise ValueError("kontekst sieci nie pasuje do zapisanego runnera")
    grant = data.get("grant")
    if grant:
        proxy.grants.restore(step["step"], step["boxIp"], grant["route"], grant["key"],
                             float(grant["expires"]) - time.time())
    proxy.register_step(step["boxIp"], step["step"], data["networkKey"], log)
    return data


def configure(proxy, placement, step: str, log: str, bg, *, codex_step_dir: str = "") -> None:
    data = read(log)
    base = f"http://{proxy.listen}:{proxy.port}"
    url = base.replace("http://", f"http://tide:{data['networkKey']}@", 1)
    bg.env.update({name: url for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")})
    saved = data.get("grant")
    if not saved:
        return
    grant = k3.Grant(step, placement.ip, saved["route"], saved["key"])
    credentials = k3.step_credentials(grant, base)
    bg.env.update(credentials.env)
    # Flagi globalne przed `exec`, także przy `exec resume`. Żadnych kluczy w argv.
    bg.argv[1:1] = credentials.args
    if credentials.files:
        # Osobny CODEX_HOME dla sesji kroku; wznowienie zachowuje katalog rodzica.
        root = codex_step_dir or bg.step_dir
        fs = StepDir(worktrees_root(os.path.realpath(bg.repo)), root)
        home = os.path.join(root, "in", "k3")
        for rel, content in credentials.files.items():
            fs.write_secret(os.path.join(home, rel), content)
        bg.env["CODEX_HOME"] = os.path.join(home, ".codex")


def revoke(proxy, step: str, log: str) -> None:
    if proxy is not None:
        proxy.revoke_step(step)
    try:
        os.unlink(_path(log))
    except FileNotFoundError:
        pass
