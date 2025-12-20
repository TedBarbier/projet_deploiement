import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Add API path to sys.path to import 'api' module
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/api')))

# Mock modules that might not be installed or need mocking before import
sys.modules['ansible_runner'] = MagicMock()

# Now import the app
from api import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    # Disable Limiter for tests if needed, or mock get_remote_address
    with app.test_client() as client:
        yield client

@pytest.fixture
def mock_db():
    with patch('api.get_db_connection') as mock_conn:
        # Mock connection and cursor
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        
        mock_conn.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        
        # Support context manager behavior for cursor if needed
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None

        yield mock_conn

@pytest.fixture
def auth_headers():
    token = "mock_token"
    headers = {
        'Authorization': f'Bearer {token}'
    }
    # Mock decode_jwt to return a valid payload
    with patch('api.decode_jwt') as mock_decode:
        mock_decode.return_value = {
            "user_id": 1,
            "username": "tester",
            "role": "user"
        }
        yield headers

@pytest.fixture
def mock_db_sched():
    with patch('scheduler.get_db_connection') as mock_conn:
        mock_connection = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.return_value = mock_connection
        mock_connection.cursor.return_value = mock_cursor
        mock_cursor.__enter__.return_value = mock_cursor
        mock_cursor.__exit__.return_value = None
        yield mock_conn
