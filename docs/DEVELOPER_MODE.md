# Developer mode

Developer mode lets an authorized user update model routing and system prompts without rebuilding the EXE. It does not enable cloud AI, disable outbound review, embed an API key, or change the network destination.

## Open the editor

1. Start with synthetic Demo data.
2. Open **隱私與模型** and enable **開發者模式**.
3. Open **調整模型／reasoning／tokens／prompts**.
4. Make one change at a time, save, rebuild the outbound preview, and compare the result on a fixed synthetic test case.

## Model routes

Each fixed workflow slot accepts:

- an OpenRouter model ID in `provider/model` form;
- reasoning effort: `none`, `low`, `medium`, `high`, `xhigh`, or `max`;
- a maximum output budget from 128 to 32,000 tokens.

Use **刷新目前 ZDR 模型目錄** to populate the pickers with live models that currently advertise a ZDR endpoint. **刷新全部模型目錄** is useful for discovery, but selecting one does not bypass the send-time ZDR check: if ZDR is enabled and the chosen model lacks a qualifying endpoint, WardLens fails closed.

## Prompts

Prompts can be edited separately for:

- `rounding` — daily rounding summary;
- `admission` — English Admission Note;
- `emergency` — adult inpatient emergency cognitive aid;
- `qa` — questions about the selected patient.

The complete system prompt is always included in the exact outbound preview. Saving a prompt change clears existing previews so an older approval cannot authorize a newer prompt. **此項回復內建值** resets one workflow; **全部回復內建值** resets the editor before saving.

Do not paste patient data, credentials, cookies, or internal URLs into a system prompt. A prompt override is persisted in `%LOCALAPPDATA%\WardLens\settings.json` and is therefore different from session-only clinical data.

## Moving settings between computers

The exported `wardlens-developer-config.json` contains only:

- schema version;
- model, reasoning and token overrides;
- complete custom prompts.

It does not contain the OpenRouter key, EMR credentials, clinical cache, audit records, or privacy acknowledgements. Nevertheless, review the custom prompts before sharing because anything manually typed into them will be exported. Import rejects unknown fields, malformed model IDs, unsupported reasoning values, out-of-range token budgets, reserved prompt-envelope markers, oversized prompts and files larger than 1 MB.

## Intentional boundary

The transport origin remains `https://openrouter.ai/api/v1`. This prevents an imported configuration from silently redirecting deidentified clinical text or the OpenRouter key to another host. An approved on-premise model gateway should be implemented as a separately reviewed transport adapter with its own credential namespace, consent text and retention guarantees—not as a free-form URL field.

ZDR means the selected endpoint is configured not to retain request data; it does not mean the request stays inside the hospital network. Hospital approval remains necessary before enabling external AI.
