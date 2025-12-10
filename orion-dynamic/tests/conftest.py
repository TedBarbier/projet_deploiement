import pytest
import sys
import os
from unittest.mock import MagicMock

# Add API directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/api')))

@pytest.fixture
def api_app():
    # Mock environment variables BEFORE importing api
    os.environ['DB_HOST'] = 'localhost'
    os.environ['DB_USER'] = 'test'
    os.environ['DB_PASSWORD'] = 'test'
    os.environ['DB_NAME'] = 'test_db'
    os.environ['JWT_SECRET_KEY'] = 'test_secret'
    os.environ['ENCRYPTION_KEY'] = 'gjh56u1j2k3l4m5n6o7p8q9r0s1t2u3v4w5x6y7z8a9=' # Valid dummy key

    from api import app
    app.config['TESTING'] = True
    return app

@pytest.fixture
def client(api_app):
    return api_app.test_client()

@pytest.fixture
def mock_db_conn(mocker):
    """Mocks the DB connection and cursor."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    # Patch get_db_connection in api module
    mocker.patch('api.get_db_connection', return_value=mock_conn)
    
    return mock_conn, mock_cursor
