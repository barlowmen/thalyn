"""Email manager and adapter tests.

Adapters are exercised against an httpx MockTransport so the
network is never touched. The manager is tested with an in-memory
adapter factory to keep the focus on draft + send semantics and
the hard-gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from thalyn_brain.email import (
    EmailAccountStore,
    EmailAuthError,
    EmailManager,
    GmailAdapter,
    GraphAdapter,
    SendNotApprovedError,
)
from thalyn_brain.email.adapters import EmailAdapter
from thalyn_brain.email.store import EmailAccountRow

# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_adapter_lists_messages() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.url.path.endswith("/messages") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": "m1"}, {"id": "m2"}],
                    "nextPageToken": "p2",
                },
            )
        if "/messages/m" in request.url.path and request.method == "GET":
            mid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": mid,
                    "threadId": f"t-{mid}",
                    "snippet": f"snippet for {mid}",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "sender@example.com"},
                            {"name": "Subject", "value": f"Subject {mid}"},
                            {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
                            {"name": "To", "value": "me@example.com"},
                        ]
                    },
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gmail.googleapis.com")

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "csec")

    adapter = GmailAdapter(account_id="acct", token_source=token_source, client=client)
    # Override the token URL so the mock transport handles it.
    adapter._TOKEN_URL = "https://gmail.googleapis.com/token"

    listing = await adapter.list_messages()
    assert listing["nextPageToken"] == "p2"
    ids = [m["id"] for m in listing["messages"]]
    assert ids == ["m1", "m2"]
    assert listing["messages"][0]["from"] == "sender@example.com"

    await adapter.aclose()


@pytest.mark.asyncio
async def test_gmail_adapter_send_encodes_rfc822() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 600})
        if request.url.path.endswith("/messages/send"):
            payload = json.loads(request.content)
            captured.update(payload)
            return httpx.Response(200, json={"id": "sent-1", "threadId": "t-1"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gmail.googleapis.com")

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "csec")

    adapter = GmailAdapter(account_id="acct", token_source=token_source, client=client)
    adapter._TOKEN_URL = "https://gmail.googleapis.com/token"

    result = await adapter.send_message(
        to=["alice@example.com"],
        cc=[],
        bcc=[],
        subject="Hi",
        body="Hello",
    )
    assert result["messageId"] == "sent-1"
    raw = captured["raw"]
    # base64url with possible padding stripping.
    import base64

    decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    assert "To: alice@example.com" in decoded
    assert "Subject: Hi" in decoded
    assert "Hello" in decoded

    await adapter.aclose()


@pytest.mark.asyncio
async def test_graph_adapter_send_serializes_message() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 600})
        if request.url.path.endswith("/sendMail"):
            payload = json.loads(request.content)
            captured.update(payload)
            return httpx.Response(202)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://graph.microsoft.com")

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "")

    adapter = GraphAdapter(account_id="acct", token_source=token_source, client=client)
    adapter._TOKEN_URL = "https://graph.microsoft.com/oauth2/v2.0/token"

    await adapter.send_message(to=["bob@example.com"], cc=[], bcc=[], subject="S", body="B")
    assert captured["message"]["subject"] == "S"
    assert captured["message"]["toRecipients"][0]["emailAddress"]["address"] == "bob@example.com"
    assert captured["saveToSentItems"] is True

    await adapter.aclose()


@pytest.mark.asyncio
async def test_adapter_surfaces_auth_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://gmail.googleapis.com")

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "csec")

    adapter = GmailAdapter(account_id="acct", token_source=token_source, client=client)
    adapter._TOKEN_URL = "https://gmail.googleapis.com/token"

    with pytest.raises(EmailAuthError):
        await adapter.list_messages()
    await adapter.aclose()


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class FakeAdapter(EmailAdapter):
    provider = "gmail"

    def __init__(self, account_id: str, token_source: Any) -> None:
        super().__init__(account_id=account_id, token_source=token_source)
        self.sent: list[dict[str, Any]] = []

    async def _exchange_refresh_token(
        self, refresh_token: str, client_id: str, client_secret: str
    ) -> tuple[str, int]:
        return ("token", 3600)

    async def list_messages(
        self,
        *,
        query: str | None = None,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> dict[str, Any]:
        return {"messages": [{"id": "m"}], "nextPageToken": None}

    async def get_message(self, message_id: str) -> dict[str, Any]:
        return {"id": message_id, "body": "hello"}

    async def send_message(
        self,
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "to": to,
            "subject": subject,
            "body": body,
            "in_reply_to": in_reply_to,
        }
        self.sent.append(record)
        return {"provider": self.provider, "messageId": "sent-id"}


@pytest.mark.asyncio
async def test_manager_send_requires_explicit_approval(tmp_path: Path) -> None:
    store = EmailAccountStore(data_dir=tmp_path)
    fake_holder: dict[str, FakeAdapter] = {}

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "csec")

    def factory(row: EmailAccountRow) -> EmailAdapter:
        adapter = FakeAdapter(row.account_id, token_source)
        fake_holder["one"] = adapter
        return adapter

    manager = EmailManager(store=store, token_source=token_source, adapter_factory=factory)

    account = await manager.add_account(provider="gmail", label="Personal", address="me@x")
    draft = await manager.create_draft(
        account_id=account.account_id,
        to=["alice@x"],
        subject="Hi",
        body="Hello",
    )

    with pytest.raises(SendNotApprovedError):
        await manager.send_draft(draft.draft_id)

    await manager.approve_draft(draft.draft_id)
    result = await manager.send_draft(draft.draft_id)
    assert result["messageId"] == "sent-id"
    assert fake_holder["one"].sent[0]["subject"] == "Hi"

    # Send-on-already-sent is no longer possible — the draft is gone.
    with pytest.raises(SendNotApprovedError):
        await manager.send_draft(draft.draft_id)

    await manager.shutdown()


@pytest.mark.asyncio
async def test_manager_listing_routes_to_adapter(tmp_path: Path) -> None:
    store = EmailAccountStore(data_dir=tmp_path)

    async def token_source(account_id: str) -> tuple[str, str, str]:
        return ("rt", "cid", "csec")

    def factory(row: EmailAccountRow) -> EmailAdapter:
        return FakeAdapter(row.account_id, token_source)

    manager = EmailManager(store=store, token_source=token_source, adapter_factory=factory)
    account = await manager.add_account(provider="gmail", label="Personal", address="me@x")
    listing = await manager.list_messages(account.account_id)
    assert [m.id for m in listing.messages] == ["m"]

    await manager.shutdown()


@pytest.mark.asyncio
async def test_credentials_cache_round_trip() -> None:
    from thalyn_brain.email.credentials import EmailCredentialsCache

    cache = EmailCredentialsCache()
    await cache.set("a", refresh_token="r", client_id="c", client_secret="s")
    assert await cache.token_source("a") == ("r", "c", "s")
    status = await cache.status("a")
    assert status == {
        "refreshTokenConfigured": True,
        "clientIdConfigured": True,
        "clientSecretConfigured": True,
    }
    assert await cache.clear("a") is True
    assert await cache.token_source("a") == ("", "", "")
