import { AlertTriangle } from "lucide-react";

import type { Budget, BudgetConsumed } from "@/lib/runs";

const PAUSE_THRESHOLD = 0.7;

type Props = {
  budget?: Budget | null;
  consumed?: BudgetConsumed | null;
  driftScore?: number;
  variant?: "compact" | "full";
};

/**
 * Per-run budget meter — shows what fraction of each cap has been
 * spent and pulses an amber warning when the drift score crosses the
 * pause threshold. Renders nothing when no budget is set so quiet
 * runs don't get visual clutter.
 */
export function BudgetMeter({
  budget,
  consumed,
  driftScore = 0,
  variant = "compact",
}: Props) {
  const dimensions = computeDimensions(budget, consumed);
  const showDrift = driftScore > 0;
  const driftWarning = driftScore >= PAUSE_THRESHOLD;

  if (dimensions.length === 0 && !showDrift) {
    return null;
  }

  return (
    <div className="space-y-1">
      {dimensions.map((dim) => (
        <Bar
          key={dim.label}
          label={dim.label}
          fraction={dim.fraction}
          value={dim.valueLabel}
          variant={variant}
        />
      ))}
      {showDrift && (
        <div
          className={`flex items-center gap-1.5 text-[10px] ${
            driftWarning
              ? "text-warning animate-pulse"
              : "text-muted-foreground"
          }`}
          aria-label={`Drift score ${(driftScore * 100).toFixed(0)} percent`}
        >
          {driftWarning && <AlertTriangle className="h-3 w-3" aria-hidden />}
          <span className="font-mono">
            drift {(driftScore * 100).toFixed(0)}%
          </span>
        </div>
      )}
    </div>
  );
}

function Bar({
  label,
  fraction,
  value,
  variant,
}: {
  label: string;
  fraction: number;
  value: string;
  variant: "compact" | "full";
}) {
  const clamped = Math.max(0, Math.min(1, fraction));
  const tone = clamped >= 1 ? "danger" : clamped >= 0.75 ? "warning" : "default";
  const trackHeight = variant === "compact" ? "h-1" : "h-1.5";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="uppercase tracking-wider">{label}</span>
        <span className="font-mono">{value}</span>
      </div>
      <div
        className={`relative ${trackHeight} w-full overflow-hidden rounded-sm bg-border`}
      >
        <div
          className={`absolute inset-y-0 left-0 ${
            tone === "danger"
              ? "bg-destructive"
              : tone === "warning"
              ? "bg-warning"
              : "bg-primary"
          }`}
          style={{ width: `${clamped * 100}%` }}
        />
      </div>
    </div>
  );
}

type Dimension = {
  label: string;
  fraction: number;
  valueLabel: string;
};

function computeDimensions(
  budget?: Budget | null,
  consumed?: BudgetConsumed | null,
): Dimension[] {
  if (!budget || !consumed) return [];
  const out: Dimension[] = [];
  if (
    typeof budget.maxIterations === "number" &&
    budget.maxIterations > 0 &&
    typeof consumed.iterations === "number"
  ) {
    out.push({
      label: "iterations",
      fraction: consumed.iterations / budget.maxIterations,
      valueLabel: `${consumed.iterations}/${budget.maxIterations}`,
    });
  }
  if (
    typeof budget.maxTokens === "number" &&
    budget.maxTokens > 0 &&
    typeof consumed.tokensUsed === "number"
  ) {
    out.push({
      label: "tokens",
      fraction: consumed.tokensUsed / budget.maxTokens,
      valueLabel: `${formatNumber(consumed.tokensUsed)}/${formatNumber(budget.maxTokens)}`,
    });
  }
  if (
    typeof budget.maxSeconds === "number" &&
    budget.maxSeconds > 0 &&
    typeof consumed.elapsedSeconds === "number"
  ) {
    out.push({
      label: "time",
      fraction: consumed.elapsedSeconds / budget.maxSeconds,
      valueLabel: `${consumed.elapsedSeconds.toFixed(1)}s/${budget.maxSeconds}s`,
    });
  }
  return out;
}

function formatNumber(n: number): string {
  if (n >= 10_000) {
    return `${(n / 1000).toFixed(1)}k`;
  }
  return n.toString();
}
