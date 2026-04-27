"""Coordinator: account store + adapter dispatch + send hard-gate.

The manager owns one adapter instance per account, lazily
constructed. Send is double-gated: the brain refuses any send
that wasn't explicitly approved by a renderer-driven approval
call, and the renderer enforces a modal-confirm UX before that
approval is issued. Together that means an unattended schedule
can draft mail freely but can never deliver it without a human
click.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from thalyn_brain.email.adapters import (
    EmailAdapter,
    EmailAuthError,
    EmailError,
    GmailAdapter,
    GraphAdapter,
)
from thalyn_brain.email.store import EmailAccountRow, EmailAccountStore


class AccountNotFoundError(EmailError):
    """No account row matches the supplied id."""


class AccountAlreadyExistsError(EmailError):
    """add_account was called with an id that already exists."""


class SendNotApprovedError(EmailError):
    """send_draft refused because no matching approval was issued."""


@dataclass
class EmailAccount:
    """Wire view of an account."""

    account_id: str
    provider: str
    label: str
    address: str
    created_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "accountId": self.account_id,
            "provider": self.provider,
            "label": self.label,
            "address": self.address,
            "createdAtMs": self.created_at_ms,
        }


@dataclass
class EmailMessage:
    """One inbox message in the wire shape the renderer reads."""

    id: str
    thread_id: str | None
    sender: str
    to: str
    subject: str
    date: str
    snippet: str
    body: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "threadId": self.thread_id,
            "from": self.sender,
            "to": self.to,
            "subject": self.subject,
            "date": self.date,
            "snippet": self.snippet,
            "body": self.body,
        }


@dataclass
class EmailMessageList:
    messages: list[EmailMessage] = field(default_factory=list)
    next_page_token: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "messages": [m.to_wire() for m in self.messages],
            "nextPageToken": self.next_page_token,
        }


@dataclass
class EmailDraft:
    """A draft the user (or agent) has prepared but not sent."""

    draft_id: str
    account_id: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body: str
    in_reply_to: str | None = None
    created_at_ms: int = 0
    approved: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "draftId": self.draft_id,
            "accountId": self.account_id,
            "to": list(self.to),
            "cc": list(self.cc),
            "bcc": list(self.bcc),
            "subject": self.subject,
            "body": self.body,
            "inReplyTo": self.in_reply_to,
            "createdAtMs": self.created_at_ms,
            "approved": self.approved,
        }


def new_account_id() -> str:
    return f"acct_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def new_draft_id() -> str:
    return f"draft_{int(time.time())}_{uuid.uuid4().hex[:8]}"


class EmailManager:
    """Per-process email coordinator."""

    def __init__(
        self,
        *,
        store: EmailAccountStore,
        token_source: Any,  # callable: (account_id) -> (refresh_token, client_id, client_secret)
        adapter_factory: Any | None = None,
    ) -> None:
        self._store = store
        self._token_source = token_source
        self._adapter_factory = adapter_factory or self._default_adapter
        self._adapters: dict[str, EmailAdapter] = {}
        self._drafts: dict[str, EmailDraft] = {}
        self._approved_drafts: set[str] = set()
        self._lock = asyncio.Lock()

    async def list_accounts(self) -> list[EmailAccount]:
        rows = await self._store.list_all()
        return [_row_to_account(row) for row in rows]

    async def add_account(self, *, provider: str, label: str, address: str) -> EmailAccount:
        account_id = new_account_id()
        try:
            row = await self._store.add(
                account_id=account_id, provider=provider, label=label, address=address
            )
        except ValueError as exc:
            raise AccountAlreadyExistsError(str(exc)) from exc
        return _row_to_account(row)

    async def remove_account(self, account_id: str) -> bool:
        async with self._lock:
            adapter = self._adapters.pop(account_id, None)
        if adapter is not None:
            try:
                await adapter.aclose()
            except Exception:
                pass
        return await self._store.delete(account_id)

    async def list_messages(
        self,
        account_id: str,
        *,
        query: str | None = None,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> EmailMessageList:
        adapter = await self._adapter_for(account_id)
        wire = await adapter.list_messages(
            query=query, page_token=page_token, max_results=max_results
        )
        messages = [_message_from_wire(m) for m in wire.get("messages", [])]
        return EmailMessageList(messages=messages, next_page_token=wire.get("nextPageToken"))

    async def get_message(self, account_id: str, message_id: str) -> EmailMessage:
        adapter = await self._adapter_for(account_id)
        wire = await adapter.get_message(message_id)
        return _message_from_wire(wire)

    # ------------------------------------------------------------------
    # Drafts and the send hard-gate
    # ------------------------------------------------------------------

    async def create_draft(
        self,
        *,
        account_id: str,
        to: list[str],
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> EmailDraft:
        # Validate the account exists before pinning the draft to it.
        if await self._store.get(account_id) is None:
            raise AccountNotFoundError(f"unknown account: {account_id}")
        draft = EmailDraft(
            draft_id=new_draft_id(),
            account_id=account_id,
            to=list(to),
            cc=list(cc or []),
            bcc=list(bcc or []),
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            created_at_ms=int(time.time() * 1000),
        )
        async with self._lock:
            self._drafts[draft.draft_id] = draft
        return draft

    async def get_draft(self, draft_id: str) -> EmailDraft | None:
        async with self._lock:
            return self._drafts.get(draft_id)

    async def list_drafts(self) -> list[EmailDraft]:
        async with self._lock:
            return list(self._drafts.values())

    async def discard_draft(self, draft_id: str) -> bool:
        async with self._lock:
            self._approved_drafts.discard(draft_id)
            return self._drafts.pop(draft_id, None) is not None

    async def approve_draft(self, draft_id: str) -> EmailDraft:
        async with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise SendNotApprovedError(f"unknown draft: {draft_id}")
            self._approved_drafts.add(draft_id)
            draft.approved = True
            return draft

    async def send_draft(self, draft_id: str) -> dict[str, Any]:
        async with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                raise SendNotApprovedError(f"unknown draft: {draft_id}")
            if draft_id not in self._approved_drafts:
                raise SendNotApprovedError(f"draft {draft_id} has not been approved by the user")
        adapter = await self._adapter_for(draft.account_id)
        result = await adapter.send_message(
            to=draft.to,
            cc=draft.cc,
            bcc=draft.bcc,
            subject=draft.subject,
            body=draft.body,
            in_reply_to=draft.in_reply_to,
        )
        async with self._lock:
            self._drafts.pop(draft_id, None)
            self._approved_drafts.discard(draft_id)
        return {**result, "draftId": draft_id}

    async def shutdown(self) -> None:
        async with self._lock:
            adapters = list(self._adapters.values())
            self._adapters.clear()
        for adapter in adapters:
            try:
                await adapter.aclose()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _adapter_for(self, account_id: str) -> EmailAdapter:
        async with self._lock:
            if account_id in self._adapters:
                return self._adapters[account_id]
            row = await self._store.get(account_id)
            if row is None:
                raise AccountNotFoundError(f"unknown account: {account_id}")
            adapter = self._adapter_factory(row)
            self._adapters[account_id] = adapter
            return adapter

    def _default_adapter(self, row: EmailAccountRow) -> EmailAdapter:
        if row.provider == "gmail":
            return GmailAdapter(account_id=row.account_id, token_source=self._token_source)
        if row.provider == "microsoft":
            return GraphAdapter(account_id=row.account_id, token_source=self._token_source)
        raise EmailError(f"unsupported provider: {row.provider}")


def _row_to_account(row: EmailAccountRow) -> EmailAccount:
    return EmailAccount(
        account_id=row.account_id,
        provider=row.provider,
        label=row.label,
        address=row.address,
        created_at_ms=row.created_at_ms,
    )


def _message_from_wire(wire: dict[str, Any]) -> EmailMessage:
    return EmailMessage(
        id=str(wire.get("id") or ""),
        thread_id=wire.get("threadId"),
        sender=wire.get("from", ""),
        to=wire.get("to", ""),
        subject=wire.get("subject", ""),
        date=wire.get("date", ""),
        snippet=wire.get("snippet", ""),
        body=wire.get("body"),
    )


__all__ = [
    "AccountAlreadyExistsError",
    "AccountNotFoundError",
    "EmailAccount",
    "EmailDraft",
    "EmailManager",
    "EmailMessage",
    "EmailMessageList",
    "SendNotApprovedError",
    "new_account_id",
    "new_draft_id",
]
# Suppress unused import — used for re-export above.
_ = EmailAuthError
