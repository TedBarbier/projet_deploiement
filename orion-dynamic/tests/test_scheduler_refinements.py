
import pytest
from unittest.mock import MagicMock, patch, ANY

def test_scheduler_main_loop_error():
    from scheduler import main
    
    # Simulate exception in loop
    with patch('schedule.run_pending', side_effect=Exception("Loop Error")), \
         patch('time.sleep', side_effect=KeyboardInterrupt): # Break loop
        
        try:
            main()
        except KeyboardInterrupt:
            pass

def test_reassign_db_error():
    from scheduler import reassign_rental_on_node_failure
    
    cursor = MagicMock()
    # Execute raises DB error
    cursor.execute.side_effect = Exception("DB Error")
    
    with pytest.raises(Exception):
        reassign_rental_on_node_failure(1, cursor)

def test_job_health_check_rollback(mock_db_sched):
    from scheduler import job_health_check
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Select succeeds
    cursor.execute.side_effect = None
    
    # But update last_checked fails (simulate DB error during commit phase or second query)
    # We can patch conn.commit to fail
    conn.commit.side_effect = Exception("Commit Fail")
    
    job_health_check()
    conn.rollback.assert_called()

def test_job_health_check_no_nodes(mock_db_sched):
    from scheduler import job_health_check
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    cursor.fetchall.return_value = []
    job_health_check()
    conn.rollback.assert_called()

def test_cleanup_resurrected_nodes_error(mock_db_sched):
    from scheduler import job_cleanup_resurrected_nodes
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Select raises
    cursor.execute.side_effect = Exception("Select Fail")
    
    job_cleanup_resurrected_nodes()
    # Should catch and log
    conn.rollback.assert_called()
