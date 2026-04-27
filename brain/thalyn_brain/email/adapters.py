"""Provider-specific HTTP adapters for Gmail and Microsoft Graph.

Both adapters present the same async surface so the manager can
treat them uniformly. Authentication is OAuth 2.0 refresh-token
flow: the user mints a refresh token through their own OAuth
client (Google Cloud / Microsoft Entra app) and pastes it via the
settings UI; the adapter exchanges it for a short-lived access
token before each batch of API calls.

Network calls use ``httpx`` (already a brain dep). The adapters
don't cache mail bodies — that's the renderer's job — so they're
safe to instantiate per request as long as the access-token cache
is shared across instances per account.
"""

from __future__ import annotations

import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class EmailError(Exception):
    """Base for adapter-level failures the manager surfaces over RPC."""


class EmailAuthError(EmailError):
    """The refresh token is missing, expired, or rejected."""


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float

    def is_valid(self) -> bool:
        return time.time() < (self.expires_at - 30)


class TokenSource(Protocol):
    """Returns a refresh token + OAuth client credentials for an account."""

    async def __call__(self, account_id: str) -> tuple[str, str, str]: ...


class EmailAdapter(ABC):
    """Per-account email-provider adapter."""

    provider: str = ""

    def __init__(
        self,
        *,
        account_id: str,
        token_source: TokenSource,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_id = account_id
        self._token_source = token_source
        self._client = client
        self._cached: _CachedToken | None = None

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def http(self) -> httpx.AsyncClient:
        # Lazily instantiate so callers without a custom client get
        # a sensible default. Tests inject a transport-bound client.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _access_token(self) -> str:
        if self._cached is not None and self._cached.is_valid():
            return self._cached.access_token
        refresh_token, client_id, client_secret = await self._token_source(self._account_id)
        if not refresh_token:
            raise EmailAuthError(f"no refresh token configured for {self._account_id}")
        token, expires_in = await self._exchange_refresh_token(
            refresh_token, client_id, client_secret
        )
        self._cached = _CachedToken(access_token=token, expires_at=time.time() + expires_in)
        return token

    @abstractmethod
    async def _exchange_refresh_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> tuple[str, int]:
        """Mint a fresh access token. Returns (token, expires_in_seconds)."""

    @abstractmethod
    async def list_messages(
        self,
        *,
        query: str | None = None,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> dict[str, Any]:
        """List inbox messages, newest first. Returns a wire-shaped dict."""

    @abstractmethod
    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Return the full message body and metadata for one id."""

    @abstractmethod
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
        """Send a new message. Returns provider id of the sent message."""


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


class GmailAdapter(EmailAdapter):
    """Gmail v1 API adapter."""

    provider = "gmail"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"
    _BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

    async def _exchange_refresh_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> tuple[str, int]:
        response = await self.http.post(
            self._TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code >= 400:
            raise EmailAuthError(f"gmail token exchange failed: {response.text}")
        body = response.json()
        return body["access_token"], int(body.get("expires_in", 3600))

    async def list_messages(
        self,
        *,
        query: str | None = None,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> dict[str, Any]:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}"}
        params: dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        response = await self.http.get(f"{self._BASE}/messages", headers=headers, params=params)
        if response.status_code >= 400:
            raise EmailError(f"gmail list failed: {response.text}")
        body = response.json()
        ids = [m["id"] for m in body.get("messages", [])]
        # Fetch metadata for each id. Batched parallel fetches keep
        # the renderer responsive on first paint.
        messages: list[dict[str, Any]] = []
        for message_id in ids:
            meta_response = await self.http.get(
                f"{self._BASE}/messages/{message_id}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            if meta_response.status_code >= 400:
                continue
            messages.append(_gmail_meta_to_wire(meta_response.json()))
        return {
            "messages": messages,
            "nextPageToken": body.get("nextPageToken"),
        }

    async def get_message(self, message_id: str) -> dict[str, Any]:
        token = await self._access_token()
        response = await self.http.get(
            f"{self._BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "full"},
        )
        if response.status_code >= 400:
            raise EmailError(f"gmail get failed: {response.text}")
        return _gmail_full_to_wire(response.json())

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
        token = await self._access_token()
        raw = _build_rfc822(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
        )
        encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
        response = await self.http.post(
            f"{self._BASE}/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": encoded},
        )
        if response.status_code >= 400:
            raise EmailError(f"gmail send failed: {response.text}")
        sent = response.json()
        return {
            "provider": self.provider,
            "messageId": sent.get("id"),
            "threadId": sent.get("threadId"),
        }


# ---------------------------------------------------------------------------
# Microsoft Graph
# ---------------------------------------------------------------------------


class GraphAdapter(EmailAdapter):
    """Microsoft Graph mail adapter (consumer + work/school)."""

    provider = "microsoft"
    _TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    _BASE = "https://graph.microsoft.com/v1.0/me"

    async def _exchange_refresh_token(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
    ) -> tuple[str, int]:
        # client_secret can be empty for desktop / public clients;
        # Graph still wants a body even without it.
        data: dict[str, str] = {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "Mail.ReadWrite Mail.Send offline_access",
        }
        if client_secret:
            data["client_secret"] = client_secret
        response = await self.http.post(self._TOKEN_URL, data=data)
        if response.status_code >= 400:
            raise EmailAuthError(f"graph token exchange failed: {response.text}")
        body = response.json()
        return body["access_token"], int(body.get("expires_in", 3600))

    async def list_messages(
        self,
        *,
        query: str | None = None,
        page_token: str | None = None,
        max_results: int = 25,
    ) -> dict[str, Any]:
        token = await self._access_token()
        params: dict[str, Any] = {
            "$top": max_results,
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview",
        }
        if query:
            params["$search"] = f'"{query}"'
        url = page_token or f"{self._BASE}/messages"
        response = await self.http.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params if not page_token else None,
        )
        if response.status_code >= 400:
            raise EmailError(f"graph list failed: {response.text}")
        body = response.json()
        return {
            "messages": [_graph_meta_to_wire(m) for m in body.get("value", [])],
            "nextPageToken": body.get("@odata.nextLink"),
        }

    async def get_message(self, message_id: str) -> dict[str, Any]:
        token = await self._access_token()
        response = await self.http.get(
            f"{self._BASE}/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            raise EmailError(f"graph get failed: {response.text}")
        return _graph_full_to_wire(response.json())

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
        token = await self._access_token()
        message = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": _recipients(to),
            "ccRecipients": _recipients(cc),
            "bccRecipients": _recipients(bcc),
        }
        if in_reply_to:
            message["internetMessageHeaders"] = [{"name": "In-Reply-To", "value": in_reply_to}]
        response = await self.http.post(
            f"{self._BASE}/sendMail",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": message, "saveToSentItems": True},
        )
        if response.status_code >= 400:
            raise EmailError(f"graph send failed: {response.text}")
        return {"provider": self.provider, "messageId": None, "threadId": None}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_rfc822(
    *,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body: str,
    in_reply_to: str | None,
) -> str:
    lines: list[str] = []
    if to:
        lines.append(f"To: {', '.join(to)}")
    if cc:
        lines.append(f"Cc: {', '.join(cc)}")
    if bcc:
        lines.append(f"Bcc: {', '.join(bcc)}")
    lines.append(f"Subject: {subject}")
    lines.append("MIME-Version: 1.0")
    lines.append('Content-Type: text/plain; charset="UTF-8"')
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
        lines.append(f"References: {in_reply_to}")
    lines.append("")
    lines.append(body)
    return "\r\n".join(lines)


def _recipients(addresses: list[str]) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": addr}} for addr in addresses if addr]


def _gmail_headers(message: dict[str, Any]) -> dict[str, str]:
    headers = message.get("payload", {}).get("headers", [])
    return {h["name"].lower(): h["value"] for h in headers}


def _gmail_meta_to_wire(message: dict[str, Any]) -> dict[str, Any]:
    headers = _gmail_headers(message)
    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
    }


def _gmail_full_to_wire(message: dict[str, Any]) -> dict[str, Any]:
    wire = _gmail_meta_to_wire(message)
    payload = message.get("payload", {})
    body = _gmail_extract_body(payload)
    wire["body"] = body
    return wire


def _gmail_extract_body(payload: dict[str, Any]) -> str:
    """Best-effort plain-text extraction from a Gmail payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if mime_type == "text/plain" and body_data:
        return _gmail_decode(body_data)
    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _gmail_extract_body(part)
            if text:
                return text
    if body_data:
        # Last resort — return whatever's there.
        return _gmail_decode(body_data)
    return ""


def _gmail_decode(data: str) -> str:
    padding = 4 - (len(data) % 4)
    if padding < 4:
        data += "=" * padding
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def _graph_meta_to_wire(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("from", {}).get("emailAddress", {})
    to_addrs = [
        r.get("emailAddress", {}).get("address", "") for r in message.get("toRecipients", [])
    ]
    return {
        "id": message.get("id"),
        "threadId": message.get("conversationId"),
        "from": sender.get("address", ""),
        "to": ", ".join(addr for addr in to_addrs if addr),
        "subject": message.get("subject", ""),
        "date": message.get("receivedDateTime", ""),
        "snippet": message.get("bodyPreview", ""),
    }


def _graph_full_to_wire(message: dict[str, Any]) -> dict[str, Any]:
    wire = _graph_meta_to_wire(message)
    body = message.get("body", {})
    wire["body"] = body.get("content", "")
    wire["bodyContentType"] = body.get("contentType", "Text")
    return wire


__all__ = [
    "EmailAdapter",
    "EmailAuthError",
    "EmailError",
    "GmailAdapter",
    "GraphAdapter",
    "TokenSource",
]
