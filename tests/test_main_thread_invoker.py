"""Root-cause validation + regression tests for the missing voice reply.

Reproduces why no reply followed ``chat submit: how r u how are you``:
``FoxBrain.capture_async``/``retrieve_async`` build their Qt signal bridges
on the *calling* thread. Voice transcriptions run on a plain ``threading.Thread``
with no Qt event loop, so the bridge's queued ``done``/``error`` events were
posted to a dispatcher-less thread and never delivered — the LLM was never
asked, and no reply/error was ever logged.

``MainThreadInvoker`` fixes this by hopping the voice/LLM callbacks onto the GUI
thread.
"""
import threading
import time

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.qt_bridge import MainThreadInvoker


@pytest.fixture(scope="module")
def app():
    inst = QApplication.instance() or QApplication([])
    yield inst


class _Bridge(QObject):
    """Minimal replica of the Qt bridge in FoxBrain.capture_async/retrieve_async."""
    done = pyqtSignal(object)


def _pump(app, predicate, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_root_cause_bridge_on_loopless_thread_never_delivers(app):
    """A bridge created on a worker thread with no event loop drops its slots.

    This is exactly why the voice reply was never generated.
    """
    delivered = []
    holder = {}

    def worker():
        bridge = _Bridge()            # constructed on the worker thread
        bridge.done.connect(delivered.append)
        holder["bridge"] = bridge

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    holder["bridge"].done.emit("reply")
    # The queued event targets the (now dead) worker thread; pumping the GUI
    # loop never delivers it.
    assert not _pump(app, lambda: bool(delivered), timeout_s=1.0)
    assert delivered == []


def test_invoker_delivers_from_worker_thread(app):
    """The fix: a MainThreadInvoker (created on the GUI thread) receives work
    from a worker thread and runs it on the main thread."""
    invoker = MainThreadInvoker()
    ran = []

    def task():
        ran.append(threading.current_thread().name)

    worker = threading.Thread(target=lambda: invoker.invoke(task))
    worker.start()
    worker.join()

    assert _pump(app, lambda: bool(ran), timeout_s=3.0)
    assert ran == ["MainThread"]  # executed on the Qt/python main thread


def test_invoker_runs_synchronously_from_main_thread(app):
    """Already on the GUI thread -> direct connection, runs immediately."""
    invoker = MainThreadInvoker()
    ran = []
    invoker.invoke(lambda: ran.append(True))
    assert ran == [True]


def test_invoker_survives_task_errors(app):
    """A failing marshalled task is logged, not propagated, and later tasks run."""
    invoker = MainThreadInvoker()
    ran = []

    def boom():
        raise RuntimeError("boom")

    invoker.invoke(boom)
    invoker.invoke(lambda: ran.append(True))

    assert ran == [True]


def test_threading_smoke_chain(app):
    """End-to-end-shaped check: worker -> invoke -> main thread, in order."""
    invoker = MainThreadInvoker()
    order = []

    def worker_job():
        invoker.invoke(lambda: order.append("main"))

    worker = threading.Thread(target=worker_job)
    worker.start()
    worker.join()

    assert _pump(app, lambda: order == ["main"], timeout_s=3.0)
    assert order == ["main"]