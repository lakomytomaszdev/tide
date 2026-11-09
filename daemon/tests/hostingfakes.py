"""Atrapy obu CLI, wyłącznie pliki scenariusza i kontrolowany wynik, bez sieci."""
import json
import os

SCRIPT = '''#!/usr/bin/python3
import json, os, sys, time
folder = os.path.dirname(__file__)
with open(os.path.join(folder, 'hosting.json')) as f:
    scenario = json.load(f)
with open(os.path.join(folder, 'hosting-calls.jsonl'), 'a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
mode = scenario.get('mode', '')
if mode == 'timeout':
    time.sleep(11)
if mode == 'blocked':
    until = time.monotonic() + 120
    while not os.path.isfile(os.path.join(folder, 'release-hosting')):
        if time.monotonic() >= until:
            print('Test nie zwolnil atrapy hostingowej', file=sys.stderr)
            sys.exit(1)
        time.sleep(0.05)
    print('Kontrolowany blad CLI po zwolnieniu bariery', file=sys.stderr)
    sys.exit(1)
if mode == 'error':
    print('FAKE_SECRET login error', file=sys.stderr)
    sys.exit(1)
if mode in ('large', 'large-stderr'):
    print('x' * (1024 * 1024 + 1), file=sys.stderr if mode == 'large-stderr' else sys.stdout)
    sys.exit(0)
if mode == 'bad-json':
    print('FAKE_SECRET not JSON')
    sys.exit(0)
if mode == 'transport':
    import http.client, socket
    from urllib.parse import urlsplit
    host = sys.argv[sys.argv.index('--hostname') + 1]
    endpoint = sys.argv[-1]
    github = os.path.basename(sys.argv[0]) == 'gh'
    api_host = 'api.github.com' if github and host == 'github.com' else host
    if github:
        with open(os.path.join(os.environ['GH_CONFIG_DIR'], 'config.yml')) as f:
            lines = f.read().splitlines()
        assert lines[0] == 'version: "1"', lines
        socket_path = json.loads(next(line for line in lines
                                      if line.startswith('http_unix_socket: ')).split(': ', 1)[1])
        class UnixHTTP(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(socket_path)
        conn = UnixHTTP(api_host)
    else:
        proxy = urlsplit(os.environ['GLAB_PROXY'])
        conn = http.client.HTTPConnection(proxy.hostname, proxy.port)
        conn.set_tunnel(scenario.get('connect_host', api_host), scenario.get('connect_port', 443))
    path = ('/' if host == 'github.com' else '/api/v3/') if github else '/api/v4/'
    path += endpoint
    # Atrapa sprawdza ramkowanie CONNECT glab, bez udawania testu TLS prawdziwego CLI.
    conn.request('GET', path, headers={'Authorization': 'Bearer FAKE_SECRET'})
    response = conn.getresponse()
    body = response.read()
    if response.status != 200:
        sys.exit(1)
    sys.stdout.buffer.write(body)
    sys.exit(0)
print(json.dumps(scenario['responses'][sys.argv[-1]]))
'''


def install(folder, scenario):
    for name in ("gh", "glab"):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(SCRIPT)
        os.chmod(path, 0o755)
    with open(os.path.join(folder, "hosting.json"), "w", encoding="utf-8") as fh:
        json.dump(scenario, fh)


def github(sha, project="team/proj", source="team/proj", branch="feature/source", host="github.com"):
    return {"number": 17, "html_url": f"https://{host}/{project}/pull/17",
            "head": {"ref": branch, "sha": sha,
                     "repo": {"full_name": source, "html_url": f"https://{host}/{source}"}},
            "base": {"ref": "release/target", "repo": {"full_name": project, "html_url": f"https://{host}/{project}"}}}


def gitlab(sha, project="team/sub/proj", source="user/sub/proj", host="git.example.org"):
    return {f"projects/{project.replace('/', '%2F')}/merge_requests/17": {
                "iid": 17, "web_url": f"https://{host}/{project}/-/merge_requests/17",
                "source_project_id": 2, "target_project_id": 1, "source_branch": "feature/source",
                "target_branch": "release/target", "sha": sha},
            "projects/1": {"id": 1, "path_with_namespace": project, "web_url": f"https://{host}/{project}"},
            "projects/2": {"id": 2, "path_with_namespace": source, "web_url": f"https://{host}/{source}"}}
