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
        ▼                                                  └─────► local repair → recheck
exact outbound preview + SHA-256
        │ clinician approval
        ├────────► clipboard (auto-clear attempt)
        └────────► OpenRouter ZDR endpoint ─► selected LLM
```

No clinical record cache, raw prompt log, response log, EMR write endpoint, browser extension, or long-lived local web server exists in the clinical workflows. A separate browser diagnostic can use an explicitly prepared Microsoft Edge WebDriver; it never shares EMR sessions or reads clinical data.

Blocked drafts are held in memory in a separate editable local dialog; they are never an approved request. Every repaired draft is canonicalized, rescanned and hashed before becoming the exact read-only preview. Both workflows still require an explicit send confirmation. See [AI and trends](AI_AND_TRENDS.md) for history bounds and coverage semantics.

An ephemeral `127.0.0.1` HTTP callback runs during explicit OpenRouter OAuth PKCE login, receives a one-time authorization code without clinical content, and closes after success/timeout. The independent browser test starts its own loopback WebDriver service and fresh temporary Edge profile, exercises a local synthetic page, then deletes the session and stops only its own process tree. No listener exists during normal EMR or LLM use.

## Portable runtime boundary

```text
WardLens.exe (native launcher) / Start-WardLens.bat (diagnostic fallback)
        │ isolated mode; fixed relative paths
        ▼
PSF-signed pythonw.exe / python.exe + PSF release runtime
        ├── app/       WardLens .py and resources
        └── packages/  hash-locked pure-Python wheels only
```

The root EXE is a small first-party launcher compiled from C source, with no shell, self-extraction, network, installer or administrator requirement. It is not Authenticode-signed; PSF runtime signatures do not cover it. Runtime bytes are pinned to python.org's SPDX SHA-256; CI verifies the runtime publisher and rejects native files under `app/`, `packages/` and the public `drivers/` directory. Startup requires no install/download. Only the separate, explicit browser-driver preparation button downloads from Microsoft with hash verification; downloaded license notices stay beside that personal copy. Startup and Tk callback failures write only bounded, privacy-safe diagnostics outside clinical export locations.

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
