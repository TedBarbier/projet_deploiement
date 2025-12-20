import pytest
from unittest.mock import MagicMock, patch, ANY

# Mock imports that might fail or need setup
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/scheduler')))

# Import scheduler module
import scheduler


def test_reassign_rental_on_node_failure(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Setup: 
    # 1. get active rentals on dead node (id=10)
    cursor.fetchall.side_effect = [
        # First fetchall: affected_rentals
        [{
            "id": 500, "user_id": 1, "active": True, 
            "leased_from": "now", "leased_until": "later", 
            "ssh_password": "enc"
        }],
        # Second fetchall: replacements nodes
        [{
            "id": 20, "ip": "1.2.3.4", "ssh_port": 2222
        }],
        # Third fetchall: user info for provisioning
        {"username": "tester"} # actually fetchone
    ]
    # Adjust fetchone behavior for user info
    cursor.fetchone.return_value = {"username": "tester"}
    
    with patch('scheduler.run_ansible_task') as mock_ansible, \
         patch('scheduler.decrypt_password') as mock_descrypt:
        
        mock_ansible.return_value = True
        mock_descrypt.return_value = "secret"
        
        scheduler.reassign_rental_on_node_failure(10, cursor)
        
        # Verify calls by searching queries
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        
        assert any("UPDATE rentals SET active=FALSE" in c for c in calls)
        assert any("INSERT INTO rentals" in c for c in calls)
        assert any("UPDATE nodes SET allocated=TRUE" in c for c in calls)
        assert any("UPDATE nodes SET allocated=FALSE, needs_cleanup=TRUE" in c for c in calls)

def test_cleanup_resurrected_nodes(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # 1. Select dirty nodes
    cursor.fetchall.side_effect = [
        [{"id": 10, "ip": "1.1.1.1", "ssh_port": 22, "hostname": "worker1"}], # nodes
        [{"username": "dirty_user", "ssh_password": "enc"}] # rentals history
    ]
    
    with patch('scheduler.run_ansible_task') as mock_ansible, \
         patch('scheduler.decrypt_password') as mock_decrypt:
        
        mock_ansible.return_value = True # Ansible Success
        
        scheduler.job_cleanup_resurrected_nodes()
        
        mock_ansible.assert_called_with('delete_user.yml', ANY, ANY, "dirty_user", ANY)
        
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        assert any("UPDATE nodes SET needs_cleanup=FALSE" in c for c in calls)

def test_health_check_dead_node(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Mock nodes to check
    # job_health_check calls:
    # 1. fetchall for SELECT ... SKIP LOCKED
    cursor.fetchall.return_value = [{"id": 10, "ip": "1.1.1.1", "ssh_port": 22}]
    
    # Mock check_node_health to return 'dead'
    with patch('scheduler.check_node_health') as mock_check:
        mock_check.return_value = 'dead'
        
        scheduler.job_health_check()
        
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        # logging.info(calls) # Debug if needed
        

def test_job_expire_leases(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Mock expired leases
    cursor.fetchall.return_value = [{
        "node_id": 10, "ip": "1.1.1.1", "ssh_port": 22,
        "rental_id": 500, "user_id": 1, "username": "expired_user",
        "ssh_password": "enc"
    }]
    
    with patch('scheduler.run_ansible_task') as mock_ansible, \
         patch('scheduler.decrypt_password') as mock_decrypt:
        
        mock_ansible.return_value = True
        mock_decrypt.return_value = "secret"
        
        scheduler.job_expire_leases()
        
        mock_ansible.assert_called_with('delete_user.yml', ANY, ANY, "expired_user", ANY)
        
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        assert any("UPDATE nodes SET allocated=FALSE" in c for c in calls)
        assert any("UPDATE rentals SET active=FALSE" in c for c in calls)

def test_job_expire_leases_ansible_fail(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Mock expired leases
    cursor.fetchall.return_value = [{
        "node_id": 10, "ip": "1.1.1.1", "ssh_port": 22,
        "rental_id": 500, "user_id": 1, "username": "expired_user",
        "ssh_password": "enc"
    }]
    
    with patch('scheduler.run_ansible_task') as mock_ansible, \
         patch('scheduler.decrypt_password') as mock_descrypt:
        mock_ansible.return_value = False # Ansible Fails
        mock_descrypt.return_value = "pass"
        
        scheduler.job_expire_leases()
        
        # DB update should NOT happen
        # We check specific updates
        calls = [c[0][0] for c in cursor.execute.call_args_list]
        assert not any("UPDATE nodes SET allocated" in c for c in calls)
        assert not any("UPDATE rentals SET active" in c for c in calls)

def test_db_connection_fail():
    # Must raise mysql.connector.Error
    from mysql.connector import Error
    with patch('mysql.connector.connect', side_effect=Error("DB Down")):
        assert scheduler.get_db_connection() is None

def test_job_migrate_dead_nodes(mock_db_sched):
    conn = mock_db_sched.return_value
    cursor = conn.cursor.return_value
    
    # Mock dead nodes
    cursor.fetchall.side_effect = [
        [{"id": 99}], # dead nodes
        [] # no affected rentals logic for this mock, or we mock reassign
    ]
    
    with patch('scheduler.reassign_rental_on_node_failure') as mock_reassign:
        scheduler.job_migrate_dead_nodes()
        
        mock_reassign.assert_called_with(99, cursor)
        assert conn.commit.called

def test_run_ansible_task_success():
    # Mock ansible_runner.run
    with patch('ansible_runner.run') as mock_run:
        mock_run.return_value.rc = 0
        res = scheduler.run_ansible_task('play.yml', '1.1.1.1', 22, 'user', 'pass')
        assert res is True

def test_run_ansible_task_failure():
    # Mock ansible_runner.run
    with patch('ansible_runner.run') as mock_run:
        mock_run.return_value.rc = 1
        mock_run.return_value.stdout = MagicMock()
        mock_run.return_value.stderr = MagicMock()
        
        res = scheduler.run_ansible_task('play.yml', '1.1.1.1', 22, 'user', 'pass')
        assert res is False
