import pytest
import bcrypt
from unittest.mock import MagicMock

def test_signup_success(client, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Setup mock: User does not exist
    cursor.fetchone.return_value = None
    
    response = client.post('/signup', json={
        "username": "newuser",
        "password": "password123"
    })
    
    assert response.status_code == 201
    assert response.json["message"] == "Compte créé avec succès"
    # Verify DB insert
    assert cursor.execute.call_count == 2 # Select then Insert
    args, _ = cursor.execute.call_args_list[1]
    assert "INSERT INTO users" in args[0]
    assert args[1][0] == "newuser" # username
    # role forced to user
    assert args[1][2] == "user"

def test_signup_existing_user(client, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Setup mock: User exists
    cursor.fetchone.return_value = {"id": 1}
    
    response = client.post('/signup', json={
        "username": "existing",
        "password": "password123"
    })
    
    assert response.status_code == 400
    assert response.json["error"] == "Utilisateur déjà existant"

def test_login_success(client, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Hash a password
    pw_hash = bcrypt.hashpw(b"password123", bcrypt.gensalt()).decode()
    
    # Setup mock: User found
    cursor.fetchone.return_value = {
        "id": 1, 
        "username": "validuser", 
        "password_hash": pw_hash, 
        "role": "user"
    }
    
    response = client.post('/login', json={
        "username": "validuser",
        "password": "password123"
    })
    
    assert response.status_code == 200
    assert "token" in response.json

def test_login_wrong_password(client, mock_db):
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Hash a password
    pw_hash = bcrypt.hashpw(b"correctpass", bcrypt.gensalt()).decode()
    
    # Setup mock: User found
    cursor.fetchone.return_value = {
        "id": 1, 
        "username": "validuser", 
        "password_hash": pw_hash, 
        "role": "user"
    }
    
    response = client.post('/login', json={
        "username": "validuser",
        "password": "wrongpass"
    })
    
    assert response.status_code == 401
    assert response.json["error"] == "Mot de passe incorrect"
