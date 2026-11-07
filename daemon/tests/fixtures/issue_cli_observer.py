"""Obserwator rzeczywistego CLI; kontroluje wyłącznie utratę odpowiedzi i przerwanie."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
with open(os.environ['ISSUE_CALLS'], 'a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[:2] == ['issue', 'open'] and os.environ.get('ISSUE_PAUSE'):
    Path(os.environ['ISSUE_PAUSE']).write_text(args[2])
    time.sleep(30)
process = subprocess.run([os.environ['REAL_TIDE'], *args], capture_output=True)
flag = Path(os.environ.get('ISSUE_LOSE_REPLY', '/nonexistent/no-flag'))
match = os.environ.get('ISSUE_LOSE_REPLY_COMMAND', 'issue create').split()
if args[:len(match)] == match and process.returncode == 0 and flag.exists():
    flag.unlink()
    print(json.dumps({'ok': False, 'error': 'unavailable', 'message': 'utracona odpowiedź'}))
    raise SystemExit(3)
sys.stdout.buffer.write(process.stdout)
sys.stderr.buffer.write(process.stderr)
raise SystemExit(process.returncode)
