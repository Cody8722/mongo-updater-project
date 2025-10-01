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
MONGO_URI = os.getenv('MONGO_URI')
DATABASE_NAME = "scheduleApp"
COLLECTION_NAME = "holidays"

# 建立 MongoDB 客戶端
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ 成功連接到 MongoDB！")
except ConnectionFailure as e:
    print(f"❌ 無法連接到 MongoDB，請檢查 MONGO_URI 環境變數。錯誤: {e}")
    client = None

# --- API 端點 (Endpoint) ---

# 【新功能】 取得特定月份的所有假日資料
@app.route('/api/get_holidays', methods=['GET'])
def get_holidays():
    if not client:
        return jsonify({"status": "error", "message": "資料庫連線失敗"}), 500

    # 1. 從前端的請求中取得 year 和 month 參數
    year = request.args.get('year')
    month = request.args.get('month')

    if not year or not month:
        return jsonify({"status": "error", "message": "缺少 'year' 或 'month' 參數"}), 400

    try:
        # 2. 建立查詢樣式，例如：找出所有 _id 以 "202510" 開頭的文件
        query_pattern = f"^{year}{str(month).zfill(2)}"
        
        db = client[DATABASE_NAME]
        collection = db[COLLECTION_NAME]

        # 3. 執行查詢
        holidays_cursor = collection.find({"_id": {"$regex": query_pattern}})
        
        # 4. 將查詢結果轉換成列表格式回傳給前端
        holidays_list = list(holidays_cursor)
        
        return jsonify({"status": "success", "data": holidays_list})

    except Exception as e:
        return jsonify({"status": "error", "message": f"發生未知錯誤: {e}"}), 500

# 更新或建立一筆假日資料
@app.route('/api/update_holiday', methods=['POST'])
def update_holiday():
    if not client:
        return jsonify({"status": "error", "message": "資料庫連線失敗"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "請求中沒有資料"}), 400

    holiday_id = data.get('id')
    update_payload = data.get('payload')

    if not holiday_id or not update_payload:
        return jsonify({"status": "error", "message": "缺少 'id' 或 'payload'"}), 400

    try:
        db = client[DATABASE_NAME]
        collection = db[COLLECTION_NAME]
        query_filter = {"_id": holiday_id}
        
        # 使用 upsert=True，如果找不到資料就新增一筆
        result = collection.update_one(query_filter, update_payload, upsert=True)
        
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)

