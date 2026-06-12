#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_totp_to_yubikey.py
=========================
將密碼管理員匯出的 JSON 檔案中的 TOTP 項目，批次寫入 YubiKey。

使用方式
--------
  python import_totp_to_yubikey.py --file <JSON 檔案路徑> [--timeout <秒數>] [--delay <秒數>]

參數說明
--------
  --file      必填。包含 TOTP 資料的 JSON 檔案路徑。
  --timeout   可選。等待每筆 ykman 指令完成的最長秒數（預設：15 秒）。
              若 YubiKey 設有「需要觸碰確認 (Require Touch)」，請將此值
              調高（例如 30 秒），以留出足夠的反應時間。
  --delay     可選。每次寫入後的等待秒數（預設：0.5 秒），避免連續寫入
              過快造成硬體或 CLI 無法反應。

JSON 檔案格式範例
-----------------
  [
    {
      "name": "Google",
      "username": "user@gmail.com",
      "totp": "otpauth://totp/Google%3Auser%40gmail.com?secret=JBSWY3DPEHPK3PXP&issuer=Google"
    },
    ...
  ]

安全提醒
--------
  執行完畢後，請務必用安全抹除工具徹底刪除來源 JSON 檔案！
  Linux/WSL：shred -vuz <檔案路徑>
  Windows  ：使用 SDelete 或 Eraser 等工具
