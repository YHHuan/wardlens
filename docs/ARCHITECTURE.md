# Architecture

## Data flow

```text
VGH EIP/QEMR (院內 HTTPS)
        │ read-only, globally rate-limited
        ▼
in-memory PatientBundle ──► local overview / local DOCX or CSV
        │
        ▼
PromptBuilder ─► deterministic de-identification ─► residual-risk scan
        │                                                  │ block
        ▼                                                  └─────► stop
exact outbound preview + SHA-256
        │ clinician approval
        ├────────► clipboard (auto-clear attempt)
        └────────► OpenRouter ZDR endpoint ─► selected LLM
```

No clinical record cache, raw prompt log, response log, EMR write endpoint, Selenium driver, browser extension, or long-lived local web server exists in the MVP.

The only local listener is an ephemeral `127.0.0.1` HTTP callback created during an explicit OpenRouter OAuth PKCE login. It receives a one-time authorization code, never clinical content, and closes immediately after success or timeout. No listener exists during normal EMR or LLM use.

## Developer configuration flow

```text
Built-in model profiles/prompts
        │ explicit developer-mode edit/import
        ▼
validated non-secret settings.json (%LOCALAPPDATA%/WardLens)
        │
        ├── model ID / reasoning / max tokens
        └── workflow-specific system prompt
                    │
                    └── full content remains visible in exact outbound preview
```

The developer JSON schema accepts only known profile slots and prompt workflows. It cannot carry an API key or change the OpenRouter transport origin. Model IDs are selected from the live catalog or validated as `provider/model`; per-request ZDR checks remain authoritative.

## Trust boundaries

1. EMR HTML is untrusted input. Parser selectors, origin allowlists and content-health checks run before data is accepted.
2. Every `<clinical_source>` is escaped and explicitly treated as data, reducing prompt-injection risk.
3. De-identification is conservative but not a guarantee. Human review is mandatory.
4. Preview integrity is enforced by hashing the exact canonical payload.
5. Model output is an unverified draft. Source IDs and hashes support review but do not prove clinical correctness.

## Source semantics

Each `SourceRecord` preserves source type, title, fetch time, observed time when explicitly parseable, source URL in memory, record ID and content SHA-256. Derived timing is deterministic. The model must not invent an event date from a document date.

## Empty-state semantics

- Missing expected DOM structure = interface failure.
- Structured table with no readable rows = `empty_unverified`.
- Detail not loaded = not loaded, never “none.”
- Declared list count different from fetched count = incomplete.
- Login/block/session errors stop further requests until explicit re-login.
