WardLens Portable v0.7.0
========================

免安裝；主介面不用 Node、pip、管理員權限或 WebDriver。

開始使用
1. 將 WardLens-Portable.zip「全部解壓縮」到本機資料夾。
2. 雙擊 WardLens.exe；它必須與 runtime、app、packages 一起保留。
   不要直接在 ZIP 預覽視窗內執行，也不要只複製單一 EXE 或 BAT。
3. 小控制視窗會開啟本機工作台，Windows 優先使用 Edge InPrivate，
   不可用時嘗試預設瀏覽器。第一次先使用「合成資料 Demo」登入。
4. 載入病人後，「查房前整理」會顯示 TODO、報告、用藥與檢驗趨勢。
   CBC 優先看 WBC／Hb／PLT；differential、詳細時間與來源可以展開。
5. 查房與急症各有可編輯資料框、問題／病況框與回答區。
   回答可閱讀 Markdown，也可「複製純文字」。背景 prompt 預設收合。
6. 在「設定」登入 OpenRouter，或貼上一次 API key 並儲存。
   Key 存在 Windows 認證管理員。每次送出仍須預覽、修正疑似識別碼及確認。

重開與結束
- 重新整理網頁後，請從控制視窗按「開啟工作台」。
- 舊介面可按「切換桌面備援」、執行 Start-WardLens-Desktop.bat，
  或使用 WardLens.exe --desktop。切換不會搬移病歷草稿。
- 控制視窗可以最小化；關閉或按「結束」會停止服務並清除本次臨床狀態。
  只關閉網頁不等於結束程式。也可在網頁按「登出清除」清除病人資料。
- Start-WardLens.bat 可保留啟動訊息；使用時不要提早關閉命令視窗。

升級與資料
- 關閉舊版、另解壓縮新資料夾，確認版本為 0.7.0。
  既有 key／自訂模型／prompt 保留；新版內建 prompt 不會覆蓋自訂內容。
- 回溯預設 3 個月，可選 1／3／6／12；更改後重新載入。
  報告明細最多 12 份，優先最新；這不保證取得完整三個月資料。
- 生命徵象是本次住院 TPR 快照，不是即時監視器。缺時間／欄位會明示。
- 程式不設預設輸出 token 額度；供應商仍有上限。中斷或截短會標示不完整。
- 靜態網頁資源隨包附上，無 CDN；臨床資料不寫入網頁儲存空間。
  no-store／InPrivate 不保證瀏覽器、剪貼簿或作業系統絕不留痕。

遇到問題
- 無法啟動時，雙擊 Diagnose-WardLens.bat，回報 WardLens-Diagnostics.txt。
- 載入或 AI 失敗時，回報版本、操作與錯誤碼；不要傳病歷、原始回應或 key。
  診斷不包含病歷、帳密、key 或絕對使用者路徑。
- 「瀏覽器測試」是獨立的 WebDriver 前置測試；日常操作不需要它。
  若測試缺驅動，可在自己的連網電腦按「從 Microsoft 準備驅動」，
  再帶完整資料夾到自己的院內電腦。公開 ZIP 不附限制重新散布的驅動；
  準備過的個人資料夾請勿重新公開。PASS 不等於 EMR 已改用 Selenium。

WardLens.exe 是未簽章的專案啟動器；官方 Python runtime 保有 PSF 簽章。
更多操作見 docs/LOCAL_UI.md、docs/AI_AND_TRENDS.md 與 docs/BROWSER_TEST.md。
