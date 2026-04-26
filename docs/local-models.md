# Local models

Thalyn ships two local-provider adapters in v0.10: **Ollama** for
cross-platform inference and **MLX** for Apple Silicon. Both run on
the user's hardware — nothing in this surface ever calls a Thalyn
server, and the API keys / model files stay on the local machine.

## Recommended models

| Provider | Default model | Why this default |
|---|---|---|
| Ollama | `qwen3-coder` | Highest tool-call reliability among open-weight options; ships in Ollama's library at the time of writing. |
| MLX | `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit` | The 4-bit MLX-community variant fits comfortably on M3 / M4 Max with 32+ GB and runs interactive. |

Override either default by editing the schedule's run template,
the chat header's provider switcher, or the per-project settings
once project-level routing lands.

## Hardware floor

Local models trade cloud capability for privacy + zero per-token
cost. The realistic floor for an interactive coding session:

- **Apple Silicon — Mac (MLX):**
  - **M3 Max 64 GB / M4 Max 64 GB+** runs Qwen3-Coder-30B at 4-bit
    quantisation comfortably (15-25 t/s steady-state, prompt eval a
    few seconds for a 4 k-token context).
  - **M-series 32 GB** drops to Qwen3-Coder-7B or a smaller
    variant; the 30B 4-bit model swaps under typical workload.
  - **Intel Macs** are not supported — MLX is Apple Silicon only.
- **Linux + NVIDIA (Ollama):**
  - **24 GB+ VRAM** (RTX 4090, 4090, A6000) runs the 30B class with
    headroom; 16 GB is workable for the 7B class.
  - **CPU-only** is technically supported by Ollama but not
    interactive enough for agent loops; expect long pauses on
    every turn.
- **Linux + AMD (Ollama):** the ROCm path works for Ollama but
  hardware support is patchier than NVIDIA; the recommendation is
  to validate against your specific GPU before relying on it.
- **Windows:** Ollama for Windows runs the same model line-up as
  Linux; same VRAM guidance applies.

## Capability deltas (cloud → local)

| Dimension | Anthropic Claude | Ollama (Qwen3-Coder) | MLX (Qwen3-Coder 4-bit) |
|---|---|---|---|
| Context window | 200 k | 32 k | 32 k |
| Tool-use support | Yes | Yes | No |
| Tool-use reliability | High | Medium | Low |
| Vision | Yes | No | No |
| Streaming | Yes | Yes | Yes |
| Local | No | Yes | Yes |

The capability-delta dialog in the chat-header switcher renders a
live version of this table whenever the user picks a different
provider; it pulls from the brain's `providers.delta` JSON-RPC so
the values stay in sync with the registry.

### What "tool-use reliability" means

Cloud Claude follows a structured tool-calling protocol with
extremely consistent JSON shapes. Local Qwen3-Coder fine-tunes
emulate that protocol; in practice they hallucinate tool names or
malformed argument JSON more often. The orchestrator's drift
monitor catches the symptom (off-plan actions, repeated failed
tool calls) but the upstream cause is the model's reliability tier
— set expectations accordingly when picking a local provider for a
multi-step agent loop.

MLX models in 2026 don't yet ship a usable structured tool-call
schema, which is why the MLX adapter's capability profile reports
`supports_tool_use=false`. Plain chat works; agent-loop work
involving tool calls should pick Anthropic or Ollama.

## Onboarding flow

Picking Ollama for the first time triggers an availability check
through `providers.check_model`. Three outcomes:

1. **`available`** — model is in the local catalogue; the next
   chat turn streams immediately.
2. **`missing`** — `providers.pull_model` runs `ollama pull` and
   surfaces progress notifications (`providers.pull_progress`)
   the renderer shows as a progress bar.
3. **`unknown`** — Ollama isn't running. The error nudges the
   user to start Ollama (`ollama serve` or the desktop app) and
   try again.

MLX always reports `unknown` because `mlx-lm` fetches the model
weights from Hugging Face on first stream — no separate pull
step. The first turn after switching to MLX may pause for ~30 s
to a couple of minutes depending on connection speed and model
size; subsequent turns are immediate.

## Audit log

The runs index already records `provider_id` per run. The audit
log file (`runs/{run_id}.log`) carries the same field on every
status entry, so a post-hoc review can answer "which model
produced this output" without joining against any other table.
This is the v0.10 deliverable for the
"audit log records the provider used per run" exit criterion;
no new fields were needed — the existing surface already answers
the question.

## Out of scope for v0.10

- **llama.cpp FFI adapter.** The placeholder remains in the
  registry; if a user installs llama.cpp directly we'll wire the
  adapter when there's demand. Ollama covers the same hardware
  range with an HTTP API.
- **Local model fine-tuning.** Out of v1 scope; users who want
  custom weights can put them in an Ollama tag / Hugging Face
  repo and Thalyn will pick them up via the standard model
  pull / load flow.
- **Cloud-GPU offload for local-style models.** Possible
  follow-up; not on the v0.10 deliverables.
