# Security

## Threat model (current)

Thalyn is in a development / personal-use scope. The threats v1 defends
against are:

- **Runaway agents** — an agent overruns its scope, deletes files,
  exhausts a credit, or invokes a destructive API.
- **Misconfigured tools** — operational mistakes that an honest mistake
  could turn into damage.
- **Prompt injection** from untrusted content (web pages, third-party
  repos) leading to runaway behaviour.

What v1 does **not** defend against:

- Targeted attackers compromising the build chain.
- Hostile dependencies in the sidecar exfiltrating secrets.
- Hostile MCP servers stealing data via the user's authorised tool
  calls.
- Side-channel attacks against the user's local machine.

These are deliberately scoped out of v1 and gated by the
[going-public hardening checklist](docs/going-public-checklist.md). If
you are evaluating Thalyn for installation by anyone who is not
themselves a contributor, that document is the contract — until every
item on it is closed, this software has not been hardened for that use
case.

The longer-form architectural threat model lives in
[`02-architecture.md`](02-architecture.md) §9.

## Reporting a vulnerability

While the project is pre-public and pre-1.0, please report security
issues by emailing **REDACTED-EMAIL** with `[security]` in the
subject. Include:

- A description of the issue and its impact.
- Reproduction steps if you have them.
- Whether you've shared the details with anyone else (please don't —
  this project does not yet have a coordinated-disclosure policy).

You will get an acknowledgement within 7 days. The fix timeline depends
on severity. Once Thalyn has a public disclosure policy (tracked on the
going-public checklist), this section will be replaced with a formal
coordinated-disclosure flow.

## Operational hygiene

- Secrets at rest are stored in the OS keychain
  (Keychain on macOS, Credential Manager on Windows, libsecret on
  Linux). Secrets do not enter process environment variables and are
  never written to disk in plain text.
- Audit logs of agent runs are written locally under the user's data
  directory. They are append-only NDJSON; they are not signed in v1.
- No telemetry leaves the machine by default. Optional crash
  reporting is supported via a user-supplied Sentry DSN; if unset,
  nothing is sent.

If you find a behaviour that contradicts any of the above, please
report it.
