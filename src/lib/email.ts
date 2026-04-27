import { invoke } from "@tauri-apps/api/core";

export type EmailProvider = "gmail" | "microsoft";

export type EmailAccount = {
  accountId: string;
  provider: EmailProvider;
  label: string;
  address: string;
  createdAtMs: number;
};

export type EmailMessage = {
  id: string;
  threadId: string | null;
  from: string;
  to: string;
  subject: string;
  date: string;
  snippet: string;
  body: string | null;
};

export type EmailMessageList = {
  messages: EmailMessage[];
  nextPageToken: string | null;
};

export type EmailDraft = {
  draftId: string;
  accountId: string;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body: string;
  inReplyTo: string | null;
  createdAtMs: number;
  approved: boolean;
};

export type EmailCredentialsStatus = {
  refreshTokenConfigured: boolean;
  clientIdConfigured: boolean;
  clientSecretConfigured: boolean;
};

export async function listAccounts(): Promise<{ accounts: EmailAccount[] }> {
  return await invoke<{ accounts: EmailAccount[] }>("email_list_accounts");
}

export async function addAccount(
  provider: EmailProvider,
  label: string,
  address: string,
): Promise<EmailAccount> {
  return await invoke<EmailAccount>("email_add_account", {
    provider,
    label,
    address,
  });
}

export async function removeAccount(accountId: string): Promise<void> {
  await invoke("email_remove_account", { accountId });
}

export async function saveCredentials(
  accountId: string,
  values: {
    refreshToken?: string;
    clientId?: string;
    clientSecret?: string;
  },
): Promise<void> {
  await invoke("email_save_credentials", {
    accountId,
    refreshToken: values.refreshToken,
    clientId: values.clientId,
    clientSecret: values.clientSecret,
  });
}

export async function credentialsStatus(
  accountId: string,
): Promise<EmailCredentialsStatus> {
  return await invoke<EmailCredentialsStatus>("email_credentials_status", {
    accountId,
  });
}

export async function listMessages(
  accountId: string,
  options: { query?: string; pageToken?: string; maxResults?: number } = {},
): Promise<EmailMessageList> {
  return await invoke<EmailMessageList>("email_list_messages", {
    accountId,
    query: options.query,
    pageToken: options.pageToken,
    maxResults: options.maxResults,
  });
}

export async function getMessage(
  accountId: string,
  messageId: string,
): Promise<EmailMessage> {
  return await invoke<EmailMessage>("email_get_message", {
    accountId,
    messageId,
  });
}

export async function createDraft(
  accountId: string,
  draft: {
    to: string[];
    cc?: string[];
    bcc?: string[];
    subject: string;
    body: string;
    inReplyTo?: string;
  },
): Promise<EmailDraft> {
  return await invoke<EmailDraft>("email_create_draft", {
    accountId,
    to: draft.to,
    cc: draft.cc,
    bcc: draft.bcc,
    subject: draft.subject,
    body: draft.body,
    inReplyTo: draft.inReplyTo,
  });
}

export async function listDrafts(): Promise<{ drafts: EmailDraft[] }> {
  return await invoke<{ drafts: EmailDraft[] }>("email_list_drafts");
}

export async function discardDraft(draftId: string): Promise<void> {
  await invoke("email_discard_draft", { draftId });
}

export async function approveDraft(draftId: string): Promise<EmailDraft> {
  return await invoke<EmailDraft>("email_approve_draft", { draftId });
}

export async function sendDraft(draftId: string): Promise<unknown> {
  return await invoke("email_send_draft", { draftId });
}
