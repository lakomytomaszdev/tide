#!/usr/bin/env python3
"""Instalacja wyłącznie własnych plików paczki, z kontrolą kolizji i kopią zapasową."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Dowiązanie w ścieżce instalacji: ' + str(path))


def install(uninstall=False):
    source = Path(__file__).resolve().parent
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    root = data / 'tide/agents'
    manifest = root / 'manifest.json'
    safe(manifest)
    owned = json.loads(manifest.read_text()) if manifest.exists() else {}
    targets = {}
    if not uninstall:
        programs = [name for name in ('claude', 'codex') if shutil.which(name)]
        # Zachowaj zainstalowane adaptery także przy chwilowym braku programu w PATH.
        if any('/.codex/skills/tide-' in p for p in owned):
            programs.append('codex')
        for program in ('claude', 'codex'):
            destination = root / program / ('tide' if program == 'claude' else 'tide-issue')
            for path in (source / program).rglob('*'):
                if path.is_file():
                    targets[str(destination / path.relative_to(source / program))] = path.read_bytes()
            targets[str(destination / 'references/synthesis.md')] = (source / 'shared/synthesis.md').read_bytes()
            targets[str(destination / 'references/decisions.md')] = (source / 'shared/decisions.md').read_bytes()
            targets[str(destination / 'scripts/issue_adapter.py')] = (source / 'shared/issue_adapter.py').read_bytes()
            if program == 'codex' and program in programs:
                for skill in ('tide-issue', 'tide-backlog', 'tide-go'):
                    discoverable = Path.home() / '.codex/skills' / skill
                    source_dir = source / 'codex' if skill == 'tide-issue' else source / 'codex' / skill
                    for path in source_dir.rglob('*'):
                        if path.is_file():
                            targets[str(discoverable / path.relative_to(source_dir))] = path.read_bytes()
                    targets[str(discoverable / 'references/decisions.md')] = (source / 'shared/decisions.md').read_bytes()
                    targets[str(discoverable / 'references/synthesis.md')] = (source / 'shared/synthesis.md').read_bytes()
                    targets[str(discoverable / 'scripts/issue_adapter.py')] = (source / 'shared/issue_adapter.py').read_bytes()
        targets[str(root / 'shared/synthesis.md')] = (source / 'shared/synthesis.md').read_bytes()
        card = root / 'codex/tide-card'
        targets[str(card / 'SKILL.md')] = (source / 'tide-card/SKILL.md').read_bytes()
        for destination in (card, root / 'claude/tide'):
            targets[str(destination / 'references/card.md')] = (source / 'shared/card.md').read_bytes()
            targets[str(destination / 'scripts/card_adapter.py')] = (source / 'shared/card_adapter.py').read_bytes()
        if 'codex' in programs:
            for name, content in list(targets.items()):
                if Path(name).is_relative_to(card):
                    targets[str(Path.home() / '.codex/skills/tide-card' / Path(name).relative_to(card))] = content
    # Preflight całej paczki przed pierwszą zmianą. Obce lub lokalnie zmienione pliki zostają.
    for name in set(owned) | set(targets):
        path = Path(name)
        if ('..' in path.parts or not path.is_absolute() or
                not (path.is_relative_to(root) or any(path.is_relative_to(Path.home() / '.codex/skills' / skill)
                                                       for skill in ('tide-issue', 'tide-backlog', 'tide-go', 'tide-card')))):
            raise ValueError('Manifest wskazuje plik spoza paczki Tide: ' + name)
        safe(path)
        if path.exists() and (not path.is_file() or name not in owned or digest(path.read_bytes()) != owned[name]):
            raise ValueError('Kolizja; zachowano plik: ' + name)
    existing = [Path(name) for name in owned if Path(name).exists()]
    if existing or manifest.exists():
        backup = root / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        safe(backup)
        backup.mkdir(parents=True)
        for index, path in enumerate(existing):
            shutil.copy2(path, backup / str(index))
        (backup / 'files.json').write_text(json.dumps([str(p) for p in existing]))
        if manifest.exists():
            shutil.copy2(manifest, backup / 'manifest.json')
    for name in owned.keys() - targets.keys():
        Path(name).unlink(missing_ok=True)
    for name, content in targets.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    root.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({name: digest(content) for name, content in targets.items()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uninstall', action='store_true')
    try:
        install(parser.parse_args().uninstall)
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
