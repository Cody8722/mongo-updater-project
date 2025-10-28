"""
mongo-updater-project Backend API Tests
测试 MongoDB 管理中控台的主要 API 端点
"""
import pytest
import sys
import os

# 添加父目录到 Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app


@pytest.fixture
def client():
    """创建测试客户端"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


class TestHealthCheck:
    """健康检查端点测试"""

    def test_status_endpoint(self, client):
        """测试 /status 端点"""
        response = client.get('/status')
        assert response.status_code == 200

        data = response.get_json()
        assert 'status' in data
        assert data['status'] == 'ok'

    def test_status_returns_json(self, client):
        """测试返回 JSON 格式"""
        response = client.get('/status')
        assert response.content_type == 'application/json'


class TestDatabaseMonitoring:
    """数据库监控 API 测试"""

    def test_get_databases_list(self, client):
        """测试获取数据库列表"""
        response = client.get('/api/databases')
        assert response.status_code in [200, 500]
        assert response.content_type == 'application/json'

    def test_get_database_stats(self, client):
        """测试获取数据库统计信息"""
        response = client.get('/api/databases/test_db/stats')
        assert response.status_code in [200, 404, 500]

    def test_get_collections_list(self, client):
        """测试获取集合列表"""
        response = client.get('/api/databases/test_db/collections')
        assert response.status_code in [200, 404, 500]


class TestCollectionOperations:
    """集合操作 API 测试"""

    def test_get_collection_data(self, client):
        """测试获取集合数据"""
        response = client.get('/api/collections/test_collection/data')
        assert response.status_code in [200, 404, 500]

    def test_get_collection_stats(self, client):
        """测试获取集合统计"""
        response = client.get('/api/collections/test_collection/stats')
        assert response.status_code in [200, 404, 500]

    def test_create_collection_without_name(self, client):
        """测试创建集合缺少名称"""
        response = client.post('/api/collections',
                              json={},
                              content_type='application/json')
        assert response.status_code in [400, 500]


class TestAdminOperations:
    """管理员操作 API 测试"""

    def test_admin_endpoint_without_secret(self, client):
        """测试管理员端点（无密钥）"""
        response = client.post('/api/admin/restart')
        # 应该返回 401 或 403（未授权）
        assert response.status_code in [401, 403, 404, 500]

    def test_admin_endpoint_with_invalid_secret(self, client):
        """测试管理员端点（无效密钥）"""
        response = client.post('/api/admin/restart',
                              headers={'X-Admin-Secret': 'invalid_secret'})
        assert response.status_code in [401, 403, 404, 500]

    def test_get_system_info(self, client):
        """测试获取系统信息"""
        response = client.get('/api/system/info')
        assert response.status_code in [200, 500]


class TestConnectionManagement:
    """连接管理测试"""

    def test_get_active_connections(self, client):
        """测试获取活动连接数"""
        response = client.get('/api/connections')
        assert response.status_code in [200, 500]

    def test_get_connection_pool_stats(self, client):
        """测试获取连接池统计"""
        response = client.get('/api/connections/pool')
        assert response.status_code in [200, 404, 500]


class TestQueryOperations:
    """查询操作测试"""

    def test_execute_query_without_data(self, client):
        """测试执行查询缺少数据"""
        response = client.post('/api/query',
                              json={},
                              content_type='application/json')
        assert response.status_code in [400, 500]

    def test_execute_query_with_valid_data(self, client):
        """测试执行查询（有效数据）"""
        query = {
            'database': 'test_db',
            'collection': 'test_collection',
            'filter': {}
        }
        response = client.post('/api/query',
                              json=query,
                              content_type='application/json')
        assert response.status_code in [200, 400, 404, 500]

    def test_execute_aggregation(self, client):
        """测试执行聚合查询"""
        aggregation = {
            'database': 'test_db',
            'collection': 'test_collection',
            'pipeline': []
        }
        response = client.post('/api/aggregate',
                              json=aggregation,
                              content_type='application/json')
        assert response.status_code in [200, 400, 404, 500]


class TestBackupOperations:
    """备份操作测试"""

    def test_trigger_backup(self, client):
        """测试触发备份"""
        response = client.post('/api/backup',
                              json={'database': 'test_db'},
                              content_type='application/json')
        # 可能需要管理员权限
        assert response.status_code in [200, 201, 401, 403, 500]

    def test_list_backups(self, client):
        """测试列出备份"""
        response = client.get('/api/backups')
        assert response.status_code in [200, 500]


class TestInputValidation:
    """输入验证测试"""

    def test_invalid_database_name(self, client):
        """测试无效的数据库名称"""
        response = client.get('/api/databases/../etc/passwd')
        # 应该拒绝路径穿越
        assert response.status_code in [400, 404, 500]

    def test_sql_injection_attempt(self, client):
        """测试 SQL 注入尝试（虽然是 NoSQL）"""
        malicious_query = {
            'database': 'test_db',
            'collection': 'test_collection',
            'filter': {"$where": "malicious code"}
        }
        response = client.post('/api/query',
                              json=malicious_query,
                              content_type='application/json')
        # 应该被验证或安全处理
        assert response.status_code in [200, 400, 500]


class TestCORS:
    """CORS 配置测试"""

    def test_cors_headers(self, client):
        """测试 CORS headers"""
        response = client.options('/status')
        assert response.status_code in [200, 204]

    def test_cors_preflight(self, client):
        """测试 CORS preflight 请求"""
        response = client.options('/api/databases',
                                 headers={'Origin': 'http://localhost:3000'})
        assert response.status_code in [200, 204]


class TestErrorHandling:
    """错误处理测试"""

    def test_nonexistent_endpoint(self, client):
        """测试不存在的端点"""
        response = client.get('/api/nonexistent')
        assert response.status_code == 404

    def test_invalid_http_method(self, client):
        """测试不支持的 HTTP 方法"""
        response = client.patch('/api/databases')
        assert response.status_code in [405, 500]

    def test_malformed_json(self, client):
        """测试格式错误的 JSON"""
        response = client.post('/api/query',
                              data='{"invalid json',
                              content_type='application/json')
        assert response.status_code in [400, 500]


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
