import { ArrowUp, Mic } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Props = {
  disabled?: boolean;
  placeholder?: string;
  onSubmit: (prompt: string) => void;
  /**
   * Visual size of the composer. ``compact`` is the legacy mosaic
   * shape — single-line presentation, smaller padding. ``roomy`` is
   * the chat-first shape — wider padding, generous typography, fits
   * the bottom of a full-width chat window. Defaults to ``compact``
   * so the legacy callers keep their layout.
   */
  size?: "compact" | "roomy";
};

/**
 * Multi-line composer. Enter sends; Shift-Enter inserts a newline;
 * ⌘/Ctrl-Enter is an explicit send alias for users who prefer the
 * Cmd-Enter convention. Auto-grows to a sensible cap then scrolls.
 *
 * The mic button is a v0.26 stub — voice input lands later (F7).
 * Rendering it now keeps the composer geometry stable across the
 * voice transition so the bottom-bar doesn't reflow when the mic
 * lights up.
 */
export function Composer({ disabled, placeholder, onSubmit, size = "compact" }: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    if (disabled) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd-Enter / Ctrl-Enter — explicit send alias.
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  const roomy = size === "roomy";

  return (
    <form
      className={cn(
        "flex items-end gap-2 border-t border-border bg-background",
        roomy ? "px-6 py-4" : "px-6 py-3",
      )}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Button
        type="button"
        size="icon"
        variant="ghost"
        disabled
        aria-label="Voice input (coming soon)"
        title="Voice input — coming soon"
        className={cn(
          "shrink-0 text-muted-foreground",
          roomy ? "h-10 w-10" : "h-9 w-9",
        )}
      >
        <Mic aria-hidden />
      </Button>
      <label htmlFor="chat-composer" className="sr-only">
        Message Thalyn
      </label>
      <textarea
        id="chat-composer"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        disabled={disabled}
        rows={1}
        placeholder={placeholder ?? "Message Thalyn…"}
        className={cn(
          "flex-1 resize-y rounded-md border border-border bg-card placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          roomy
            ? "min-h-[48px] max-h-60 px-4 py-3 text-base"
            : "min-h-[40px] max-h-48 px-3 py-2 text-sm",
        )}
      />
      <Button
        type="submit"
        size="icon"
        disabled={disabled || !value.trim()}
        aria-label="Send message"
        className={cn(roomy ? "h-10 w-10" : undefined)}
      >
        <ArrowUp aria-hidden />
      </Button>
    </form>
  );
}
