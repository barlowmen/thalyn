/**
 * Project-tag pill (F8.5 / F3.7).
 *
 * Renders a small coloured pill above each chat message that's
 * tagged to a project. The colour is derived deterministically from
 * the project's stable handle (slug or id) so the same project is
 * always the same colour across days, sessions, and devices.
 *
 * The pill is presentation-only — it doesn't open the switcher or
 * navigate anywhere. The eternal-thread shape pins one project per
 * turn, so the pill just labels the turn.
 */

import { projectColor } from "@/lib/project-color";

type Props = {
  /** The stable handle the colour hashes off — slug preferred, id as
   *  fallback. The actual string only matters for stability across
   *  rerenders; both shapes give a stable hue. */
  seed: string;
  /** The user-facing project name on the pill. */
  name: string;
};

export function ProjectTag({ seed, name }: Props) {
  const colour = projectColor(seed);
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider"
      style={{
        backgroundColor: colour.background,
        color: colour.foreground,
        borderColor: colour.border,
      }}
      aria-label={`Project: ${name}`}
    >
      {name}
    </span>
  );
}
