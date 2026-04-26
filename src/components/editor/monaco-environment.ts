/**
 * Monaco needs language workers to run validation, formatting, and
 * IntelliSense off the main thread. Vite's `?worker` import keeps
 * them bundled with the app — no CDN reach-out, no flash of broken
 * editor on load.
 *
 * We register the workers once at import time. The set covers the
 * languages we ship a default mode for; LSP-driven languages plug
 * into Monaco via the language API and don't need a worker entry
 * here.
 */

import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

type MonacoEnvironment = {
  getWorker(_: string, label: string): Worker;
};

const env: MonacoEnvironment = {
  getWorker(_workerId, label) {
    switch (label) {
      case "json":
        return new JsonWorker();
      case "typescript":
      case "javascript":
        return new TsWorker();
      default:
        return new EditorWorker();
    }
  },
};

(self as unknown as { MonacoEnvironment: MonacoEnvironment }).MonacoEnvironment =
  env;
