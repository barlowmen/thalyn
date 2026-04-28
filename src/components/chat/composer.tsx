import { ArrowUp } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Props = {
  disabled?: boolean;
  placeholder?: string;
  onSubmit: (prompt: string) => void;
};

/**
 * Multi-line composer. Enter sends; Shift-Enter inserts a newline.
 * Auto-grows to a sensible cap then scrolls.
 */
export function Composer({ disabled, placeholder, onSubmit }: Props) {
  const [value, setValue] = useState("");

  const submit = () => {
    if (disabled) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="flex items-end gap-2 border-t border-border bg-background px-6 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
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
          "min-h-[40px] max-h-48 flex-1 resize-y rounded-md border border-border bg-card px-3 py-2 text-sm",
          "placeholder:text-muted-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      />
      <Button
        type="submit"
        size="icon"
        disabled={disabled || !value.trim()}
        aria-label="Send message"
      >
        <ArrowUp aria-hidden />
      </Button>
    </form>
  );
}
