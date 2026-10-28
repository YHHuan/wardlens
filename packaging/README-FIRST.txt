WardLens Portable v0.6.0
========================

這是免安裝版本，不需要系統管理員權限，也不會執行 pip 或連網安裝套件。

1. 將 WardLens-Portable.zip「全部解壓縮」到本機資料夾。
2. 不要直接在 ZIP 預覽視窗內執行，也不要只複製單一 BAT 或 EXE。
3. 雙擊 WardLens.exe。它必須與 runtime、app、packages 資料夾一起保留。
4. 第一次先使用「合成資料 Demo」確認介面。
5. 若無法啟動，雙擊 Diagnose-WardLens.bat，再回報 WardLens-Diagnostics.txt。
6. 若登入後「載入選取病人資料」出現未預期錯誤，回報錯誤碼及視窗列出的
   operation-error.log（通常在 %LOCALAPPDATA%\WardLens），不需提供病歷或帳密。
7. 預覽被 possible_long_identifier 擋下時，新版會開啟本機修正視窗。
   先核對橘色標記、遮罩識別碼，再按「重新檢查並建立預覽」，閱讀後才送出。
8. 左側預設回溯近 3 個月，可選 1／3／6／12 個月；調整後重新載入。
   病人總覽會列出實際辨識日期與缺漏。報告明細每次最多 12 份，優先最新；
   不可把索引或擷取範圍當成完整報告。詳見 docs/AI_AND_TRENDS.md。
9. 查房／病歷 AI 和急症處置各有可編輯資料框、問題／病況框、回答區。
   資料框刪改後不會附回原始病歷。送出時才開啟去識別確認，背景 prompt 預設收合。
10. v0.5.0 修正中文 UTF-8 串流誤判損毀。若仍失敗，回報 WL-SSE-* 錯誤碼即可，
    不需提供病歷、原始串流或 API key。真正中斷／截短仍會標示內容不完整。
11. v0.6.0 預設不加程式 token 額度；供應商本身仍有上限。開發者模式留白／填 0
    使用供應商預設，亦可指定正整數。舊內建額度會遷移，其他手動額度保留。
12. 本次住院 TPR 生命徵象會連同原表時間帶入；不是即時監視器。缺欄位／日期會明示。
13. 「瀏覽器測試」可檢查 Edge、中文、18 列表格與動態更新。先執行測試，再儲存
    WardLens-Browser-Diagnostics.txt 回傳。若缺少驅動，可在自己的連網電腦按
    「從 Microsoft 準備驅動」，再帶整個資料夾到院內。公開 ZIP 不內附授權限制
    重新散布的 WebDriver；準備後的資料夾請勿重新公開。詳見 docs/BROWSER_TEST.md。

升級請關閉舊版、另解壓縮新資料夾，確認標題為 0.6.0；既有 key／自訂模型／prompt 保留。

WardLens.exe 直接開啟介面；如果需要命令視窗看啟動訊息，可改用 Start-WardLens.bat，
此時 WardLens 開啟期間不要關閉命令視窗。診斷不包含病歷、帳密、key 或絕對使用者路徑。
WardLens.exe 是專案原始碼編譯的未簽章啟動器；官方 Python runtime 保有 PSF 簽章。

技術與安全邊界請閱讀 README.md、SECURITY.md 與 docs 資料夾。
