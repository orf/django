import contextlib
import functools

import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path
import traceback

import threading

from django.apps import apps
from django.dispatch import Signal

import signal

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

        yield path.absolute()


class BaseReloader:
    def __init__(self):
        self.extra_files = set()
        self.extra_directories = set()

    def watch(self, path, glob):
        path = Path(path)

        if glob:
            self.extra_directories.add((path, glob))
        else:
            self.extra_files.add(path.absolute())

    def watched_files(self):
        yield from iter_all_python_module_files()
        yield from self.extra_files

        for directory, pattern in self.extra_directories:
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


class StatReloader(BaseReloader):
    def run_loop(self):
        file_times = {}

        while True:
            for path, mtime in self.snapshot():
                previous_time = file_times.get(path)

                if previous_time is None:
                    file_times[path] = mtime

                elif previous_time != mtime:
                    results = file_changed.send(sender=self, file_path=path)
                    if not any(res[1] for res in results):
                        self.trigger_reload(path)
                    file_times[path] = mtime

            time.sleep(1)

    def snapshot(self):
        for file in self.watched_files():
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue

            yield file, mtime


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


def run_with_reloader(main_func, *args, **kwargs):
    import signal
    signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))

    with contextlib.suppress(KeyboardInterrupt):
        if os.environ.get(DJANGO_AUTORELOAD_ENV) == '1':
            main_func = check_errors(main_func)
            thread = threading.Thread(target=main_func, args=args, kwargs=kwargs)
            thread.setDaemon(True)
            thread.start()

            StatReloader().run()
        else:
            exit_code = StatReloader().restart_with_reloader()
            sys.exit(exit_code)
