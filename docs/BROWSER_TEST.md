# 瀏覽器相容性測試

v0.7.0 的本機網頁工作台直接使用瀏覽器，**不需要 WebDriver，也不必先跑這項測試**；病歷擷取仍使用原本的 Requests 流程。網頁的開啟／重開與桌面備援見 [本機工作台](LOCAL_UI.md)。

本頁是 v0.6.0 起提供的獨立測試，確認電腦是否能啟動 Edge WebDriver、讀取中文、讀取 18 列表格、點擊按鈕並等候動態更新。它使用 Selenium 同樣採用的 W3C WebDriver 介面，沒有內附 Selenium Python SDK，也尚未把 EMR 抓取改成瀏覽器操作。這項 PASS 不能當成整套 Selenium EMR 已驗收。

## 操作

1. 完整解壓縮 Portable ZIP，開啟 `WardLens.exe`，進入「瀏覽器測試」。不需要登入醫院或 OpenRouter。
2. 先按「執行本機測試」（桌面備援為「執行本機瀏覽器測試」）。沒有驅動或版本不合仍能產生診斷紀錄。
3. 若顯示 `DRIVER_NOT_PREPARED`，可在自己的連網電腦按「從 Microsoft 準備驅動（需外網）」。它只下載版本鎖定的官方驅動，核對 ZIP／EXE SHA-256，保留附帶授權文件；不安裝、不更改系統政策。再將整個資料夾帶到自己的測試電腦。
4. 再執行測試。通過版本與 hash 檢查後，會暫時開啟合成測試頁，結束時關閉該測試工作階段。
5. 按「儲存診斷紀錄」，回傳 `WardLens-Browser-Diagnostics.txt`，PASS／FAIL 都有用。

公開 ZIP 沒有內附 Microsoft WebDriver：其附帶 EULA 第 3.e 節限制重新散布。請勿把個人準備過、含驅動的整包重新公開發布。若院內不能下載，可先在自己的電腦準備；若無法準備，先回傳版本紀錄即可。

CLI 診斷也可執行 `WardLens.exe --browser-diagnostics`，紀錄寫入 `%LOCALAPPDATA%\WardLens\WardLens-Browser-Diagnostics.txt`。正常失敗會以報告內的 `result: FAIL` 與固定代碼表示，請勿只用 process exit code 判斷相容性。

## 紀錄內容與代碼

紀錄只包含 WardLens／Windows 架構／Edge／驅動版本、固定測試名稱與結果。沒有病歷、帳密、API key、cookies、既有瀏覽器資料、主機名稱、使用者路徑或原始錯誤訊息。測試只開啟合成的本機 `data:` 頁面，使用全新暫時設定檔，並且不登入醫院。它不使用既有 EMR session，也不傳送 OpenRouter 請求；瀏覽器自身仍受系統網路與企業政策管理。

| 代碼 | 意義／下一步 |
|---|---|
| `WINDOWS_64BIT_REQUIRED` | 此試驗支援 64 位元 Windows；x64 套件也可在支援模擬的 ARM64 上測試。 |
| `EDGE_NOT_FOUND` | 未在標準路徑找到 Edge；回傳紀錄。 |
| `DRIVER_VERSION_MISMATCH` | Edge 與已鎖定驅動的前三段版本不相同；回傳版本紀錄，再準備相符版本。不要為配合測試降級院內瀏覽器。 |
| `DRIVER_NOT_PREPARED` | 尚無本機驅動；使用準備按鈕，或先回傳紀錄。 |
| `DRIVER_HASH_MISMATCH` | 檔案與鎖定版本不符，未執行。重新準備或回傳紀錄。 |
| `DRIVER_START_FAILED`／`DRIVER_EXITED`／`DRIVER_NOT_READY` | 驅動無法啟動或未就緒；需依實機紀錄判斷。 |
| `WEBDRIVER_CONNECTION_FAILED`／`WEBDRIVER_COMMAND_REJECTED` | 無法連到本機服務或瀏覽器拒絕命令；可能涉及版本、架構或企業政策。 |
| `UNICODE_DOM_MISMATCH`／`TABLE_COUNT_MISMATCH`／`DYNAMIC_UPDATE_TIMEOUT` | 合成資料測試未達預期；回傳紀錄。 |
| `UNEXPECTED_PROBE_ERROR` | 未分類錯誤，已移除原始例外內容；回傳紀錄。 |

PASS 代表這幾項測試通過，尚未驗證院內登入、SSO、iframe、病歷頁面與擷取完整性。瀏覽器自動化仍可能被網站或院內政策辨識／阻擋；本工具沒有規避或隱藏機制。先用此紀錄決定後續的 Selenium adapter 與版本準備方式，既有 Requests 擷取流程持續使用。

Microsoft 要求 Edge 與 WebDriver 的前三段版本相符：[Microsoft Edge WebDriver](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/)。
