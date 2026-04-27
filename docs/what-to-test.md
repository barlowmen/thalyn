# What to test — pilot smoke list for the human user

A focused list of things to actually exercise before we sign anything
off. Each item names the surface, the action, and the *gotcha to
watch for* — the thing that, if it goes wrong, would tell us
something we don't already know.

This is not a regression suite (CI handles those). This is the
"things that need eyeballs and judgement" list. Work top to bottom;
flag anything off in the feedback template.

## A. First-launch happy path

- [ ] **Cold launch.** `pnpm tauri dev` from a clean clone. Time it
  on your hardware. The window should reach a usable chat surface in
  under ~3 seconds after the dev server is ready.
  *Watch for:* visible flash-of-unstyled-content, layout shift, or a
  long blank state.

- [ ] **Brain handshake.** The chat surface header should show a
  "ready" pill within ~1 second of the window appearing. If it sits
  on "starting" for more than ~5 seconds, surface that.

- [ ] **Theme switch.** Toggle light / dark from the bottom of the
  activity rail. Switch should be instant, with no flash. Re-launch
  the app — the theme should persist.

## B. Chat with the brain

- [ ] **Five-turn conversation** with Claude (default provider).
  Token streaming should feel responsive (no long stalls before the
  first token).
  *Watch for:* tokens arriving in a single dump at the end (means
  streaming is broken), or visible jank during streaming.

- [ ] **A "do something" prompt** — e.g. "look at `src/lib/email.ts`
  and tell me what changes you'd make." The brain should produce a
  plan, render it in the inspector, and pause for approval.
  *Watch for:* plan steps that don't make sense for the request, or
  the plan tree being unreadable in under 30 seconds (per F11.6).

- [ ] **Approve and run.** Approve the plan; watch the action log
  populate as the agent works. Drift score should stay near zero on
  a focused task.
  *Watch for:* the run completing without surfacing what it actually
  did, or the action log going silent for a long stretch.

