"""Jeden hostowy slot v1: trwała kolejka i rekonstrukcja z Incusa.

mutex chroni pomiar/dopuszczenie, lease całe zlecenie (także okno stop/start).
Koniec klienta zwalnia tylko lease. Własność znika dopiero po pełnym odczycie
Incusa: brak operacji w toku i wszystkie instancje zatrzymane lub usunięte.
Pliki pozostają na hoście, poza montowaniami pudełka.
"""
from contextlib import contextmanager
from functools import wraps
import fcntl
import json
import os
import threading
import time

GiB = 1024 ** 3
RECHECK_SECONDS = 5
_local = threading.local()
changed = threading.Event()


def read_memory():
    """Wyłącznie hostowe procfs; swap nie jest częścią budżetu."""
    values = {}
    with open('/proc/meminfo', encoding='ascii') as stream:
        for line in stream:
            key, _, value = line.partition(':')
            if key in ('MemTotal', 'MemAvailable'):
                parts = value.split()
                if key in values or len(parts) != 2 or parts[1] != 'kB':
                    raise ValueError('niepoprawne hostowe meminfo')
                values[key] = int(parts[0]) * 1024
    total, available = values['MemTotal'], values['MemAvailable']
    if not 0 <= available <= total or total <= 0:
        raise ValueError('niepoprawna pamięć hosta')
    return total, available


def memory_limit(total):
    from .builder import BuildError
    if total <= 20 * GiB:
        raise BuildError('RAM hosta ≤ 20 GiB — start/budowa odmówione')
    return min(24 * GiB, total - 20 * GiB)


def limits():
    total, _ = read_memory()
    return {'limits.cpu': 8, 'limits.memory': str(memory_limit(total))}


def folder():
    root = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(root, 'tide', 'box-admission')


@contextmanager
def locked():
    path = folder()
    os.makedirs(path, mode=0o700, exist_ok=True)
    with open(os.path.join(path, 'mutex'), 'a') as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)


def load():
    try:
        with open(os.path.join(folder(), 'state.json'), encoding='utf-8') as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {'v': 1, 'owner': None, 'queue': [], 'history': []}
    if (not isinstance(state, dict) or type(state.get('v')) is not int or state['v'] != 1
            or not isinstance(state.get('queue'), list) or not isinstance(state.get('history'), list)
            or (state.get('owner') is not None and not isinstance(state['owner'], dict))):
        raise ValueError('nieznany stan globalnego slotu')
    if state['owner'] is not None and any(not isinstance(state['owner'].get(key), str)
                                          or not state['owner'][key] for key in ('project', 'name', 'kind')):
        raise ValueError('nieznana własność globalnego slotu')
    if state['owner'] is not None and state['owner']['kind'] not in ('build', 'base', 'start', 'reset', 'recovered'):
        raise ValueError('nieznany rodzaj właściciela globalnego slotu')
    for request in state['queue']:
        if (not isinstance(request, dict)
                or any(not isinstance(request.get(key), str) or not request[key]
                       for key in ('id', 'project', 'name', 'reason'))
                or not isinstance(request.get('args'), dict)
                or request.get('kind') not in ('build', 'base', 'start')
                or request['id'] != f"{request['project']}/{request['kind']}/{request['name']}"):
            raise ValueError('nieznany stan kolejki pudełek')
    return state


