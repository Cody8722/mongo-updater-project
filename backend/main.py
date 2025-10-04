import os
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from flask_cors import CORS
from bson import json_util
import json
from datetime import datetime, timedelta # ⭐ 新增 timedelta

# --- 初始化 ---
load_dotenv()
app = Flask(__name__)
CORS(app)

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
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ 成功連線到 MongoDB!")

    db = client.scheduleApp 
    holidays_collection = db.holidays

    compressor_db = client['compressor_db']
    tasks_collection = compressor_db['tasks']
    
    print("✅ 已連接到壓縮工具資料庫")

except Exception as e:
    print(f"❌ 無法連線到 MongoDB: {e}")

# --- API 路由 ---
@app.route('/status', methods=['GET'])
def get_status():
    if client and db and tasks_collection is not None:
        try:
            client.admin.command('ping')
            task_count = tasks_collection.count_documents({})
            return jsonify({
                "status": "ok", 
                "db_status": "connected",
                "compressor_tasks_count": task_count
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "db_status": "disconnected"}), 500

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

@app.route('/admin/api/decompression-logs', methods=['GET'])
def get_decompression_logs():
    secret = request.args.get('secret')
    if not secret or secret != ADMIN_SECRET: return jsonify({"error": "管理員密碼錯誤"}), 403
    if tasks_collection is None: return jsonify({"error": "壓縮工具資料庫未連線"}), 500
    try:
        pipeline = [
            {'$match': {'type': 'decompress', 'status': '完成', 'ip_address': {'$exists': True}}},
            {'$sort': {'created_at': -1}},
            {'$group': {
                '_id': '$ip_address', 'count': {'$sum': 1},
                'files': {'$push': {
                    'filename': '$result_filename',
                    'original_filename': '$params.expected_filename',
                    'timestamp': '$created_at'
                }},
                'last_activity': {'$first': '$created_at'}
            }},
            {'$sort': {'last_activity': -1}},
            {'$project': {'_id': 0, 'ip_address': '$_id', 'count': 1, 'files': 1, 'last_activity': 1}}
        ]
        logs = list(tasks_collection.aggregate(pipeline))
        return json.loads(json_util.dumps(logs)), 200
    except Exception as e:
        return jsonify({"error": f"伺服器內部發生錯誤: {e}"}), 500

@app.route('/admin/api/compression-stats', methods=['GET'])
def get_compression_stats():
    secret = request.args.get('secret')
    if not secret or secret != ADMIN_SECRET: return jsonify({"error": "未授權"}), 403
    try:
        total_tasks = tasks_collection.count_documents({})
        completed_tasks = tasks_collection.count_documents({'status': '完成'})
        failed_tasks = tasks_collection.count_documents({'status': '失敗'})
        
        fs_files = compressor_db['fs.files']
        total_storage = fs_files.aggregate([{'$group': {'_id': None, 'total': {'$sum': '$length'}}}])
        storage_used = list(total_storage)
        storage_bytes = storage_used[0]['total'] if storage_used else 0
        
        return jsonify({
            'total_tasks': total_tasks, 'completed_tasks': completed_tasks, 'failed_tasks': failed_tasks,
            'success_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 2),
            'storage_used_bytes': storage_bytes,
            'storage_used_mb': round(storage_bytes / (1024 * 1024), 2)
        }), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

# ⭐ 新增：即時任務狀態 API
@app.route('/admin/api/active-tasks', methods=['GET'])
def get_active_tasks():
    """取得目前正在執行的壓縮/解壓縮任務狀態"""
    secret = request.args.get('secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403
    
    try:
        # 查詢最近 10 分鐘內的任務
        recent_time = datetime.now() - timedelta(minutes=10)
        active_tasks = tasks_collection.find({
            'created_at': {'$gte': recent_time},
            'status': {'$in': ['處理中', '等待中', '完成', '失敗']} # 也包含剛完成或失敗的
        }).sort('created_at', -1).limit(20) # 最多顯示 20 筆
        
        return json.loads(json_util.dumps(list(active_tasks))), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 本地開發專用 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

