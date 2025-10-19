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
import requests
import time

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
    default_limits=[]  # 暫時停用全局限流
    # 說明: Flask-Limiter 預設使用記憶體儲存，對於管理後台已足夠。
    # 若需跨容器/重啟的持續性限流，可考慮設定 storage_uri="redis://..."
)


# --- 資料庫連線 ---
MONGO_URI = os.getenv('MONGO_URI')
ADMIN_SECRET = os.getenv('ADMIN_SECRET')

# --- 服務 URL 配置 ---
COMPRESSOR_URL = os.getenv('COMPRESSOR_URL', 'http://localhost:5000')
SCHEDULE_URL = os.getenv('SCHEDULE_URL', 'http://localhost:3000')

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
@limiter.exempt
def get_status():
    task_count = 0
    try:
        if client is not None:
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
@limiter.limit("100 per minute")
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
@limiter.limit("50 per minute")
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
    if tasks_collection is None or compressor_db is None: return jsonify({"error": "資料庫未初始化"}), 500
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
    if tasks_collection is None:
        return jsonify({"error": "資料庫未初始化"}), 500

    try:
        recent_time = datetime.now() - timedelta(minutes=10)
        active_tasks = tasks_collection.find({
            'created_at': {'$gte': recent_time},
            'status': {'$in': ['處理中', '等待中', '完成', '失敗']}
        }).sort('created_at', -1).limit(20)

        return json.loads(json_util.dumps(list(active_tasks))), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/api/all-files', methods=['GET'])