- [ ] **Take-over flow.** Once a sub-agent is running, click its
  tile in the inspector → **Take over**. You should land in a fresh
  chat thread with read access to the sub-agent's history. **Hand
  back** when you're done — the sub-agent should resume.
  *Watch for:* state confusion (the take-over thread polluting the
  sub-agent's conversation), or the sub-agent failing to resume.

## C. Provider swap

- [ ] **Local model.** If you have Ollama with `qwen3-coder` (or
  similar) installed, switch the provider via the brain-mode badge
  in the chat header. The capability-delta dialog should pop with
  honest expectations. Send a message — it should be served locally.
  *Watch for:* silent capability degradation (e.g. tool calls just
  failing instead of being flagged as "less reliable").

## D. Editor + terminal

- [ ] **Open a TypeScript file** from the sidebar in the editor
  surface. LSP should kick in (highlighting + completions) within a
  couple of seconds.
  *Watch for:* completions never appearing, or the editor cold-start
  taking more than a couple of seconds (the chunk is lazy-loaded).

- [ ] **Ghost-text suggestion.** Pause typing for ~300 ms in a spot
  that warrants a suggestion. The ghost text should appear.
  *Watch for:* suggestions that consistently make no sense, or the
  suggestion never showing up.

- [ ] **Open a terminal** from the rail. Run `ls`, `pwd`, something
  trivial. Then in chat, ask the brain something like "what's in the
  terminal right now?" — it should be able to attach and observe.
  *Watch for:* the terminal not echoing keystrokes, or the brain
  saying it can't see the session when the attach should work.

## E. Browser surface

- [ ] **Start the browser.** Click **Start** in the browser surface.
  A headed Chromium should appear (separate window in this iteration).
  Status should flip to "running" with a profile path shown.
  *Watch for:* discovery failing on a machine that does have Chrome
  (file an issue with which binary you have installed).

- [ ] **Agent-driven navigation.** Ask the brain "go to
  example.com and tell me the page title." It should drive the
  Chromium window and report back.
  *Watch for:* the agent saying it can't reach the browser, or the
  navigation succeeding but the title coming back empty.

## F. Email surface

- [ ] **Add an account.** Settings → Email accounts → add a Gmail
  or Microsoft account. You'll need to mint OAuth credentials from
  your own Google Cloud / Microsoft Entra app — there's no shortcut.
  *Watch for:* the credentials-fields UI being unclear about which
  field maps to which OAuth value.

- [ ] **Read the inbox.** Click **Email** in the rail. Pick the
  account. Recent messages should load.
  *Watch for:* a long stall on first paint, errors that don't
  explain whether the refresh token is bad or the API call failed,
  or per-message metadata being missing (subject / sender empty).

- [ ] **Compose, prepare, send.** New message → fill it in → **Prepare
  to send** → confirm modal appears → **Send**. The message should
  actually arrive at the recipient.
  *Watch for:* the confirm modal being skippable, or send succeeding
  with bad content (e.g. body truncated).

- [ ] **Critical:** Try to make the brain send mail without you
  clicking through the modal. Ask it explicitly: "send a test email
  to me@example.com." It should refuse — the brain rejects every
  send that hasn't been approved through the renderer's modal flow.
  *Watch for:* this *not* working as described — that would be a
  serious regression of the hard gate.

## G. Connectors (MCP)

- [ ] **Install a first-party connector** (Slack is the easiest to
  test with a personal workspace). Paste the bot token + workspace
  ID. Click **Start**. Live tools should appear.
  *Watch for:* `npx` failures (often a Node version mismatch), or
  the tool list being empty after start.

- [ ] **Grant gating.** The default grants should include
  non-sensitive tools only (e.g. `slack_list_channels`,
  `slack_get_channel_history`) and *exclude* sensitive ones
  (e.g. `slack_post_message`). Try asking the brain to post a
  message — it should refuse with a clear "tool not granted"
  message. Promote the tool in the grants list and try again.
  *Watch for:* sensitive tools defaulting to granted, or the
  ungranted call still going through to the upstream MCP server.

## H. Schedules

- [ ] **Create a near-term schedule** ("at the next minute, run a
  trivial agent that says hello"). The OS scheduler should fire it,
  the run should appear in the runs index, and the result should be
  visible afterwards.
  *Watch for:* the schedule never firing, or firing but the run not
  appearing in the index.

- [ ] **Sleep prevention.** Start a long-ish run. The system should
  *not* sleep mid-run, but the **display** should still be allowed
  to power off (per F5.3).

## I. Crash recovery

- [ ] **Kill mid-run.** Start a long agent run. Kill the Thalyn
  process (Cmd-Q is fine; SIGKILL is the harder test). Relaunch.
  The run should resume within ~30 seconds at the last checkpoint.
  *Watch for:* the run silently restarting from scratch, or the
  resumed run getting stuck.

## J. Accessibility & polish

- [ ] **Keyboard-only navigation.** Try driving every primary
  surface (chat, settings, connectors, email) with the keyboard
  alone. Tab order should be sane; visible focus rings everywhere.
  *Watch for:* focus traps, invisible focus, or surfaces that
  outright don't respond to keyboard input.

- [ ] **Reduced motion.** Turn on macOS → Accessibility → Display
  → Reduce motion. Re-launch Thalyn. Streaming text shouldn't
  blink the cursor; status transitions should be instant.

- [ ] **Reduced transparency.** Same panel, **Reduce transparency**.
  Vibrancy / glass surfaces (chat input bar, command palette) should
  fall back to opaque equivalents.

- [ ] **Screen reader spot-check.** Open VoiceOver. Tab through the
  chat surface — the streaming output region should announce as
  polite live; agent status changes should speak; the plan tree
  should navigate as a tree.

## K. Privacy posture

- [ ] **No network traffic at idle.** With observability disabled
  and no agent active, run `lsof -i -P` (or your egress sniffer of
  choice). Thalyn should hold no outbound connections except what
  Tauri / Vite need for the dev server.

- [ ] **No telemetry by default.** Confirm Settings → Observability
  shows both fields empty. Confirm crashes don't leave the machine
  unless you've pasted a Sentry DSN.

## L. The end-of-week feel test

- [ ] **Spend a real working session in Thalyn.** Don't just smoke
  the items above; try to do something you'd otherwise alt-tab to
  another app for. Note any moment you reached for a different tool
  and why. That's the gap.

---

When you're done, capture findings in the
[feedback template](feedback-template.md). The two questions that
matter most are at the top of that template; everything else is in
service of those.
