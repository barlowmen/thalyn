import type { editor, IDisposable, Position } from "monaco-editor";

import { fetchInlineSuggestion } from "@/lib/inline-suggest";
import type { MonacoNamespace } from "@/lib/lsp/monaco-bridge";

/**
 * Wire Monaco's inline-suggest API to the brain's `inline.suggest`
 * service. Monaco itself debounces the call (`inlineSuggest.delay`,
 * configured in the editor options); we just answer when asked.
 *
 * The provider tags each request with a monotonic id, the active
 * model version, and the cursor offset so stale results are dropped
 * — if the user types past the position we requested for, the
 * suggestion belongs to a different cursor and we ignore it.
 */
export type InlineSuggestRegistration = IDisposable;

const PREFIX_BUDGET_CHARS = 4_000;
const SUFFIX_BUDGET_CHARS = 1_000;

export function registerInlineSuggestProvider(options: {
  monaco: MonacoNamespace;
  language: string;
}): InlineSuggestRegistration {
  let nextId = 1;

  return options.monaco.languages.registerInlineCompletionsProvider(
    options.language,
    {
      provideInlineCompletions: async (model, position, _context, token) => {
        const requestId = `inline_${nextId++}`;
        const versionAtRequest = model.getVersionId();
        const cursorOffset = model.getOffsetAt(position);

        const prefix = sliceBefore(model, position, PREFIX_BUDGET_CHARS);
        const suffix = sliceAfter(model, position, SUFFIX_BUDGET_CHARS);

        try {
          const result = await fetchInlineSuggestion({
            prefix,
            suffix,
            language: options.language,
            requestId,
          });

          if (token.isCancellationRequested) return { items: [] };
          if (model.getVersionId() !== versionAtRequest) return { items: [] };
          if (model.getOffsetAt(position) !== cursorOffset) return { items: [] };
          if (!result.suggestion) return { items: [] };

          return {
            items: [
              {
                insertText: result.suggestion,
                range: {
                  startLineNumber: position.lineNumber,
                  startColumn: position.column,
                  endLineNumber: position.lineNumber,
                  endColumn: position.column,
                },
              },
            ],
          };
        } catch {
          // Brain returned an error or the provider isn't configured;
          // fall through to no completions rather than showing a
          // toast for every keystroke.
          return { items: [] };
        }
      },
      disposeInlineCompletions: () => {
        // No native resources held by the provider — nothing to free.
      },
    },
  );
}

function sliceBefore(
  model: editor.ITextModel,
  position: Position,
  budget: number,
): string {
  const offset = model.getOffsetAt(position);
  const start = Math.max(0, offset - budget);
  const startPosition = model.getPositionAt(start);
  return model.getValueInRange({
    startLineNumber: startPosition.lineNumber,
    startColumn: startPosition.column,
    endLineNumber: position.lineNumber,
    endColumn: position.column,
  });
}

function sliceAfter(
  model: editor.ITextModel,
  position: Position,
  budget: number,
): string {
  const offset = model.getOffsetAt(position);
  const end = Math.min(model.getValueLength(), offset + budget);
  const endPosition = model.getPositionAt(end);
  return model.getValueInRange({
    startLineNumber: position.lineNumber,
    startColumn: position.column,
    endLineNumber: endPosition.lineNumber,
    endColumn: endPosition.column,
  });
}
