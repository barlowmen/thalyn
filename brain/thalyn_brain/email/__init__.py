"""Email subsystem — Gmail + Microsoft Graph behind one wire shape.

The renderer drives the inbox / thread / compose surfaces through
:mod:`thalyn_brain.email_rpc`; the agent reads the same data
through the same RPC. Send is hard-gated: the brain refuses to
deliver any message that wasn't explicitly approved by a renderer
session, regardless of whether the request originates from the
agent or the user, so an unattended schedule cannot send mail
without the user clicking through.
"""

from thalyn_brain.email.adapters import (
    EmailAdapter,
    EmailAuthError,
    EmailError,
    GmailAdapter,
    GraphAdapter,
)
from thalyn_brain.email.credentials import EmailCredentialsCache
from thalyn_brain.email.manager import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    EmailAccount,
    EmailDraft,
    EmailManager,
    EmailMessage,
    EmailMessageList,
    SendNotApprovedError,
)
from thalyn_brain.email.store import EmailAccountStore

__all__ = [
    "AccountAlreadyExistsError",
    "AccountNotFoundError",
    "EmailAccount",
    "EmailAccountStore",
    "EmailAdapter",
    "EmailAuthError",
    "EmailCredentialsCache",
    "EmailDraft",
    "EmailError",
    "EmailManager",
    "EmailMessage",
    "EmailMessageList",
    "GmailAdapter",
    "GraphAdapter",
    "SendNotApprovedError",
]
