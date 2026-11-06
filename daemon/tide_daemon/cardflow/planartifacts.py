"""Ograniczony pakiet Markdownów planowania, niezależny od katalogu wyjściowego autora."""
import os
import posixpath
import re
import stat
from urllib.parse import quote, unquote, urlsplit

MAX_FILE = 512 * 1024
MAX_TOTAL = 16 * 1024 * 1024
LINK = re.compile(r'\]\(\s*(<[^>]*>|[^\s)]+)|^\s*\[[^\]]+\]:\s*(<[^>]*>|\S+)', re.M)


def keep_committed_plan_text(state):
    """Nowa zatwierdzona propozycja dostarcza tekst do pierwszego udanego commita.

    Późniejsze zmiany stanu zachowują też ręczne dopiski w worktree. Numery zdarzeń
    odróżniają wynik zapisu poprzedniej wersji od zapisu bieżącej propozycji.
    """
    proposed = state.plan_proposed or {}
    return bool(state.plan_revision) or any(
        action == 'plan-commit' and result.get('ok') and
        int(result.get('seq') or 0) > int(proposed.get('seq') or 0)
        for (action, _), result in state.results.items())


def read(root, *, validate=True, plan_name=None):
    """Czyta tylko *.md obok planu, tickets/*.md i followups/**/*.md, bez dowiązań.

    Deskryptory katalogów i O_NOFOLLOW chronią również przed podmianą ścieżki podczas odczytu.
    Cały pakiet jest sprawdzany przed pierwszym zapisem migawki.
    """
    files = {}
    total = 0

    def walk(fd, prefix):
        nonlocal total
        for name in sorted(os.listdir(fd)):
            if not prefix and plan_name and name.endswith('.md') and name not in (plan_name, 'spec.md', 'discovery.md'):
                continue
            rel = posixpath.join(prefix, name)
            meta = os.stat(name, dir_fd=fd, follow_symlinks=False)
            selected = name.endswith('.md') or prefix.startswith('followups') or (not prefix and name in ('tickets', 'followups'))
            if not selected:
                continue
            if stat.S_ISLNK(meta.st_mode):
                raise ValueError(f'dowiązanie w artefaktach: {rel}')
            if stat.S_ISDIR(meta.st_mode):
                if prefix == 'tickets' or (not prefix and name not in ('tickets', 'followups')):
                    continue
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, rel)
                finally:
                    os.close(child)
            elif name.endswith('.md'):
                if not stat.S_ISREG(meta.st_mode):
                    raise ValueError(f'artefakt nie jest zwykłym plikiem: {rel}')
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, 'rb') as fh:
                    if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                        raise ValueError(f'artefakt nie jest zwykłym plikiem: {rel}')
                    body = fh.read(MAX_FILE + 1)
                total += len(body)
                if len(body) > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError('artefakty planowania przekraczają 512 KiB/pliku lub 16 MiB/całości')
                body.decode('utf-8')
                files[rel] = body

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        walk(fd, '')
    finally:
        os.close(fd)
    if validate:
        check_links(files)
    return files


def check_links(files):
    """Lokalny link do followups musi wskazywać Markdown wewnątrz tego pakietu."""
    for source, body in files.items():
        for match in LINK.finditer(body.decode('utf-8')):
            raw = (match.group(1) or match.group(2)).strip('<>')
            url = urlsplit(raw)
            if (url.scheme and url.scheme != 'file') or url.netloc:
                continue
            path = unquote(url.path)
            if not path or ('followups' not in path.split('/') and not source.startswith('followups/')):
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
            if url.scheme == 'file' or path.startswith('/') or target.startswith('../') or target not in files:
                raise ValueError(f'{source}: brak lokalnego celu followups lub wyjście poza artefakty: {raw}')


def relocate_ticket_links(text, followups):
    """Przenosi cele szkiców z tickets/ do katalogu kartki, zachowując składnię i fragmenty.

    Używa tych samych dopasowań co kontrola pakietu: inline, <cel> i definicje referencji.
    Zmienia tylko ścieżkę celu; tytuł, otoczenie i końcówka URL pozostają bez zmian.
    """
    def relocate(match):
        group = 1 if match.group(1) is not None else 2
        token = match.group(group)
        raw = token[1:-1] if token.startswith('<') else token
        url = urlsplit(raw)
        if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
            return match.group()
        target = posixpath.normpath(posixpath.join('tickets', unquote(url.path)))
        if not target.startswith('followups/'):
            return match.group()
        path = quote(posixpath.join(followups, target[len('followups/'):]), safe='/')
        start = match.start(group) - match.start() + int(token.startswith('<'))
        return match.group()[:start] + path + match.group()[start + len(url.path):]

    return LINK.sub(relocate, text)


def save(journal, card, relative, files):
    for name, body in files.items():
        journal.write_file(card, os.path.join(relative, name), body)


def prepare(source, step_dir, *, seed=False, plan_name='plan.md'):
    """Komplet do odczytu dla review; poprawka zaczyna z tymi samymi szkicami i planem."""
    files = read(source)
    for name, body in files.items():
        targets = [os.path.join(step_dir, 'in', 'proposal', name)]
        if seed:
            targets.append(os.path.join(step_dir, 'out', 'plan.md' if name == plan_name else name))
        for target in targets:
            relative = os.path.relpath(target, step_dir)
            current = step_dir
            if os.path.islink(current):
                raise ValueError('dowiązanie katalogu kroku')
            os.makedirs(current, exist_ok=True)
            parts = relative.split(os.sep)
            for part in parts[:-1]:
                current = os.path.join(current, part)
                if os.path.islink(current):
                    raise ValueError(f'dowiązanie w przekazaniu artefaktów: {relative}')
                os.makedirs(current, exist_ok=True)
            if os.path.islink(target):
                raise ValueError(f'dowiązanie w przekazaniu artefaktów: {relative}')
            if os.path.exists(target):
                continue
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as fh:
                fh.write(body)
    return ('\nZamrożony komplet Markdownów (plan, kartki i szkice followups, także w podkatalogach): '
            + os.path.join(step_dir, 'in', 'proposal')
            + '\nZachowaj tekst planu poza tabelą oraz szkice i względne linki. '
              'Szkic zakresu następczego nie oznacza opublikowanej karty ani dostępnej funkcji.\n')
