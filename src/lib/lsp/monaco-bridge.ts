import type { editor, IDisposable, Position, languages } from "monaco-editor";

import { LspClient } from "@/lib/lsp/client";

/**
 * Wire one Monaco model to one LSP session. Covers the slice of LSP
 * we care about for v0.12: initialise / didOpen / didChange,
 * publishDiagnostics → markers, completion, hover. Other LSP features
 * (signature help, references, rename) plug in here with the same
 * shape.
 */
export type MonacoNamespace = typeof import("monaco-editor");

type Diagnostic = {
  range: { start: Position; end: Position };
  severity?: number;
  message: string;
  source?: string;
  code?: string | number;
};

type CompletionItem = {
  label: string | { label: string };
  kind?: number;
  detail?: string;
  documentation?: string | { value: string };
  insertText?: string;
};

type HoverResult = {
  contents:
    | string
    | { kind?: string; value: string }
    | Array<string | { kind?: string; value: string }>;
};

export type LspBridge = {
  client: LspClient;
  dispose(): Promise<void>;
};

export async function attachLspBridge(options: {
  monaco: MonacoNamespace;
  model: editor.ITextModel;
  language: string;
  fileUri: string;
  rootUri?: string | null;
}): Promise<LspBridge> {
  const { monaco, model, language, fileUri, rootUri = null } = options;
  const client = new LspClient();
  await client.start(language);

  await client.request("initialize", {
    processId: null,
    clientInfo: { name: "thalyn", version: "0.0.0" },
    rootUri,
    capabilities: {
      textDocument: {
        synchronization: { didSave: true },
        completion: { completionItem: { snippetSupport: false } },
        hover: { contentFormat: ["markdown", "plaintext"] },
        publishDiagnostics: {},
      },
    },
  });
  await client.notify("initialized", {});

  await client.notify("textDocument/didOpen", {
    textDocument: {
      uri: fileUri,
      languageId: language,
      version: 1,
      text: model.getValue(),
    },
  });

  let version = 1;
  const disposables: IDisposable[] = [];

  // Stream content changes → didChange.
  disposables.push(
    model.onDidChangeContent(() => {
      version += 1;
      void client.notify("textDocument/didChange", {
        textDocument: { uri: fileUri, version },
        contentChanges: [{ text: model.getValue() }],
      });
    }),
  );

  // Diagnostics → Monaco markers.
  const unsubDiag = client.subscribe("textDocument/publishDiagnostics", (params) => {
    const payload = params as { uri?: string; diagnostics?: Diagnostic[] } | undefined;
    if (!payload || payload.uri !== fileUri) return;
    const markers = (payload.diagnostics ?? []).map(diagnosticToMarker);
    monaco.editor.setModelMarkers(model, "lsp", markers);
  });

  // Completion provider.
  const completionRegistration = monaco.languages.registerCompletionItemProvider(
    language,
    {
      triggerCharacters: [".", ":", "(", " "],
      provideCompletionItems: async (_model, position) => {
        const result = (await client.request("textDocument/completion", {
          textDocument: { uri: fileUri },
          position: positionToLsp(position),
        })) as { items?: CompletionItem[] } | CompletionItem[] | null;
        const items = Array.isArray(result) ? result : (result?.items ?? []);
        return {
          suggestions: items.map((item) =>
            completionItemToMonaco(monaco, item, position),
          ),
        };
      },
    },
  );
  disposables.push(completionRegistration);

  // Hover provider.
  const hoverRegistration = monaco.languages.registerHoverProvider(language, {
    provideHover: async (_model, position) => {
      const result = (await client.request("textDocument/hover", {
        textDocument: { uri: fileUri },
        position: positionToLsp(position),
      }).catch(() => null)) as HoverResult | null;
      if (!result) return null;
      const contents = normaliseHoverContents(result.contents);
      return { contents };
    },
  });
  disposables.push(hoverRegistration);

  return {
    client,
    async dispose() {
      unsubDiag();
      for (const item of disposables) item.dispose();
      monaco.editor.setModelMarkers(model, "lsp", []);
      await client.stop();
    },
  };
}

function diagnosticToMarker(diag: Diagnostic): editor.IMarkerData {
  return {
    severity: lspSeverityToMonaco(diag.severity ?? 1),
    message: diag.message,
    source: diag.source,
    code: diag.code !== undefined ? String(diag.code) : undefined,
    startLineNumber: diag.range.start.lineNumber + 1,
    startColumn: diag.range.start.column + 1,
    endLineNumber: diag.range.end.lineNumber + 1,
    endColumn: diag.range.end.column + 1,
  };
}

function lspSeverityToMonaco(severity: number): editor.IMarkerData["severity"] {
  // Monaco's MarkerSeverity numeric values: Hint=1, Info=2, Warning=4, Error=8.
  // LSP severity: Error=1, Warning=2, Information=3, Hint=4.
  switch (severity) {
    case 1:
      return 8;
    case 2:
      return 4;
    case 3:
      return 2;
    default:
      return 1;
  }
}

function positionToLsp(position: Position): { line: number; character: number } {
  return {
    line: position.lineNumber - 1,
    character: position.column - 1,
  };
}

function completionItemToMonaco(
  monaco: MonacoNamespace,
  item: CompletionItem,
  position: Position,
): languages.CompletionItem {
  const label = typeof item.label === "string" ? item.label : item.label.label;
  return {
    label,
    kind: item.kind ?? monaco.languages.CompletionItemKind.Text,
    detail: item.detail,
    documentation: item.documentation,
    insertText: item.insertText ?? label,
    range: {
      startLineNumber: position.lineNumber,
      startColumn: position.column,
      endLineNumber: position.lineNumber,
      endColumn: position.column,
    },
  };
}

function normaliseHoverContents(
  raw: HoverResult["contents"],
): { value: string }[] {
  if (Array.isArray(raw)) return raw.map(toHoverPart);
  return [toHoverPart(raw)];
}

function toHoverPart(part: string | { value: string }): { value: string } {
  if (typeof part === "string") return { value: part };
  return { value: part.value };
}
