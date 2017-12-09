import contextlib
import functools
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from django.apps import apps
from django.dispatch import Signal

autoreload_started = Signal()
file_changed = Signal(providing_args=['path', 'kind'])

DJANGO_AUTORELOAD_ENV = 'RUN_MAIN'

# If an error is raised while importing a file, it is not placed
# in sys.modules. This means any future modifications are not
# caught. We keep a list of these file paths to continue to
# watch them in the future.
_error_files = []
_exception = None

try:
    import termios
except ImportError:
    termios = None


try:
    import pywatchman
except ImportError:
    pywatchman = None


def ensure_echo_on():
    if termios:
        fd = sys.stdin
        if fd.isatty():
            attr_list = termios.tcgetattr(fd)
            if not attr_list[3] & termios.ECHO:
                attr_list[3] |= termios.ECHO
                if hasattr(signal, 'SIGTTOU'):
                    old_handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                else:
                    old_handler = None
                termios.tcsetattr(fd, termios.TCSANOW, attr_list)
                if old_handler is not None:
                    signal.signal(signal.SIGTTOU, old_handler)


def iter_all_python_module_files():
    sys_file_paths = [
        getattr(module, '__file__', None)
        for module in sys.modules.values()
    ]

    for filename in sys_file_paths + _error_files:
        if not filename:
            continue

        path = pathlib.Path(filename)

        if path.suffix in {'.pyc', '.pyo'}:
            yield path.with_suffix('.py')

        yield path.resolve().absolute()


class BaseReloader:
    def __init__(self):
        self.extra_files = set()
        self.extra_globs = set()

    def watch(self, path, glob):
        path = Path(path)

        if not path.is_absolute():
            raise RuntimeError('{0} must be absolute'.format(path))

        if glob:
            self.extra_globs.add((path, glob))
        else:
            self.extra_files.add(path.absolute())

    def watched_files(self, include_globs=True):
        yield from iter_all_python_module_files()
        yield from self.extra_files

        if include_globs:
            for directory, pattern in self.extra_globs:
                yield from directory.glob(pattern)

    def run(self):
        while not apps.ready:
            time.sleep(0.1)

        autoreload_started.send(sender=self)
        self.run_loop()

    def run_loop(self):
        pass

    def get_child_arguments(self):
        """
        Returns the executable. This contains a workaround for windows
        if the executable is incorrectly reported to not have the .exe
        extension which can cause bugs on reloading.
        """
        import django.__main__

        args = [sys.executable] + ['-W%s' % o for o in sys.warnoptions]
        if sys.argv[0] == django.__main__.__file__:
            # The server was started with `python -m django runserver`.
            args += ['-m', 'django']
            args += sys.argv[1:]
        else:
            args += sys.argv

        return args

    def restart_with_reloader(self):
        print('Watching for file changes with {0}'.format(self.__class__.__name__))
        new_environ = os.environ.copy()
        new_environ[DJANGO_AUTORELOAD_ENV] = '1'
        args = self.get_child_arguments()

        while True:
            exit_code = subprocess.call(args, env=new_environ, close_fds=False)

            if exit_code != 3:
                return exit_code

    def trigger_reload(self, filename, kind='changed'):
        print('{0} {1}, reloading'.format(filename, kind))
        sys.exit(3)

    def is_available(self):
        raise NotImplementedError()

    def notify_file_changed(self, path):
        results = file_changed.send(sender=self, file_path=path)
        if not any(res[1] for res in results):
            self.trigger_reload(path)


class StatReloader(BaseReloader):
    def run_loop(self):
        file_times = {}

        while True:
            for path, mtime in self.snapshot():
                previous_time = file_times.get(path)

                if previous_time is None:
                    file_times[path] = mtime

                elif previous_time != mtime:
                    self.notify_file_changed(path)
                    file_times[path] = mtime

            time.sleep(1)

    def snapshot(self):
        for file in self.watched_files():
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue

            yield file, mtime

    def is_available(self):
        return True


class WatchmanReloader(BaseReloader):
    @property
    def subscription_name(self):
        return 'django:{0}'.format(os.getpid())

    def watch_roots(self, client):
        watched_files = list(self.watched_files(include_globs=False))
        roots = self.find_common_roots([p.parent for p in watched_files])

        for root in roots:
            children = [str(f.relative_to(root)) for f in watched_files if root in f.parents]
            watch_expression = {
                'fields': ['name'],
                'dedup_results': True,
                'empty_on_fresh_instance': True,
                'expression': ['allof', ['type', 'f'], ['name', children, 'wholename']]
            }

            client.query('subscribe', str(root), self.subscription_name, watch_expression)

        for directory, glob in self.extra_globs:
            # Watchman cannot watch roots that do not exist.
            if not directory.exists():
                continue

            glob_expression = {
                'fields': ['name'],
                'dedup_results': True,
                'empty_on_fresh_instance': True,
                'expression': ['allof', ['type', 'f'], ['match', glob, 'wholename']]
            }

            client.query('subscribe', str(directory), self.subscription_name, glob_expression)

    def run_loop(self):
        with pywatchman.client() as client:
            self.watch_roots(client)
            while True:
                try:
                    result = client.receive()
                except pywatchman.SocketTimeout as e:
                    continue

                root = Path(result['root'])
                for path in result['files']:
                    self.notify_file_changed(root / path)

    def find_common_roots(self, paths):
        """Out of some paths it finds the common roots that need monitoring."""
        paths = [x.parts for x in paths]
        root = {}
        for chunks in sorted(paths, key=len, reverse=True):
            node = root
            for chunk in chunks:
                node = node.setdefault(chunk, {})
            node.clear()

        rv = set()

        def _walk(node, path):
            for prefix, child in node.items():
                _walk(child, path + (prefix,))
            if not node:
                subpath = '/'.join(path[1:])
                rv.add(path[0] + subpath)

        _walk(root, ())

        return {Path(item) for item in rv}

    def is_available(self):
        if pywatchman is None:
            return False

        try:
            with pywatchman.client() as c:
                c.setTimeout(1)
                return 'version' in c.capabilityCheck()
        except Exception:
            return False


def raise_last_exception():
    global _exception
    if _exception is not None:
        raise _exception[0](_exception[1]).with_traceback(_exception[2])


def check_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        global _exception
        try:
            fn(*args, **kwargs)
        except Exception:
            _exception = sys.exc_info()

            et, ev, tb = _exception

            if getattr(ev, 'filename', None) is None:
                # get the filename from the last item in the stack
                filename = traceback.extract_tb(tb)[-1][0]
            else:
                filename = ev.filename

            if filename not in _error_files:
                _error_files.append(filename)

            raise

    return wrapper


def get_reloader():
    for reloader in (WatchmanReloader(), StatReloader()):
        if reloader.is_available():
            return reloader


def run_with_reloader(main_func, *args, **kwargs):
    import signal
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))

    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_ENV) == '1':
            main_func = check_errors(main_func)
            thread = threading.Thread(target=main_func, args=args, kwargs=kwargs)
            thread.setDaemon(True)
            thread.start()

            get_reloader().run()
        else:
            exit_code = get_reloader().restart_with_reloader()
            sys.exit(exit_code)
