# 🔐 YubiKey TOTP 批次匯入工具

將 **Bitwarden** 密碼管理員匯出的 JSON 檔案中的 TOTP 2FA 金鑰，透過 `ykman` CLI 批次寫入 YubiKey。

> ⚠️ **此工具涉及高度敏感的 2FA Secret，請在安全的離線環境中執行。**

---

## ✨ 功能特色

- ✅ 相容兩種 Bitwarden 匯出格式（Authenticator App 匯出 & 完整密碼庫匯出）
- ✅ 自動偵測 JSON 格式，無須手動設定
- ✅ 執行前顯示預覽清單，並要求使用者二次確認
- ✅ **零明文輸出**：TOTP Secret 絕不出現在終端機畫面或 Log 中
- ✅ `subprocess` timeout 防呆，避免 YubiKey 觸碰確認卡死腳本
- ✅ 完成後顯示安全清理警告（`shred` / `sdelete`）
- ✅ 純 Python 標準函式庫，無須安裝第三方套件

---

## 🖥️ 環境需求

| 項目 | 需求 |
|------|------|
| Python | 3.9 以上（使用 `list[dict]` 型別標記） |
| ykman | [yubikey-manager](https://developers.yubico.com/yubikey-manager/) |
| 作業系統 | Windows、Linux、WSL (Ubuntu/Debian) |

### 安裝 yubikey-manager

```bash
# Ubuntu / Debian / WSL
sudo apt install yubikey-manager

# macOS
brew install ykman

# Windows
# 至官網下載安裝程式：https://developers.yubico.com/yubikey-manager/
```

---

## 📂 支援的輸入格式

### 格式 A：Bitwarden Authenticator App 匯出

```json
{
  "encrypted": false,
  "items": [
    {
      "name": "Google",
      "login": {
        "username": "user@gmail.com",
        "totp": "otpauth://totp/Google:user@gmail.com?secret=XXXX&issuer=Google"
      },
      "type": 1
    }
  ]
}
```

### 格式 B：Bitwarden 完整密碼庫匯出

```json
{
  "encrypted": false,
  "folders": [ ... ],
  "items": [
    {
      "name": "Binance",
      "login": {
        "username": "user@gmail.com",
        "password": "...",
        "totp": "otpauth://totp/Binance:user@gmail.com?secret=XXXX&issuer=Binance"
      }
    },
    {
      "name": "無 2FA 的帳號",
      "login": {
        "username": "user@gmail.com",
        "password": "..."
      }
    }
  ]
}
```

> 沒有 `totp` 欄位的項目會自動略過，**不會報錯**。

---

## 🚀 使用方式

```bash
# 基本用法
python import_totp_to_yubikey.py --file <JSON 檔案路徑>

# 完整參數
python import_totp_to_yubikey.py \
  --file bitwarden_export.json \
  --timeout 30 \
  --delay 1.0
```

### 參數說明

| 參數 | 縮寫 | 預設值 | 說明 |
|------|------|--------|------|
| `--file` | `-f` | （必填） | JSON 檔案路徑 |
| `--timeout` | `-t` | `15` 秒 | 每筆 `ykman` 指令的最長等待秒數。若 YubiKey 設有**觸碰確認 (Require Touch)**，請調高至 `30` 秒 |
| `--delay` | `-d` | `0.5` 秒 | 每筆寫入後的等待間隔，避免連續寫入過快 |

---

## 🔄 執行流程

```
啟動腳本
  │
  ▼
[步驟 1] ykman info — 確認 YubiKey 已插入且 ykman 可用
  │  失敗 → 顯示錯誤並終止
  ▼
[步驟 2] 讀取並解析 JSON
  │  自動偵測格式 A / B，過濾無 totp 的項目
  ▼
[步驟 3] 預覽清單（僅顯示 name + username，不顯示 Secret）
  │  詢問使用者確認 (y/n)，n → 安全退出
  ▼
[步驟 4] 批次執行 ykman oath accounts uri <totp_uri>
  │  顯示每筆進度：[1/N] 正在寫入... ✓ 成功 / ✗ 失敗
  ▼
[步驟 5] 顯示結果摘要 + 安全清理警告
```

---

## 🛡️ 安全設計

### 零明文輸出

TOTP URI（含 Secret）在整個執行流程中**絕不會出現在終端機畫面**：

- 預覽階段僅顯示 `name` 與 `username`
- `subprocess.run()` 使用 `capture_output=True` 靜默所有輸出
- 僅在 `stderr` 有內容（即發生錯誤）時才顯示除錯訊息，且 `stderr` 不含 Secret

### 防止 Shell Injection

```python
# ✅ 正確：list 方式傳參，不使用 shell=True
subprocess.run(["ykman", "oath", "accounts", "uri", totp_uri], ...)

# ❌ 危險：字串拼接 + shell=True
subprocess.run(f'ykman oath accounts uri "{totp_uri}"', shell=True)
```

### Timeout 防呆

每筆指令設有 timeout 限制（預設 15 秒），避免因 YubiKey 等待觸碰確認而卡死腳本。

---

## 🧹 執行後：安全刪除來源檔案

來源 JSON 包含所有 TOTP 的明文 Secret，**必須使用安全抹除工具**，而非普通刪除。

```bash
# Linux / WSL（推薦）
shred -vuz bitwarden_export.json
# -v 顯示進度  -u 刪除檔案  -z 最後覆寫為零

# Windows — 使用 SDelete（Microsoft Sysinternals）
sdelete -p 3 bitwarden_export.json

# Windows — 或使用 Eraser（GUI 工具）
# https://eraser.heidi.ie/
```

> ⚠️ `rm` / `del` 只移除目錄項目，資料仍殘留在磁碟上，可被復原工具讀取。

---

## 📁 專案結構

```
.
├── import_totp_to_yubikey.py   # 主腳本
├── get_sample.py               # 工具：將匯出 JSON 去識別化以產生結構樣本
└── README.md
```

### get_sample.py 用途

用於將真實匯出檔去識別化（遞迴將所有 Value 替換為佔位符），產生可安全分享的結構樣本，方便除錯與提交 Issue。

```bash
python get_sample.py
# 輸入：bitwarden_export.json（真實檔案）
# 輸出：masked_sample.json（去識別化結構樣本）
```

---

## ❓ 常見問題

**Q：YubiKey 的 OATH Accounts 儲存上限是多少？**
A：YubiKey 5 系列最多可儲存 **64 組** TOTP/HOTP 帳號。

**Q：出現 `[✗] 未偵測到 YubiKey` 怎麼辦？**
A：
- 確認 YubiKey 已插入 USB 埠
- Linux/WSL 使用者請確認 udev 規則已設定：`sudo apt install yubikey-manager` 通常會一併處理
- 嘗試以管理員/sudo 身份執行

**Q：想匯入的項目已存在於 YubiKey 中會怎樣？**
A：`ykman` 會回傳錯誤，腳本會標記為失敗並繼續處理下一筆，不會強制覆蓋。若需覆蓋，請手動執行 `ykman oath accounts delete <name>` 後再重新匯入。

**Q：為何不使用 Python 的 YubiKey 函式庫（如 `yubikey-manager` Python API）？**
A：為了降低依賴複雜度，以及確保與系統已安裝的 `ykman` 版本保持一致，本工具選擇透過 `subprocess` 呼叫 CLI。

---

## 📄 授權

MIT License
