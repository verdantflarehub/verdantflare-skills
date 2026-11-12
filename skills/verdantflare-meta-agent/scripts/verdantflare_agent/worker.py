"""Persistent, adapter-independent delivery and observation loop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol
import uuid

from .store import Store, Conflict


class NotAccepted(Exception):
    """Only adapters with evidence of non-submission may raise this exception."""
    def __init__(self, code="OFFLINE"):
        self.code = code


@dataclass
class Reconciliation:
    receipt: dict | None = None
    retry_safe_evidence: str | None = None


@dataclass
class Observation:
    cursor: object = None
    terminal: str | None = None
    result: dict | None = None
    acknowledgment: dict | None = None


class Adapter(Protocol):
    async def deliver(self, message: dict, endpoint: dict, handoff: dict) -> dict: ...
    async def reconcile(self, message: dict, endpoint: dict) -> Reconciliation: ...
    async def observe(self, message: dict, endpoint: dict) -> Observation: ...
    async def close(self): ...


class WorkerLock:
    """OS-owned lock: a crashed process releases it; a slow live worker does not."""
    def __init__(self, database):
        self.path = Path(str(database) + ".worker.lock")
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise Conflict("WORKER_ALREADY_RUNNING") from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


class Worker:
    def __init__(self, store: Store, adapters: dict[str, Adapter], *, poll_seconds=5, call_timeout=45):
        if poll_seconds <= 0 or call_timeout <= 0:
            raise ValueError("positive worker intervals required")
        self.store = store
        self.adapters = adapters
        self.poll_seconds = poll_seconds
        self.call_timeout = call_timeout
        self.owner = uuid.uuid4().hex

    def adapter(self, message):
        endpoint = self.store.session(message["body"]["recipient"])
        return self.adapters.get(endpoint["service"]), endpoint

    async def _heartbeat(self, message):
        while True:
            await asyncio.sleep(10)
            if not self.store.renew(message["id"], message["token"]):
                return

    async def _deliver(self, message):
        adapter, endpoint = self.adapter(message)
        if adapter is None:
            self.store.delivery_error(message["id"], message["token"], known_unsent=True, reason="ADAPTER_MISSING")
            return
        heartbeat = asyncio.create_task(self._heartbeat(message))
        try:
            handoff = self.store.handoff(message["body"]["assignment_id"], message["body"]["revision"])
            receipt = await asyncio.wait_for(adapter.deliver(message, endpoint, handoff), self.call_timeout)
            self.store.delivered(message["id"], message["token"], receipt)
        except NotAccepted as error:
            self.store.delivery_error(message["id"], message["token"], known_unsent=True, reason=error.code)
        except asyncio.CancelledError:
            self.store.delivery_error(message["id"], message["token"])
            raise
        except Exception:
            # Timeout, malformed receipt, transport disconnect, or local write error
            # can all occur AFTER external acceptance. Never assume unsent.
            self.store.delivery_error(message["id"], message["token"])
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def step(self):
        self.store.recover()
        for message in self.store.pending({"unknown"}):
            if message["due"] > self.store.clock() or not self.store.is_current(message):
                continue
            adapter, endpoint = self.adapter(message)
            if adapter is None:
                continue
            try:
                answer = await asyncio.wait_for(adapter.reconcile(message, endpoint), self.call_timeout)
                if answer.receipt is not None:
                    self.store.delivered(message["id"], message["token"], answer.receipt)
                elif answer.retry_safe_evidence:
                    self.store.reconcile_unsent(message["id"], answer.retry_safe_evidence)
            except Exception:
                # Read failures do not change an uncertain submission into unsent.
                continue
        for _ in range(20):
            message = self.store.claim(self.owner)
            if message is None:
                break
            await self._deliver(message)
        for message in self.store.pending({"delivered", "acknowledged"}):
            if message["observed"]:
                continue
            adapter, endpoint = self.adapter(message)
            if adapter is None or endpoint["generation"] != message["route_generation"]:
                continue
            try:
                observation = await asyncio.wait_for(adapter.observe(message, endpoint), self.call_timeout)
                if observation.acknowledgment is not None:
                    self.store.acknowledge(message["id"], observation.acknowledgment)
                self.store.observation(message["id"], cursor=observation.cursor,
                                       terminal=observation.terminal, result=observation.result)
            except Exception:
                # Preserve receipt and cursor; retry reads of the SAME external task.
                self.store.observation_error(message["id"])
                continue

    async def run(self, stop: asyncio.Event | None = None):
        stop = stop or asyncio.Event()
        with WorkerLock(self.store.path):
            try:
                while not stop.is_set():
                    await self.step()
                    try:
                        await asyncio.wait_for(stop.wait(), self.poll_seconds)
                    except asyncio.TimeoutError:
                        pass
            finally:
                # Shared adapter objects may be referenced by more than one service alias.
                unique = {id(adapter): adapter for adapter in self.adapters.values()}
                await asyncio.gather(*(adapter.close() for adapter in unique.values()), return_exceptions=True)