def save(state):
    path = os.path.join(folder(), 'state.json')
    with open(path + '.tmp', 'w', encoding='utf-8') as stream:
        os.chmod(stream.fileno(), 0o600)
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(path + '.tmp', path)
    fd = os.open(folder(), os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def request(incus, kind, name, **args):
    return {'id': f'{incus.project}/{kind}/{name}', 'project': incus.project,
            'kind': kind, 'name': name, 'args': args}


def inventory(incus):
    """Niepełna lista, przejście stanu lub operacja serwera blokują dopuszczenie."""
    operations = incus.operations()
    if any(op.get('status') not in ('Success', 'Failure', 'Cancelled') for op in operations):
        raise ValueError('operacja Incusa w toku lub nieznana')
    instances = incus.list()
    active = []
    for inst in instances:
        if not isinstance(inst, dict) or not inst.get('name'):
            raise ValueError('niepełna lista instancji Incusa')
        status = str(inst.get('status', '')).lower()
        if status not in ('running', 'stopped'):
            raise ValueError(f"nieznany stan Incusa: {inst['name']} ({status})")
        if status == 'running':
            active.append(inst['name'])
    if len(active) > 1:
        raise ValueError('więcej niż jeden aktywny zasób Incusa — start zablokowany')
    return active


def reconcile(state, incus, busy=False):
    project = state.setdefault('incusProject', incus.project)
    if project != incus.project:
        raise ValueError('zmiana projektu Incusa wymaga uzgodnienia istniejących zasobów hosta')
    owner = state['owner']
    if owner and owner.get('project') != incus.project:
        raise ValueError('nieznana własność globalnego slotu w innym projekcie Incusa')
    active = inventory(incus)
    if busy:
        return active
    state['owner'] = ({'project': incus.project, 'name': active[0], 'kind': 'recovered'} if active else None)
    return active


def wait(state, req, reason):
    from .builder import SlotWaiting
    entry = next((q for q in state['queue'] if q['id'] == req['id']), None)
    if entry is None:
        entry = dict(req, queued=time.time())
        state['queue'].append(entry)
    entry['reason'] = reason
    save(state)
    raise SlotWaiting(reason)


@contextmanager
def operation(incus, req, *, enqueue=True):
    from .builder import BuildError, SlotWaiting
    def defer(state, reason):
        if enqueue:
            wait(state, req, reason)
        raise BuildError(reason)
    current = getattr(_local, 'request', None)
    if current is not None:
        if (current['project'], current['name']) != (req['project'], req['name']):
            raise BuildError('zagnieżdżona operacja innego pudełka jest niedozwolona')
        yield
        return
    lease = None
    acquired = False
    try:
        with locked():
            state = load()
            dispatched = getattr(_local, 'dispatched', None)
            if dispatched and not any(q['id'] == dispatched for q in state['queue']):
                raise BuildError('oczekujące zlecenie zostało anulowane')
            lease = open(os.path.join(folder(), 'lease'), 'a')
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                defer(state, 'zajęty globalny slot: trwa budowa lub operacja pudełka')
            try:
                active = reconcile(state, incus)
            except Exception as exc:
                defer(state, f'nieznany stan globalnego slotu: {exc}')
            if active and active != [req['name']]:
                defer(state, 'zajęty globalny slot: ' + active[0])
            # Ponowne wejście do już działającego pudełka nie jest nowym startem.
            reuse = req['kind'] in ('start', 'reset') and active == [req['name']]
            if state['queue'] and state['queue'][0]['id'] != req['id'] and not reuse:
                defer(state, 'czeka w kolejce na globalny slot')
            try:
                total, available = read_memory()
                limit = memory_limit(total)
            except BuildError:
                state['queue'] = [q for q in state['queue'] if q['id'] != req['id']]
                save(state)
                if state['owner'] is None:
                    changed.set()
                raise
            except (OSError, ValueError, KeyError) as exc:
                defer(state, f'brak poprawnego pomiaru pamięci hosta: {exc}')
            if not reuse and available < limit + 4 * GiB:
                defer(state, f'za mało MemAvailable: {available} B; potrzeba {limit + 4 * GiB} B')
            state['queue'] = [q for q in state['queue'] if q['id'] != req['id']]
            state['owner'] = {k: req[k] for k in ('project', 'name', 'kind')}
            save(state)  # zamiar przed jakąkolwiek mutacją Incusa
        _local.request = req
        try:
            yield
        finally:
            _local.request = None
            with locked():
                state = load()
                try:
                    reconcile(state, incus)
                    state.pop('error', None)
                except Exception as exc:
                    state['error'] = f'nieznany stan globalnego slotu: {exc}'
                save(state)
                if state['owner'] is None:
                    changed.set()
    except (BuildError, SlotWaiting):
        raise
    finally:
        if lease is not None:
            if acquired:
                fcntl.flock(lease, fcntl.LOCK_UN)
            lease.close()


def start(incus, name, *, slug=None, card=None):
    """Pomiar bezpośrednio przed startem, pod tym samym mutexem co dopuszczenie."""
    req = request(incus, 'start', name, slug=slug or name, card=card)
    with operation(incus, req):
        with locked():
            active = inventory(incus)
            if active and active != [name]:
                from .builder import BuildError
                raise BuildError('zajęty globalny slot: ' + active[0])
            try:
                total, available = read_memory()
                limit = memory_limit(total)
            except (OSError, ValueError, KeyError) as exc:
                defer_start(f'brak poprawnego pomiaru pamięci hosta: {exc}')
            if not active and available < limit + 4 * GiB:
                # Zachowujemy rodzaj i parametry pierwotnego zlecenia budowy.
                defer_start(f'za mało MemAvailable: {available} B; potrzeba {limit + 4 * GiB} B')
            incus.config_set(name, {'limits.cpu': 8, 'limits.memory': str(limit)})
            incus.start(name)


def defer_start(reason):
    req = _local.request
    if req['kind'] in ('build', 'base', 'start'):
        wait(load(), req, reason)
    from .builder import BuildError
    raise BuildError(reason)


def cancel(identifier):
    """Anulowanie wyłącznie oczekującego żądania; nigdy rezerwacji aktywnej."""
    with locked():
        state = load()
        before = len(state['queue'])
        state['queue'] = [q for q in state['queue'] if q['id'] != identifier]
        save(state)
        changed.set()
        return len(state['queue']) != before


def snapshot(incus):
    with locked():
        state = load()
        with open(os.path.join(folder(), 'lease'), 'a') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                busy = False
            except BlockingIOError:
                busy = True
            try:
                reconcile(state, incus, busy)
                state.pop('error', None)
            except Exception as exc:
                state['error'] = f'nieznany stan globalnego slotu: {exc}'
            save(state)
        return state


def queued(kind):
    """Publiczne operacje builderów zachowują parametry do ponowienia w usłudze."""
    def decorate(method):
        @wraps(method)
        def run(self, *args, **kwargs):
            if kind == 'base':
                incus, name, params = self.incus, self.settings.base_image, {}
            else:
                import inspect
                from . import state as bs
                bound = inspect.signature(method).bind(self, *args, **kwargs)
                bound.apply_defaults()
                params = {k: v for k, v in bound.arguments.items() if k != 'self'}
                slug, card = params['slug'], params.get('card')
                name = bs.card_box_name(slug, card) if card else bs.box_name(slug)
                incus = self.incus
            with operation(incus, request(incus, kind, name, **params)):
                return method(self, *args, **kwargs)
        return run
    return decorate


def tick(builder):
    """Jedna próba FIFO; wołana co 5 s oraz po potwierdzonym zwolnieniu slotu."""
    from .builder import SlotWaiting
    state = snapshot(builder.incus)  # restart zawsze zaczyna od zasobów hosta
    if state.get('error') or not state['queue']:
        return
    req = state['queue'][0]
    if state['owner'] and not (req['kind'] == 'start' and state['owner']['name'] == req['name']):
        return
    if req['project'] != builder.incus.project:
        return
    try:
        _local.dispatched = req['id']
        if req['kind'] == 'build':
            builder.build(**req['args'])
        elif req['kind'] == 'base':
            from .base import BaseBuilder
            BaseBuilder(builder.settings, builder.incus, builder.policy).build()
        elif req['kind'] == 'start':
            from .lifecycle import Lifecycle
            Lifecycle(builder).start(**req['args'])
    except SlotWaiting:
        return
    except Exception as exc:
        with locked():
            state = load()
            state['queue'] = [q for q in state['queue'] if q['id'] != req['id']]
            state['history'] = (state['history'] + [dict(req, error=str(exc))])[-100:]
            save(state)
    finally:
        _local.dispatched = None
