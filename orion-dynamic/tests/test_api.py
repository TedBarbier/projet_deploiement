import pytest
import sys
import os
import json
from unittest.mock import MagicMock, call
from datetime import datetime

# Add API directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/api')))

@pytest.fixture
def client(mocker):
    # Mock environment variables BEFORE importing api
    os.environ['DB_HOST'] = 'localhost'
    os.environ['DB_USER'] = 'test'
    os.environ['DB_PASSWORD'] = 'test'
    os.environ['DB_NAME'] = 'test_db'
    os.environ['JWT_SECRET_KEY'] = 'test_secret'
    # Valid Fernet key (32 items)
    os.environ['ENCRYPTION_KEY'] = 'gjh56u1j2k3l4m5n6o7p8q9r0s1t2u3v4w5x6y7z8a9='

    # Mock DB connection at module level
    mocker.patch('mysql.connector.connect')
    
    from api import app
    app.config['TESTING'] = True
    
    with app.test_client() as client:
        yield client

@pytest.fixture
def mock_db_conn(mocker):
    mock_connect = mocker.patch('api.get_db_connection')
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.lastrowid = 123 # Mock ID for inserts
    
    return mock_conn, mock_cursor

def test_health(client):
    res = client.get('/health')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'healthy'
    assert 'hostname' in data

def test_login_success(client, mock_db_conn):
    conn, cursor = mock_db_conn
    
    # User data
    # Need to mock bcrypt inside api to match hash?
    # Or just mock fetchone to return what verifies?
    # Actually api uses bcrypt checking.
    # We should generate a real hash for the test password.
    import bcrypt
    password = "password123"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_row = {"id": 1, "username": "testuser", "password_hash": hashed, "role": "user"}
    
    # Mock DB response
    cursor.fetchone.return_value = user_row
    
    # Call login
    res = client.post('/login', json={"username": "testuser", "password": password})
    
    assert res.status_code == 200
    assert 'token' in res.get_json()

def test_login_fail(client, mock_db_conn):
    conn, cursor = mock_db_conn
    cursor.fetchone.return_value = None # User not found
    
    res = client.post('/login', json={"username": "unknown", "password": "pwd"})
    assert res.status_code == 401

def test_rent_no_nodes(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    
    # Mock auth decorator to bypass token check or return a user
    mocker.patch('api.decode_jwt', return_value={"user_id": 1, "username": "test", "role": "user"})
    
    # Mock node selection: return None (or empty list for fetchall)
    cursor.fetchall.return_value = []
    
    # Add json payload
    payload = {"duration_hours": 1, "ssh_password": "pass"}
    res = client.post('/rent', headers={"Authorization": "Bearer fake_token"}, json=payload)
    assert res.status_code == 503
    assert "Pas assez de workers libres" in res.get_json()['error']

def test_rent_success(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    
    # Mock auth
    mocker.patch('api.decode_jwt', return_value={"user_id": 1, "username": "test", "role": "user"})
    
    # Mock node selection (SELECT ... FOR UPDATE)
    # API uses fetchall(), so we return a list of nodes
    cursor.fetchall.return_value = [
        {"id": 10, "ip": "1.2.3.4", "ssh_port": 22, "last_checked": datetime.now(), "allocated": 0, "hostname": "worker-1", "status": "alive"}
    ]
    
    # Mock ansible
    mocker.patch('api.run_ansible_provision', return_value=True)
    
    payload = {"duration_hours": 1, "ssh_password": "pass"}
    res = client.post('/rent', headers={"Authorization": "Bearer fake_token"}, json=payload)
    
    # Check api.py logic: returns {"allocated": [...]} and 200
    assert res.status_code == 200
    data = res.get_json()
    assert "allocated" in data
    assert len(data["allocated"]) == 1
    assert data["allocated"][0]["client_user"] == "test"
    
    # Verify DB updates were called
    assert cursor.execute.call_count >= 2

def test_release_success(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    
    # Mock auth
    mocker.patch('api.decode_jwt', return_value={"user_id": 1, "username": "test", "role": "user"})
    
    # Mock finding rental
    cursor.fetchone.return_value = {
        "id": 50, 
        "node_id": 10, 
        "user_id": 1,
        "username": "testuser", # Added username as used in api lines 450
        "ip": "1.2.3.4",
        "ssh_port": 22,
        "active": 1,
        "ssh_password": "encrypted_pass" # Used in cleanup
    }
    
    # Mock decrypt
    mocker.patch('api.decrypt_password', return_value="pass")
    
    # Mock ansible cleanup
    # release_lease uses run_ansible_provision with 'delete_user.yml'
    mocker.patch('api.run_ansible_provision', return_value=True)
    
    res = client.post('/release/50', headers={"Authorization": "Bearer fake_token"})
    
    assert res.status_code == 200
    assert "Lease libérée avec succès" in res.get_json()['message']

def test_signup_success(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    # Mock user check (not found)
    cursor.fetchone.return_value = None
    
    # Mock insert
    cursor.lastrowid = 2
    
    # Needs valid Fernet for password encryption? No, signup uses bcrypt hash, but encryption key is for SSH.
    # Actually API uses bcrypt for user password.
    
    res = client.post('/signup', json={"username": "newuser", "password": "newpassword"})
    assert res.status_code == 201
    assert "Compte créé avec succès" in res.get_json()['message']

def test_list_nodes(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    
    # Mock auth
    mocker.patch('api.decode_jwt', return_value={"user_id": 1, "username": "test", "role": "user"})
    
    # Return list of nodes with keys expected by list_nodes loop
    cursor.fetchall.return_value = [
        {
            "node_id": 1, "hostname": "node1", "ssh_port": 22, "status": "alive", "allocated": 0,
            "rental_id": None
        },
        {
            "node_id": 2, "hostname": "node2", "ssh_port": 22, "status": "alive", "allocated": 1,
            "rental_id": 100, "rental_user_id": 1, "leased_from": datetime.now(), "leased_until": datetime.now(), "active": 1
        }
    ]
    
    res = client.get('/nodes', headers={"Authorization": "Bearer fake_token"})
    assert res.status_code == 200
    assert len(res.get_json()) == 2

def test_extend_lease(client, mock_db_conn, mocker):
    conn, cursor = mock_db_conn
    
    # Mock auth
    mocker.patch('api.decode_jwt', return_value={"user_id": 1, "username": "test", "role": "user"})
    
    # Mock finding rental
    # Must be active
    cursor.fetchone.return_value = {
        "id": 50, "node_id": 10, "user_id": 1, "active": 1, "leased_until": datetime.now()
    }
    
    # Add additional_hours
    res = client.post('/extend/50', headers={"Authorization": "Bearer fake_token"}, json={"additional_hours": 1})
    assert res.status_code == 200
    assert "Bail prolongé avec succès" in res.get_json()['message']
