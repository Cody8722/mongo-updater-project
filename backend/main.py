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
compressor_tasks_collection = None # 新增壓縮工具的集合

try:
    if not MONGO_URI:
        raise ValueError("錯誤：找不到 MONGO_URI 環境變數。")
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("成功連線到 MongoDB！")
    
    # 連接到不同的資料庫集合
    db = client.scheduleApp
    holidays_collection = db.holidays
    compressor_tasks_collection = db.compressor_tasks # 初始化壓縮工具集合

except Exception as e:
    print(f"無法連線到 MongoDB: {e}")

# --- 輔助函式 ---
def admin_required(f):
    """ 一個裝飾器，用來驗證管理員密碼 """
    def decorated_function(*args, **kwargs):
        secret = request.args.get('secret')
        if not secret:
            return jsonify({"error": "缺少管理員密碼"}), 401
        if secret != ADMIN_SECRET:
            return jsonify({"error": "管理員密碼錯誤"}), 403
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# --- API 路由 ---

@app.route('/status', methods=['GET'])
def get_status():
    """ 檢查連線狀態，並回傳壓縮任務總數 """
    if client is not None and db is not None:
        try:
            client.admin.command('ping')
            task_count = 0
            # ⭐ 更新：如果集合存在，就計算文件總數
            if compressor_tasks_collection is not None:
                task_count = compressor_tasks_collection.count_documents({})
            return jsonify({"status": "ok", "db_status": "connected", "compressor_tasks_count": task_count}), 200
        except Exception as e:
            return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "db_status": "disconnected"}), 500

# ... (日曆相關的 API /get_holidays, /update_holiday 保持不變) ...
@app.route('/get_holidays', methods=['GET'])
def get_holidays():
    if holidays_collection is None:
        return jsonify({"error": "資料庫集合未初始化"}), 500
    try:
        year = request.args.get('year')
        month = request.args.get('month')
        if not year or not month: return jsonify({"error": "缺少年份或月份參數"}), 400
        query_pattern = f"^{year}{str(month).zfill(2)}"
        cursor = holidays_collection.find({"_id": {"$regex": query_pattern}})
        return json.loads(json_util.dumps(list(cursor))), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/update_holiday', methods=['POST'])
def update_holiday():
    if holidays_collection is None:
        return jsonify({"error": "資料庫集合未初始化"}), 500
    try:
        data = request.get_json()
        if not data or '_id' not in data: return jsonify({"error": "無效的請求資料"}), 400
        doc_id = data['_id']
        update_data = {k: v for k, v in data.items() if k != '_id'}
        result = holidays_collection.update_one({"_id": doc_id}, {"$set": update_data}, upsert=True)
        if result.upserted_id or result.modified_count > 0:
            return jsonify({"message": "資料已成功儲存"}), 200
        return jsonify({"message": "資料無變動"}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

# --- 管理員專用 API ---

@app.route('/admin/api/decompression-logs', methods=['GET'])
@admin_required
def get_decompression_logs():
    """ 獲取解壓縮日誌 """
    if compressor_tasks_collection is None:
        return jsonify({"error": "壓縮工具資料庫集合未初始化"}), 500
    try:
        pipeline = [
            {"$sort": {"timestamp": -1}},
            {"$group": {
                "_id": "$ip_address",
                "count": {"$sum": 1},
                "last_activity": {"$first": "$timestamp"},
                "files": {"$push": {
                    "filename": "$filename",
                    "original_filename": "$original_filename",
                    "timestamp": "$timestamp"
                }}
            }},
            {"$project": {
                "_id": 0, "ip_address": "$_id", "count": 1, 
                "last_activity": 1, "files": 1
            }},
            {"$sort": {"last_activity": -1}}
        ]
        logs = list(compressor_tasks_collection.aggregate(pipeline))
        return json.loads(json_util.dumps(logs)), 200
    except Exception as e:
        return jsonify({"error": f"伺服器內部發生錯誤: {e}"}), 500

# ⭐ 新增：全新的壓縮工具統計 API
@app.route('/admin/api/compression-stats', methods=['GET'])
@admin_required
def get_compression_stats():
    """ 獲取壓縮工具的整體使用統計數據 """
    if compressor_tasks_collection is None:
        return jsonify({"error": "壓縮工具資料庫集合未初始化"}), 500
    try:
        # 1. 取得儲存空間使用量
        stats = db.command("collstats", "compressor_tasks")
        storage_bytes = stats.get("size", 0)

        # 2. 進行聚合查詢來計算任務狀態
        pipeline = [
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1}
            }}
        ]
        status_counts = list(compressor_tasks_collection.aggregate(pipeline))
        
        # 3. 整理數據
        completed_tasks = 0
        failed_tasks = 0
        for item in status_counts:
            if item['_id'] == 'completed':
                completed_tasks = item['count']
            elif item['_id'] == 'failed':
                failed_tasks = item['count']
        
        total_tasks = completed_tasks + failed_tasks
        success_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

        # 4. 組合回傳結果
        result = {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "success_rate": round(success_rate, 2),
            "storage_used_bytes": storage_bytes,
            "storage_used_mb": round(storage_bytes / (1024 * 1024), 2)
        }
        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": f"伺服器內部發生錯誤: {e}"}), 500

# --- 本地開發專用 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

