# WardLens

WardLens 是給台灣 PGY／住院醫師使用的 Windows 查房輔助工具。它以唯讀方式整理 VGH EMR 病人清單與選取病人的 admission note、progress note、用藥、檢驗趨勢及報告索引，再由使用者決定是否把「先去識別、再人工預覽」的內容送往 OpenRouter。

> 狀態：`v0.4.0` 是安全導向 portable pilot：新增被擋預覽的本機修正流程、更新模型，並預設查詢近三個月的檢驗／報告。院內資料載入已獲使用者回報成功；新增報告明細與各模型實際生成仍需院內／帳戶驗收。它不是已核准的醫療器材，不會自動寫回 EMR。

## 目前功能

- 顯示超過 15 人的清單，追蹤分頁、去重、頁面宣告總數與實際取得數。
- 只在使用者選取病人後抓詳細內容，不預抓整批病歷。
- 本機整理最新 admission note 頂端 TODO、近期報告與來源 hash。
- 產生查房重點、英文 Admission Note、自由提問與成人住院急症處置草稿。
- 保留快速整理／急症第一輪與慢速複核，提供 OpenAI、Claude、Gemini 共七條路由。
- OpenRouter 瀏覽器一鍵登入（OAuth PKCE），不必手打長 key；亦可從剪貼簿貼上一次。
- 開發者模式可調整七條路由的模型 ID、reasoning、max tokens 與四套 system prompt，並從 OpenRouter 即時載入最新／ZDR 模型目錄；升級保留自訂模型與 prompt。
- 預覽遇到疑似長識別碼時，可在本機反白遮罩／修正，再重新檢查；不會直接解除阻擋。
- 可選近 1／3／6／12 個月回溯，保留累積檢驗日期／欄位；年度報告索引依範圍篩選，最多讀取最新 12 份明細。涵蓋日期與缺漏會列入總覽和 AI input。
- 開發者設定可匯入／匯出 JSON；不含 API key，但會包含完整自訂 prompt。
- 一鍵複製「實際去識別 input」到其他 AI；60 秒後只在剪貼簿仍未變更時嘗試清除。
- 匯出本機 DOCX／CSV patient list；未載入欄位明確標示「不代表沒有資料」。
- 合成 18 人 Demo，可在不連 EMR、不使用 API key 的情況驗證介面。

## 快速使用

