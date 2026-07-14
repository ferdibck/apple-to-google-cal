from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, cast


class InstanceAlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = cast(BinaryIO, self.path.open("a+b"))
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise InstanceAlreadyRunningError(
                        "Another synchronizer instance is running."
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(  # type: ignore[attr-defined]
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                    )
                except OSError as exc:
                    raise InstanceAlreadyRunningError(
                        "Another synchronizer instance is running."
                    ) from exc
        except Exception:
            handle.close()
            raise
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        handle.close()
        self._handle = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
