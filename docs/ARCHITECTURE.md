# Architecture

## Local UI and clinical data flow

```text
WardLens.exe / Start-WardLens.bat
        │ bundled PSF Python; no install
        ├── small Tk controller ──► reopen / desktop fallback / stop
        └── authenticated 127.0.0.1 server ◄── local browser UI
                     │                         bundled HTML/CSS/JS
                     ▼
             ClinicalSession (memory)
                     │
VGH EIP/QEMR ◄── existing Requests adapter, read-only and rate-limited
                     │
                     ▼
              PatientBundle ──► deterministic overview / trend tables
        │                   explicit local DOCX / CSV export
        │
        ▼
PromptBuilder ─► deterministic de-identification ─► residual-risk scan
        │                                                  │ block
        ▼                                                  └─────► local repair → recheck
exact outbound preview + SHA-256
        │ clinician approval
        ├────────► clipboard (auto-clear attempt)
        └────────► OpenRouter ZDR endpoint ─► selected LLM
                                                    │
                                  safe Markdown / plain-text copy
```

The v0.7 main UI uses an ordinary browser, preferably installed Edge InPrivate on Windows. It does not use Selenium or WebDriver. EMR login, cookies, fetching and LLM streaming remain in Python; the existing Requests adapter is unchanged. The independent WebDriver diagnostic is a feasibility probe for a future EMR adapter.

`web/launcher.py` owns the controller window and server lifecycle. `web/server.py` serves a fixed static allowlist and authenticated JSON API. `services/session.py` owns clinical state, jobs, preview integrity, settings and credential access. `services/rounding.py` builds the clinical overview; `web/assets/` renders it and the two AI workspaces. The target computer needs no Node, frontend build, CDN scripts or remote fonts.

The loopback server remains active while the application runs. Its URL carries a random token in the fragment; JavaScript immediately removes that fragment and keeps the token only in memory. Every API call needs `X-WardLens-Token`. The server validates the exact loopback Host, requires the exact Origin on POST, provides no CORS access, and uses CSP, `no-store`, fixed response types, bounded bodies, workers and timeouts. It does not serve arbitrary files or write HTTP access logs. Refreshing loses the token; the controller must reopen the UI.

Clinical state stays in Python memory and the browser DOM/JavaScript memory, not localStorage, sessionStorage, IndexedDB or a service worker. API keys and EMR cookies are never returned in the state DTO. Logout clears clinical state; closing the controller stops the server and closes the session. Closing only a browser tab does not immediately stop the process. Idle expiry is a fallback, not a substitute for explicit exit. `no-store` and InPrivate reduce persistence but cannot guarantee no browser, extension, clipboard or OS traces.

## Requests and consent

Each patient selection has a session epoch and each workspace draft has a revision. The controller rejects stale operations and permits only one active clinical/network job. An old tab cannot reuse its preview for a changed patient or draft. Generation must match the current preview ID and canonical payload hash, carry required confirmations, and consume its send authorization once. The LLM stream remains in Python; browser polling reads the accumulated answer rather than parsing provider SSE again.

Blocked drafts stay in memory and are not approved requests. A repair is canonicalized, rescanned and hashed before it becomes a read-only preview. Changing the patient, draft, model or prompt invalidates approval. Stale-data and slow emergency-mode confirmations are enforced by the controller, not only UI controls. See [AI and trends](AI_AND_TRENDS.md) for history bounds and coverage semantics.

A separate temporary `127.0.0.1` callback exists during explicit OpenRouter OAuth PKCE login. It receives a one-time code without clinical content and closes after success/timeout. The independent browser test starts its own loopback WebDriver service and fresh temporary Edge profile, exercises synthetic data and cleans up its own session/process tree. Neither is the main UI server or a replacement for the Requests EMR session.

## Portable runtime boundary

```text
WardLens.exe (native launcher) / Start-WardLens.bat
        │ isolated mode; fixed relative paths
        ▼
PSF-signed pythonw.exe / python.exe + PSF release runtime
        ├── app/       WardLens .py and bundled web/assets/
        └── packages/  hash-locked pure-Python wheels only
```

The root EXE is a small first-party launcher compiled from C source, with no shell, self-extraction, network, installer or administrator requirement. It is not Authenticode-signed; PSF runtime signatures do not cover it. Runtime bytes are pinned to python.org's SPDX SHA-256; CI verifies the runtime publisher and rejects native files under `app/`, `packages/` and the public `drivers/` directory. Startup requires no install/download. Only the separate, explicit browser-driver preparation button downloads from Microsoft with hash verification; downloaded license notices stay beside that personal copy. Startup and Tk callback failures write only bounded, privacy-safe diagnostics outside clinical export locations.

`Start-WardLens-Desktop.bat` or `--desktop` opens the Tk backup. Switching from the controller closes the current web session; clinical drafts are not transferred.

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

The developer JSON schema accepts only known profile slots and prompt workflows. It cannot carry an API key or change the OpenRouter transport origin. Model IDs are selected from the live catalog or validated as `provider/model`; per-request ZDR checks remain authoritative. Upgrades preserve saved custom prompts and profiles; new built-in wording does not silently replace custom content.

## Trust boundaries

1. EMR HTML is untrusted input. Parser selectors, origin allowlists and content-health checks run before data is accepted.
2. Every `<clinical_source>` is escaped and explicitly treated as data, reducing prompt-injection risk.
3. De-identification is conservative but not a guarantee. Human review is mandatory.
4. Preview integrity is enforced by hashing the exact canonical payload.
5. Markdown uses a limited DOM renderer without raw HTML or executable links/images. The original remains available for inspection; copied plain text removes presentation syntax. This controls rendering, not clinical correctness.
6. Model output is an unverified draft. Built-in prompts keep SRC/hash/D± codes out of ordinary prose while retaining relevant intervals, stale-data warnings and contradictions. Technical source details support review but do not prove a generated claim is correct.

## Source semantics

Each `SourceRecord` preserves source type, title, fetch time, observed time when explicitly parseable, source URL in memory, record ID and content SHA-256. Derived timing is deterministic. The model must not invent an event date from a document date.

Trend values are parsed directly from source tables, not generated by an LLM. CBC shows WBC/Hb/PLT first; differential is expandable. Different units stay separate; unknown times are not placed into an invented chronology. Conflicting same-time values remain visible. Each row shows the last known measurement time and span, with original values/times/source references in details. A chart requires usable numeric values, units and known, non-conflicting times; it does not fill gaps or infer normal ranges. A date-only coordinate does not establish a collection time of midnight.

## Empty-state semantics

- Missing expected DOM structure = interface failure.
- Structured table with no readable rows = `empty_unverified`.
- Detail not loaded = not loaded, never “none.”
- Declared list count different from fetched count = incomplete.
- No usable timestamp or only one observation = insufficient evidence for a timed trend.
- Login/block/session errors stop further requests until explicit re-login.

There is no persistent clinical cache, raw prompt/response log, EMR write endpoint, automatic order placement or background all-patient crawl. Explicit exports and clipboard operations are user-controlled copies outside this memory-only boundary.
