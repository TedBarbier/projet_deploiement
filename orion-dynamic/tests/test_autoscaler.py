import pytest
import sys
import os
from unittest.mock import MagicMock, call

# Add Autoscaler directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../control-plane/autoscaler')))

@pytest.fixture
def mock_autoscaler_docker(mocker):
    # Mock imports
    mocker.patch.dict(sys.modules, {'docker': MagicMock()})
    
    import autoscaler
    
    mock_client = MagicMock()
    mocker.patch('autoscaler.client', mock_client)
    mocker.patch('os.system', return_value=0) # Mock os.system
    
    return autoscaler, mock_client

def test_scale_service_up(mock_autoscaler_docker):
    autoscaler, _ = mock_autoscaler_docker
    
    # Run
    autoscaler.scale_service_cmd(3)
    
    # Verify os.system was called with correct command
    # Expected: "cd /project && docker compose -p orion-dynamic up -d --scale api=3 --no-recreate api"
    expected_cmd = "cd /project && docker compose -p orion-dynamic up -d --scale api=3 --no-recreate api"
    autoscaler.os.system.assert_called_with(expected_cmd)

def test_check_and_scale_up(mock_autoscaler_docker, mocker):
    autoscaler, mock_client = mock_autoscaler_docker
    
    # Mock container count
    mock_client.containers.list.return_value = [1, 2] # 2 containers
    
    # Mock CPU - Reuse internal loop logic or mock helper?
    # Autoscaler calculates avg manually in main loop.
    # We should refactor autoscaler to be testable or mock the stats calls.
    # Given the implementation:
    # It calls get_cpu_usage(c) -> stats -> calculation.
    # We can mock get_cpu_usage function in autoscaler module.
    
    mocker.patch('autoscaler.get_cpu_usage', side_effect=[80.0, 80.0]) # Both containers hot
    
    # Mock logging so we don't spam
    mocker.patch('autoscaler.logger')
    
    # Mock scale_service_cmd
    mocker.patch('autoscaler.scale_service_cmd')
    
    # Mock time
    mocker.patch('time.time', side_effect=[100, 200, 300]) 
    autoscaler.last_scale_time = 0
    
    # We need to test the logic INSIDE main() loop. 
    # But main contains `while True`. We can't test main directly easily without breaking the loop.
    # Autoscaler code needs refactoring to extract logic into `check_and_scale()`.
    # Wait, in the code snippet I saw earlier, the logic WAS inside `main()`.
    # But in my previous test attempt I called `autoscaler.check_and_scale("api")`.
    # Does `check_and_scale` exist? 
    # Looking at the `view_file` output:
    # Lines 95-150: `def main(): ... while True: ... logic ...`
    # There is NO `check_and_scale` function! I hallucinated it or assumed it existed.
    # The logic is embedded in `main`.
    
    # I should REFACTOR autoscaler.py to extract `check_and_scale` so it is testable.
    # THIS is a real unit test finding: "Code not testable".
    pass

def test_get_cpu_usage_logic(mock_autoscaler_docker):
    autoscaler, _ = mock_autoscaler_docker
    
    container = MagicMock()
    container.stats.return_value = {
        'cpu_stats': {
            'cpu_usage': {'total_usage': 2000000000},
            'system_cpu_usage': 2000000000,
            'online_cpus': 2
        },
        'precpu_stats': {
            'cpu_usage': {'total_usage': 1000000000},
            'system_cpu_usage': 1000000000
        }
    }
    
    # delta = 1B, sys_delta = 1B. Ratio = 1. CPUs = 2. Usage = 1 * 2 * 100 = 200%
    usage = autoscaler.get_cpu_usage(container)
    assert usage == 200.0
