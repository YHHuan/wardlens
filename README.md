# WardLens

WardLens 是給台灣 PGY／住院醫師使用的 Windows 查房輔助工具。它以唯讀方式整理 VGH EMR 病人清單與選取病人的 admission note、progress note、用藥、檢驗趨勢及報告索引，再由使用者決定是否把「先去識別、再人工預覽」的內容送往 OpenRouter。

> 狀態：`v0.2.0` 是安全導向 MVP。Demo 與離線測試可用；真實 VGH adapter 仍須在院內網路以合成／授權測試病例完成 pilot。它不是已核准的醫療器材，也不應自動寫回 EMR。

## 目前功能

- 顯示超過 15 人的清單，追蹤分頁、去重、頁面宣告總數與實際取得數。
- 只在使用者選取病人後抓詳細內容，不預抓整批病歷。
- 本機整理最新 admission note 頂端 TODO、近期報告與來源 hash。
- 產生查房重點、英文 Admission Note、自由提問與成人住院急症處置草稿。
- `fast`、`deep`、Gemini Flash、Claude 第二意見四種路由。
- OpenRouter 瀏覽器一鍵登入（OAuth PKCE），不必手打長 key；亦可從剪貼簿貼上一次。
- 開發者模式可調整五條路由的模型 ID、reasoning、max tokens 與四套 system prompt，並從 OpenRouter 即時載入最新／ZDR 模型目錄。
- 開發者設定可匯入／匯出 JSON；不含 API key，但會包含完整自訂 prompt。
- 一鍵複製「實際去識別 input」到其他 AI；60 秒後只在剪貼簿仍未變更時嘗試清除。
- 匯出本機 DOCX／CSV patient list；未載入欄位明確標示「不代表沒有資料」。
- 合成 18 人 Demo，可在不連 EMR、不使用 API key 的情況驗證介面。

## 快速使用

1. 從 GitHub Releases 下載 `WardLens-OneFile.exe`，或下載較容易讓院內 IT 檢查的 `WardLens-Onedir.zip`。
2. 先勾選「合成資料 Demo」並登入，載入 18 人清單。
3. 真實環境取消 Demo，輸入入口網帳密；帳密只留在本次記憶體，不會儲存。
4. 輸入醫師燈號／病房後載入清單，選一名病人，再載入詳細資料。
5. 若院方允許外部 AI，到「隱私與模型」啟用外部 AI、確認政策，再按「用瀏覽器登入 OpenRouter」；授權後 key 會自動存進 Windows Credential Manager。
6. 每次先按「建立去識別預覽」，閱讀完整 payload，再送出或複製。

急症頁面的文字框可用 Windows `Win+H` 語音輸入。若病人有 peri-arrest、嚴重呼吸窘迫、休克、意識惡化或大出血，先叫 senior／RRT／Code／ICU，不要等待模型。

## Fast 與 Slow 怎麼選

| 模式 | 預設模型 | 推理 | 建議用途 |
|---|---|---|---|
| `fast` | `openai/gpt-5.6-terra` | low | 日常查房整理、TODO、報告摘要 |
| `emergency_fast` | `openai/gpt-5.6-sol` | low | 急症第一輪、先取得短而可執行的清單 |
| `deep` | `openai/gpt-5.6-sol` | high | 複雜處置、來源矛盾、第一輪後複核 |
| `gemini_fast` | `google/gemini-3.7-flash` | low | 低延遲替代模型 |
| `claude_second` | `anthropic/claude-sonnet-5` | high | 跨模型第二意見 |

實際延遲也受 prompt 長度、OpenRouter provider 與網路影響。急症建議採「Sol/low 先答 → 病人穩定後 Sol/high 或 Claude 複核」，不在第一輪使用長推理阻塞床邊處置。模型 ID 會變動；發行前應用 OpenRouter models API 與 ZDR endpoint API 重驗。

OpenAI 官方建議以較低 reasoning effort 壓低延遲，只有在評測顯示有實質收益時提高；OpenRouter 支援 per-request `provider.zdr=true`。WardLens 會先即時檢查該模型是否仍有 ZDR endpoint，檢查失敗就不送出。[OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model) · [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr)

## API key 不必手打

推薦按「用瀏覽器登入 OpenRouter」：WardLens 會產生 PKCE verifier、只在 `127.0.0.1` 暫時監聽一次 callback，開啟 OpenRouter 官方授權頁，再自動交換 user-controlled API key。callback 只帶短效授權碼，不帶病歷；key 驗證後只存 Windows Credential Manager。OpenRouter 官方支援桌面／local-first app 使用任意 localhost port。[OpenRouter OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth)

院內瀏覽器若擋 OAuth，可在 OpenRouter 複製 key 後按「從剪貼簿貼上一次」；WardLens 讀取後會立即清除仍相同的剪貼簿內容。移除本機 key 不等於在 OpenRouter 撤銷，停用時仍須到 OpenRouter 帳戶撤銷該 key。

