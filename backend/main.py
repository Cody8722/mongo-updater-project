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
ADMIN_SECRET = os.getenv('ADMIN_SECRET')
client = None
db = None
compressor_db = None  # 新增:壓縮工具的資料庫
holidays_collection = None
tasks_collection = None  # 新增:壓縮工具的任務集合

try:
    if not MONGO_URI:
        raise ValueError("錯誤:找不到 MONGO_URI 環境變數。")
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print("✅ 成功連線到 MongoDB!")

    # 行事曆資料庫
    db = client.scheduleApp 
    holidays_collection = db.holidays

    # 壓縮工具資料庫(與 app.py 共用)
    compressor_db = client['compressor_db']
    tasks_collection = compressor_db['tasks']
    
    print("✅ 已連接到壓縮工具資料庫")

except Exception as e:
    print(f"❌ 無法連線到 MongoDB: {e}")

# --- API 路由 ---
@app.route('/status', methods=['GET'])
def get_status():
    """檢查與資料庫的連線狀態。"""
    if client is not None and db is not None:
        try:
            client.admin.command('ping')
            # 額外檢查壓縮工具資料庫
            task_count = tasks_collection.count_documents({})
            return jsonify({
                "status": "ok", 
                "db_status": "connected",
                "compressor_tasks_count": task_count
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "db_status": "disconnected", "message": "MongoDB client is not initialized."}), 500

@app.route('/get_holidays', methods=['GET'])
def get_holidays():
    """根據年份和月份獲取假日資料。"""
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
    """更新或插入一筆假日資料。"""
    if holidays_collection is None:
        return jsonify({"error": "資料庫集合未初始化"}), 500
    try:
        data = request.get_json()
        if not data or '_id' not in data:
            return jsonify({"error": "無效的請求資料,缺少 _id"}), 400
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

@app.route('/admin/api/decompression-logs', methods=['GET'])
def get_decompression_logs():
    """
    🆕 整合功能:從壓縮工具的資料庫讀取解壓縮日誌
    此 API 會查詢 compressor_db 中的 tasks collection
    """
    # 1. 認證
    secret = request.args.get('secret')
    if not secret:
        return jsonify({"error": "缺少管理員密碼"}), 401
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        return jsonify({"error": "管理員密碼錯誤"}), 403
    
    # 2. 查詢與資料處理
    if tasks_collection is None:
        return jsonify({"error": "壓縮工具資料庫未連線"}), 500
    try:
        # 使用 Aggregation Pipeline 分析解壓縮任務
        pipeline = [
            # 只查詢已完成的解壓縮任務
            {
                '$match': {
                    'type': 'decompress',
                    'status': '完成',
                    'ip_address': {'$exists': True}
                }
            },
            # 按時間排序
            {'$sort': {'created_at': -1}},
            # 按 IP 分組統計
            {
                '$group': {
                    '_id': '$ip_address',
                    'count': {'$sum': 1},
                    'files': {
                        '$push': {
                            'filename': '$result_filename',
                            'original_filename': '$params.expected_filename',
                            'timestamp': '$created_at'
                        }
                    },
                    'last_activity': {'$first': '$created_at'}
                }
            },
            # 最終排序
            {'$sort': {'last_activity': -1}},
            # 重新命名欄位
            {
                '$project': {
                    '_id': 0,
                    'ip_address': '$_id',
                    'count': 1,
                    'files': 1,
                    'last_activity': 1
                }
            }
        ]
        
        logs = list(tasks_collection.aggregate(pipeline))
        
        # 3. 成功回應
        return json.loads(json_util.dumps(logs)), 200
            
    except Exception as e:
        print(f"❌ 管理員日誌查詢錯誤: {e}")
        return jsonify({"error": f"伺服器內部發生錯誤: {str(e)}"}), 500

@app.route('/admin/api/compression-stats', methods=['GET'])
def get_compression_stats():
    """
    🆕 新增功能:取得壓縮工具的統計資訊
    包含總任務數、成功率、儲存空間使用等
    """
    secret = request.args.get('secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403
        
    try:
        total_tasks = tasks_collection.count_documents({})
        completed_tasks = tasks_collection.count_documents({'status': '完成'})
        failed_tasks = tasks_collection.count_documents({'status': '失敗'})
        
        # 計算儲存空間使用
        fs_files = compressor_db['fs.files']
        total_storage = fs_files.aggregate([
            {'$group': {'_id': None, 'total': {'$sum': '$length'}}}
        ])
        storage_used = list(total_storage)
        storage_bytes = storage_used[0]['total'] if storage_used else 0
        
        return jsonify({
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'failed_tasks': failed_tasks,
            'success_rate': round((completed_tasks / total_tasks * 100) if total_tasks > 0 else 0, 2),
            'storage_used_bytes': storage_bytes,
            'storage_used_mb': round(storage_bytes / (1024 * 1024), 2)
        }), 200
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 本地開發專用啟動區 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

