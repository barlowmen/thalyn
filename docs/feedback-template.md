# Feedback template — paste, fill, send

A short, copy-pasteable template for capturing what you saw while
using Thalyn. The goal is "easy" — answer what you can, skip what
you can't, don't dress it up.

If something's broken, file an issue. If something's *off* — slow,
confusing, surprising, missing — that's what this template is for.

---

## Two questions that matter most

**1. Did Thalyn earn its place in your day?**
Yes / No / Mixed — and one sentence on why.

> _your answer_

**2. What's the one thing you'd change first?**
Not the longest list — the *first* thing.

> _your answer_

---

## Session context

- **When:** YYYY-MM-DD
- **Hardware:** (e.g. M3 Max 64 GB)
- **OS:** (e.g. macOS 14.5)
- **Build:** `git rev-parse --short HEAD` →
- **Brain provider used:** (Claude Sonnet / Claude Opus / local Qwen / other)

---

## Findings

For each thing worth flagging, drop one of these blocks. Use as many
or as few as you need.

### Finding 1

- **Surface:** (chat / editor / terminal / browser / email / connectors / settings / other)
- **What happened:**
- **What you expected:**
- **Steps if reproducible:**
- **Severity:** blocker / major / minor / nit
- **Screenshot or log paste:** (optional)

### Finding 2

- **Surface:**
- **What happened:**
- **What you expected:**
- **Steps if reproducible:**
- **Severity:**
- **Screenshot or log paste:**

(repeat as needed)

---

## Polish

A loose place for things that don't fit a "finding" — copy that
read awkwardly, microcopy that confused you, status messages that
were unhelpful, places where you wanted to undo and couldn't.

> _your notes_

---

## What you reached for instead

Did you alt-tab to another app at any point? Which app, what for?
This is the most useful single signal for whether the "one app for
the workflow" promise is landing.

> _your answer_

---

## What worked well

Don't skip this — calling out things that landed prevents the next
iteration from accidentally regressing them.

> _your answer_

---

## Anything else

> _your answer_

---

*One last thing:* if a sandbox boundary, a hard gate (email send,
destructive shell, push to main), or the prompt-injection defenses
behaved in any way that surprised you, please **flag it on the first
line** of "Findings" — those are load-bearing safety properties and
need to be triaged ahead of polish work.
