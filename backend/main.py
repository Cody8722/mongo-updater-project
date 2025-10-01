import os
from flask import Flask, request, jsonify
from pymongo import MongoClient
from dotenv import load_dotenv
from flask_cors import CORS
from bson import json_util
import json

# --- 初始化 ---

# 載入 .env 檔案中的環境變數
load_dotenv()

# 建立 Flask 應用程式實例
app = Flask(__name__)
# 啟用 CORS，允許來自任何來源的跨域請求，方便前端開發
CORS(app)

# --- 資料庫連線 ---

# 從環境變數中取得 MongoDB 連線 URI
MONGO_URI = os.getenv('MONGO_URI')
client = None
db = None
holidays_collection = None

# 嘗試建立資料庫連線
try:
    if not MONGO_URI:
        # 如果找不到連線字串，拋出錯誤，這會讓 /status 回報錯誤
        raise ValueError("錯誤：找不到 MONGO_URI 環境變數。請檢查您的 .env 檔案或伺服器設定。")
    
    client = MongoClient(MONGO_URI)
    # 進行一次伺服器 ping 測試來確認連線是否成功
    client.admin.command('ping')
    print("成功連線到 MongoDB！")
    # 連接到 scheduleApp 資料庫
    db = client.scheduleApp
    # 取得 holidays 集合
    holidays_collection = db.holidays

except Exception as e:
    # 如果連線失敗，印出錯誤訊息，後續的 /status API 會處理這個狀態
    print(f"無法連線到 MongoDB: {e}")


# --- API 路由 (Endpoints) ---

@app.route('/status', methods=['GET'])
def get_status():
    """
    檢查與資料庫的連線狀態。
    """
    if client and db:
        try:
            # 再次 ping 伺服器以確保連線仍然活躍
            client.admin.command('ping')
            return jsonify({"status": "ok", "db_status": "connected"}), 200
        except Exception as e:
            return jsonify({"status": "error", "db_status": "disconnected", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "db_status": "disconnected", "message": "MongoDB client is not initialized."}), 500


@app.route('/get_holidays', methods=['GET'])
def get_holidays():
    """
    根據年份和月份獲取假日資料。
    """
    if not holidays_collection:
        return jsonify({"error": "資料庫集合未初始化"}), 500

    try:
        year = request.args.get('year')
        month = request.args.get('month')

        if not year or not month:
            return jsonify({"error": "缺少年份或月份參數"}), 400

        # 建立查詢的正則表達式，例如：^202510
        query_pattern = f"^{year}{str(month).zfill(2)}"
        
        # 查詢 _id 符合 "YYYYMM" 開頭的所有文件
        cursor = holidays_collection.find({"_id": {"$regex": query_pattern}})
        
        # 將查詢結果轉換為 list of dicts
        holidays_list = list(cursor)
        
        # 使用 bson.json_util 來正確處理 MongoDB 的特殊資料型態 (例如 ObjectId)
        return json.loads(json_util.dumps(holidays_list)), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update_holiday', methods=['POST'])
def update_holiday():
    """
    更新或插入一筆假日資料。
    """
    if not holidays_collection:
        return jsonify({"error": "資料庫集合未初始化"}), 500

    try:
        data = request.get_json()
        if not data or '_id' not in data:
            return jsonify({"error": "無效的請求資料，缺少 _id"}), 400

        doc_id = data['_id']
        
        # 準備要更新或插入的資料，移除 _id 欄位
        update_data = {k: v for k, v in data.items() if k != '_id'}

        # 使用 update_one 搭配 upsert=True
        # 如果找到符合 _id 的文件，就更新它
        # 如果找不到，就將 filter (_id) 和 update_data ($set) 的內容合併起來，插入一筆新的文件
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
    # 下方的程式碼只有在你於自己的電腦上，透過終端機執行 `python main.py` 指令時，
    # 才會被執行。它的主要目的是啟動一個方便我們開發和除錯的測試伺服器。
    #
    # 在 Zeabur 上，應用程式是由 Gunicorn 啟動的，所以 Zeabur 會完全忽略這一段。

    # 從環境變數中讀取 PORT，如果找不到，就預設使用 5000。
    # 這讓我們可以彈性地指定要用哪個端口，同時也確保在本地執行時有一個預設值。
    port = int(os.environ.get('PORT', 5000))

    # 啟動 Flask 內建的開發伺服器
    app.run(
        host='0.0.0.0',  # 監聽所有網路介面，讓我們可以從同一個網路下的其他裝置連線測試
        port=port,       # 使用我們上面指定的端口
        debug=True       # 開啟偵錯模式，當程式碼有變動並存檔時，伺服器會自動重啟，非常方便！
    )

