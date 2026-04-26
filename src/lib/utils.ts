import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge Tailwind class names without conflicts.
 *
 * Standard shadcn/ui helper — `clsx` resolves conditional segments and
 * `tailwind-merge` collapses competing utilities so the last one wins
 * (e.g. `cn("p-2", "p-4")` returns `"p-4"`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
