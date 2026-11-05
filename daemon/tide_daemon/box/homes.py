"""Domy agentów pudełek dostępne na hoście; skaner nie podąża za dowiązaniami z pudełka."""
import os


def directories(path):
    try:
        return [entry.path for entry in os.scandir(path) if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def safe_child(root, *parts):
    path = root
    for part in parts:
        path = os.path.join(path, part)
        if os.path.islink(path) or not os.path.isdir(path):
            return ""
    return path


def homes(env=None):
    env = os.environ if env is None else env
    data = env.get("XDG_DATA_HOME") or os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "share")
    root = os.path.join(data, "tide", "box")
    return [home for project in directories(root) if (home := safe_child(project, "home"))]


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
