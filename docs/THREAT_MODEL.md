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
| Long key copied or mistyped | OAuth PKCE browser login; one-time loopback code; paste-once fallback; key stored in WinCred, never returned in the browser state DTO | clearing an input does not clear the system clipboard; compromised browser/account/Windows session remains able to obtain or use keys |
| EMR password left on disk | session-only variable; UI clears password field; no credential log | process-memory compromise remains possible |
| Website or another process invokes the local API | bind only to 127.0.0.1; random in-memory token in an API header; exact Host; POST requires exact Origin; no CORS or query/cookie authentication | same-user malware, a compromised browser or an extension with access can still steal the token or read displayed data |
| Local page leaks token or clinical data | fragment removed immediately; no clinical browser storage/service worker/CDN; CSP; no-referrer; no-store; Edge InPrivate preferred; logout clears clinical state | browser/extension/clipboard/OS traces are not eliminated; a fallback default browser may not be private; closing only a tab does not end the service |
| Arbitrary files exposed or local server exhausted | fixed static allowlist; bounded headers/body/response/workers/timeouts; idle shutdown; no HTTP request logging; safe fixed errors | local processes can still deny service; loopback is not a sandbox against a compromised account |
| Old tab or duplicate send uses the wrong patient | server-side session epoch, draft revision, preview ID/hash, single active job and one-use send authorization | displayed snapshots can become stale; current bedside data still needs confirmation |
| Prompt injection in chart | source blocks escaped and labelled untrusted; fixed system prompt | models are probabilistic; source review remains required |
| Model or source HTML executes in the UI | limited Markdown rendered through safe DOM construction; raw HTML/links/images not activated; restrictive CSP; raw source displayed as text | renderer changes require hostile-content regression; these controls do not validate clinical claims |
| Trend implies false chronology or precision | values parsed locally; separate units and unknown times; latest-time conflicts retained; original values/time/source expandable; no invented interpolation or reference ranges | document times may differ from specimen times; sparse or truncated source data can miss changes |
| Empty scrape treated as normal | expected selectors, source status, declared/fetched reconciliation, warnings | live HTML variants need hospital pilot fixtures |
| Excessive requests / IP lock | single selected patient detail load; global rolling limiter; request budget; no parallel scraper; stop on block markers | thresholds can change without notice |
| Stale/broken session causes mass false-empty | discard entire session after network/auth failure; explicit re-login | SSO behavior must be validated live |
| Clipboard or explicit exports leak data | copying input requires reviewed deidentified payload; answer copy and DOCX/CSV export are explicit; attempt conditional clipboard clear after 60 seconds | answers/exports can contain clinical content; browser permission can prevent clearing; clipboard sync or another app can copy before clear |
| AI draft copied as fact | source details available; missing/stale/conflicting data visible; no automatic EMR write | source hashes do not prove generated claims; clinician can still fail to review |
| Supply-chain or binary reputation | minimal first-party C launcher plus readable BAT; pinned PSF runtime verified by SPDX hash and Authenticode publisher; hash-locked pure-Python wheels; source + CI logs + release hashes | WardLens.exe is not signed; hospital policy can deny it, BAT, Python, a new hash, or the download origin |
| Unsafe developer configuration | explicit mode and warning; strict JSON schema; bounded prompts and validated optional token caps; fixed OpenRouter origin; preview invalidated after edits | provider output/cost limits apply when the app cap is omitted; a clinician can still author an unsafe prompt or choose a weak model |
| Browser diagnostic exposes existing sessions | fresh temporary profile; synthetic local page only; loopback service; fixed report fields with no raw exceptions, URLs or paths; cleanup own process tree | host policies can deny automation; this pilot does not validate EMR integration |
| Driver download substitution or redistribution | explicit official pinned download; ZIP and EXE hashes; retain vendor notices; no driver binary in public releases | versions must match installed Edge; public package cannot bundle the restricted driver |

The main browser UI uses the existing Requests EMR adapter and does not require a driver. The separate WebDriver probe validates only its synthetic cases, not an EMR migration. The local UI server stays active until application exit or idle expiry; closing the controller stops it. User-selected exports, copied text, saved developer prompts, OS credentials and nonclinical settings are outside the session-only clinical-state boundary. Custom prompts must not contain patient data or secrets.

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
