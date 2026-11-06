#!/usr/bin/env python3
"""Jawna, sieciowa ocena syntezy; poza make test i bez atrap programów modeli."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parent.parent


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def command(argv, env, cwd, prefix, input_text=None, timeout=600):
    write_json(prefix.with_suffix('.command.json'), argv)
    result = subprocess.run(argv, env=env, cwd=cwd, input=input_text, text=True,
                            capture_output=True, timeout=timeout)
    prefix.with_suffix('.stdout').write_text(result.stdout, encoding='utf-8')
    prefix.with_suffix('.stderr').write_text(result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{argv[0]}: exit {result.returncode}; zobacz {prefix}.stderr / .stdout')
    return result.stdout


def run(args):
    # Odczyt ścieżek przed utworzeniem izolacji; żadnego importu konfiguracji użytkownika.
    original_home = Path.home()
    original_codex = Path(os.environ.get('CODEX_HOME') or original_home / '.codex')
    programs = {name: shutil.which(name) for name in ('claude', 'codex')}
    if not all(programs.values()):
        raise RuntimeError('Wymagane rzeczywiste programy claude i codex w PATH; nie używaj atrap.')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture_spec = importlib.util.spec_from_file_location('issue_fixture', PACKAGE / 'eval/fixture.py')
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    # Materializowany plik jest również stałym artefaktem testów w repozytorium.
    png = PACKAGE / 'eval/filtr.png'
    image = fixture.png()
    if png.exists() and png.read_bytes() != image:
        raise RuntimeError('filtr.png różni się od kontrolowanego generatora; nie nadpisano pliku.')
    if not png.exists():
        png.write_bytes(image)
    inputs = output / 'input'
    inputs.mkdir()
    for name in ('conversation.txt', 'opis.txt', 'rubric.json', 'filtr.png'):
        shutil.copy2(PACKAGE / 'eval' / name, inputs / name)
    write_json(output / 'input-sha256.json', {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs.iterdir()})
    with tempfile.TemporaryDirectory(prefix='issue-synthesis-', dir=output) as runtime_name:
        runtime = Path(runtime_name)
        env = {**os.environ, 'HOME': str(runtime / 'home'), 'XDG_DATA_HOME': str(runtime / 'data'),
               'XDG_CONFIG_HOME': str(runtime / 'config'), 'XDG_CACHE_HOME': str(runtime / 'cache'),
               'XDG_STATE_HOME': str(runtime / 'state'), 'CODEX_HOME': str(runtime / 'home/.codex'),
               'CLAUDE_CONFIG_DIR': str(runtime / 'home/.claude'), 'PYTHONPATH': '',
               'GIT_CONFIG_GLOBAL': str(runtime / 'gitconfig'), 'GIT_CONFIG_NOSYSTEM': '1',
               'TIDE_CLAUDE_BIN': '', 'TIDE_CODEX_DESKTOP': '', 'TIDE_ACTIVITY_LOOK_ENGINE': '',
               'TIDE_YOUTRACK_CONFIG': str(runtime / 'no-youtrack'), 'TIDE_KAMP': '',
               'TIDE_MODELS_CONFIG': str(runtime / 'no-models'), 'LIMITY_CONFIG': str(runtime / 'no-limits'),
               'DOCKER_HOST': 'unix://' + str(runtime / 'no-docker'),
               'WAYLAND_DISPLAY': str(runtime / 'no-wayland'), 'QT_QPA_PLATFORM': 'offscreen'}
        for name in ('HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                     'XDG_STATE_HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
            Path(env[name]).mkdir(parents=True, exist_ok=True)
        Path(env['GIT_CONFIG_GLOBAL']).write_text('[user]\n name = Tide tests\n email = tests@localhost\n[commit]\n gpgsign = false\n')
        # Tylko uwierzytelnienie, nigdy config.toml, settings.json, hooki ani MCP.
        for source, destination in ((original_codex / 'auth.json', Path(env['CODEX_HOME']) / 'auth.json'),
                                    (original_home / '.claude/.credentials.json',
                                     Path(env['CLAUDE_CONFIG_DIR']) / '.credentials.json')):
            if source.is_file():
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
        install_env = {**env, 'PATH': str(Path(programs['codex']).parent) + os.pathsep + env['PATH']}
        command([sys.executable, str(PACKAGE / 'install.py')], install_env, runtime,
                output / 'install-package')
        binary = runtime / 'bin'
        binary.mkdir()
        real_cli = binary / 'tide'
        shutil.copy2(REPO / 'cli/tide.py', real_cli)
        real_cli.chmod(0o755)
        env['PATH'] = str(binary) + os.pathsep + env['PATH']
        window = Path(env['XDG_DATA_HOME']) / 'tide/tide-window/tide.py'
        window.parent.mkdir(parents=True)
        window.write_text('import pathlib,sys\npathlib.Path(' + repr(str(output / 'opened')) +
                          ').write_text(sys.argv[-1])\n')
        config = runtime / 'bus.conf'
        config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=' + str(runtime) +
                          '</listen><auth>EXTERNAL</auth><policy context="default">'
                          '<allow send_destination="*"/><allow own="*"/></policy></busconfig>')
        bus = subprocess.Popen(['dbus-daemon', '--config-file=' + str(config), '--nofork',
                                '--nopidfile', '--print-address=1'], stdout=subprocess.PIPE, text=True)
        daemon = None
        try:
            env['DBUS_SESSION_BUS_ADDRESS'] = bus.stdout.readline().strip()
            if not env['DBUS_SESSION_BUS_ADDRESS']:
                raise RuntimeError('Prywatna magistrala nie wystartowała.')
            flags = ['--no-processes', '--no-notifications', '--no-tab-marks', '--no-idle',
                     '--no-report', '--no-limits', '--no-tasks', '--no-coordination', '--no-youtrack',
                     '--no-models', '--no-docker', '--no-krunner', '--no-monitors', '--no-activity-look',
                     '--no-stream-shortcuts']
            with (output / 'daemon.log').open('w') as log:
                daemon = subprocess.Popen([sys.executable, str(REPO / 'daemon/tide-daemon.py'),
                                           '--board-dir', str(output / 'board'), '--workspace', str(runtime),
                                           '--claude-dir', str(runtime / 'claude'), '--codex-dir',
                                           str(runtime / 'codex'), *flags], env=env, stdout=log, stderr=log)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                ready = subprocess.run([str(real_cli), 'info', '--json'], env=env, capture_output=True)
                if ready.returncode == 0:
                    break
                if daemon.poll() is not None:
                    raise RuntimeError('Daemon zakończył się; zobacz daemon.log.')
                time.sleep(.1)
            else:
                raise RuntimeError('Brak gotowości API po 20 sekundach.')
            from PyQt6.QtCore import QCoreApplication
            from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
            app = QCoreApplication.instance() or QCoreApplication([])
            connection = QDBusConnection.connectToBus(env['DBUS_SESSION_BUS_ADDRESS'], 'issue-eval')
            msg = QDBusMessage.createMethodCall('org.tide.Daemon', '/Board', 'org.tide.Daemon.Board', 'UpsertProject')
            msg.setArguments([json.dumps({'name': 'plasma-tide'})])
            reply = connection.call(msg, QDBus.CallMode.Block, 10000)
            if not reply.arguments() or not json.loads(reply.arguments()[0]).get('ok'):
                raise RuntimeError('Nie utworzono izolowanego projektu.')
            write_json(output / 'project.json', json.loads(reply.arguments()[0]))
            for program, model in (('claude', args.claude_model), ('codex', args.codex_model)):
                evaluate_one(program, model, programs[program], output, inputs, runtime, env)
            QDBusConnection.disconnectFromBus('issue-eval')
        finally:
            for process in (daemon, bus):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if bus.stdout:
                bus.stdout.close()
    write_json(output / 'status.json', {'result': 'needs-rubric', 'programs': list(programs),
                                       'message': 'Oceń oba raporty i wyniki CLI; nie jest to automatyczny pass.'})


def evaluate_one(program, model, executable, output, inputs, runtime, env):
    folder = output / program
    folder.mkdir()
    version = command([executable, '--version'], env, runtime, folder / 'version').strip()
    root = (Path(env['XDG_DATA_HOME']) / 'tide/agents/claude/tide' if program == 'claude' else
            Path(env['HOME']) / '.codex/skills/tide-issue')
    entry = root / ('commands/issue.md' if program == 'claude' else 'SKILL.md')
    helper = [sys.executable, str(root / 'scripts/issue_adapter.py')]
    initial = folder / 'initial.md'
    initial.write_text('Relacja użytkownika o filtrze projektu po restarcie. Synteza w toku.\n')
    begin = command([*helper, 'begin', '--session', str(folder / 'session'), '--title',
                     'Filtr projektu po restarcie Tide', '--project', 'plasma-tide', '--report', str(initial)],
                    env, runtime, folder / 'begin')
    instructions = entry.read_text() + '\n\n' + (root / 'references/synthesis.md').read_text()
    (folder / 'instructions.md').write_text(instructions)
    protocol = ('Protokół kontrolowanej oceny: jesteś na etapie syntezy. Helper już wykonał begin; '
                'wynik: ' + begin + '\nPo Twojej odpowiedzi helper wykona finish, dołączy filtr.png i opis.txt '
                'oraz oznaczy --missing "drugi obraz". Nie wykonuj operacji narzędziowych ani nie czytaj '
                'projektu. Zwróć wyłącznie końcową treść raportu Markdown bez frontmatteru i bez ogrodzenia '
                'bloku kodu. Rozróżnij dostępne pliki od potwierdzenia ich skopiowania: transport dopiero '
                'nastąpi. Raport ma pozostać draft. Drugi obraz nie ma bajtów. Użytkownik nie zrezygnował '
                'z niego. Tekst opis.txt:\n' + (inputs / 'opis.txt').read_text() + '\n\nRozmowa:\n' +
                (inputs / 'conversation.txt').read_text())
    prompt = instructions + '\n\n' + protocol
    (folder / 'prompt.txt').write_text(prompt)
    report_file = folder / 'model-report.md'
    observed = []
    if program == 'claude':
        mcp = runtime / 'empty-mcp.json'
        write_json(mcp, {'mcpServers': {}})
        message = {'type': 'user', 'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': prompt},
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                       'data': base64.b64encode((inputs / 'filtr.png').read_bytes()).decode()}}]}}
        raw = command([executable, '-p', '--model', model, '--setting-sources', '', '--strict-mcp-config',
                       '--mcp-config', str(mcp), '--tools', '', '--plugin-dir', str(root),
                       '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose'],
                      env, runtime, folder / 'model', json.dumps(message) + '\n')
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        finals = [e for e in events if e.get('type') == 'result']
        if not finals or finals[-1].get('is_error') or not finals[-1].get('result', '').strip():
            raise RuntimeError('Claude nie zwrócił raportu; zobacz model.stdout.')
        report_file.write_text(finals[-1]['result'])
        observed = sorted({e['message']['model'] for e in events if e.get('message', {}).get('model')})
    else:
        command([executable, '--ask-for-approval', 'never', 'exec', '--model', model,
                 '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--json',
                 '--image', str(inputs / 'filtr.png'), '--output-last-message', str(report_file), '-'],
                env, runtime, folder / 'model', prompt)
        # Nie zgaduj resolved model, jeśli format zdarzeń programu go nie ujawnia.
    report = report_file.read_text(encoding='utf-8')
    if not report.strip():
        raise RuntimeError('Program nie zapisał raportu.')
    final = json.loads(command([*helper, 'finish', '--session', str(folder / 'session'),
                               '--title', 'Filtr projektu po restarcie Tide', '--project', 'plasma-tide',
                               '--report', str(report_file), '--file', str(inputs / 'filtr.png'),
                               '--file', str(inputs / 'opis.txt'), '--missing', 'drugi obraz'],
                              env, runtime, folder / 'finish'))
    real_cli = str(runtime / 'bin/tide')
    issue = json.loads(command([real_cli, 'issue', 'show', final['id'], '--json'],
                              env, runtime, folder / 'show'))['issue']
    (folder / 'report.md').write_text(issue['report'])
    if issue['state'] != 'draft' or len(issue['attachments']) != 2:
        raise RuntimeError('Oczekiwano draft z dwoma dostępnymi dowodami.')
    copies = folder / 'copies'
    copies.mkdir()
    for attachment in issue['attachments']:
        destination = copies / attachment['name']
        command([real_cli, 'issue', 'attachment', final['id'], '--attachment', attachment['id'],
                 '--expected-version', str(issue['version']), '--output', str(destination), '--json'],
                env, runtime, folder / ('read-' + attachment['id']))
        if destination.read_bytes() != (inputs / attachment['name']).read_bytes():
            raise RuntimeError('Kopia dowodu różni się od wejścia.')
    write_json(folder / 'metadata.json', {'program': program, 'version': version, 'requestedModel': model,
                                         'observedModels': observed, 'timestamp': datetime.now(timezone.utc).isoformat(),
                                         'issue': final['id'], 'state': issue['state'], 'versionOfIssue': issue['version'],
                                         'protocol': 'real-synthesis-cli-transport-separate',
                                         'adapterSha256': hashlib.sha256(instructions.encode()).hexdigest()})
    rubric = json.loads((inputs / 'rubric.json').read_text())
    write_json(folder / 'grading.json', {'verdict': 'pending', 'items': [
        {**item, 'result': 'pending', 'evidence': ''} for item in rubric]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--claude-model', required=True)
    parser.add_argument('--codex-model', required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Zapisano rzeczywiste raporty. Wymagają osobnej oceny rubryką: ' + str(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
