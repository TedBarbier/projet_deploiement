
import os
import logging
import paramiko
from scheduler import check_node_health, resolve_worker_ip, WORKER_SSH_USER, WORKER_SSH_PASS, SSH_TIMEOUT

# Setup minimal logging
logging.basicConfig(level=logging.INFO)

# Target: Node 1 (worker-01)
# Derived from DB: ip='172.17.0.2', ssh_port=22221
RAW_IP = '172.17.0.2'
PORT = 22221

RESOLVED_IP = resolve_worker_ip(RAW_IP)
print(f"DEBUG: Resolving {RAW_IP} -> {RESOLVED_IP}")

print(f"DEBUG: Checking health of {RESOLVED_IP}:{PORT}...")
status = check_node_health(RESOLVED_IP, PORT)
print(f"DEBUG: Result Status = {status}")

# Manual Paramiko verify
print("\n--- Manual Paramiko Check ---")
try:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=RESOLVED_IP, port=PORT, username=WORKER_SSH_USER,
                   password=WORKER_SSH_PASS, timeout=SSH_TIMEOUT,
                   allow_agent=False, look_for_keys=False)
    print("PARAMIKO: Connected successfully (ALIVE)")
    client.close()
except Exception as e:
    print(f"PARAMIKO: Connection Failed (DEAD) - {e}")
