# LINE Bot 會議記錄助理

把 LINE 語音、影片或影音檔轉成繁體中文會議記錄。這個專案沿用
`錄音轉文字` 專案的 ffmpeg 壓縮／切段概念，介面改成 LINE Messaging API，
並新增結構化摘要。

## 功能

- 接受 LINE 原生語音與影片訊息
- 接受 mp3、m4a、wav、flac、ogg、webm 等音訊檔，以及
  mp4、mov、mkv、avi、m4v、wmv、3gp、ts 等常見影片檔
- 驗證 `x-line-signature`，避免偽造 webhook
- webhook 先立即回覆，耗時工作放到背景執行
- 用 ffmpeg 直接擷取影片音軌，避免耗時且不必要的整支影片重編碼
- 將音軌轉成 16kHz 單聲道 MP3，每 10 分鐘切段後轉錄
- 產生重點摘要、決議、待辦事項、未解問題與完整逐字稿
- 支援一對一、群組與多人聊天室
- 完整結果另存到伺服器的 `records/` 目錄

## 架構

```text
LINE webhook
  → 驗證簽章
  → 立即回覆「處理中」
  → 下載影音
  → ffmpeg 擷取音軌、壓縮與切段
  → OpenAI 語音轉文字
  → OpenAI Responses API 整理會議記錄
  → LINE push message 傳回原聊天室
```

## 1. 建立 LINE Bot

1. 到 [LINE Official Account Manager](https://manager.line.biz/) 建立 LINE 官方帳號。
2. 在 Official Account Manager 啟用 Messaging API，選擇管理它的 Provider。
3. 到 [LINE Developers Console](https://developers.line.biz/console/) 開啟自動建立的
   Messaging API channel。
4. 在 Basic settings 與 Messaging API 頁面取得：
   - Channel secret
   - Channel access token
5. 關閉 LINE Official Account Manager 的自動回應與歡迎訊息，避免重複答覆。

## 2. 本機安裝

需求：Python 3.11、[uv](https://docs.astral.sh/uv/)、ffmpeg。

Windows 安裝 ffmpeg：

```powershell
winget install Gyan.FFmpeg
```

建立設定：

```powershell
Copy-Item .env.example .env
```

編輯 `.env`，填入三個必要密鑰：

```dotenv
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
OPENAI_API_KEY=sk-...
```

啟動：

```powershell
uv sync
uv run uvicorn linebot_meeting.app:app --reload --port 8000
```

確認服務：<http://127.0.0.1:8000/health>

## 3. 讓 LINE 連到本機

LINE webhook 必須是公開 HTTPS 網址。開發時可用 ngrok 或 Cloudflare Tunnel，
例如：

```powershell
ngrok http 8000
```

把產生的 HTTPS 網址加上 `/webhook`，填到 LINE Developers Console：

```text
https://你的網址.ngrok-free.app/webhook
```

按下 **Verify**，成功後開啟 **Use webhook**。

## 4. Docker／Render 部署

專案已含 `Dockerfile` 與 `render.yaml`。部署到 Render 時設定：

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `OPENAI_API_KEY`

`render.yaml` 已設定 Singapore 區域、Free instance 與 `/health` 健康檢查。
Free instance 閒置後會休眠，而且不保留 `records/` 內的檔案，適合測試但不建議
當正式服務；正式上線時請將 `plan` 改成 `starter` 或更高方案。

部署完成後，將 `https://你的服務.onrender.com/webhook` 設成 LINE webhook。

注意：免費或無持久磁碟的主機重啟後，`records/` 可能被清空。正式環境應把完整
會議記錄改存到資料庫或物件儲存；背景工作也建議改接工作佇列，避免部署或重啟時
中斷正在轉錄的會議。

## 設定值

| 環境變數 | 預設值 | 說明 |
|---|---:|---|
| `TRANSCRIBE_MODEL` | `gpt-4o-transcribe` | 語音辨識模型 |
| `SUMMARY_MODEL` | `gpt-5.6-luna` | 會議整理模型 |
| `TRANSCRIBE_PROMPT` | 內建繁中提示 | 可加入公司、人名與產品專有名詞 |
| `CHUNK_MINUTES` | `10` | 每個轉錄片段的最長分鐘數 |
| `MAX_SOURCE_MB` | `200` | 接收音訊的大小上限 |
| `MAX_TRANSCRIPT_MESSAGES` | `15` | 最多傳回幾段逐字稿；0 代表不傳逐字稿 |
| `RECORDS_DIR` | `records` | 伺服器端 Markdown 記錄目錄 |

## 測試與檢查

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

測試不會呼叫 LINE 或 OpenAI API，也不需要真實金鑰。

## 正式環境建議

目前版本適合 MVP 與小團隊試用。正式上線前建議補上：

- Redis／Celery、雲端工作佇列，取代程序內背景任務
- PostgreSQL 或物件儲存，保存逐字稿與會議記錄
- 依 LINE user／group ID 的使用權限與用量限制
- 隱私告知、保存期限、自動刪除與敏感資料政策
- OpenAI 與 LINE API 的用量監控及告警
