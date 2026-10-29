"""Trwałe operacje YouTracka w dzienniku karty; bez sekretów w zdarzeniach.

Po niepewnym założeniu zgłoszenia wolno wyłącznie odczytywać znacznik. Pusty indeks nie jest
potwierdzeniem braku zapisu. Nie wysyłamy drugiego POST nawet po długim opóźnieniu indeksu.
"""
from ..coordination import youtrack as yt


class TrackerOutbox:
    def __init__(self, journal, clock):
        self.journal = journal
        self.clock = clock

    def reset_failures(self, card):
        from .state import fold
        failures = fold(self.journal.read(card)).tracker_failures()
        for event in failures:
            self.journal.append(card, 'youtrack.reset', action=event['action'], key=event['key'],
                                rejectedResult=event['seq'], allowWrite=bool(event.get('retryWriteSafe')))
        return bool(failures)

    def run(self, card, action, key, payload):
        events = self.journal.read(card)
        reset = next((e for e in reversed(events) if e['type'] == 'youtrack.reset' and
                      e.get('action') == action and e.get('key') == key), {})
        reset_seq = reset.get('seq', 0)
        result = next((e for e in reversed(events) if e['type'] == 'result' and e['seq'] > reset_seq and
                       e.get('action') == action and e.get('key') == key), None)
        if result:
            if result.get('ok'):
                return result
            if result.get('permanent'):
                raise yt.YouTrackError(result['error'], permanent=True)
        intent = next((e for e in reversed(events) if e['type'] == 'intent' and
                       e.get('action') == action and e.get('key') == key), None)
        retry = next((e for e in reversed(events) if e['type'] == 'youtrack.retry' and e['seq'] > reset_seq and
                      e.get('action') == action and e.get('key') == key), None)
        if retry and self.clock() < retry['after']:
            raise yt.YouTrackError('YouTrack: operacja czeka na ponowienie')
        payload = (intent or {}).get('payload') or payload
        recover_only = intent is not None and (intent['seq'] > reset_seq or not reset.get('allowWrite'))
        write_started = False
        config = yt.load_config()
        try:
            if not yt.same_server(config['url'], payload['url']):
                raise yt.YouTrackError('inny serwer niż w ustawieniach YouTracka', permanent=True)
            token = yt.read_token(config)
            url = payload['url']
            if payload['op'] == 'issue':
                found = yt.find_issue(url, payload['project'], card, payload['number'], token)
                if not found and recover_only:
                    raise yt.YouTrackError('YouTrack: czekam na potwierdzenie założenia zgłoszenia')
            if intent is None or (intent['seq'] < reset_seq and not recover_only):
                intent = self.journal.append(card, 'intent', action=action, key=key, youtrack=True, payload=payload)
            if payload['op'] == 'issue':
                write_started = not found
                issue = found or yt.create_issue(url, payload['project'], payload['title'], payload['description'],
                                                card, payload['number'], token)
                data = {'ticket': issue['id'], 'title': payload['title'],
                        'map': {f"#{payload['number']}": issue['id']}}
            elif payload['op'] == 'link':
                data = yt.link_issues(url, payload['ticket'], payload['parent'], token)
            else:
                data = yt.add_comment(url, payload['ticket'], payload['text'], card, payload['ticket'],
                                      payload['event'], token)
            return self.journal.append(card, 'result', action=action, key=key, ok=True, **data)
        except yt.YouTrackError as exc:
            if exc.permanent:
                self.journal.append(card, 'result', action=action, key=key, ok=False,
                                    error=str(exc), permanent=True, retryWriteSafe=not recover_only or write_started)
            else:
                self.journal.append(card, 'youtrack.retry', action=action, key=key,
                                    after=self.clock() + 30,
                                    error=('YouTrack: czekam na potwierdzenie założenia zgłoszenia'
                                           if payload['op'] == 'issue' and intent else str(exc)))
            raise