1. [直接下載 v0.4.0 Portable ZIP](https://github.com/YHHuan/wardlens/releases/download/v0.4.0/WardLens-Portable.zip)，或從 [GitHub Releases](https://github.com/YHHuan/wardlens/releases) 選 `WardLens-Portable.zip`。不是「Code → Download ZIP」或 `WardLens-Source.zip`。
2. 將 ZIP **完整解壓縮**到本機資料夾；不可在 ZIP 預覽內執行，也不可只複製單一 BAT。
3. 雙擊 `Start-WardLens.bat`。啟動期間保留命令視窗；第一次先勾選「合成資料 Demo」並登入，確認能載入 18 人清單。
4. 真實環境取消 Demo，輸入入口網帳密；帳密只留在本次記憶體，不會儲存。
5. 輸入醫師燈號／病房後載入清單，選一名病人；左側選回溯月份（預設 3），再載入詳細資料。更改範圍後需重新載入。
6. 若院方允許外部 AI，到「隱私與模型」啟用外部 AI、確認政策，再按「用瀏覽器登入 OpenRouter」；授權後 key 會自動存進 Windows Credential Manager。
7. 可先按「檢查連線／目前模型」確認 key 與公開目錄（不傳病歷、不生成）；再用合成 Demo 測試實際生成。帳戶須有足夠 credits／key 額度。
8. 每次先按「建立去識別預覽」。若出現本機修正視窗，檢查橘色疑似識別碼、遮罩後按「重新檢查並建立預覽」。閱讀完整 payload，才送出或複製。

目標電腦不需要安裝 Python、pip 或套件，也不需要系統管理員權限。若沒有出現視窗，雙擊 `Diagnose-WardLens.bat`；它會保留命令視窗並產生不含病歷、帳密、API key 與絕對使用者路徑的 `WardLens-Diagnostics.txt`。

若登入與清單正常，但載入病歷出現「未預期錯誤」，v0.3.1 會顯示操作／例外代碼及 `operation-error.log` 的位置（通常是 `%LOCALAPPDATA%\WardLens`）。該檔只包含版本、錯誤類型與程式位置，不含病歷、帳密、API key、原始回應或例外訊息。請回報這份診斷，不要上傳病歷 HTML。`Diagnose-WardLens.bat` 是離線檢查，無法單憑它確認院內連線正常。

急症頁面的文字框可用 Windows `Win+H` 語音輸入。若病人有 peri-arrest、嚴重呼吸窘迫、休克、意識惡化或大出血，先叫 senior／RRT／Code／ICU，不要等待模型。

## Fast 與 Slow 怎麼選

| 模式 | 預設模型 | 推理 | 建議用途 |
|---|---|---|---|
| `fast` | `openai/gpt-6.1-sol` | low | 日常查房整理、TODO、報告摘要 |
| `emergency_fast` | `openai/gpt-6.1-sol` | low | 急症第一輪；不可等 AI 才處置 |
| `deep` | `openai/gpt-6-astra` | high | 複雜處置、來源矛盾、第一輪後複核 |
| `gemini_fast` | `google/gemini-3.8-flash` | low | 快速替代模型 |
| `claude_second` | `anthropic/claude-sonnet-5.5` | high | 跨模型第二意見 |
| `claude_deep` | `anthropic/claude-opus-5.5` | high | Claude 深入複核 |
| `gemini_deep` | `google/gemini-4-argon` | high | Gemini 深入複核 |

型號於 **2026-10-16** 依 [OpenAI 官方目錄](https://developers.openai.com/api/docs/models)、[Claude 官方目錄](https://platform.claude.com/docs/en/models/overview)、[Gemini 官方目錄](https://ai.google.dev/gemini-api/docs/models) 與 [OpenRouter 即時模型資料](https://openrouter.ai/api/v1/models) 核對。這不是臨床效能評測；快速／深入是工作流程配置，沒有保證秒數。Anthropic 另有更新的 Fable 5.1，但核對當下沒有 OpenRouter ZDR endpoint，因此不列為本版臨床預設。

急症採「Sol/low 先整理 → 病人穩定後 Astra/high 或跨模型複核」。實際延遲取決於 input 長度、推理預算、provider 與網路。每次送出前會檢查當前模型／reasoning 相容性及 ZDR 目錄，不自動換模型或放寬隱私條件。若已自訂舊模型，請在開發者模式按「模型改用新版內建值（保留 prompt）」再儲存。

OpenAI 官方建議以較低 reasoning effort 壓低延遲，只有在評測顯示有實質收益時提高；OpenRouter 支援 per-request `provider.zdr=true`。WardLens 會先即時檢查該模型是否仍有 ZDR endpoint，檢查失敗就不送出。[OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model) · [OpenRouter ZDR](https://openrouter.ai/docs/guides/features/zdr)

## API key 不必手打

推薦按「用瀏覽器登入 OpenRouter」：WardLens 會產生 PKCE verifier、只在 `127.0.0.1` 暫時監聽一次 callback，開啟 OpenRouter 官方授權頁，再自動交換 user-controlled API key。callback 只帶短效授權碼，不帶病歷；key 驗證後只存 Windows Credential Manager。OpenRouter 官方支援桌面／local-first app 使用任意 localhost port。[OpenRouter OAuth PKCE](https://openrouter.ai/docs/guides/overview/auth/oauth)

院內瀏覽器若擋 OAuth，可在 OpenRouter 複製 key 後按「從剪貼簿貼上一次」；WardLens 讀取後會立即清除仍相同的剪貼簿內容。移除本機 key 不等於在 OpenRouter 撤銷，停用時仍須到 OpenRouter 帳戶撤銷該 key。

## 開發者模式

在「隱私與模型」啟用後可：

- 編輯上述七條路由的模型 ID、reasoning 與 token 上限；可只更新模型並保留 prompt。
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

## 為什麼改成 BAT portable package

實際比對可運作的 `vgh-psy-extract` 後，關鍵不是 BAT 本身能繞過防護：BAT 最後仍啟動一份 Python。它使用 Python Software Foundation 官方發行的 runtime，而舊 WardLens one-file／onedir 則含每次 build 都會改變、且沒有可信 publisher 的 PyInstaller executable。院內端點政策很可能在 Python process 啟動前就封鎖後者，因此 WardLens 看起來「完全沒反應」，也來不及寫 Python 錯誤紀錄。

`v0.3.0` 改為同樣易稽核的分層方式：可閱讀的 BAT 只呼叫 ZIP 內的 PSF 官方 `python.exe`，完整 runtime ZIP 先以 python.org SPDX 所列 SHA-256 驗證，CI 再驗證 `python.exe`、`pythonw.exe`、`python314.dll` 的 Authenticode publisher。WardLens 與第三方套件只有 `.py`／resource 檔，且第三方 pure-Python wheels 全部鎖版本與 SHA-256；目標電腦不執行 PowerShell、安裝程式、pip、下載器或 execution-policy bypass。

這會大幅減少未知 executable，但不能保證任何特定醫院政策一定放行。若仍被阻擋，應把 Release 的 `SHA256SUMS.txt`、`BUILD_PROVENANCE.txt` 與診斷結果交給院內 IT 做 publisher／hash allowlist；不要停用 Defender 或規避政策。

## VGH adapter 的防呆

依公開工具與 `labdata_runner` 經驗重新實作，沒有直接複製未授權原始碼：

- 每名病人先跑 `findPatient → findEmr` 建立 context，再讀後續 endpoint。
- 所有 request 共用 rolling 60/min limiter，避免病人內 detail request burst。
- 網路例外後丟棄整個 `requests.Session`；不在壞 session 上假裝重新登入。
- `resdtable`／`reslist` 不存在時視為介面異常，不回傳空資料。
- `Error.jsp`／強 block marker 會乾淨停止；正式報告文字中的單字 `blocked` 不會誤判。
- 病人清單總數不符、重複頁面或達上限時標示 incomplete。
- 不使用 `resdtmonth=00`；預設 3 個月，介面可選 1／3／6／12 個月。
- 每次最多 40 個院內請求、最多 12 份最新報告全文；跨年索引分年查詢。遇到未辨識日期、未讀全文、分頁或截短會明示，不能視為完整三個月。

預覽修正、OpenRouter 連線及趨勢限制見 [AI 與歷史資料操作說明](docs/AI_AND_TRENDS.md)。

真實 pilot 步驟見 [docs/VGH_PILOT.md](docs/VGH_PILOT.md)。

## 開發

原始碼開發需求：Python 3.11+；Release 使用鎖定的官方 Windows Python 3.14.7 runtime。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m wardlens --demo
```

在 Windows 建立免安裝版本（build 電腦需可連 python.org／PyPI，目標電腦不需連線安裝）：

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
