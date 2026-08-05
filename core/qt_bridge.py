"""Utilities for hopping from worker threads onto the Qt main thread.

Voice transcriptions arrive on ``VoiceInput``'s plain ``threading.Thread``
worker, and LLM results arrive on the ``fox-llm`` pool threads — neither has a
Qt event loop. Qt UI calls and the Qt signal bridges used by
``FoxBrain.capture_async``/``retrieve_async`` must run on the GUI thread, so
worker callbacks are marshalled there via ``MainThreadInvoker``.
"""
from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

log = get_logger("qt_bridge")


class MainThreadInvoker(QObject):
    """Queues a callable onto the Qt main thread from any worker thread.

    Emitting a signal from a worker thread is thread-safe: Qt delivers it as a
    queued connection to the receiver's event loop. Because this object is
    created on the main thread, ``invoke`` from a worker runs the callable on
    the GUI thread, and runs it synchronously when already on the main thread.
    """

    _task = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._task.connect(self._execute)

    def _execute(self, fn):
        try:
            fn()
        except Exception as e:
            log.error("marshalled UI task failed: %s", e)

    def invoke(self, fn):
        """Queue ``fn`` to run on the Qt main thread; returns immediately."""
        try:
            self._task.emit(fn)
        except Exception as e:
            log.error("failed to marshal UI task: %s", e)
