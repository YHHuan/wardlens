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
| EMR password left on disk | session-only variable; UI clears password field; no credential log | process-memory compromise remains possible |
| Prompt injection in chart | source blocks escaped and labelled untrusted; fixed system prompt | models are probabilistic; source review remains required |
| Empty scrape treated as normal | expected selectors, source status, declared/fetched reconciliation, warnings | live HTML variants need hospital pilot fixtures |
| Excessive requests / IP lock | single selected patient detail load; global rolling limiter; request budget; no parallel scraper; stop on block markers | thresholds can change without notice |
| Stale/broken session causes mass false-empty | discard entire session after network/auth failure; explicit re-login | SSO behavior must be validated live |
| Clipboard sync leaks data | only deidentified prompt; warning; conditional auto-clear | cloud clipboard may sync before clear |
| AI draft copied as fact | source markers, visible warning, no automatic EMR write | clinician can still fail to review |
| Supply-chain or binary reputation | source + CI tests + hashes + dual artifacts; optional trusted signing | unsigned first release may be blocked |

## Deliberate non-features

- no hardcoded shared API key or password;
- no proxy that accepts a short shared password;
- no Defender/SmartScreen bypass instructions;
- no automatic order placement or note submission;
- no persistent clinical cache;
- no background all-patient longitudinal crawl;
- no silent provider fallback.

## Before production use

Obtain hospital privacy/security approval, review the OpenRouter/provider agreement and data region, establish a code-signing identity or IT allowlist, validate selectors with authorized synthetic cases, run red-team de-identification fixtures, and define incident response and release ownership.
