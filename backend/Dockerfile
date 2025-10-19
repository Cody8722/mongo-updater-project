#Dockerfile
#--- 步驟 1: 選擇基礎環境 ---
#我們選擇一個官方的、輕量級的 Python 3.11 環境作為基礎
FROM python:3.11-slim

#--- 步驟 2: 設定工作目錄 ---
#在容器內建立一個叫做 /app 的資料夾，並且進入它
WORKDIR /app

#--- 步驟 3: 安裝必要的工具 (最關鍵的一步) ---
#先只複製 requirements.txt 這個檔案進來
COPY requirements.txt .

#執行安裝指令。這些安裝好的函式庫會被「烤」進這個映像檔裡，
#以後每次部署都不需要重新安裝了！
RUN pip install --no-cache-dir -r requirements.txt

#--- 步驟 4: 複製你的應用程式程式碼 ---
#將 backend 資料夾裡的其他所有檔案都複製進來
COPY . .

#--- 步驟 5: 設定啟動指令 ---
#這行會取代 Procfile 的功能，告訴容器啟動時要執行什麼指令
#--bind 0.0.0.0:8080 讓容器可以從外部被存取
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8080"]