@limiter.limit("20 per minute")
def get_all_files():
    secret = request.headers.get('X-Admin-Secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403
    if tasks_collection is None or compressor_db is None:
        return jsonify({"error": "資料庫未初始化"}), 500

    try:
        # 獲取查詢參數
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        skip = (page - 1) * limit

        # 計算總數
        total_count = tasks_collection.count_documents({'type': 'compress', 'status': '完成'})

        # 獲取壓縮任務列表（只取完成的，因為只有完成的才有檔案）
        tasks = tasks_collection.find({
            'type': 'compress',
            'status': '完成',
            'result_file_id': {'$exists': True}
        }).sort('created_at', -1).skip(skip).limit(limit)

        files = []
        fs_files = compressor_db['fs.files']

        for task in tasks:
            # 獲取檔案大小
            file_info = fs_files.find_one({'_id': task.get('result_file_id')})
            files.append({
                '_id': str(task['_id']),
                'filename': task.get('result_filename', '未知'),
                'original_filename': task.get('params', {}).get('raw_filename', '未知'),
                'created_at': task.get('created_at'),
                'file_size': file_info.get('length', 0) if file_info else 0,
                'ip_address': task.get('ip_address', '未知')
            })

        return jsonify({
            'files': files,
            'total': total_count,
            'page': page,
            'limit': limit,
            'total_pages': (total_count + limit - 1) // limit
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/api/batch-delete', methods=['POST'])
@limiter.limit("5 per minute")
def admin_batch_delete():
    secret = request.headers.get('X-Admin-Secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403
    if tasks_collection is None or compressor_db is None:
        return jsonify({"error": "資料庫未初始化"}), 500

    try:
        data = request.get_json()
        task_ids = data.get('task_ids', [])

        if not task_ids:
            return jsonify({"error": "未提供任務 ID"}), 400

        # 轉換字串 ID 為 ObjectId
        from bson.objectid import ObjectId
        object_ids = [ObjectId(tid) for tid in task_ids]

        # 找到所有對應的任務
        tasks = tasks_collection.find({'_id': {'$in': object_ids}})

        deleted_count = 0
        fs_files = compressor_db['fs.files']
        fs_chunks = compressor_db['fs.chunks']

        for task in tasks:
            if task.get('result_file_id'):
                # 刪除 GridFS 檔案
                fs_files.delete_one({'_id': task['result_file_id']})
                fs_chunks.delete_many({'files_id': task['result_file_id']})

            # 刪除任務記錄
            tasks_collection.delete_one({'_id': task['_id']})
            deleted_count += 1

        return jsonify({
            "success": True,
            "deleted_count": deleted_count
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/admin/api/system-health', methods=['GET'])
@limiter.limit("300 per minute")
def get_system_health():
    secret = request.headers.get('X-Admin-Secret')
    if not secret or secret != ADMIN_SECRET:
        return jsonify({"error": "未授權"}), 403

    health_data = {
        'timestamp': datetime.now().isoformat(),
        'services': {},
        'storage': {},
        'database': {}
    }

    # 檢查 MongoDB
    try:
        if client is not None:
            client.admin.command('ping')
            health_data['database']['status'] = 'healthy'
            health_data['database']['message'] = '連線正常'

            # 獲取詳細資料庫統計
            if tasks_collection is not None and holidays_collection is not None:
                # 壓縮任務統計
                total_tasks = tasks_collection.count_documents({})
                processing_tasks = tasks_collection.count_documents({'status': '處理中'})
                waiting_tasks = tasks_collection.count_documents({'status': '等待中'})
                completed_today = tasks_collection.count_documents({
                    'status': '完成',
                    'created_at': {'$gte': datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)}
                })

                # 假日記錄統計
                holidays_count = holidays_collection.count_documents({})

                health_data['database']['total_tasks'] = total_tasks
                health_data['database']['processing_tasks'] = processing_tasks
                health_data['database']['waiting_tasks'] = waiting_tasks
                health_data['database']['completed_today'] = completed_today
                health_data['database']['holidays_count'] = holidays_count

                # 資料庫大小資訊
                if compressor_db is not None:
                    stats = compressor_db.command('dbStats')
                    health_data['database']['db_size_mb'] = round(stats.get('dataSize', 0) / (1024 * 1024), 2)
                    health_data['database']['collections_count'] = stats.get('collections', 0)
        else:
            health_data['database']['status'] = 'unhealthy'
            health_data['database']['message'] = 'MongoDB client 未初始化'
    except Exception as e:
        health_data['database']['status'] = 'unhealthy'
        health_data['database']['message'] = str(e)

    # 檢查壓縮工具服務
    try:
        start_time = time.time()
        response = requests.get(f'{COMPRESSOR_URL}/storage-stats', timeout=5)
        response_time = round((time.time() - start_time) * 1000, 2)

        if response.status_code == 200:
            health_data['services']['compressor'] = {
                'status': 'healthy',
                'message': '服務正常',
                'response_time_ms': response_time,
                'url': COMPRESSOR_URL
            }

            # 獲取儲存空間資訊
            storage_data = response.json()
            health_data['storage'] = {
                'used_mb': storage_data.get('used_space_mb', 0),
                'total_mb': storage_data.get('total_space_mb', 512),
                'available_mb': storage_data.get('available_mb', 0),
                'usage_percent': storage_data.get('usage_percent', 0),
                'file_count': storage_data.get('file_count', 0),
                'warning_level': storage_data.get('warning_level', 'normal')
            }
        else:
            health_data['services']['compressor'] = {
                'status': 'unhealthy',
                'message': f'HTTP {response.status_code}',
                'response_time_ms': response_time,
                'url': COMPRESSOR_URL
            }
    except requests.exceptions.Timeout:
        health_data['services']['compressor'] = {
            'status': 'unhealthy',
            'message': '連線超時',
            'url': COMPRESSOR_URL
        }
    except Exception as e:
        health_data['services']['compressor'] = {
            'status': 'unhealthy',
            'message': str(e),
            'url': COMPRESSOR_URL
        }

    # 檢查班表工具服務
    try:
        start_time = time.time()
        response = requests.get(SCHEDULE_URL, timeout=5)
        response_time = round((time.time() - start_time) * 1000, 2)

        if response.status_code == 200:
            health_data['services']['schedule'] = {
                'status': 'healthy',
                'message': '服務正常',
                'response_time_ms': response_time,
                'url': SCHEDULE_URL
            }
        else:
            health_data['services']['schedule'] = {
                'status': 'unhealthy',
                'message': f'HTTP {response.status_code}',
                'response_time_ms': response_time,
                'url': SCHEDULE_URL
            }
    except requests.exceptions.Timeout:
        health_data['services']['schedule'] = {
            'status': 'unhealthy',
            'message': '連線超時',
            'url': SCHEDULE_URL
        }
    except Exception as e:
        health_data['services']['schedule'] = {
            'status': 'unhealthy',
            'message': str(e),
            'url': SCHEDULE_URL
        }

    # 計算整體健康狀態
    all_healthy = (
        health_data['database']['status'] == 'healthy' and
        health_data['services'].get('compressor', {}).get('status') == 'healthy' and
        health_data['services'].get('schedule', {}).get('status') == 'healthy'
    )

    health_data['overall_status'] = 'healthy' if all_healthy else 'degraded'

    return jsonify(health_data), 200

# --- 本地開發專用 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

