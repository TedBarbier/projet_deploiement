import pytest
from unittest.mock import MagicMock, patch, ANY
import sys
import os

# Add autoscaler to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/autoscaler')))

import autoscaler

# Mock Docker Container
def mock_container(name, cpu_usage_percent):
    container = MagicMock()
    container.name = name
    
    # Simple stat mock to force get_cpu_usage to return `cpu_usage_percent`
    # Formula: (cpu_delta / system_cpu_delta) * online_cpus * 100
    # Let online_cpus = 1
    # system_cpu_delta = 100
    # cpu_delta = cpu_usage_percent
    
    container.stats.return_value = {
        'cpu_stats': {
            'cpu_usage': {'total_usage': 100 + cpu_usage_percent, 'percpu_usage': [0]},
            'system_cpu_usage': 200,
            'online_cpus': 1
        },
        'precpu_stats': {
            'cpu_usage': {'total_usage': 100},
            'system_cpu_usage': 100
        }
    }
    return container

@pytest.fixture
def mock_docker_client():
    with patch('autoscaler.client') as mock_client:
        yield mock_client

@patch('autoscaler.scale_service_cmd')
def test_autoscaler_scale_up(mock_scale_cmd, mock_docker_client):
    # Setup: 1 container with 80% CPU
    c1 = mock_container("api-1", 80.0)
    mock_docker_client.containers.list.return_value = [c1]
    
    # Inject dependencies to avoid infinite loop
    # We will invoke the logic inside the loop manually or refactor code?
    # Since main() has a while True loop, we cannot test it directly easily without refactoring or throwing an exception to break the loop.
    # Instead, let's extract the loop body logic into a function 'check_and_scale' or just mock time.sleep to raise an exception after 1 call.
    
    # We will mock the 'main' loop by copy-pasting the logic or just verify the helper functions if we can't change code.
    # But better: let's test the logic by mocking time.sleep to break execution
    
    with patch('time.sleep', side_effect=InterruptedError):
        try:
            autoscaler.main()
        except InterruptedError:
            pass
    
    # Verify scale up
    # Current count = 1. Threshold Up = 50. Avg = 80.
    # New count should be 2.
    mock_scale_cmd.assert_called_with(2)

@patch('autoscaler.scale_service_cmd')
def test_autoscaler_scale_down(mock_scale_cmd, mock_docker_client):
    # Setup: 3 containers with 5% CPU each
    c1 = mock_container("api-1", 5.0)
    c2 = mock_container("api-2", 5.0)
    c3 = mock_container("api-3", 5.0)
    mock_docker_client.containers.list.return_value = [c1, c2, c3]
    
    with patch('time.sleep', side_effect=InterruptedError):
        try:
            autoscaler.main()
        except InterruptedError:
            pass
            
    # Current = 3. Avg = 5. Threshold Down = 10.
    # New count = 2.
    mock_scale_cmd.assert_called_with(2)

@patch('autoscaler.scale_service_cmd')
def test_autoscaler_no_action(mock_scale_cmd, mock_docker_client):
    # Setup: 2 containers with 30% CPU (stable)
    c1 = mock_container("api-1", 30.0)
    c2 = mock_container("api-2", 30.0)
    mock_docker_client.containers.list.return_value = [c1, c2]
    
    with patch('time.sleep', side_effect=InterruptedError):
        try:
            autoscaler.main()
        except InterruptedError:
            pass
            
    # No scale called
    mock_scale_cmd.assert_not_called()


def test_scale_service_cmd(mock_docker_client):
    # Test success
    with patch('os.system') as mock_os:
        mock_os.return_value = 0 # Success
        autoscaler.scale_service_cmd(3)
        mock_os.assert_called_once()
        assert "scale api=3" in mock_os.call_args[0][0]
        
    # Test failure
    with patch('os.system') as mock_os:
        mock_os.return_value = 1 # Fail
        # Should log error but not crash
        autoscaler.scale_service_cmd(3)

def test_main_loop_error(mock_docker_client):
    # Simulate an error in the loop logic to verify try/except block
    mock_docker_client.containers.list.side_effect = Exception("Docker API Fail")
    
    with patch('time.sleep', side_effect=InterruptedError):
        try:
            autoscaler.main()
        except InterruptedError:
            pass
    # If we reached here without crash, exception was caught
    
def test_get_cpu_usage_edge_cases():
    c = MagicMock()
    # Case 1: missing stats
    c.stats.return_value = {'cpu_stats': {}, 'precpu_stats': {}}
    assert autoscaler.get_cpu_usage(c) == 0.0
    
    # Case 2: exception
    c.stats.side_effect = Exception("Stats fail")
    assert autoscaler.get_cpu_usage(c) == 0.0
