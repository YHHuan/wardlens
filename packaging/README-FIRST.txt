WardLens Portable v0.4.0
========================

這是免安裝版本，不需要系統管理員權限，也不會執行 pip 或連網安裝套件。

1. 將 WardLens-Portable.zip「全部解壓縮」到本機資料夾。
2. 不要直接在 ZIP 預覽視窗內執行，也不要只複製單一 BAT 或 EXE。
3. 雙擊 Start-WardLens.bat。
4. 第一次先使用「合成資料 Demo」確認介面。
5. 若無法啟動，雙擊 Diagnose-WardLens.bat，再回報 WardLens-Diagnostics.txt。
6. 若登入後「載入選取病人資料」出現未預期錯誤，回報錯誤碼及視窗列出的
   operation-error.log（通常在 %LOCALAPPDATA%\WardLens），不需提供病歷或帳密。
7. 預覽被 possible_long_identifier 擋下時，新版會開啟本機修正視窗。
   先核對橘色標記、遮罩識別碼，再按「重新檢查並建立預覽」，閱讀後才送出。
8. 左側預設回溯近 3 個月，可選 1／3／6／12 個月；調整後重新載入。
   病人總覽會列出實際辨識日期與缺漏。報告明細每次最多 12 份，優先最新；
   不可把索引或擷取範圍當成完整報告。詳見 docs/AI_AND_TRENDS.md。

啟動時會保留一個命令視窗；WardLens 開啟期間不要關閉它。關閉 WardLens 視窗後，
命令視窗會自動結束。診斷紀錄刻意不包含病歷內容、帳密、API key 或絕對使用者路徑。

技術與安全邊界請閱讀 README.md、SECURITY.md 與 docs 資料夾。
