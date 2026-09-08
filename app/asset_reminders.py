"""Idempotent local inbox reminders for configured workspace identities."""
import asyncio
import logging
from contextlib import asynccontextmanager

from .security import Principal

logger = logging.getLogger(__name__)


def dispatch_workspace_reminders(store, authenticator):
    if authenticator.environment != "production":
        principals = [Principal("local-developer", "local", "admin")]
    else:
        rank = {"reader": 0, "requester": 1, "legal_reviewer": 2, "admin": 3}
        identities = {}
        for entry in authenticator.keys.values():
            role = entry.get("role")
            if role not in rank:
                continue
            principal = Principal(str(entry["subject"]), str(entry["tenantId"]), role)
            key = (principal.tenant_id, principal.subject)
            if key not in identities or rank[role] > rank[identities[key].role]:
                identities[key] = principal
        principals = identities.values()
    for principal in principals:
        store.dispatch_reminders(actor=principal)


def reminder_lifespan(get_store, get_authenticator):
    return periodic_lifespan(lambda: dispatch_workspace_reminders(get_store(), get_authenticator()))


def periodic_lifespan(callback):
    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()

        async def run():
            while not stop.is_set():
                try:
                    await asyncio.to_thread(callback)
                except Exception:
                    # Do not log contract text or identity configuration.
                    logger.error("Contract reminder check failed; retrying next minute")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60)
                except TimeoutError:
                    pass

        worker = asyncio.create_task(run())
        try:
            yield
        finally:
            stop.set()
            await worker
    return lifespan
