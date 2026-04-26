import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/**
 * Renderer-side LSP client. The brain spawns and supervises the
 * actual language server; this client speaks JSON-RPC 2.0 over the
 * Tauri bridge to it.
 *
 * One client per session. Caller owns lifecycle: `start()` spawns the
 * server (returning the negotiated session id), `request` /
 * `notify` push messages, `subscribe` registers handlers for
 * server-initiated notifications, and `stop()` shuts the session
 * down. Outgoing request ids are monotonic and tracked here so the
 * brain doesn't need to know about them.
 */
export type LspMessage = {
  jsonrpc: "2.0";
  id?: number | string;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
};

type LspMessageEvent = {
  sessionId: string;
  message: LspMessage;
};

type LspErrorEvent = {
  sessionId: string;
  error: string;
};

type StartResult = {
  sessionId: string;
  language: string;
  command: string[];
  startedAtMs: number;
};

type NotificationHandler = (params: unknown) => void;
type ErrorHandler = (error: string) => void;

export class LspClient {
  private sessionId: string | null = null;
  private nextRequestId = 1;
  private readonly pending = new Map<number, {
    resolve: (value: unknown) => void;
    reject: (err: Error) => void;
  }>();
  private readonly notificationHandlers = new Map<string, Set<NotificationHandler>>();
  private readonly errorHandlers = new Set<ErrorHandler>();
  private unlistenMessage: UnlistenFn | null = null;
  private unlistenError: UnlistenFn | null = null;

  async start(language: string): Promise<StartResult> {
    if (this.sessionId !== null) {
      throw new Error("LSP client already started");
    }
    this.unlistenMessage = await listen<LspMessageEvent>("lsp:message", (event) =>
      this.handleIncoming(event.payload),
    );
    this.unlistenError = await listen<LspErrorEvent>("lsp:error", (event) =>
      this.handleError(event.payload),
    );

    const result = (await invoke<StartResult>("lsp_start", { language })) ?? null;
    if (!result || typeof result.sessionId !== "string") {
      throw new Error("brain returned no LSP session id");
    }
    this.sessionId = result.sessionId;
    return result;
  }

  async stop(): Promise<void> {
    const sessionId = this.sessionId;
    this.sessionId = null;
    if (this.unlistenMessage) {
      this.unlistenMessage();
      this.unlistenMessage = null;
    }
    if (this.unlistenError) {
      this.unlistenError();
      this.unlistenError = null;
    }
    for (const slot of this.pending.values()) {
      slot.reject(new Error("LSP session stopped"));
    }
    this.pending.clear();
    if (sessionId) {
      await invoke("lsp_stop", { sessionId }).catch(() => undefined);
    }
  }

  /** Send a request to the server and resolve with its response. */
  async request<T = unknown>(method: string, params: unknown = {}): Promise<T> {
    const sessionId = this.requireSession();
    const id = this.nextRequestId++;
    const message: LspMessage = { jsonrpc: "2.0", id, method, params };
    const promise = new Promise<unknown>((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    await invoke("lsp_send", { sessionId, message });
    return (await promise) as T;
  }

  /** Send a fire-and-forget notification (no response expected). */
  async notify(method: string, params: unknown = {}): Promise<void> {
    const sessionId = this.requireSession();
    const message: LspMessage = { jsonrpc: "2.0", method, params };
    await invoke("lsp_send", { sessionId, message });
  }

  /** Register a handler for server-initiated notifications. */
  subscribe(method: string, handler: NotificationHandler): () => void {
    let set = this.notificationHandlers.get(method);
    if (!set) {
      set = new Set();
      this.notificationHandlers.set(method, set);
    }
    set.add(handler);
    return () => {
      set!.delete(handler);
      if (set!.size === 0) this.notificationHandlers.delete(method);
    };
  }

  /** Register a handler for transport-level errors (e.g. stdin closed). */
  onError(handler: ErrorHandler): () => void {
    this.errorHandlers.add(handler);
    return () => this.errorHandlers.delete(handler);
  }

  private requireSession(): string {
    if (this.sessionId === null) {
      throw new Error("LSP client is not started");
    }
    return this.sessionId;
  }

  private handleIncoming(event: LspMessageEvent): void {
    if (event.sessionId !== this.sessionId) return;
    const { message } = event;
    if (message.id !== undefined && message.id !== null) {
      const id = typeof message.id === "number" ? message.id : Number(message.id);
      const slot = this.pending.get(id);
      if (!slot) return;
      this.pending.delete(id);
      if (message.error) {
        slot.reject(new Error(`${message.error.code}: ${message.error.message}`));
      } else {
        slot.resolve(message.result);
      }
      return;
    }
    if (message.method) {
      const set = this.notificationHandlers.get(message.method);
      if (!set) return;
      for (const handler of set) handler(message.params);
    }
  }

  private handleError(event: LspErrorEvent): void {
    if (event.sessionId !== this.sessionId) return;
    for (const handler of this.errorHandlers) handler(event.error);
  }
}
