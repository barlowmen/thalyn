import { useCallback, useEffect, useRef, useState } from "react";

import {
  type ChatChunk,
  type ChatChunkEvent,
  sendChat,
  subscribeChatChunks,
} from "@/lib/chat";

import type { AssistantSegment, Message } from "./types";

type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "error"; message: string };

let nextId = 0;
const newId = () => `m_${Date.now()}_${nextId++}`;

/**
 * Owns the message list and the active assistant message; subscribes
 * to chat:chunk events for the lifetime of the hook and routes them
 * into the in-memory message shape.
 */
export function useChat({
  providerId,
  systemPrompt,
}: {
  providerId: string;
  systemPrompt?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const sessionRef = useRef<string>(`sess_${Date.now()}`);
  const activeAssistantId = useRef<string | null>(null);

  // Single subscription for the lifetime of the hook.
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let active = true;
    subscribeChatChunks((event: ChatChunkEvent) => {
      if (event.sessionId !== sessionRef.current) return;
      applyChunk(event.chunk);
    })
      .then((fn) => {
        if (!active) {
          fn();
          return;
        }
        unlisten = fn;
      })
      .catch(() => {
        // No-op outside Tauri (storybook / playwright); the surface
        // still renders with whatever messages are seeded.
      });
    return () => {
      active = false;
      unlisten?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyChunk = useCallback((chunk: ChatChunk) => {
    setMessages((current) => {
      const next = [...current];
      const lastIndex = next.length - 1;
      const last = next[lastIndex];
      const isAssistant = last?.role === "assistant";
      const assistant = isAssistant ? (last as Extract<Message, { role: "assistant" }>) : null;

      switch (chunk.kind) {
        case "start": {
          const message: Message = {
            id: newId(),
            role: "assistant",
            segments: [],
            model: chunk.model,
            done: false,
          };
          activeAssistantId.current = message.id;
          next.push(message);
          return next;
        }
        case "text": {
          if (!assistant) return next;
          const segments = appendText(assistant.segments, chunk.delta);
          next[lastIndex] = { ...assistant, segments };
          return next;
        }
        case "tool_call": {
          if (!assistant) return next;
          const segments: AssistantSegment[] = [
            ...assistant.segments,
            {
              kind: "tool_call",
              callId: chunk.callId,
              tool: chunk.tool,
              input: chunk.input,
            },
          ];
          next[lastIndex] = { ...assistant, segments };
          return next;
        }
        case "tool_result": {
          if (!assistant) return next;
          const segments = assistant.segments.map((segment) =>
            segment.kind === "tool_call" && segment.callId === chunk.callId
              ? {
                  ...segment,
                  output: chunk.output,
                  isError: chunk.isError,
                }
              : segment,
          );
          next[lastIndex] = { ...assistant, segments };
          return next;
        }
        case "stop": {
          if (!assistant) return next;
          next[lastIndex] = {
            ...assistant,
            done: true,
            totalCostUsd: chunk.totalCostUsd,
          };
          return next;
        }
        case "error": {
          if (!assistant) return next;
          const segments: AssistantSegment[] = [
            ...assistant.segments,
            { kind: "error", message: chunk.message, code: chunk.code },
          ];
          next[lastIndex] = { ...assistant, segments, done: true };
          return next;
        }
      }
    });
  }, []);

  const send = useCallback(
    async (prompt: string) => {
      const trimmed = prompt.trim();
      if (!trimmed) return;
      setStatus({ kind: "sending" });
      const userMessage: Message = {
        id: newId(),
        role: "user",
        text: trimmed,
      };
      setMessages((current) => [...current, userMessage]);
      try {
        await sendChat({
          sessionId: sessionRef.current,
          providerId,
          prompt: trimmed,
          systemPrompt,
        });
        setStatus({ kind: "idle" });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setStatus({ kind: "error", message });
      }
    },
    [providerId, systemPrompt],
  );

  return { messages, status, send };
}

function appendText(
  segments: AssistantSegment[],
  delta: string,
): AssistantSegment[] {
  const last = segments[segments.length - 1];
  if (last && last.kind === "text") {
    return [
      ...segments.slice(0, -1),
      { kind: "text", text: last.text + delta },
    ];
  }
  return [...segments, { kind: "text", text: delta }];
}
