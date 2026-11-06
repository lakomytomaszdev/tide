"""Domy agentów pudełek dostępne na hoście; skaner nie podąża za dowiązaniami z pudełka."""
import os
from . import logfiles


def directories(path):
    try:
        return [child for name in logfiles.names(path) if (child := safe_child(path, name))]
    except OSError:
        return []


def safe_child(root, *parts):
    path = os.path.join(root, *parts)
    try:
        fd = logfiles._fd(path, directory=True)
        os.close(fd)
        return path
    except OSError:
        return ""


def homes(env=None):
    env = os.environ if env is None else env
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "share")
    root = os.path.join(data, "tide", "box")
    found = []
    for project in directories(root):
        if home := safe_child(project, "home"):
            found.append(home)
        if cards := safe_child(project, "cards"):
            found.extend(home for card in directories(cards) if (home := safe_child(card, "home")))
    return found


def claude_projects(env=None):
    return [path for home in homes(env) if (path := safe_child(home, ".claude", "projects"))]


def codex_roots(env=None):
    roots = []
    for home in homes(env):
        root = safe_child(home, ".codex")
        if root:
            roots.append(root)
            steps = safe_child(root, "steps")
            if steps:
                roots.extend(directories(steps))
    return roots
