/* Transparent portable-folder launcher; no installer, unpacking or network. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <strsafe.h>
#include <wchar.h>

#define CAPACITY 32768

static int fail(BOOL quiet, DWORD code, const wchar_t *message)
{
    if (!quiet) {
        wchar_t detail[1024];
        if (SUCCEEDED(StringCchPrintfW(detail, 1024, L"%s\n\n錯誤碼：%lu\n可執行同資料夾的 Diagnose-WardLens.bat 檢查。", message, code))) {
            MessageBoxW(NULL, detail, L"WardLens 無法啟動", MB_OK | MB_ICONERROR);
        }
    }
    return (int)(code ? code : 1);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    wchar_t root[CAPACITY], runtime[CAPACITY], module[CAPACITY], command[CAPACITY];
    wchar_t *separator;
    DWORD length, exit_code = 1;
    BOOL quiet = arguments && *arguments;
    BOOL inherit = FALSE;
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    UNREFERENCED_PARAMETER(instance);
    UNREFERENCED_PARAMETER(previous);
    UNREFERENCED_PARAMETER(show);

    length = GetModuleFileNameW(NULL, root, CAPACITY);
    if (!length || length >= CAPACITY) {
        return fail(quiet, ERROR_BAD_PATHNAME, L"無法辨識程式資料夾，請重新完整解壓縮。");
    }
    separator = wcsrchr(root, L'\\');
    if (!separator) {
        return fail(quiet, ERROR_BAD_PATHNAME, L"無法辨識程式資料夾。");
    }
    separator[1] = L'\0';
    if (FAILED(StringCchPrintfW(runtime, CAPACITY, L"%sruntime\\%s", root, quiet ? L"python.exe" : L"pythonw.exe")) ||
        FAILED(StringCchPrintfW(module, CAPACITY, L"%sapp\\wardlens\\__main__.py", root))) {
        return fail(quiet, ERROR_FILENAME_EXCED_RANGE, L"資料夾路徑過長，請移到較短的路徑。");
    }
    if (GetFileAttributesW(runtime) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(module) == INVALID_FILE_ATTRIBUTES) {
        return fail(quiet, ERROR_FILE_NOT_FOUND,
            L"請將 WardLens-Portable.zip 完整解壓縮後再開啟。\nWardLens.exe 必須與 runtime、app、packages 資料夾放在一起，不能只複製 EXE。");
    }
    if (FAILED(StringCchPrintfW(command, CAPACITY, L"\"%s\" -I -B -X utf8 -m wardlens %s", runtime, arguments ? arguments : L""))) {
        return fail(quiet, ERROR_BAD_ARGUMENTS, L"啟動參數過長。");
    }

    startup.cb = sizeof(startup);
    /* Preserve redirected diagnostic output, if the caller supplied handles. */
    if (quiet && GetStdHandle(STD_OUTPUT_HANDLE) && GetStdHandle(STD_OUTPUT_HANDLE) != INVALID_HANDLE_VALUE) {
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
        startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
        startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
        inherit = TRUE;
    }
    /* Explicit absolute application path: no PATH lookup and no command shell. */
    if (!CreateProcessW(runtime, command, NULL, NULL, inherit, CREATE_NO_WINDOW, NULL, root, &startup, &process)) {
        DWORD error = GetLastError();
        return fail(quiet, error, L"無法啟動附帶的 Python 執行環境。請檢查是否完整解壓縮，或使用 Start-WardLens.bat 查看原因。");
    }
    CloseHandle(process.hThread);
    if (WaitForSingleObject(process.hProcess, INFINITE) != WAIT_OBJECT_0 ||
        !GetExitCodeProcess(process.hProcess, &exit_code)) {
        exit_code = GetLastError();
        if (!exit_code) exit_code = 1;
    }
    CloseHandle(process.hProcess);
    if (exit_code) {
        return fail(quiet, exit_code, L"WardLens 已停止。請使用 Start-WardLens.bat 或診斷工具查看啟動結果。");
    }
    return 0;
}
