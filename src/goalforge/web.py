"""Loopback-only local UI backed by the same engine as the CLI."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shlex
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .engine import Engine
from .provider import Budget, Client
from .state import Store, create_run, git
from .workspace import Workspace


class WebApp:
    def __init__(self, repo: Path, runs: Path, config: dict):
        self.repo, self.runs, self.config = repo.resolve(), runs.resolve(), config
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.thread = None
        self.active = None
        self.cancelled = threading.Event()
        self.approvals = {}
        self.current_client = None
        self.initial_usage = {}

    def store(self, run_id):
        if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id or run_id in {'.', '..'}:
            raise ValueError('Invalid run ID')
        store = Store(self.runs / run_id, self.config.get('LLM_API_KEY', ''))
        if not store.file.is_file():
            raise ValueError('Run not found')
        return store

    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def snapshot(self, run_id, cursor=0):
        store = self.store(run_id)
        events = []
        path = store.root / 'events.jsonl'
        # Byte cursors avoid retransmitting the complete trace on every poll.
        if path.exists():
            with path.open('rb') as stream:
                if cursor < 0 or cursor > path.stat().st_size:
                    raise ValueError('Invalid event cursor')
                stream.seek(cursor)
                for _ in range(250):
                    line = stream.readline()
                    if not line.endswith(b'\n'):
                        break  # A concurrent writer may still be finishing this record.
                    events.append(json.loads(line))
                    cursor = stream.tell()
        with self.lock:
            approvals = [{k: v for k, v in a.items() if k not in {'done', 'answer'}}
                         for a in self.approvals.values() if a['run_id'] == run_id]
            running = self.active == run_id and self.busy()
            live_usage = None
            if running and self.current_client:
                budget = self.current_client.budget
                live_usage = {name: self.initial_usage.get(name, 0) + getattr(budget, field)
                              for name, field in [('requests', 'used'), ('input_tokens', 'input_tokens'),
                                                  ('output_tokens', 'output_tokens')]}
        return store.redact(dict(state=store.read(), events=events, cursor=cursor,
                                 approvals=approvals, running=running, live_usage=live_usage,
                                 stopping=running and self.cancelled.is_set()))

    def start(self, data, resume=False):
        with self.lock:
            if self.busy():
                raise ValueError('A run is already active. Pause it before starting another.')
            key = self.config.get('LLM_API_KEY')
            if not key:
                raise ValueError('Set LLM_API_KEY in the server .env file, then restart the server.')
            def number(name, default, maximum):
                value = data.get(name, default)
                if isinstance(value, bool):
                    raise ValueError(f'Invalid {name}')
                value = int(value)
                if not 1 <= value <= maximum:
                    raise ValueError(f'{name} must be between 1 and {maximum}')
                return value
            workers = number('workers', 3, 8)
            calls = number('max_calls', 80, 10000)
            iterations = number('iterations', 5, 100)
            steps = number('role_steps', 12, 100)
            instructions = str(data.get('instructions', '')).strip()
            if len(instructions) > 16000:
                raise ValueError('Instructions must be under 16,000 characters')
            store = self.store(data.get('id')) if resume else None
            prior = store.read() if store else {}
            model = str(data.get('model') or self.config.get('LLM_MODEL') or prior.get('model', ''))
            base = self.config.get('LLM_BASE_URL') or prior.get('base_url')
            if not model or not base:
                raise ValueError('Configure LLM_MODEL and LLM_BASE_URL in the server .env file.')
            client = Client(base, model, key, Budget(calls), 90, 4096)
            if resume:
                with store.lock():
                    prior = store.read()
                    if prior['status'] == 'complete' and not instructions:
                        raise ValueError('This run is complete. Enter follow-up instructions to continue.')
                    if instructions:
                        goal = prior['goal'] + '\n\nFollow-up instructions:\n' + instructions
                        if len(goal) > 16000:
                            raise ValueError('Combined goal is too long; start a new run.')
                        prior['goal'] = goal
                        prior['status'] = 'ready'
                        store.event('user_instruction', text=instructions)
                    prior.update(model=model, base_url=base)
                    store.write(prior)
            else:
                goal = str(data.get('goal', '')).strip()
                if not goal or len(goal) > 16000:
                    raise ValueError('Enter a goal under 16,000 characters')
                checks = [shlex.split(line) for line in str(data.get('checks', '')).splitlines() if line.strip()]
                if not checks or any(not c for c in checks):
                    raise ValueError('Enter at least one verification command (one per line).')
                protected = [s.strip() for s in str(data.get('protected', '')).splitlines() if s.strip()]
                if any(Path(p).is_absolute() or '..' in Path(p).parts for p in protected):
                    raise ValueError('Protected paths must be repository-relative')
                repo = Path(str(data.get('repo') or self.repo)).expanduser().resolve()
                store = create_run(repo, self.runs, goal, checks, protected,
                                   instructions or 'Follow existing project conventions. Make focused, maintainable changes.', model, base)
            store.secret = key
            state = store.read()
            self.cancelled = threading.Event()
            self.active = state['id']
            self.current_client = client
            self.initial_usage = state.get('usage', {}).copy()
            self.approvals.clear()

            def approve(argv):
                entry = dict(id=secrets.token_hex(8), run_id=state['id'], argv=argv,
                             done=threading.Event(), answer=False)
                with self.lock:
                    if self.cancelled.is_set():
                        return False
                    self.approvals[entry['id']] = entry
                store.event('web_approval_requested', id=entry['id'], argv=argv)
                while not entry['done'].wait(.2):
                    if self.cancelled.is_set():
                        break
                with self.lock:
                    self.approvals.pop(entry['id'], None)
                answer = entry['answer'] and not self.cancelled.is_set()
                store.event('web_approval_resolved', id=entry['id'], allowed=answer)
                return answer

            def execute():
                try:
                    workspace = Workspace(Path(state['workspace']), approve, 120, state['protected'], key)
                    engine = Engine(store, client, workspace, steps,
                                    lambda message: store.event('log', message=message),
                                    workers=workers, cancelled=self.cancelled)
                    engine.execute(iterations)
                except Exception as exc:
                    store.event('server_error', message=str(exc))
                finally:
                    store.event('web_run_stopped')
            self.thread = threading.Thread(target=execute, name='goalforge-web-run')
            self.thread.start()
            return {'id': state['id']}

    def pause(self, run_id):
        with self.lock:
            if self.active != run_id or not self.busy():
                raise ValueError('This run is not active in this server')
            self.cancelled.set()
            return {'message': 'Pause requested; waiting for in-flight requests and commands to finish.'}

    def integrate(self, run_id):
        with self.lock:
            if self.busy():
                raise ValueError('Pause the active run before integrating changes')
            store = self.store(run_id)
            with store.lock():
                state = store.read()
                repo = Path(state['repo'])
                if git(repo, 'status', '--porcelain'):
                    raise ValueError('Commit or stash changes in the original folder before integrating')
                if state['accepted_commit'] == state['start_commit']:
                    raise ValueError('This run has no accepted changes to integrate')
                # Merge only the reviewed commit, not potentially unreviewed branch movement.
                result = git(repo, 'merge', '--ff-only', state['accepted_commit'])
                store.event('user_integrated', commit=state['accepted_commit'], repo=str(repo))
                return {'message': result, 'repo': str(repo)}

    def resolve_approval(self, data):
        with self.lock:
            entry = self.approvals.get(data.get('id'))
            if entry is None or entry['done'].is_set() or type(data.get('allow')) is not bool:
                raise ValueError('Invalid or expired approval')
            entry['answer'] = data['allow']
            entry['done'].set()
            return {'ok': True}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value, status=200, content_type='application/json'):
        if content_type == 'application/json':
            value = Store(self.server.app.runs, self.server.app.config.get('LLM_API_KEY', '')).redact(value)
        payload = value.encode() if isinstance(value, str) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self):
        # Reject DNS rebinding and cross-site requests, even on a local server.
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host not in allowed or (origin and origin != 'http://' + host):
            self.respond({'error': 'Local same-origin requests only'}, 403)
            return False
        if self.path.startswith('/api/') and not secrets.compare_digest(
                self.headers.get('X-GoalForge-Token', ''), self.server.app.token):
            self.respond({'error': 'Invalid session token. Open the URL printed by the server.'}, 403)
            return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        app = self.server.app
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        arg = lambda name, default='': query.get(name, [default])[0]
        try:
            if url.path in {'/', '/app.js', '/style.css'}:
                name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[url.path]
                types = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}
                return self.respond((Path(__file__).parent / 'web_assets' / name).read_text(), content_type=types[url.path])
            if url.path == '/api/config':
                return self.respond(dict(repo=str(app.repo), model=app.config.get('LLM_MODEL', ''),
                                         key_configured=bool(app.config.get('LLM_API_KEY'))))
            if url.path == '/api/runs':
                runs = []
                for p in sorted(app.runs.glob('*/state.json'), reverse=True):
                    try:
                        state = json.loads(p.read_text())
                        runs.append({k: state.get(k) for k in ('id', 'goal', 'status', 'repo', 'model')})
                    except (OSError, ValueError):
                        continue
                return self.respond(app.store(runs[0]['id']).redact(runs) if runs else [])
            if url.path == '/api/run':
                return self.respond(app.snapshot(arg('id'), int(arg('cursor', '0'))))
            if url.path == '/api/folder':
                root = Path(arg('path', str(app.repo))).expanduser().resolve()
                if not root.is_dir():
                    raise ValueError('Folder not found')
                children = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith('.'))[:500]
                return self.respond(dict(path=str(root), parent=str(root.parent), folders=children))
            if url.path == '/api/patch':
                store = app.store(arg('id'))
                attempt = int(arg('attempt', '0'))
                agent = arg('agent')
                if attempt < 1 or agent not in {f'coder-{n}' for n in range(1, 9)}:
                    raise ValueError('Invalid worker or attempt')
                path = store.root / 'workers' / f'attempt-{attempt}' / f'{agent}.patch'
                if not path.is_file():
                    return self.respond({'patch': '', 'available': False})
                return self.respond(store.redact({'patch': path.read_text()[:200000], 'available': True}))
            if url.path in {'/api/files', '/api/file', '/api/diff'}:
                store = app.store(arg('id'))
                state = store.read()
                workspace = Workspace(Path(state['workspace']), lambda _: False)
                if url.path == '/api/files':
                    return self.respond(workspace.files())
                if url.path == '/api/file':
                    return self.respond(store.redact(workspace.execute('read_file', dict(path=arg('path'), start=int(arg('start','1')), count=500), False)))
                diff = git(workspace.root, 'diff', '--no-ext-diff', '--no-textconv', state['start_commit'], '--', strip=False)
                return self.respond(store.redact(dict(diff=diff[:200000], truncated=len(diff)>200000)))
            self.respond({'error': 'Not found'}, 404)
        except (ValueError, OSError, RuntimeError) as exc:
            self.respond({'error': str(exc)}, 400)

    def do_POST(self):
        if not self.authorized():
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100000:
                raise ValueError('Request body must be under 100 KB')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            app = self.server.app
            if self.path == '/api/start':
                result = app.start(data)
            elif self.path == '/api/resume':
                result = app.start(data, resume=True)
            elif self.path == '/api/pause':
                result = app.pause(data.get('id'))
            elif self.path == '/api/integrate':
                result = app.integrate(data.get('id'))
            elif self.path == '/api/approve':
                result = app.resolve_approval(data)
            else:
                return self.respond({'error': 'Not found'}, 404)
            self.respond(result)
        except (ValueError, OSError, RuntimeError, TypeError) as exc:
            self.respond({'error': str(exc)}, 400)


def serve(repo, runs, config, port=8765, open_browser=True):
    app = WebApp(repo, runs, config)
    server = Server(('127.0.0.1', port), app)
    url = f'http://127.0.0.1:{server.server_port}/#token={app.token}'
    print('GoalForge local UI: ' + url, flush=True)
    print('Press Ctrl-C to stop. API keys stay on the server.', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nPausing active run; waiting for in-flight work to finish...', flush=True)
    finally:
        app.cancelled.set()
        if app.thread:
            app.thread.join()
        server.server_close()
    return 0
