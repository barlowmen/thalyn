"""JSON-RPC surface for the email subsystem.

The Rust core wraps every method in a Tauri command. The send
hard-gate is enforced here as well as in the renderer: the brain
will not send a draft that hasn't passed through ``email.approve``
since the last ``email.create_draft``, regardless of the caller.
"""

from __future__ import annotations

from thalyn_brain.email import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    EmailError,
    EmailManager,
    SendNotApprovedError,
)
from thalyn_brain.email.credentials import EmailCredentialsCache
from thalyn_brain.email.store import EMAIL_PROVIDERS
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_email_methods(
    dispatcher: Dispatcher,
    manager: EmailManager,
    *,
    credentials: EmailCredentialsCache | None = None,
) -> None:
    creds = credentials or EmailCredentialsCache()

    async def email_list_accounts(_: RpcParams) -> JsonValue:
        accounts = await manager.list_accounts()
        return {"accounts": [a.to_wire() for a in accounts]}

    async def email_add_account(params: RpcParams) -> JsonValue:
        provider = _require_str(params, "provider")
        if provider not in EMAIL_PROVIDERS:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"unknown provider: {provider}",
            )
        label = _require_str(params, "label")
        address = _require_str(params, "address")
        try:
            account = await manager.add_account(provider=provider, label=label, address=address)
        except AccountAlreadyExistsError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return account.to_wire()

    async def email_remove_account(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        removed = await manager.remove_account(account_id)
        return {"removed": removed}

    async def email_list_messages(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        query_value = params.get("query")
        query = query_value if isinstance(query_value, str) else None
        page_token_value = params.get("pageToken")
        page_token = page_token_value if isinstance(page_token_value, str) else None
        max_results_value = params.get("maxResults", 25)
        max_results = int(max_results_value) if isinstance(max_results_value, (int, float)) else 25
        try:
            listing = await manager.list_messages(
                account_id, query=query, page_token=page_token, max_results=max_results
            )
        except AccountNotFoundError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except EmailError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return listing.to_wire()

    async def email_get_message(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        message_id = _require_str(params, "messageId")
        try:
            message = await manager.get_message(account_id, message_id)
        except AccountNotFoundError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except EmailError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return message.to_wire()

    async def email_create_draft(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        to = _require_str_list(params, "to")
        cc = _optional_str_list(params, "cc")
        bcc = _optional_str_list(params, "bcc")
        subject = _require_str(params, "subject", allow_empty=True)
        body = _require_str(params, "body", allow_empty=True)
        in_reply_to_value = params.get("inReplyTo")
        in_reply_to = in_reply_to_value if isinstance(in_reply_to_value, str) else None
        try:
            draft = await manager.create_draft(
                account_id=account_id,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                in_reply_to=in_reply_to,
            )
        except AccountNotFoundError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return draft.to_wire()

    async def email_list_drafts(_: RpcParams) -> JsonValue:
        drafts = await manager.list_drafts()
        return {"drafts": [d.to_wire() for d in drafts]}

    async def email_get_draft(params: RpcParams) -> JsonValue:
        draft_id = _require_str(params, "draftId")
        draft = await manager.get_draft(draft_id)
        if draft is None:
            raise RpcError(code=INVALID_PARAMS, message=f"unknown draft: {draft_id}")
        return draft.to_wire()

    async def email_discard_draft(params: RpcParams) -> JsonValue:
        draft_id = _require_str(params, "draftId")
        discarded = await manager.discard_draft(draft_id)
        return {"discarded": discarded}

    async def email_approve_draft(params: RpcParams) -> JsonValue:
        draft_id = _require_str(params, "draftId")
        try:
            draft = await manager.approve_draft(draft_id)
        except SendNotApprovedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return draft.to_wire()

    async def email_send_draft(params: RpcParams) -> JsonValue:
        draft_id = _require_str(params, "draftId")
        try:
            return await manager.send_draft(draft_id)
        except SendNotApprovedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except AccountNotFoundError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except EmailError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc

    async def email_set_credentials(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        refresh_token = _require_str(params, "refreshToken")
        client_id = _require_str(params, "clientId")
        client_secret = params.get("clientSecret", "")
        if not isinstance(client_secret, str):
            raise RpcError(
                code=INVALID_PARAMS, message="clientSecret must be a string when provided"
            )
        await creds.set(
            account_id,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
        return {"updated": True}

    async def email_clear_credentials(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        cleared = await creds.clear(account_id)
        return {"cleared": cleared}

    async def email_credentials_status(params: RpcParams) -> JsonValue:
        account_id = _require_str(params, "accountId")
        return await creds.status(account_id)

    dispatcher.register("email.set_credentials", email_set_credentials)
    dispatcher.register("email.clear_credentials", email_clear_credentials)
    dispatcher.register("email.credentials_status", email_credentials_status)
    dispatcher.register("email.list_accounts", email_list_accounts)
    dispatcher.register("email.add_account", email_add_account)
    dispatcher.register("email.remove_account", email_remove_account)
    dispatcher.register("email.list_messages", email_list_messages)
    dispatcher.register("email.get_message", email_get_message)
    dispatcher.register("email.create_draft", email_create_draft)
    dispatcher.register("email.list_drafts", email_list_drafts)
    dispatcher.register("email.get_draft", email_get_draft)
    dispatcher.register("email.discard_draft", email_discard_draft)
    dispatcher.register("email.approve_draft", email_approve_draft)
    dispatcher.register("email.send_draft", email_send_draft)


def _require_str(params: RpcParams, key: str, *, allow_empty: bool = False) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    if not allow_empty and not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"{key!r} must be non-empty")
    return value


def _require_str_list(params: RpcParams, key: str) -> list[str]:
    value = params.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RpcError(code=INVALID_PARAMS, message=f"{key!r} must be an array of strings")
    if not value:
        raise RpcError(code=INVALID_PARAMS, message=f"{key!r} must not be empty")
    return [v for v in value]


def _optional_str_list(params: RpcParams, key: str) -> list[str]:
    value = params.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RpcError(code=INVALID_PARAMS, message=f"{key!r} must be an array of strings")
    return [v for v in value]
