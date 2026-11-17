"""Work on a connection that only one thread may use at a time.

A worker thread cannot be stopped. When the Runtime cancels a call (the run is cancelled or the
tool's timeout passes), the awaiting coroutine stops at once but the thread runs to its end. If
the connection's lock were released then, the next call would start a second thread on the same
connection (``imaplib`` and ``sqlite3`` connections are not safe for that). So the lock is held
until the thread ends, while the cancelled caller returns on schedule: the same rule as the
Runtime's own ``max_concurrency`` slot for synchronous tools.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable


async def run_exclusive[T](lock: asyncio.Lock, work: Callable[[], T]) -> T:
    """Run ``work`` in a worker thread while holding ``lock`` until the thread ends.

    A caller that is cancelled while waiting gets ``CancelledError`` at once; the lock is
    released only when the thread has finished, and the thread's outcome is then discarded.
    """
    await lock.acquire()
    try:
        future = asyncio.ensure_future(asyncio.to_thread(work))
    except BaseException:
        lock.release()
        raise

    def finished(done: asyncio.Future[T]) -> None:
        lock.release()
        if not done.cancelled():
            # Retrieve an outcome nobody awaits any more, so asyncio does not log it.
            with contextlib.suppress(BaseException):
                done.exception()

    future.add_done_callback(finished)
    return await asyncio.shield(future)
