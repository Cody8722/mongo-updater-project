import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from dotenv import load_dotenv

# 載入 .env 檔案中的環境變數 (主要用於本地測試)
load_dotenv()

app = Flask(__name__)
# 允許所有來源的跨域請求，這樣前端才能呼叫後端
CORS(app)

# --- 從環境變數讀取資料庫連線資訊 ---
# 這是安全作法，密碼不會寫死在程式碼裡
# 在 Zeabur 上，我們會直接設定這個環境變數
MONGO_URI = os.getenv('MONGO_URI')
DATABASE_NAME = "scheduleApp"
COLLECTION_NAME = "holidays"

# 建立 MongoDB 客戶端
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # 檢查連線是否成功
    client.admin.command('ping')
    print("✅ 成功連接到 MongoDB！")
except ConnectionFailure as e:
    print(f"❌ 無法連接到 MongoDB，請檢查 MONGO_URI 環境變數。錯誤: {e}")
    client = None

# --- API 端點 (Endpoint) ---
# 這就是我們信差接收信件的地址
@app.route('/api/update_holiday', methods=['POST'])
def update_holiday():
    if not client:
        # 如果資料庫連線失敗，回傳錯誤訊息
        return jsonify({"status": "error", "message": "資料庫連線失敗"}), 500

    # 1. 從前端寄來的信件(request)中讀取 JSON 資料
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "請求中沒有資料"}), 400

    # 2. 取得要查詢的 _id 和要更新的內容
    holiday_id = data.get('id')
    update_payload = data.get('payload')

    if not holiday_id or not update_payload:
        return jsonify({"status": "error", "message": "缺少 'id' 或 'payload'"}), 400

    try:
        # 3. 執行資料庫操作
        db = client[DATABASE_NAME]
        collection = db[COLLECTION_NAME]

        query_filter = {"_id": holiday_id}
        
        # 使用 update_one 搭配 upsert=True
        # 如果找不到資料，就新增一筆
        result = collection.update_one(query_filter, update_payload, upsert=True)
        
        # 4. 根據結果回報給前端
        if result.upserted_id:
            message = f"成功新增資料，ID: {result.upserted_id}"
        elif result.modified_count > 0:
            message = "成功更新資料！"
        else:
            message = "找到資料，但內容無變化。"

        return jsonify({"status": "success", "message": message})

    except OperationFailure as e:
        return jsonify({"status": "error", "message": f"資料庫操作失敗: {e}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"發生未知錯誤: {e}"}), 500

# 讓 Flask 應用程式開始運行
if __name__ == '__main__':
    # Zeabur 會使用 Gunicorn 等伺服器，所以這裡主要是本地測試用
    # 預設會在 http://127.0.0.1:5000 啟動
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)