## 開發者模式

在「隱私與模型」啟用後可：

- 編輯 `fast`、`deep`、`emergency_fast`、`gemini_fast`、`claude_second` 的模型 ID、reasoning 與 token 上限。
- 即時取得 OpenRouter 全部模型或目前具有 ZDR endpoint 的模型清單。
- 分別編輯查房、Admission Note、急症與病人問答 prompt；變更後既有 outbound 預覽會立即失效。
- 回復內建值，或跨電腦匯入／匯出不含 key 的 JSON。

傳輸端刻意固定為 OpenRouter；開發者設定不能偷偷改成其他 host。完整操作與安全邊界見 [docs/DEVELOPER_MODE.md](docs/DEVELOPER_MODE.md)。

## 隱私與安全邊界

- 雲端 AI 預設關閉；ZDR 預設開啟且 fail closed。
- 去識別是 deterministic 過濾加人工預覽，不代表法律上的匿名化。姓名、病歷號、床位、電話、email、精確日期等會被移除／相對化，疑似殘留長識別碼會直接封鎖。
- 實際送出的 canonical prompt 必須與使用者看到的 SHA-256 完全一致。
- API key 不寫死、不放 GitHub；使用 Windows Credential Manager。EMR 帳密不保存。
- 不把病歷、prompt 或 response 寫入 log；audit 只有時間、事件、模型與 payload hash。
- 不自動寫回 EMR，不自動下 order，也不在本機建立病歷 cache。

台灣《個人資料保護法》把病歷、醫療與健康檢查列為個人資料及特種資料，且要求不得逾越特定目的必要範圍；是否可把院內資料交由外部處理者，必須由院方依其契約、告知、跨境、安全維護與內規判斷，不是勾選 ZDR 就自動合法。[全國法規資料庫](https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=I0050021)

「院內電腦是封閉環境」只適用於本機擷取與整理。按下送出 OpenRouter 時，去識別後的 payload 仍會經網路交給 OpenRouter／上游 provider；ZDR 是留存控制，不代表資料從未離開院內。WardLens 因此不會略過預覽、逐次確認或 ZDR fail-closed。

完整威脅模型見 [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)。

## 為什麼舊學長 EXE 可執行，新檔可能被擋

檢查三個舊 Release 後，它們都是未簽章的 x64 PyInstaller one-file EXE，沒有一個特殊「醫院允許格式」。因此較合理的解釋是既有檔案 hash／來源／端點政策或院內 allowlist 已有信任，而不是應模仿某種方式繞過防護。

Microsoft 說明 SmartScreen 會同時看 publisher reputation 與 file hash reputation；未簽章的新版本每次都要重新累積 reputation，而 enterprise policy 可以完全禁止使用者略過。正規路徑是：

1. 同時提供 one-file 與 onedir ZIP、SHA-256、原始碼及 CI build 紀錄。
2. 請院內 IT 以 hash／publisher allowlist 核准，而不是關閉 Defender 或繞過政策。
3. 正式部署使用固定 publisher 的可信 RSA code-signing certificate，或採 Microsoft Store／Artifact Signing。

[Microsoft SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) · [Smart App Control signing](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)

## VGH adapter 的防呆

依公開工具與 `labdata_runner` 經驗重新實作，沒有直接複製未授權原始碼：

- 每名病人先跑 `findPatient → findEmr` 建立 context，再讀後續 endpoint。
- 所有 request 共用 rolling 60/min limiter，避免病人內 detail request burst。
- 網路例外後丟棄整個 `requests.Session`；不在壞 session 上假裝重新登入。
- `resdtable`／`reslist` 不存在時視為介面異常，不回傳空資料。
- `Error.jsp`／強 block marker 會乾淨停止；正式報告文字中的單字 `blocked` 不會誤判。
- 病人清單總數不符、重複頁面或達上限時標示 incomplete。
- 不使用 `resdtmonth=00`；預設 60 個月，設定硬上限 240 個月。

真實 pilot 步驟見 [docs/VGH_PILOT.md](docs/VGH_PILOT.md)。

## 開發

需求：Python 3.11+；Windows 官方 Python 通常含 Tkinter。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m wardlens --demo
```

建立免安裝版本：

```powershell
.\scripts\build_windows.ps1
```

輸出位於 `dist\release`。GitHub tag `v*` 會在 Windows runner 重建並建立 Release。詳見 [docs/RELEASE.md](docs/RELEASE.md)。

## 參考專案

- [max870121/Auto_Anes](https://github.com/max870121/Auto_Anes)
- [max870121/VGH_web](https://github.com/max870121/VGH_web)
- [max870121/Autogenerate-admission](https://github.com/max870121/Autogenerate-admission)
- [medinoter](https://medinoter.vercel.app/)（介面／workflow 概念參考）

前三個 repository 未附明確 license，因此本專案只用公開 endpoint 行為作相容性研究，程式碼從零實作；不要把上游原始碼直接貼入本專案。
