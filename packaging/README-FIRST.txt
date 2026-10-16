WardLens Portable v0.3.1
========================

這是免安裝版本，不需要系統管理員權限，也不會執行 pip 或連網安裝套件。

1. 將 WardLens-Portable.zip「全部解壓縮」到本機資料夾。
2. 不要直接在 ZIP 預覽視窗內執行，也不要只複製單一 BAT 或 EXE。
3. 雙擊 Start-WardLens.bat。
4. 第一次先使用「合成資料 Demo」確認介面。
5. 若無法啟動，雙擊 Diagnose-WardLens.bat，再回報 WardLens-Diagnostics.txt。
6. 若登入後「載入選取病人資料」出現未預期錯誤，回報錯誤碼及視窗列出的
   operation-error.log（通常在 %LOCALAPPDATA%\WardLens），不需提供病歷或帳密。

啟動時會保留一個命令視窗；WardLens 開啟期間不要關閉它。關閉 WardLens 視窗後，
命令視窗會自動結束。診斷紀錄刻意不包含病歷內容、帳密、API key 或絕對使用者路徑。

技術與安全邊界請閱讀 README.md、SECURITY.md 與 docs 資料夾。
