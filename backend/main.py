import os
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from flask_cors import CORS
from bson import json_util
import json
from datetime import datetime

# --- 初始化 ---
load_dotenv()
app = Flask(__name__)
CORS(app)

# --- 資料庫連線 ---
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_SECRET = os.getenv('ADMIN_SECRET') # 讀取管理員密碼
client = None
db = None
holidays_collection = None
decompression_logs_collection = None # 新增日誌集合

try:
    if not MONGO_URI:
        raise ValueError("錯誤：找不到 MONGO_URI 環境變數。")
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("成功連線到 MongoDB！")
    # 共用同一個資料庫實例
    db = client.scheduleApp 
    # 初始化各個 Collection
    holidays_collection = db.holidays
    decompression_logs_collection = db.decompression_logs # 假設日誌存在這裡

except Exception as e:
    print(f"無法連線到 MongoDB: {e}")


# --- API 路由 (Endpoints) ---

# ... 原本的 /status, /get_holidays, /update_holiday 路由保持不變 ...
@app.route('/status', methods=['GET'])
def get_status():
    """ 檢查與資料庫的連線狀態。 """
    if client is not None and db is not None:
        try:
            client.admin.command('ping')
            return jsonify({"status": "ok", "db_status": "connected"}), 200
        except Exception as e:
            return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "db_status": "disconnected", "message": "MongoDB client is not initialized."}), 500


@app.route('/get_holidays', methods=['GET'])
def get_holidays():
    """ 根據年份和月份獲取假日資料。 """
    if holidays_collection is None:
        return jsonify({"error": "資料庫集合未初始化"}), 500

    try:
        year = request.args.get('year')
        month = request.args.get('month')
        if not year or not month:
            return jsonify({"error": "缺少年份或月份參數"}), 400
        
        query_pattern = f"^{year}{str(month).zfill(2)}"
        cursor = holidays_collection.find({"_id": {"$regex": query_pattern}})
        holidays_list = list(cursor)
        return json.loads(json_util.dumps(holidays_list)), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update_holiday', methods=['POST'])
def update_holiday():
    """ 更新或插入一筆假日資料。 """
    if holidays_collection is None:
        return jsonify({"error": "資料庫集合未初始化"}), 500

    try:
        data = request.get_json()
        if not data or '_id' not in data:
            return jsonify({"error": "無效的請求資料，缺少 _id"}), 400

        doc_id = data['_id']
        update_data = {k: v for k, v in data.items() if k != '_id'}

        result = holidays_collection.update_one(
            {"_id": doc_id},
            {"$set": update_data},
            upsert=True
        )

        if result.upserted_id or result.modified_count > 0:
            return jsonify({"message": "資料已成功儲存"}), 200
        else:
            return jsonify({"message": "資料無變動"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 新增的管理員 API ---
@app.route('/admin/api/decompression-logs', methods=['GET'])
def get_decompression_logs():
    """
    根據 API 文件規格，提供受密碼保護的解壓縮日誌查詢功能。
    """
    # 1. 認證 (Authentication)
    secret = request.args.get('secret')
    if not secret:
        return jsonify({"error": "缺少管理員密碼"}), 401
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        return jsonify({"error": "管理員密碼錯誤"}), 403
    
    # 2. 查詢與資料處理
    if decompression_logs_collection is None:
        return jsonify({"error": "伺服器內部發生錯誤，請稍後再試。"}), 500

    try:
        # 使用 MongoDB Aggregation Pipeline 來處理資料
        pipeline = [
            # 步驟 1: 依時間倒序排序，方便後續取最新活動
            { '$sort': { 'timestamp': -1 } },
            # 步驟 2: 依 IP 位址分組
            {
                '$group': {
                    '_id': '$ip_address',
                    'last_activity': { '$first': '$timestamp' }, # 排序後的第一筆即是最新活動
                    'count': { '$sum': 1 },
                    'files': {
                        '$push': {
                            'filename': '$filename',
                            'original_filename': '$original_filename',
                            'timestamp': '$timestamp'
                        }
                    }
                }
            },
            # 步驟 3: 整理輸出格式
            {
                '$project': {
                    '_id': 0,
                    'ip_address': '$_id',
                    'last_activity': 1,
                    'count': 1,
                    'files': 1
                }
            },
            # 步驟 4: 最終再依最新活動時間排序一次
            { '$sort': { 'last_activity': -1 } }
        ]
        
        logs = list(decompression_logs_collection.aggregate(pipeline))
        
        # 3. 成功回應
        return json.loads(json_util.dumps(logs)), 200
        
    except Exception as e:
        print(f"管理員日誌查詢錯誤: {e}")
        return jsonify({"error": "伺服器內部發生錯誤，請稍後再試。"}), 500


# --- 本地開發專用啟動區 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

