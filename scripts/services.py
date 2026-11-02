"""Local Windows service supervisor. Shutdown uses owned Popen handles only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.cache' / 'services'
STATE = RUNTIME / 'state.json'
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
URLS = {'chroma': 'http://127.0.0.1:8100/api/v2/heartbeat',
        'api': 'http://127.0.0.1:8091/api/health', 'frontend': 'http://127.0.0.1:5174/'}


class ProcessGroup:
    """Windows closes the complete owned process tree when the handle closes."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong), ('flags', w.DWORD),
                        ('minimum', c.c_size_t), ('maximum', c.c_size_t), ('active', w.DWORD),
                        ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', c.c_ulonglong * 6), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]
        self.kernel = c.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended(); limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            raise RuntimeError('Unable to create the project process group.')
    def add(self, child):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            child.terminate()
            raise RuntimeError('Unable to attach a service to the project process group.')
    def close(self):
        self.kernel.CloseHandle(self.handle)


def read_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def healthy(url):
    try:
        with build_opener(ProxyHandler({})).open(url, timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def live(state):
    return state.get('status') in ('starting', 'running', 'stopping') and time.time() - state.get('heartbeat', 0) < 10


def write_state(value):
    value['heartbeat'] = time.time()
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(STATE)


def supervise():
    import msvcrt
    lock = (RUNTIME / 'supervisor.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return  # Another supervisor owns the project; never start duplicate workers.
    token = uuid.uuid4().hex
    state = dict(token=token, pid=os.getpid(), status='starting', services={})
    children, logs = {}, []
    group = ProcessGroup()
    stop_file = RUNTIME / f'stop-{token}'
    try:
        write_state(state)
        if any(healthy(url) for url in URLS.values()):
            raise RuntimeError('A service port is already occupied. Stop the separately started project services first.')
        sys.path.insert(0, str(ROOT / 'backend'))
        from app.db import session_factory
        from sqlalchemy import text
        with session_factory()() as db:
            db.execute(text('SELECT 1'))
        node = shutil.which('node')
        vite = ROOT / 'frontend' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
        if not node or not vite.is_file():
            raise RuntimeError('Node.js or frontend dependencies are missing. Run npm ci in frontend first.')
        commands = {
            'chroma': [str(PYTHON), 'scripts/run_chroma.py', 'run', '--path', './data/chroma', '--host', '127.0.0.1', '--port', '8100'],
            'api': [str(PYTHON), '-m', 'uvicorn', 'app.main:app', '--app-dir', 'backend', '--host', '127.0.0.1', '--port', '8091', '--no-access-log'],
            'worker': [str(PYTHON), '-u', '-m', 'app.worker'],
            'frontend': [node, str(vite), '--host', '127.0.0.1', '--port', '5174', '--strictPort'],
        }
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT / 'backend')
        env['PYTHONIOENCODING'] = 'utf-8'
        for name, command in commands.items():
            output = (RUNTIME / f'{name}.log').open('ab', buffering=0)
            logs.append(output)
            children[name] = subprocess.Popen(command, cwd=ROOT / 'frontend' if name == 'frontend' else ROOT, env=env, stdin=subprocess.DEVNULL,
                                              stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
            group.add(children[name])
            state['services'][name] = children[name].pid
        deadline = time.monotonic() + 60
        while not stop_file.exists():
            if any(child.poll() is not None for child in children.values()):
                raise RuntimeError('A project service exited. See the service logs in .cache/services.')
            if state['status'] == 'starting':
                if all(healthy(url) for url in URLS.values()):
                    state['status'] = 'running'
                elif time.monotonic() > deadline:
                    raise RuntimeError('Service startup timed out. See .cache/services logs.')
            write_state(state)
            time.sleep(1)
        state['status'] = 'stopping'
        write_state(state)
    except Exception as error:
        state['status'] = 'failed'
        # Database exception messages may contain connection metadata.
        state['error'] = str(error) if type(error) is RuntimeError else 'Startup failed. Check MySQL availability and project configuration.'
    finally:
        group.close()
        for child in children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.terminate()
        for output in logs:
            output.close()
        stop_file.unlink(missing_ok=True)
        if state['status'] != 'failed':
            state['status'] = 'stopped'
        write_state(state)
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status', '_run'])
    action = parser.parse_args().action
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if action == '_run':
        supervise()
        return 0
    state = read_state()
    if action == 'status':
        print('Supervisor:', state.get('status', 'not started') if live(state) else 'not running')
        for name, url in URLS.items():
            print(name + ':', 'ready' if healthy(url) else 'offline')
        print('worker:', 'running' if live(state) and state.get('status') == 'running' else 'not managed')
        return 0
    if action == 'stop':
        if not live(state):
            print('No managed services are running. Other project processes were not touched.')
            return 0
        (RUNTIME / ('stop-' + state['token'])).touch()
        for _ in range(50):
            if read_state().get('status') in ('stopped', 'failed'):
                print('Project services stopped. MySQL and stored data were preserved.')
                return 0
            time.sleep(1)
        print('Shutdown timed out. See .cache/services/state.json.')
        return 1
    started_at = time.time()
    if live(state):
        if state.get('status') == 'running':
            print('Project services already running: http://127.0.0.1:5174/')
            return 0
    else:
        output = (RUNTIME / 'supervisor.log').open('ab', buffering=0)
        subprocess.Popen([str(PYTHON), str(Path(__file__).resolve()), '_run'], cwd=ROOT,
                         stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
        output.close()
    for _ in range(70):
        state = read_state()
        if live(state) and state.get('status') == 'running':
            print('Project ready: http://127.0.0.1:5174/')
            print('API, Chroma, worker and frontend are running. Logs: .cache/services/')
            return 0
        if state.get('status') == 'failed' and state.get('heartbeat', 0) >= started_at:
            print(state.get('error', 'Startup failed.'))
            return 1
        time.sleep(1)
    print('Startup timed out. See .cache/services/supervisor.log.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
