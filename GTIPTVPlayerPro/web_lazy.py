"""Thread-safe, first-use web services, without a resident worker/cache."""
import importlib
import threading


class LazyService:
    def __init__(self, module, name, factory=False):
        self.module, self.name, self.factory = module, name, factory
        self.value = None
        self.lock = threading.Lock()

    def _get(self):
        with self.lock:
            if self.value is None:
                symbol = getattr(importlib.import_module('.'+self.module, __package__), self.name)
                self.value = symbol() if self.factory else symbol
            return self.value

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def shutdown(self):
        with self.lock: value = self.value
        if value is not None:
            callback = getattr(value, 'shutdown', None)
            if callable(callback): return callback()
