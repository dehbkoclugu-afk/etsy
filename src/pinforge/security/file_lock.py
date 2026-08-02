from __future__ import annotations

import os
import time
from typing import BinaryIO
from pathlib import Path


class FileLock:
    def __init__(self, path: str | Path, *, timeout: float = 15) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.handle: BinaryIO | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        self.handle = handle
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                _lock(handle)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    self.handle = None
                    raise TimeoutError(f"Kilit alınamadı: {self.path}")
                time.sleep(0.1)

    def __exit__(self, *_: object) -> None:
        if self.handle is not None:
            _unlock(self.handle)
            self.handle.close()
            self.handle = None


if os.name == "nt":
    import msvcrt

    def _lock(handle) -> None:  # noqa: ANN001
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]

    def _unlock(handle) -> None:  # noqa: ANN001
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _lock(handle) -> None:  # noqa: ANN001
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle) -> None:  # noqa: ANN001
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
