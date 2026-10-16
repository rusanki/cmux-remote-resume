#!/usr/bin/env python3
"""Ask the Codex app server what it thinks of the installed hooks.

    cd <project> && python3 <remote-root>/remote/check-hooks.py

Prints the server's errors/warnings for the current directory and one line per
hook with its key, hash, trust status and enabled flag, so a hook that was
installed but is not trusted shows up here instead of silently doing nothing.
"""
import json
import os
import selectors
import subprocess
import time

server = subprocess.Popen(['codex', 'app-server', '--listen', 'stdio://'],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, bufsize=1)
selector = selectors.DefaultSelector()
selector.register(server.stdout, selectors.EVENT_READ)


def request(request_id, method, params):
    server.stdin.write(json.dumps(dict(id=request_id, method=method, params=params)) + '\n')
    server.stdin.flush()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not selector.select(1):
            continue
        line = server.stdout.readline()
        if not line:
            raise RuntimeError('server closed')
        message = json.loads(line)
        if message.get('id') == request_id:
            if 'error' in message:
                raise RuntimeError(message['error'])
            return message['result']
    raise TimeoutError(method)


try:
    request(1, 'initialize', {'clientInfo': {'name': 'cmux-resume-check', 'version': '1'}})
    result = request(2, 'hooks/list', {'cwds': [os.getcwd()]})
    for entry in result['data']:
        print('errors:', entry['errors'], 'warnings:', entry['warnings'])
        for hook in entry['hooks']:
            print(json.dumps({key: hook[key] for key in ('key', 'currentHash', 'trustStatus', 'enabled')}))
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
