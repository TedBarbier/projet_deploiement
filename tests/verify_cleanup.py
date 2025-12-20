
import sys
import unittest
from unittest.mock import MagicMock, patch

# Mock libraries not available or needed for logic test
sys.modules["mysql"] = MagicMock()
sys.modules["mysql.connector"] = MagicMock()
sys.modules["mysql.connector.errorcode"] = MagicMock()
sys.modules["paramiko"] = MagicMock()
sys.modules["ansible_runner"] = MagicMock()
sys.modules["schedule"] = MagicMock()
sys.modules["cryptography.fernet"] = MagicMock()
sys.modules["cryptography.fernet"].Fernet.generate_key.return_value = b"key"

# Now import the scheduler (adjust path as needed)
import os
sys.path.append(os.path.abspath("orion-dynamic/control-plane/scheduler"))
import scheduler

class TestSchedulerLogic(unittest.TestCase):
    
    @patch("scheduler.get_db_connection")
    @patch("scheduler.run_ansible_task")
    def test_migration_and_cleanup(self, mock_ansible, mock_db):
        print("\n--- Starting Verification Simulation ---")
        
        # Setup Mock DB
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Scenario Data
        # Node A: ID 1, Dead, Allocated
        # Node B: ID 2, Alive, Free
        # Rental: ID 100, User 10, Node 1, Active
        
        # 1. Test job_migrate_dead_nodes
        print("SIMULATION: Node 1 dies (job_migrate_dead_nodes)")
        
        # Mock finding dead node
        mock_cursor.fetchall.side_effect = [
            [{'id': 1}],                    # for job_migrate_dead_nodes (SELECT dead nodes)
            [{'id': 100, 'node_id': 1, 'user_id': 10, 'leased_from': 'now', 'leased_until': 'later', 'ssh_password': 'enc', 'active': 1}], # for reassign... (SELECT active rentals)
            [{'id': 2, 'ip': '1.2.3.4', 'ssh_port': 22}], # for reassign... (SELECT replacements)
            {'username': 'testuser'},       # for reassign... (SELECT username)
            [],                             # for job_cleanup_resurrected_nodes (SELECT alive & free nodes) - initially none to prevent early triggering
        ]
        
        mock_ansible.return_value = True # Ansible succeeds
        
        scheduler.job_migrate_dead_nodes()
        
        # Verify Migration
        print("VERIFICATION: Checking calls made during migration...")
        calls = mock_cursor.execute.call_args_list
        
        # Check if new rental created
        insert_calls = [c for c in calls if "INSERT INTO rentals" in c[0][0]]
        if insert_calls:
            print("[OK] New rental inserted on new node.")
        else:
            print("[FAIL] No new rental inserted.")

        # Check if old rental deactivated
        idx_update_rental = next((i for i, c in enumerate(calls) if "UPDATE rentals SET active=FALSE" in c[0][0]), -1)
        if idx_update_rental != -1:
            print("[OK] Old rental set to active=FALSE.")
        else:
            print("[FAIL] Old rental not deactivated.")
            
        # Check if old node allocated set to FALSE
        idx_update_node = next((i for i, c in enumerate(calls) if "UPDATE nodes SET allocated=FALSE" in c[0][0] and c[0][1] == (1,)), -1)
        if idx_update_node != -1:
            print("[OK] Old Node 1 set to allocated=FALSE.")
        else:
            print("[FAIL] Old Node 1 not freed.")


        # 2. Test job_cleanup_resurrected_nodes
        print("\nSIMULATION: Node 1 resurrects (job_cleanup_resurrected_nodes)")
        
        # Reset mocks for next phase
        mock_cursor.execute.reset_mock()
        mock_ansible.reset_mock()
        mock_ansible.return_value = True
        
        # Mock DB state for resurrection
        # 1. Select resurrected nodes -> Node 1 (Alive, Allocate=False)
        # 2. Select rentals on Node 1 -> returns the OLD rental (which is now inactive!)
        
        mock_cursor.fetchall.side_effect = [
             [{'id': 1, 'ip': '1.1.1.1', 'ssh_port': 22}], # Resurrected nodes
             [{'user_id': 10, 'username': 'testuser', 'ssh_password': 'enc'}], # Rentals on node 1 (Note: inactive ones too!)
        ]
        
        scheduler.job_cleanup_resurrected_nodes()
        
        # Verify Cleanup
        print("VERIFICATION: Checking cleanup calls...")
        
        # Check if Ansible delete_user called
        if mock_ansible.called:
             args = mock_ansible.call_args[0]
             if args[0] == 'delete_user.yml' and args[3] == 'testuser':
                 print(f"[OK] Ansible called to delete 'testuser' from resurrected node.")
             else:
                 print(f"[FAIL] Ansible called with wrong args: {args}")
        else:
            print("[FAIL] Ansible delete_user NOT called.")

        print("--- Simulation Complete ---")

if __name__ == "__main__":
    unittest.main()