"""

import argparse
import json
import subprocess
import sys
import time


# ────────────────────────────────────────────────
# ANSI 終端機顏色代碼（讓輸出更易讀）
# ────────────────────────────────────────────────
class Colors:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    DIM     = "\033[2m"


def cprint(color: str, msg: str) -> None:
    """帶顏色輸出到終端機。"""
    print(f"{color}{msg}{Colors.RESET}")


def separator(char: str = "─", width: int = 60) -> None:
    """印出分隔線。"""
    print(Colors.DIM + char * width + Colors.RESET)


# ────────────────────────────────────────────────
# 步驟一：環境檢查 — 確認 YubiKey 已插入且 ykman 可用
# ────────────────────────────────────────────────
def check_yubikey() -> None:
    """
    執行 `ykman info` 來確認：
      1. `ykman` 指令存在於系統 PATH 中。
      2. YubiKey 已正確插入並被系統識別。

    若任一條件不符，立即中止腳本。
    """
    cprint(Colors.CYAN, "\n[*] 正在檢查 YubiKey 連線狀態...")

    try:
        result = subprocess.run(
            ["ykman", "info"],
            capture_output=True,   # 同時捕獲 stdout 與 stderr
            text=True,             # 以文字模式回傳（非 bytes）
            timeout=10             # 10 秒內若無回應視為失敗
        )
    except FileNotFoundError:
        # ykman 不在 PATH 中
        cprint(Colors.RED, "[✗] 錯誤：找不到 `ykman` 指令。")
        cprint(Colors.YELLOW, "    請先安裝 yubikey-manager：")
        cprint(Colors.YELLOW, "    Ubuntu/WSL：sudo apt install yubikey-manager")
        cprint(Colors.YELLOW, "    Windows   ：https://developers.yubico.com/yubikey-manager/")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        cprint(Colors.RED, "[✗] 錯誤：`ykman info` 執行超時，YubiKey 可能未正確連接。")
        sys.exit(1)

    if result.returncode != 0:
        # ykman 回傳非零狀態碼，通常代表找不到 YubiKey
        cprint(Colors.RED, "[✗] 錯誤：未偵測到 YubiKey，或存取失敗。")
        cprint(Colors.YELLOW, "    請確認：")
        cprint(Colors.YELLOW, "    1. YubiKey 已插入 USB 埠")
        cprint(Colors.YELLOW, "    2. 目前使用者有足夠的裝置存取權限")
        if result.stderr.strip():
            # 僅在有 stderr 訊息時才顯示除錯資訊
            cprint(Colors.DIM, f"    除錯訊息：{result.stderr.strip()}")
        sys.exit(1)

    # 成功：截取並顯示 YubiKey 裝置資訊（過濾空白行）
    device_info_lines = [
        line for line in result.stdout.splitlines() if line.strip()
    ]
    cprint(Colors.GREEN, "[✓] YubiKey 偵測成功！")
    separator()
    for line in device_info_lines:
        print(f"    {line}")
    separator()


# ────────────────────────────────────────────────
# 步驟二：讀取並解析 JSON 檔案
# ────────────────────────────────────────────────
def load_totp_entries(filepath: str) -> list[dict]:
    """
    從指定路徑讀取 JSON 檔案，兼容以下兩種 Bitwarden 匯出格式：

    ┌─────────────────────────────────────────────────────────────────┐
    │ 格式 A：Bitwarden Authenticator App 匯出                        │
    │  結構：{ "encrypted": false, "items": [ ... ] }                 │
    │  每筆：{ "name": "...", "login": { "username": "...",           │
    │                                    "totp": "otpauth://..." } }  │
    ├─────────────────────────────────────────────────────────────────┤
    │ 格式 B：Bitwarden 完整密碼庫匯出                                  │
    │  結構：{ "encrypted": false, "folders": [...], "items": [...] } │
    │  每筆：{ "name": "...", "login": { "username": "...",           │
    │                                    "totp": "otpauth://..." } }  │
    │  注意：大多數項目無 totp 欄位，僅少數有，會自動跳過沒有 totp 的項目。│
    └─────────────────────────────────────────────────────────────────┘

    兩種格式的 Key 路徑完全相同（name / login.username / login.totp），
    差異在於格式 B 頂層多了 folders 陣列，且絕大多數 item 沒有 totp。

    Args:
        filepath: JSON 檔案的路徑字串。

    Returns:
        驗證通過、包含 totp 的項目清單，每筆為含 name/username/totp 的 dict。

    Raises:
        SystemExit: 若檔案不存在、JSON 格式錯誤，或最終無任何有效項目。
    """
    cprint(Colors.CYAN, f"\n[*] 正在讀取 JSON 檔案：{filepath}")

    # ── 1. 讀取與基本 JSON 解析 ──────────────────────────────────────
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        cprint(Colors.RED, f"[✗] 錯誤：找不到檔案 '{filepath}'")
        sys.exit(1)
    except json.JSONDecodeError as e:
        cprint(Colors.RED, f"[✗] 錯誤：JSON 格式解析失敗 — {e}")
        sys.exit(1)

    # ── 2. 自動判斷根層級結構，萃取 items 陣列 ───────────────────────
    # 格式 A / B 的根層級皆為 dict，且 items 鍵對應實際資料陣列。
    # 若根層級直接就是陣列（舊版或其他工具匯出），也一併支援。
    if isinstance(raw, dict):
        items = raw.get("items", [])
        if not isinstance(items, list):
            cprint(Colors.RED,
                   "[✗] 錯誤：JSON 中的 'items' 欄位不是陣列，無法解析。")
            sys.exit(1)
        # 判斷格式以顯示偵測到的類型
        if "folders" in raw:
            cprint(Colors.CYAN,
                   "    → 偵測到：Bitwarden 完整密碼庫匯出格式（含 folders）")
        else:
            cprint(Colors.CYAN,
                   "    → 偵測到：Bitwarden Authenticator App 匯出格式")
    elif isinstance(raw, list):
        # 根層級直接是陣列的舊版格式
        items = raw
        cprint(Colors.CYAN, "    → 偵測到：純陣列格式（根層級為 list）")
    else:
        cprint(Colors.RED, "[✗] 錯誤：無法識別的 JSON 根層級結構。")
        sys.exit(1)

    cprint(Colors.CYAN, f"    → 共讀取 {len(items)} 筆原始資料，開始過濾...\n")

    # ── 3. 逐筆解析，安全萃取 name / username / totp ────────────────
    valid_entries = []   # 最終有效清單
    skipped_count = 0    # 統計跳過筆數（含無 totp 的正常項目）
    no_totp_count = 0    # 單獨統計「本來就沒有 2FA」的項目數

    for idx, item in enumerate(items, start=1):
        # 防呆：每筆必須是 dict 物件
        if not isinstance(item, dict):
            cprint(Colors.YELLOW, f"[!] 警告：第 {idx} 筆不是物件，已跳過。")
            skipped_count += 1
            continue

        # 安全取得 name（退路：用索引標記）
        name = item.get("name") or f"<第 {idx} 筆>"

        # 安全取得 login 子物件
        login = item.get("login")
        if not isinstance(login, dict):
            # 此項目根本沒有 login 區塊（例如 type != 1 的 note/card 類型）
            # 靜默跳過，不視為錯誤
            skipped_count += 1
            no_totp_count += 1
            continue

        # ⚠️ 安全取值：totp 欄位不存在或為 None / 空字串時，靜默跳過
        # 絕對不在此處印出 totp 的值（零明文輸出原則）
        totp_uri = login.get("totp")
        if not totp_uri:
            # 此項目沒有設定 2FA，這是正常情況，不需要警告
            skipped_count += 1
            no_totp_count += 1
            continue

        # 基本格式驗證：totp URI 必須以 otpauth:// 開頭
        if not str(totp_uri).startswith("otpauth://"):
            cprint(Colors.YELLOW,
                   f"[!] 警告：「{name}」的 totp 欄位格式不正確"
                   f"（非 otpauth:// 開頭），已跳過。")
            skipped_count += 1
            continue

        # 安全取得 username（可能為 None，轉為空字串備用）
        username = login.get("username") or ""

        # 所有檢查通過，加入有效清單
        # 統一輸出格式：{ name, username, totp }
        valid_entries.append({
            "name":     name,
            "username": username,
            "totp":     totp_uri,   # ⚠️ 此值僅存於記憶體，不會被印出
        })

    # ── 4. 輸出統計摘要 ──────────────────────────────────────────────
    other_skipped = skipped_count - no_totp_count
    cprint(Colors.CYAN,
           f"    → 解析完成：{len(valid_entries)} 筆含 TOTP，"
           f"{no_totp_count} 筆無 2FA（已略過）"
           + (f"，{other_skipped} 筆格式異常（已略過）"
              if other_skipped > 0 else ""))

    if not valid_entries:
        cprint(Colors.RED, "[✗] 錯誤：沒有任何有效的 TOTP 資料可供匯入。")
        sys.exit(1)

    return valid_entries


# ────────────────────────────────────────────────
# 步驟三：預覽清單並請求使用者確認
# ────────────────────────────────────────────────
def preview_and_confirm(entries: list[dict]) -> None:
    """
    在終端機顯示即將匯入的項目清單，並等待使用者確認。

    【安全設計】：此函式絕對不會印出 totp URI 或 Secret，
    僅顯示 name 與 username 供使用者核對。

    Args:
        entries: 驗證通過的 TOTP 項目清單。

    Raises:
        SystemExit: 若使用者選擇不繼續。
    """
    total = len(entries)

    separator("═")
    cprint(Colors.BOLD + Colors.MAGENTA, "  即將匯入至 YubiKey 的 TOTP 項目清單")
    separator("═")

    for i, entry in enumerate(entries, start=1):
        name     = entry["name"]
        username = entry["username"]
        # ⚠️ 安全提醒：只印 name 與 username，totp 欄位在此絕對不輸出
        print(f"  {Colors.CYAN}{i:>3}.{Colors.RESET} {Colors.BOLD}{name}{Colors.RESET}"
              f"  {Colors.DIM}({username}){Colors.RESET}")

    separator("═")
    cprint(Colors.YELLOW,
           f"\n  共解析出 {Colors.BOLD}{total}{Colors.RESET}"
           f"{Colors.YELLOW} 筆資料，確認無誤並開始匯入？(y/n) ")

    # 讀取使用者輸入，並對非預期輸入進行保守處理（預設拒絕）
    try:
        answer = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # 處理 Ctrl+C 或管道輸入結束的情況
        print()
        cprint(Colors.YELLOW, "\n[!] 操作已取消（使用者中斷）。")
        sys.exit(0)

    if answer != "y":
        cprint(Colors.YELLOW, "\n[!] 使用者選擇不繼續，安全退出。")
        sys.exit(0)

    cprint(Colors.GREEN, "\n[✓] 已確認，開始批次匯入...\n")


# ────────────────────────────────────────────────
# 步驟四：執行批次匯入
# ────────────────────────────────────────────────
def import_entries(entries: list[dict], timeout: int, delay: float) -> None:
    """
    逐筆執行 `ykman oath accounts uri "<totp>"` 將 TOTP 寫入 YubiKey。

    【安全設計】：
      - subprocess 的 stdout 輸出會被捕獲並過濾，不會顯示在終端機上。
      - 僅在 stderr 有內容時才印出除錯訊息。
      - TOTP URI 本身絕對不會出現在任何終端機輸出中。

    【Timeout 防呆】：
      - 每筆指令設有 timeout 秒限制，避免 YubiKey 需要觸碰確認時
        造成腳本無限期卡死。

    Args:
        entries: 驗證通過的 TOTP 項目清單。
        timeout: 每筆指令的最長等待秒數。
        delay:   每次寫入後的等待秒數。
    """
    total         = len(entries)
    success_count = 0
    fail_count    = 0
    failed_names  = []  # 記錄失敗的項目名稱（不記錄 totp 內容）

    for i, entry in enumerate(entries, start=1):
        name     = entry["name"]
        username = entry["username"]
        totp_uri = entry["totp"]  # totp URI 僅在此變數中存在，不會被印出

        # 顯示進度（不含任何 totp 資訊）
        progress_prefix = (
            f"[{Colors.CYAN}{i}/{total}{Colors.RESET}] "
            f"正在寫入：{Colors.BOLD}{name}{Colors.RESET} "
            f"{Colors.DIM}({username}){Colors.RESET}... "
        )
        print(progress_prefix, end="", flush=True)

        try:
            result = subprocess.run(
                # 將 totp_uri 作為獨立的清單元素傳入，
                # 避免 shell injection 的風險（不使用 shell=True）
                ["ykman", "oath", "accounts", "uri", totp_uri],
                capture_output=True,  # 捕獲 stdout/stderr，避免 totp 洩漏到終端機
                text=True,
                timeout=timeout       # 防呆：超時視為失敗
            )

            if result.returncode == 0:
                cprint(Colors.GREEN, "✓ 成功")
                success_count += 1
            else:
                cprint(Colors.RED, "✗ 失敗")
                fail_count += 1
                failed_names.append(name)
                # 僅在有 stderr 時才印出除錯資訊（totp 不會出現在 stderr 中）
                if result.stderr.strip():
                    cprint(Colors.DIM, f"    ↳ 錯誤訊息：{result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            cprint(Colors.RED, f"✗ 逾時（{timeout} 秒）")
            cprint(Colors.YELLOW,
                   f"    ↳ 提示：若 YubiKey 設有「需要觸碰確認」，"
                   f"請觸碰 YubiKey 上的感應區，或以 --timeout 增加等待時間。")
            fail_count += 1
            failed_names.append(name)

        except Exception as e:
            # 捕獲其他非預期錯誤，同樣不洩漏 totp 資訊
            cprint(Colors.RED, f"✗ 非預期錯誤")
            cprint(Colors.DIM, f"    ↳ {type(e).__name__}: {e}")
            fail_count += 1
            failed_names.append(name)

        finally:
            # 無論成功與否，都等待指定秒數再繼續
            # 避免連續寫入過快，使 YubiKey 或 ykman 來不及反應
            if i < total:  # 最後一筆不需要等待
                time.sleep(delay)

    # 匯入完成：顯示摘要
    print()
    separator("═")
    cprint(Colors.BOLD + Colors.MAGENTA, "  匯入結果摘要")
    separator("═")
    cprint(Colors.GREEN,  f"  成功：{success_count} 筆")
    if fail_count > 0:
        cprint(Colors.RED, f"  失敗：{fail_count} 筆")
        cprint(Colors.YELLOW, "  失敗的項目（僅顯示名稱）：")
        for name in failed_names:
            cprint(Colors.DIM, f"    - {name}")
    separator("═")


# ────────────────────────────────────────────────
# 步驟五：安全清理警告
# ────────────────────────────────────────────────
def print_cleanup_warning(filepath: str) -> None:
    """
    在腳本執行完畢後，顯示強烈的安全清理警告，
    提醒使用者徹底刪除包含明文 TOTP 種子的來源 JSON 檔案。
    """
    print()
    separator("!", width=60)
    cprint(Colors.BOLD + Colors.RED,
           "  ⚠️  重要安全警告：請立即刪除來源 JSON 檔案！  ⚠️")
    separator("!", width=60)
    print(f"""
  來源檔案：{Colors.BOLD}{filepath}{Colors.RESET}

  {Colors.YELLOW}該檔案包含所有 TOTP 的明文 Secret（種子），
  一旦洩漏，攻擊者可完全複製您的雙因素驗證器！

  {Colors.BOLD}請使用安全抹除工具，而非普通刪除（rm / del）：{Colors.RESET}

  {Colors.GREEN}Linux / WSL：{Colors.RESET}
    shred -vuz "{filepath}"
    （-v: 顯示進度, -u: 刪除檔案, -z: 最後覆寫為零）

  {Colors.GREEN}Windows：{Colors.RESET}
    1. 使用 SDelete（Sysinternals）：
       sdelete -p 3 "{filepath}"
    2. 或使用 Eraser（https://eraser.heidi.ie/）

  {Colors.RED}使用 rm 或 del 只會移除目錄項目，資料仍殘留在磁碟上！{Colors.RESET}
