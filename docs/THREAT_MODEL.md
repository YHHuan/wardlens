# Threat model

## Protected assets

- patient identifiers and clinical text;
- EIP credentials and authenticated cookies;
- OpenRouter API key;
- integrity of outbound prompts and clinical drafts;
- availability of the hospital information system.

## Principal threats and controls

| Threat | Control | Residual risk |
|---|---|---|
| PHI sent to cloud | cloud off by default; deterministic redaction; residual scan; exact preview; hash equality; per-request confirmation | narrative can contain indirect identifiers or rare-disease combinations |
| Key extracted from EXE | no embedded key/password; OS credential vault | compromised Windows account can access the vault |
| Long key copied or mistyped | OAuth PKCE browser login; one-time loopback code; clipboard fallback clears immediately | compromised browser/account/Windows session remains able to obtain or use keys |
| EMR password left on disk | session-only variable; UI clears password field; no credential log | process-memory compromise remains possible |
| Prompt injection in chart | source blocks escaped and labelled untrusted; fixed system prompt | models are probabilistic; source review remains required |
| Empty scrape treated as normal | expected selectors, source status, declared/fetched reconciliation, warnings | live HTML variants need hospital pilot fixtures |
| Excessive requests / IP lock | single selected patient detail load; global rolling limiter; request budget; no parallel scraper; stop on block markers | thresholds can change without notice |
| Stale/broken session causes mass false-empty | discard entire session after network/auth failure; explicit re-login | SSO behavior must be validated live |
| Clipboard sync leaks data | only deidentified prompt; warning; conditional auto-clear | cloud clipboard may sync before clear |
| AI draft copied as fact | source markers, visible warning, no automatic EMR write | clinician can still fail to review |
| Supply-chain or binary reputation | minimal first-party C launcher plus readable BAT; pinned PSF runtime verified by SPDX hash and Authenticode publisher; hash-locked pure-Python wheels; source + CI logs + release hashes | WardLens.exe is not signed; hospital policy can deny it, BAT, Python, a new hash, or the download origin |
| Unsafe developer configuration | explicit mode and warning; strict JSON schema; bounded prompts and validated optional token caps; fixed OpenRouter origin; preview invalidated after edits | provider output/cost limits apply when the app cap is omitted; a clinician can still author an unsafe prompt or choose a weak model |
| Browser diagnostic exposes existing sessions | fresh temporary profile; synthetic local page only; loopback service; fixed report fields with no raw exceptions, URLs or paths; cleanup own process tree | host policies can deny automation; this pilot does not validate EMR integration |
| Driver download substitution or redistribution | explicit official pinned download; ZIP and EXE hashes; retain vendor notices; no driver binary in public releases | versions must match installed Edge; public package cannot bundle the restricted driver |

## Deliberate non-features

- no hardcoded shared API key or password;
- no proxy that accepts a short shared password;
- no API key in developer export/import files;
- no arbitrary LLM host override through developer mode;
- no Defender/SmartScreen bypass instructions;
- no WardLens-generated PyInstaller executable or startup-time PowerShell/pip/download step;
- no automatic driver download during a probe, stealth automation or protection bypass;
- no automatic order placement or note submission;
- no persistent clinical cache;
- no background all-patient longitudinal crawl;
- no silent provider fallback.

## Before production use

Obtain hospital privacy/security approval, review the OpenRouter/provider agreement and data region, establish a code-signing identity or IT allowlist, validate selectors with authorized synthetic cases, run red-team de-identification fixtures, and define incident response and release ownership.
