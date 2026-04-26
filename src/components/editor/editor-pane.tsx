import { useCallback, useEffect, useRef, useState } from "react";

import Editor, { loader, type Monaco, type OnMount } from "@monaco-editor/react";
import type { IDisposable } from "monaco-editor";
import * as monaco from "monaco-editor";

import { useTheme } from "@/components/theme-provider";
import { registerInlineSuggestProvider } from "@/components/editor/inline-suggest-provider";
import {
  monacoThemeForDocument,
  thalynDarkTheme,
  thalynLightTheme,
  THALYN_DARK,
  THALYN_LIGHT,
} from "@/components/editor/monaco-theme";

import "@/components/editor/monaco-environment";

loader.config({ monaco });

const SCRATCH_BUFFER = `// Welcome to Thalyn.
//
// This is a scratch buffer — type freely. The file-tree, multi-tab
// editor, and LSP wiring land in the commits that follow. Keybindings
// and command palette already work the way you expect.
//
function greet(name: string) {
  return \`hello, \${name}\`;
}

console.log(greet("thalyn"));
`;

/**
 * Concrete Monaco mount. Defines our themes once, syncs the active
 * theme with the rest of the shell, and stretches the editor to fill
 * the surface.
 */
export function EditorPane() {
  const { theme } = useTheme();
  const [monacoTheme, setMonacoTheme] = useState<string>(() =>
    monacoThemeForDocument(),
  );
  const inlineRegistrationRef = useRef<IDisposable | null>(null);

  const handleMount = useCallback<OnMount>((_editor, monacoApi: Monaco) => {
    monacoApi.editor.defineTheme(THALYN_DARK, thalynDarkTheme);
    monacoApi.editor.defineTheme(THALYN_LIGHT, thalynLightTheme);
    monacoApi.editor.setTheme(monacoThemeForDocument());
    inlineRegistrationRef.current = registerInlineSuggestProvider({
      monaco: monacoApi,
      language: "typescript",
    });
  }, []);

  useEffect(() => {
    return () => {
      inlineRegistrationRef.current?.dispose();
      inlineRegistrationRef.current = null;
    };
  }, []);

  useEffect(() => {
    setMonacoTheme(monacoThemeForDocument());
  }, [theme]);

  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => setMonacoTheme(monacoThemeForDocument());
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [theme]);

  return (
    <Editor
      height="100%"
      defaultLanguage="typescript"
      defaultValue={SCRATCH_BUFFER}
      theme={monacoTheme}
      onMount={handleMount}
      loading={<EditorLoading />}
      options={{
        fontFamily:
          "'Geist Mono', 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace",
        fontSize: 13,
        lineHeight: 1.5,
        smoothScrolling: true,
        cursorBlinking: "smooth",
        cursorSmoothCaretAnimation: "on",
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        renderLineHighlight: "line",
        padding: { top: 12, bottom: 12 },
        guides: {
          bracketPairs: true,
          indentation: true,
        },
        bracketPairColorization: { enabled: true },
        automaticLayout: true,
        inlineSuggest: {
          enabled: true,
          mode: "subwordSmart",
          showToolbar: "onHover",
        },
      }}
    />
  );
}

function EditorLoading() {
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-xs text-muted-foreground">Loading editor…</p>
    </div>
  );
}
