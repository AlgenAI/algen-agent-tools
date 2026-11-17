"""How connectors use the Runtime's cancellation, cache and repeat-guard semantics (0.2.0a13).

- A cancelled or timed-out call on a thread-bound connection (IMAP, SQLite) returns at once,
  but the connection stays locked until its worker thread ends, so no second thread uses it.
- Listings of state that changes on its own are ``volatile``: never cached or held back as a
  repeat.
- Tools that store or read an artifact through the deployment's sink or loader key cached
  results on the run (``cache_context``), so a cached result never skips the loader's check.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from algen_agent_tools._core.threads import run_exclusive
from algen_agent_tools.attachments import AttachmentRef, AttachmentUpload
from algen_agent_tools.discord import discord_toolkit
from algen_agent_tools.documents import documents_toolkit
from algen_agent_tools.gmail import gmail_toolkit
from algen_agent_tools.imap import imap_toolkit
from algen_agent_tools.outlook import outlook_toolkit
from algen_agent_tools.telegram import telegram_toolkit


async def test_a_cancelled_caller_returns_but_the_lock_waits_for_the_thread() -> None:
    lock = asyncio.Lock()
    release = threading.Event()
    started = threading.Event()
    running: list[str] = []

    def slow() -> str:
        running.append("slow")
        started.set()
        release.wait(5)
        running.append("slow done")
        return "slow"

    def fast() -> str:
        running.append("fast")
        return "fast"

    first = asyncio.create_task(run_exclusive(lock, slow))
    await asyncio.to_thread(started.wait, 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first  # the caller stops waiting on schedule
    assert lock.locked()  # the thread still runs on the connection

    second = asyncio.create_task(run_exclusive(lock, fast))
    await asyncio.sleep(0.05)
    assert running == ["slow"]  # the next call waits for the thread, not for the caller
    release.set()
    assert await second == "fast"
    assert running == ["slow", "slow done", "fast"]
    assert not lock.locked()


async def test_a_timeout_does_not_let_two_threads_share_the_connection() -> None:
    lock = asyncio.Lock()
    active = 0
    overlap = False

    def work() -> None:
        nonlocal active, overlap
        active += 1
        overlap = overlap or active > 1
        time.sleep(0.1)
        active -= 1

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await run_exclusive(lock, work)
    await run_exclusive(lock, work)
    assert overlap is False


async def test_an_error_in_an_abandoned_thread_is_not_logged_as_unretrieved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lock = asyncio.Lock()
    release = threading.Event()

    def failing() -> None:
        release.wait(5)
        raise RuntimeError("boom")

    task = asyncio.create_task(run_exclusive(lock, failing))
    await asyncio.sleep(0.02)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with lock:  # released once the thread ended
        pass
    assert "exception was never retrieved" not in caplog.text


def _definitions(toolkit: Any) -> dict[str, Any]:
    return {tool.definition.name: tool.definition for tool in toolkit.tools}


async def _sink(upload: AttachmentUpload, context: Any) -> AttachmentRef:
    raise AssertionError("not called")


async def _loader(reference: AttachmentRef, context: Any) -> bytes:
    raise AssertionError("not called")


@pytest.mark.parametrize(
    ("toolkit", "volatile"),
    [
        (gmail_toolkit(), {"gmail.list_messages", "gmail.get_thread"}),
        (outlook_toolkit(), {"outlook.list_messages", "outlook.get_thread"}),
        (imap_toolkit(), {"imap.list_messages", "imap.get_thread"}),
        (telegram_toolkit(), {"telegram.list_messages"}),
        (discord_toolkit(), {"discord.list_messages"}),
    ],
)
def test_listings_of_changing_state_are_volatile(toolkit: Any, volatile: set[str]) -> None:
    definitions = _definitions(toolkit)
    assert {name for name, d in definitions.items() if d.volatile} == volatile


def test_tools_that_store_or_load_artifacts_key_cached_results_on_the_run() -> None:
    gmail = _definitions(gmail_toolkit(artifact_sink=_sink))
    assert gmail["gmail.get_attachment"].cache_context == frozenset({"run_id"})
    telegram = _definitions(telegram_toolkit(artifact_sink=_sink))
    assert telegram["telegram.get_attachment"].cache_context == frozenset({"run_id"})
    documents = _definitions(documents_toolkit(artifact_loader=_loader))
    assert documents["documents.extract_text"].cache_context == frozenset({"run_id"})
    # A listing reads only the deployment's mailbox: its cache key is unchanged.
    assert gmail["gmail.list_messages"].cache_context == frozenset()
