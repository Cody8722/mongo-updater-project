import os
from flask import Flask, request, jsonify, redirect
from pymongo import MongoClient
from dotenv import load_dotenv
from flask_cors import CORS
from bson import json_util
import json
from datetime import datetime, timedelta
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- 初始化 ---
load_dotenv()
app = Flask(__name__)

# ✅ 安全性微調: 移除 "null" 來源，加入更精確的開發來源
CORS(app, origins=[
    "https://mongo-updater-project.zeabur.app", # ⚠️ 部署前請務必換成您真實的前端網址
    "http://localhost:3000",
    "http://127.0.0.1:3000"
])

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"]
    # 說明: Flask-Limiter 預設使用記憶體儲存，對於管理後台已足夠。
    # 若需跨容器/重啟的持續性限流，可考慮設定 storage_uri="redis://..."
)


# --- 資料庫連線 ---
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_SECRET = os.getenv('ADMIN_SECRET')
client = None
db = None
compressor_db = None
holidays_collection = None
tasks_collection = None

try:
    if not MONGO_URI:
        raise ValueError("錯誤:找不到 MONGO_URI 環境變數。")
    
    client = MongoClient(
        MONGO_URI, 
        serverSelectionTimeoutMS=5000,
        maxPoolSize=10,
        minPoolSize=2,
        maxIdleTimeMS=45000
    )
    client.admin.command('ping')
    print("✅ 成功連線到 MongoDB!")

    db = client.scheduleApp 
    holidays_collection = db.holidays

    compressor_db = client['compressor_db']
    tasks_collection = compressor_db['tasks']
    
    print("✅ 已連接到壓縮工具資料庫")

except Exception as e:
    print(f"❌ 無法連線到 MongoDB: {e}")

# ✅ 安全性建議: 強制 HTTPS (在支援 X-Forwarded-Proto 的反向代理後方)
@app.before_request
def force_https():
    # 當不在偵錯模式，且請求不是安全或 X-Forwarded-Proto header 不是 https 時
    if not app.debug and not request.is_secure and request.headers.get('x-forwarded-proto', 'http') != 'https':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)

# --- API 路由 ---
@app.route('/status', methods=['GET'])
def get_status():
    task_count = 0
    try:
        if client:
            client.admin.command('ping')
            if tasks_collection is not None:
                task_count = tasks_collection.count_documents({})
            
            return jsonify({
                "status": "ok", 
                "db_status": "connected",
                "compressor_tasks_count": task_count
            }), 200
        else:
            raise Exception("MongoDB client is not initialized.")
    except Exception as e:
        return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500

@app.route('/get_holidays', methods=['GET'])
def get_holidays():
    if holidays_collection is None: return jsonify({"error": "資料庫集合未初始化"}), 500
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
    if holidays_collection is None: return jsonify({"error": "資料庫集合未初始化"}), 500
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

@app.route('/admin/api/compression-stats', methods=['GET'])
@limiter.limit("20 per minute")
def get_compression_stats():
    secret = request.headers.get('X-Admin-Secret')
    if not secret or secret != ADMIN_SECRET: return jsonify({"error": "未授權"}), 403
    try:
        total_tasks = tasks_collection.count_documents({})
        completed_tasks = tasks_collection.count_documents({'status': '完成'})
        failed_tasks = tasks_collection.count_documents({'status': '失敗'})
        
        fs_files = compressor_db['fs.files']
        storage_agg = list(fs_files.aggregate([{'$group': {'_id': None, 'total': {'$sum': '$length'}}}]))
        storage_bytes = storage_agg[0]['total'] if storage_agg else 0
        
        return jsonify({
            'total_tasks': total_tasks, 'completed_tasks': completed_tasks, 'failed_tasks': failed_tasks,
            'success_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 2),
            'storage_used_bytes': storage_bytes,
            'storage_used_mb': round(storage_bytes / (1024 * 1024), 2)
        }), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/admin/api/active-tasks', methods=['GET'])
@limiter.limit("20 per minute")
def get_active_tasks():
    secret = request.headers.get('X-Admin-Secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403
    
    try:
        recent_time = datetime.now() - timedelta(minutes=10)
        active_tasks = tasks_collection.find({
            'created_at': {'$gte': recent_time},
            'status': {'$in': ['處理中', '等待中', '完成', '失敗']}
        }).sort('created_at', -1).limit(20)
        
        return json.loads(json_util.dumps(list(active_tasks))), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 本地開發專用 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

