import pytest
import sys
import os
from unittest.mock import MagicMock, call

# Add Scheduler directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/scheduler')))

@pytest.fixture
def mock_scheduler_db(mocker):
    # Mock environment BEFORE import
    os.environ['DB_HOST'] = 'localhost'
    os.environ['DB_USER'] = 'test'
    os.environ['DB_PASSWORD'] = 'test'
    os.environ['DB_NAME'] = 'test_db'
    os.environ['ENCRYPTION_KEY'] = 'gjh56u1j2k3l4m5n6o7p8q9r0s1t2u3v4w5x6y7z8a9='

    # Import scheduler module inside fixture to ensure env vars are set
    import scheduler
    
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    
    # Patch get_db_connection
    mocker.patch('scheduler.get_db_connection', return_value=mock_conn)
    
    return scheduler, mock_conn, mock_cursor

def test_job_health_check_skip_locked(mock_scheduler_db, mocker):
    scheduler, conn, cursor = mock_scheduler_db
    
    # Mock behavior: 
    # 1. Select nodes returns a list
    cursor.fetchall.return_value = [{"id": 1, "ip": "1.2.3.4", "ssh_port": 22}]
    
    # Mock resolve_worker_ip and check_node_health
    mocker.patch('scheduler.resolve_worker_ip', return_value="1.2.3.4")
    mocker.patch('scheduler.check_node_health', return_value="healthy")
    
    # Run job
    scheduler.job_health_check()
    
    # Verify SKIP LOCKED was used in SELECT
    # We inspect the call args of cursor.execute
    # The first call should be the SELECT
    select_call = cursor.execute.call_args_list[0]
    sql_query = select_call[0][0] # First arg of first call
    
    assert "SELECT id, ip, ssh_port" in sql_query
    assert "FOR UPDATE SKIP LOCKED" in sql_query
    
    # Verify UPDATE last_checked happened
    # Verify commit happened
    assert conn.commit.call_count >= 1

def test_job_health_check_no_nodes(mock_scheduler_db):
    scheduler, conn, cursor = mock_scheduler_db
    
    # Return empty list
    cursor.fetchall.return_value = []
    
    scheduler.job_health_check()
    
    # Should rollback
    conn.rollback.assert_called()
    # Should NOT attempt health checks
    cursor.execute.assert_called_once() # Only the SELECT

def test_resolve_worker_ip_docker_internal(mock_scheduler_db):
    scheduler, _, _ = mock_scheduler_db
    
    # Test transformation
    assert scheduler.resolve_worker_ip("172.17.0.5") == "host.docker.internal"
    # Test pass-through
    assert scheduler.resolve_worker_ip("192.168.1.50") == "192.168.1.50"
