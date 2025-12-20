import pytest
from unittest.mock import MagicMock, ANY, patch
from datetime import datetime, timedelta

# Helper to get a valid token
def get_auth_token(client, role="user"):
    # In a real test we would generate a valid JWT using the shared secret
    # But since we mock everything, we can just use the login endpoint if we mocked the DB correctly.
    # Alternatively, we can mock the jwt.decode in api.py, but that's invasive.
    # A cleaner way for unit tests is to rely on 'test_request_context' or generate a real token 
    # since we have access to JWT_SECRET via the app config/env.
    
    from api import generate_jwt
    return generate_jwt(user_id=1, username="tester", role=role)

def test_rent_no_nodes_available(client, mock_db):
    token = get_auth_token(client)
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Mock no nodes returned
    cursor.fetchall.return_value = []
    
    response = client.post('/rent', 
        headers={"Authorization": f"Bearer {token}"},
        json={"duration_hours": 1, "count": 1}
    )
    
    assert response.status_code == 503
    assert "Pas assez de workers libres" in response.json["error"]

def test_rent_success(client, mock_db):
    token = get_auth_token(client)
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Mock 1 available node
    cursor.fetchall.return_value = [{
        "id": 101,
        "ip": "1.2.3.4",
        "ssh_port": 2222
    }]
    cursor.lastrowid = 500 # Rental ID
    
    # Mock Ansible success
    with patch('api.run_ansible_provision') as mock_ansible:
        mock_ansible.return_value = True
        
        response = client.post('/rent', 
            headers={"Authorization": f"Bearer {token}"},
            json={"duration_hours": 2, "count": 1}
        )
        
        assert response.status_code == 200
        data = response.json["allocated"][0]
        assert data["rental_id"] == 500
        assert data["host_ip"] == "1.2.3.4"
        
        # Verify DB updates
        # Check that we selected FOR UPDATE
        assert "SELECT * FROM nodes" in cursor.execute.call_args_list[0][0][0]
        assert "FOR UPDATE" in cursor.execute.call_args_list[0][0][0]
        
        # Check Insert Rental
        assert "INSERT INTO rentals" in cursor.execute.call_args_list[1][0][0]
        
        # Check Node Status Update
        assert "UPDATE nodes SET allocated=TRUE" in cursor.execute.call_args_list[2][0][0]

def test_release_success(client, mock_db):
    token = get_auth_token(client)
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Mock rental lookup
    # Row: id, ..., user_id=1 (matches token), active=True, ip, host...
    cursor.fetchone.return_value = {
        "id": 500,
        "user_id": 1,
        "active": True,
        "node_id": 101,
        "username": "tester",
        "ip": "1.2.3.4",
        "ssh_port": 22,
        "ssh_password": "encrypted" # would need decryption mock if used
    }
    
    with patch('api.run_ansible_provision') as mock_ansible, \
         patch('api.decrypt_password') as mock_decrypt:
        
        mock_ansible.return_value = True
        mock_decrypt.return_value = "secret"
        
        response = client.post('/release/500', 
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert response.json["message"] == "Lease libérée avec succès"
        
        # Verify updates
        # Rental inactive
        assert "UPDATE rentals SET active = FALSE" in cursor.execute.call_args_list[-2][0][0]
        assert "UPDATE nodes SET allocated = FALSE" in cursor.execute.call_args_list[-1][0][0]

def test_extend_lease(client, mock_db):
    token = get_auth_token(client)
    conn = mock_db.return_value
    cursor = conn.cursor.return_value
    
    # Mock rental
    now = datetime.now()
    cursor.fetchone.return_value = {
        "id": 500,
        "user_id": 1,
        "active": True,
        "leased_until": now
    }
    
    response = client.post('/extend/500', 
        headers={"Authorization": f"Bearer {token}"},
        json={"additional_hours": 3}
    )
    
    assert response.status_code == 200
    assert response.json["rental_id"] == 500
    
    # Check Update
    # Args should contain new date > now
    args = cursor.execute.call_args[0]
    assert "UPDATE rentals SET leased_until" in args[0]