""")
    separator("!", width=60)


# ────────────────────────────────────────────────
# 主程式進入點
# ────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    """
    使用 argparse 解析命令列參數。

    Returns:
        解析完成的 Namespace 物件，包含 file、timeout、delay 屬性。
    """
    parser = argparse.ArgumentParser(
        description="將密碼管理員匯出的 JSON 檔案中的 TOTP 項目批次寫入 YubiKey。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用範例：
  python import_totp_to_yubikey.py --file export.json
  python import_totp_to_yubikey.py --file export.json --timeout 30 --delay 1.0
        """
    )

    parser.add_argument(
        "--file", "-f",
        required=True,
        metavar="<路徑>",
        help="包含 TOTP 資料的 JSON 檔案路徑（必填）"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=15,
        metavar="<秒>",
        help="每筆 ykman 指令的最長等待秒數（預設：15）。"
             "若 YubiKey 設有觸碰確認，請調高此值（如 30）。"
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=0.5,
        metavar="<秒>",
        help="每次寫入後的等待秒數（預設：0.5）。"
             "避免連續寫入過快。"
    )

    return parser.parse_args()


def main() -> None:
    """腳本主流程。"""
    args = parse_args()

    cprint(Colors.BOLD + Colors.MAGENTA,
           "\n╔══════════════════════════════════════╗")
    cprint(Colors.BOLD + Colors.MAGENTA,
           "║   YubiKey TOTP 批次匯入工具 v1.0     ║")
    cprint(Colors.BOLD + Colors.MAGENTA,
           "╚══════════════════════════════════════╝")

    # 步驟一：確認 YubiKey 已連接且 ykman 可用
    check_yubikey()

    # 步驟二：讀取並驗證 JSON 資料
    entries = load_totp_entries(args.file)

    # 步驟三：預覽清單並等待使用者確認（不輸出任何 totp 內容）
    preview_and_confirm(entries)

    # 步驟四：批次執行 ykman 寫入
    import_entries(entries, timeout=args.timeout, delay=args.delay)

    # 步驟五：顯示安全清理警告
    print_cleanup_warning(args.file)


if __name__ == "__main__":
    main()
