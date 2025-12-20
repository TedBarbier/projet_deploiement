
import pytest
from unittest.mock import MagicMock, patch

# -----------------------------------------------------------------------------
# API Gap Coverage
# -----------------------------------------------------------------------------

def test_get_db_connection_success():
    from api import get_db_connection
    with patch('mysql.connector.connect') as mock_connect:
        conn = get_db_connection()
        assert conn is not None

def test_generate_jwt_bytes():
    from api import generate_jwt
    # Mock jwt.encode to return bytes (legacy behavior simulation)
    with patch('jwt.encode', return_value=b'somebytes'):
        token = generate_jwt(1, 'user', 'user')
        assert isinstance(token, str)
        assert token == 'somebytes'

def test_endpoints_db_none(client, auth_headers):
    # api.get_db_connection returning None triggers 500
    with patch('api.get_db_connection', return_value=None):
        # Signup
        res = client.post('/signup', json={"username": "u", "password": "p"})
        assert res.status_code == 500
        
        # Login
        res = client.post('/login', json={"username": "u", "password": "p"})
        assert res.status_code == 500
        
        # Rent
        res = client.post('/rent', headers=auth_headers, json={"duration_hours": 1, "count": 1})
        assert res.status_code == 500
        
        # Release
        res = client.post('/release/1', headers=auth_headers)
        assert res.status_code == 500
        
        # Extend
        res = client.post('/extend/1', headers=auth_headers, json={"additional_hours": 1})
        assert res.status_code == 500
        
        # List Nodes
        res = client.get('/nodes', headers=auth_headers)
        assert res.status_code == 500
        
        # Get Password
        res = client.get('/lease/1/password', headers=auth_headers)
        assert res.status_code == 500

def test_validation_errors(client, mock_db):
    # Signup validation failure (username too short)
    res = client.post('/signup', json={"username": "u", "password": "p"})
    # If validation fails, 400. If validation broken but DB success, 201.
    # If DB fails (mocked), 500.
    # We want 400.
    assert res.status_code == 400
    
    # Login validation failure (missing field)
    res = client.post('/login', json={"username": "u"})
    assert res.status_code == 400

def test_rent_invalid_inputs(client, auth_headers):
    # Duration <= 0
    res = client.post('/rent', headers=auth_headers, json={"duration_hours": 0})
    assert res.status_code == 400
    
    # Invalid duration type
    res = client.post('/rent', headers=auth_headers, json={"duration_hours": "abc"})
    assert res.status_code == 400

def test_rent_exception_rollback(client, auth_headers, mock_db):
    conn = mock_db.return_value
    # Simulate exception during execution
    conn.start_transaction.side_effect = Exception("DB Transaction Fail")
    
    res = client.post('/rent', headers=auth_headers, json={"duration_hours": 1})
    assert res.status_code == 500
    conn.rollback.assert_called() # Check rollback called

def test_release_edge_cases(client, auth_headers, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # 1. Lease not found
    cursor.fetchone.return_value = None
    res = client.post('/release/999', headers=auth_headers)
    assert res.status_code == 404
    conn.rollback.assert_called()
    
    # 2. Permission denied
    cursor.fetchone.return_value = {"id": 1, "user_id": 999} # Not me
    res = client.post('/release/1', headers=auth_headers)
    assert res.status_code == 403
    
    # 3. Already inactive
    cursor.fetchone.return_value = {"id": 1, "user_id": 1, "active": False}
    res = client.post('/release/1', headers=auth_headers)
    assert res.status_code == 400

def test_extend_edge_cases(client, auth_headers, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # 1. Invalid additional_hours
    res = client.post('/extend/1', headers=auth_headers, json={"additional_hours": -1})
    assert res.status_code == 400
    
    # 2. Lease not found
    cursor.fetchone.return_value = None
    res = client.post('/extend/1', headers=auth_headers, json={"additional_hours": 1})
    assert res.status_code == 404
    
    # 3. Not active
    cursor.fetchone.return_value = {"id": 1, "user_id": 1, "active": False}
    res = client.post('/extend/1', headers=auth_headers, json={"additional_hours": 1})
    assert res.status_code == 400

def test_run_ansible_logging_exception():
    # Cover line 121-122 in api.py: Exception during logging error?
    # Or exception during runner.
    from api import run_ansible_provision
    
    with patch('ansible_runner.run') as mock_run:
        mock_run.return_value.rc = 1
        # Make stdout.read() raise Exception
        mock_run.return_value.stdout.read.side_effect = Exception("Log Read Fail")
        
        # Should catch and return False, not crash
        res = run_ansible_provision('p', 'i', 22, 'u', 'p')
        assert res is False

def test_require_admin_decorator():
    from flask import Flask
    from api import require_admin
    
    # Test strict decorator logic
    app = Flask("test")
    
    @app.route("/admin")
    @require_admin
    def admin_route():
        return "ok"
    
    with app.test_client() as c:
        # 1. No auth
        res = c.get("/admin")
        assert res.status_code == 401
        
        # 2. User role
        with patch('api.decode_jwt', return_value={"role": "user", "user_id": 1}):
            res = c.get("/admin", headers={"Authorization": "Bearer tok"})
            assert res.status_code == 403
            
        # 3. Admin role
        with patch('api.decode_jwt', return_value={"role": "admin", "user_id": 1}):
            res = c.get("/admin", headers={"Authorization": "Bearer tok"})
            assert res.status_code == 200

def test_get_user_by_id_helper():
    # Cover helpers
    from api import get_user_by_id
    mock_conn = MagicMock()
    mock_cursor = mock_conn.cursor.return_value
    mock_cursor.fetchone.return_value = "found"
    
    u = get_user_by_id(mock_conn, 1)
    assert u == "found"
