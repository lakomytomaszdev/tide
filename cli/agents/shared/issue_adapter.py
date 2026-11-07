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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    os.chmod(path, 0o600)


def cli(*args):
    process = subprocess.run(['tide', *map(str, args), '--json'], capture_output=True, text=True)
    try:
        reply = json.loads(process.stdout)
    except ValueError:
        raise AdapterError('CLI nie zwrócił JSON: ' + process.stderr) from None
    if process.returncode or not reply.get('ok'):
        raise AdapterError(json.dumps(reply, ensure_ascii=False))
    return reply


def decision_cli(*args):
    """Zachowuje wynik startError jako dane, respektując kod wyjścia CLI."""
    process = subprocess.run(['tide', *map(str, args), '--json'], capture_output=True, text=True)
    try:
        reply = json.loads(process.stdout)
    except ValueError:
        raise AdapterError('CLI nie zwrócił JSON: ' + process.stderr) from None
    if not reply.get('ok') or (process.returncode and not reply.get('startError')):
        raise AdapterError(json.dumps(reply, ensure_ascii=False))
    return reply


def check_api(args):
    info = cli('info')
    action = args.action
    required = {'issue.' + capability for capability in ('create', 'show', 'update', 'attach', 'submit')}
    if action == 'backlog' or (action == 'inspect' and args.destination == 'backlog'):
        required.add('issue.accept')
    if action == 'go' or (action == 'inspect' and args.destination == 'go'):
        required.add('issue.go')
    if action in ('retry-inspect', 'retry-start', 'retry-replay'):
        required.add('issue.retry-start')
    if info.get('apiVersion') != 1 or not required.issubset(info.get('capabilities', [])):
        raise AdapterError('Brak wymaganej możliwości w publicznym API spraw v1.')


def dispatch_decision(payload):
    args = ['backlog' if payload['verb'] == 'backlog' else 'go', payload['id'],
            '--expected-version', str(payload['expectedVersion']), '--request-id', payload['requestId']]
    if payload['verb'] == 'go' and payload.get('launch') is not None:
        folder = Path(payload['_session'])
        launch_file = folder / 'decision-launch.json'
        launch_file.write_text(json.dumps(payload['launch'], ensure_ascii=False), encoding='utf-8')
        args += ['--launch', str(launch_file)]
    return decision_cli(*args)


def dispatch_retry(payload):
    return decision_cli('issue', 'retry-start', payload['id'], '--intent', payload['intent'],
                        '--expected-start-version', str(payload['expectedStartVersion']),
                        '--request-id', payload['requestId'])


def missing_notice(missing):
    if not missing:
        return ''
    return '\n\nBrak dostępu do bajtów dowodów: ' + ', '.join(missing) + '. Pozostaje draft.\n'


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
    for action in ('inspect', 'backlog', 'go', 'replay', 'retry-inspect', 'retry-start', 'retry-replay'):
        command = commands.add_parser(action)
        command.add_argument('--session', required=True, type=Path)
        if action not in ('replay', 'retry-replay'):
            command.add_argument('--id', required=True)
        if action == 'inspect':
            command.add_argument('--destination', required=True, choices=('backlog', 'go'))
            command.add_argument('--launch', type=Path)
        if action in ('backlog', 'go', 'retry-start'):
            command.add_argument('--decision', required=True)
        if action == 'backlog':
            command.add_argument('--expected-version', required=True, type=int)
        if action == 'go':
            command.add_argument('--expected-version', required=True, type=int)
            command.add_argument('--launch', type=Path)
        if action == 'retry-start':
            command.add_argument('--intent', required=True)
            command.add_argument('--expected-start-version', required=True, type=int)
    return root


