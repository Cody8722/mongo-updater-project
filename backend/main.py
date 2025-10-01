import os
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from flask_cors import CORS
from bson import json_util
import json

# --- 初始化 ---
load_dotenv()
app = Flask(__name__)
CORS(app)

# --- 資料庫連線 ---
MONGO_URI = os.getenv('MONGO_URI')
client = None
db = None
holidays_collection = None

try:
    if not MONGO_URI:
        raise ValueError("錯誤：找不到 MONGO_URI 環境變數。")
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # 加上超時設定
    client.admin.command('ping')
    print("成功連線到 MongoDB！")
    db = client.scheduleApp
    holidays_collection = db.holidays

except Exception as e:
    print(f"無法連線到 MongoDB: {e}")


# --- API 路由 (Endpoints) ---

@app.route('/status', methods=['GET'])
def get_status():
    """ 檢查與資料庫的連線狀態。 """
    # ⭐️ 修正 #1：使用 'is not None' 進行更精確的檢查
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
    # ⭐️ 修正 #2：將 'if not holidays_collection' 改成 'if holidays_collection is None'
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
    # ⭐️ 修正 #3：將 'if not holidays_collection' 改成 'if holidays_collection is None'
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


# --- 本地開發專用啟動區 ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

