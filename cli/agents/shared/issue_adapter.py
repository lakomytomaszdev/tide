#!/usr/bin/env python3
"""Trwałe operacje adapterów; syntezę wykonuje program prowadzący rozmowę."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


class AdapterError(Exception):
    pass


def save(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def cli(*args):
    process = subprocess.run(['tide', *map(str, args), '--json'], capture_output=True, text=True)
    try:
        reply = json.loads(process.stdout)
    except ValueError:
        raise AdapterError('CLI nie zwrócił JSON: ' + process.stderr) from None
    if process.returncode or not reply.get('ok'):
        raise AdapterError(json.dumps(reply, ensure_ascii=False))
    return reply


def check_api():
    info = cli('info')
    required = {'issue.' + action for action in ('create', 'show', 'update', 'attach', 'submit')}
    if info.get('apiVersion') != 1 or not required.issubset(info.get('capabilities', [])):
        raise AdapterError('Wymagane API spraw v1 z create/show/update/attach/submit.')


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest='action', required=True)
    for action in ('begin', 'resume', 'finish', 'waive'):
        command = commands.add_parser(action)
        command.add_argument('--session', required=True, type=Path)
        if action in ('begin', 'finish'):
            command.add_argument('--title', required=True)
            command.add_argument('--project', required=True)
            command.add_argument('--report', required=True, type=Path)
        if action == 'resume':
            command.add_argument('--id', required=True)
        if action == 'finish':
            command.add_argument('--file', action='append', default=[])
            command.add_argument('--missing', action='append', default=[])
        if action == 'waive':
            command.add_argument('--evidence', required=True)
            command.add_argument('--decision', required=True)
    return root


def run(args):
    check_api()
    folder = args.session.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / 'session.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else None

    def persist(issue):
        state.update(id=issue['id'], version=issue['version'], state=issue['state'])
        save(state_path, state)
        return issue

    def update(report, title, project):
        report_file = folder / 'report.md'
        report_file.write_text(report, encoding='utf-8')
        return persist(cli('issue', 'update', state['id'], '--expected-version', state['version'],
                           '--title', title, '--project', project, '--report', report_file)['issue'])

    def open_issue():
        cli('issue', 'open', state['id'])
        return state

    if args.action == 'begin':
        if not state:
            if not args.title.strip() or not args.project.strip():
                raise AdapterError('Wymagany jawny niepusty tytuł i projekt.')
            state = {'requestId': str(uuid.uuid4()), 'title': args.title, 'project': args.project,
                     'initialReport': args.report.read_text(encoding='utf-8'), 'missing': [], 'attachErrors': []}
            save(state_path, state)
        if 'id' not in state:
            initial = folder / 'initial.md'
            initial.write_text(state['initialReport'], encoding='utf-8')
            persist(cli('issue', 'create', '--title', state['title'], '--project', state['project'],
                        '--report', initial, '--request-id', state['requestId'])['issue'])
        # Granica procesu przed długą syntezą; użytkownik widzi trwały numer szkicu.
        return open_issue()
    if args.action == 'resume':
        if state:
            raise AdapterError('Sesja już istnieje; zachowaj jej wersję. Nowy odczyt wymaga rozstrzygnięcia konfliktu.')
        issue = cli('issue', 'show', args.id)['issue']
        state = {'title': issue['title'], 'project': issue['project'], 'missing': [], 'attachErrors': []}
        persist(issue)
        return open_issue()
    if not state or 'id' not in state:
        raise AdapterError('Najpierw begin albo resume.')
    # Odczyt służy wykryciu konfliktu, nigdy cichemu odświeżeniu expectedVersion.
    issue = cli('issue', 'show', state['id'])['issue']
    if issue['version'] != state['version']:
        raise AdapterError('version-conflict: zatrzymano; rozstrzygnij zmianę z użytkownikiem.')
    if args.action == 'finish':
        report = args.report.read_text(encoding='utf-8')
        state['missing'] = list(dict.fromkeys([*state['missing'], *args.missing]))
        # Zapisz ograniczenia, zanim zaczniesz kopiowanie dowodów.
        if state['missing']:
            report += '\n\nBrak dostępu do bajtów dowodów: ' + ', '.join(state['missing']) + '. Pozostaje draft.\n'
        update(report, args.title, args.project)
        state.update(title=args.title, project=args.project)
        save(state_path, state)
        for filename in args.file:
            try:
                persist(cli('issue', 'attach', state['id'], '--expected-version', state['version'],
                            '--file', filename)['issue'])
                if filename in state['attachErrors']:
                    state['attachErrors'].remove(filename)
                    save(state_path, state)
            except AdapterError as exc:
                state['attachErrors'].append(filename)
                update(report + '\n\nBłąd attach: ' + filename + '. Nie potwierdzono załączenia; pozostaje draft.\n',
                       args.title, args.project)
                raise AdapterError('Pozostawiono draft ' + state['id'] + ': ' + str(exc)) from None
    else:
        if args.evidence not in state['missing'] or not args.decision.strip():
            raise AdapterError('Wymagana odrębna jawna rezygnacja z konkretnego brakującego dowodu.')
        report = issue['report'] + ('\n\nJawna rezygnacja użytkownika z dowodu „' + args.evidence +
                 '”: ' + args.decision + '\nOgraniczenie dowodów pozostaje; pliku nie dołączono.\n')
        update(report, state['title'], state['project'])
        state['missing'].remove(args.evidence)
        save(state_path, state)
    if not state['missing'] and not state['attachErrors']:
        persist(cli('issue', 'submit', state['id'], '--expected-version', state['version'])['issue'])
    return open_issue()


def main():
    try:
        print(json.dumps(run(parser().parse_args()), ensure_ascii=False))
    except (AdapterError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