def run(args):
    check_api(args)
    folder = args.session.resolve()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    state_path = folder / 'session.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else None

    def session_state():
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        return json.loads(state_path.read_text()) if state_path.exists() else {}

    def issue_show(ident):
        return cli('issue', 'show', ident)

    def show_reply(issue, **extra):
        return {'ok': True, 'issue': issue, **extra}

    def require_decision(expected, received, prior=None):
        # Jawna wersja może być potwierdzona w samym poleceniu. Potwierdzenie
        # „tego raportu” działa wyłącznie dla migawki zapisanej przez inspect.
        exact = expected.casefold()
        if received.strip().casefold() == exact:
            return True
        if prior and prior.get('confirmation', '').casefold() == received.strip().casefold():
            if prior.get('expected') == expected:
                return False
        raise AdapterError('Brak jednoznacznej decyzji dla dokładnej wersji i ustawień: ' + expected)

    if args.action in ('inspect', 'backlog', 'go', 'replay', 'retry-inspect', 'retry-start', 'retry-replay'):
        state = session_state()
        if args.action in ('inspect', 'retry-inspect'):
            shown = issue_show(args.id)
            issue = shown['issue']
            if args.action == 'inspect':
                launch = json.loads(args.launch.read_text(encoding='utf-8')) if args.launch else None
                if launch is not None and not isinstance(launch, dict):
                    raise AdapterError('Ustawienia go muszą być obiektem JSON.')
                if args.destination == 'backlog' and launch is not None:
                    raise AdapterError('Backlog nie przyjmuje ustawień go.')
                expected = f"{args.destination} {args.id} v{issue['version']}"
                state['presented'] = {'id': args.id, 'version': issue['version'], 'issue': issue,
                                      'destination': args.destination, 'launch': launch,
                                      'expected': expected, 'confirmation': 'Potwierdzam ' + expected}
                save(state_path, state)
                return show_reply(issue, presented=True, destination=args.destination, launch=launch,
                                  confirmation=state['presented']['confirmation'])
            current = shown.get('start')
            if not isinstance(current, dict):
                raise AdapterError('Sprawa nie ma bieżącego startu do ponowienia.')
            state['retryPresented'] = {'id': args.id, 'intentId': current.get('intentId'),
                                       'startVersion': current.get('startVersion'), 'retryable': current.get('retryable'),
                                       'start': current}
            save(state_path, state)
            return show_reply(issue, currentStart=current, presented=True)
        if args.action in ('backlog', 'go'):
            verb = args.action
            expected_version = args.expected_version
            expected = f'{verb} {args.id} v{expected_version}'
            explicit = require_decision(expected, args.decision, state.get('presented'))
            if not explicit and state.get('presented') and state['presented'].get('id') == args.id:
                if state['presented'].get('version') != expected_version:
                    raise AdapterError('version-conflict: przedstawiona wersja nie zgadza się z decyzją.')
                if state['presented'].get('destination') != verb:
                    raise AdapterError('Decyzja dotyczy innego przeznaczenia; pokaż raport ponownie.')
            launch = None
            if verb == 'go' and args.launch is not None:
                launch = json.loads(args.launch.read_text(encoding='utf-8'))
                if not isinstance(launch, dict):
                    raise AdapterError('Ustawienia go muszą być obiektem JSON.')
            if not explicit and state.get('presented', {}).get('expected') == expected and state['presented'].get('launch') != launch:
                raise AdapterError('Ustawienia różnią się od przedstawionych; pokaż raport i ustawienia ponownie.')
            payload = {'id': args.id, 'verb': verb, 'expectedVersion': expected_version,
                       'launch': launch, 'requestId': str(uuid.uuid4()), 'decision': args.decision,
                       '_session': str(folder)}
            prior = state.get('decisionOperation')
            if prior:
                if {key: prior.get(key) for key in ('id', 'verb', 'expectedVersion', 'launch', 'decision')} != {
                        key: payload.get(key) for key in ('id', 'verb', 'expectedVersion', 'launch', 'decision')}:
                    state.setdefault('decisionHistory', []).append(prior)
                    state['decisionOperation'] = payload
                    save(state_path, state)
                else:
                    payload = prior
            else:
                state['decisionOperation'] = payload
                save(state_path, state)
            return dispatch_decision(payload)
        if args.action == 'replay':
            payload = state.get('decisionOperation')
            if not payload:
                raise AdapterError('Brak zapisanej tożsamości operacji do odtworzenia.')
            return dispatch_decision(payload)
        if args.action == 'retry-start':
            presented = state.get('retryPresented')
            if not presented or presented.get('id') != args.id or presented.get('intentId') != args.intent:
                raise AdapterError('Najpierw pokaż bieżący start i jego intentId przez retry-inspect.')
            expected = f"ponów start {args.id} intent {args.intent} v{args.expected_start_version}"
            require_decision(expected, args.decision)
            if presented.get('startVersion') != args.expected_start_version or presented.get('retryable') is not True:
                raise AdapterError('start-conflict: aktualny start nie uprawnia do ponowienia.')
            payload = {'id': args.id, 'intent': args.intent,
                       'expectedStartVersion': args.expected_start_version,
                       'requestId': str(uuid.uuid4()), 'decision': args.decision,
                       '_session': str(folder)}
            prior = state.get('retryOperation')
            if prior:
                if any(prior.get(key) != payload.get(key) for key in
                       ('id', 'intent', 'expectedStartVersion', 'decision')):
                    if not presented or presented.get('startVersion') != args.expected_start_version:
                        raise AdapterError('Najpierw pokaż aktualny start i uzyskaj odrębną decyzję retry.')
                    state.setdefault('retryHistory', []).append(prior)
                    state['retryOperation'] = payload
                    save(state_path, state)
                else:
                    payload = prior
            else:
                state['retryOperation'] = payload
                save(state_path, state)
            return dispatch_retry(payload)
        if args.action == 'retry-replay':
            payload = state.get('retryOperation')
            if not payload:
                raise AdapterError('Brak zapisanej tożsamości retry do odtworzenia.')
            return dispatch_retry(payload)

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
        try:
            cli('issue', 'open', state['id'])
        except AdapterError as exc:
            raise AdapterError('Zapisano ' + state['id'] + '; błąd open: ' + str(exc)) from None
        return {'ok': True, **state}

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

        def described():
            result = report + missing_notice(state['missing'])
            if state['attachErrors']:
                result += '\n\nBłąd attach: ' + ', '.join(state['attachErrors']) + '. Nie potwierdzono załączenia; pozostaje draft.\n'
            for waiver in state.get('waivers', []):
                result += '\n\n' + waiver
            return result

        # Zapisz ograniczenia, zanim zaczniesz kopiowanie dowodów.
        update(described(), args.title, args.project)
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
                if filename not in state['attachErrors']:
                    state['attachErrors'].append(filename)
                update(described(), args.title, args.project)
                raise AdapterError('Pozostawiono draft ' + state['id'] + ': ' + str(exc)) from None
        # Po udanym ponowieniu błędu usuń nieaktualny opis awarii z raportu.
        update(described(), args.title, args.project)
    else:
        if args.evidence not in state['missing'] or not args.decision.strip():
            raise AdapterError('Wymagana odrębna jawna rezygnacja z konkretnego brakującego dowodu.')
        waiver = ('Jawna rezygnacja użytkownika z dowodu „' + args.evidence +
                  '”: ' + args.decision + '\nOgraniczenie dowodów pozostaje; pliku nie dołączono.\n')
        remaining = [item for item in state['missing'] if item != args.evidence]
        report = issue['report'].replace(missing_notice(state['missing']), missing_notice(remaining))
        report += '\n\n' + waiver
        update(report, state['title'], state['project'])
        state.setdefault('waivers', []).append(waiver)
        state['missing'] = remaining
        save(state_path, state)
    if not state['missing'] and not state['attachErrors'] and state['state'] == 'draft':
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
