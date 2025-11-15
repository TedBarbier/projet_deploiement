#!/usr/bin/env python3
import os
import logging
import schedule
import time
import tempfile
import paramiko
import mysql.connector
from mysql.connector import errorcode
import ansible_runner
from datetime import datetime

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='[SCHEDULER] %(asctime)s: %(message)s')

# --- Env ---
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

WORKER_SSH_USER = os.getenv('WORKER_SSH_USER', 'root')
WORKER_SSH_PASS = os.getenv('WORKER_SSH_PASS', 'password')
SSH_TIMEOUT = 5  # secondes

# --- DB connection ---
def get_db_connection():
    try:
        return mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=True
        )
    except mysql.connector.Error as err:
        logging.error(f"Erreur de connexion à la DB: {err}")
        return None

# --- Résolution IP Docker ---
def resolve_worker_ip(ip):
    """Retourne l'IP à utiliser pour se connecter au worker.
    Remplace les IP Docker 172.17.* par host.docker.internal.
    """
    if ip.startswith("172.17."):
        return "host.docker.internal"
    return ip

# --- Ansible runner ---
def run_ansible_task(playbook_name, host_ip, host_port, client_user, client_pass=None):
    inventory = {
        'all': {
            'hosts': {
                'target_node': {
                    'ansible_host': host_ip,
                    'ansible_port': host_port,
                    'ansible_user': WORKER_SSH_USER,
                    'ansible_password': WORKER_SSH_PASS,
                    'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
                }
            }
        }
    }
    extravars = {"target_user": client_user}
    if client_pass:
        extravars["target_pass"] = client_pass
    playbook_path = f"/ansible/{playbook_name}"
    logging.info(f"Exécution d'Ansible ({playbook_name}) sur {host_ip}:{host_port} pour {client_user}...")
    with tempfile.TemporaryDirectory() as tmpdir:
        r = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extravars
        )
        if r.rc != 0:
            logging.error(f"Échec d'Ansible ({playbook_name}) sur {host_ip}:{host_port}, RC={r.rc}")
            return False
    logging.info(f"Ansible ({playbook_name}) terminé pour {client_user}.")
    return True

# --- Health check ---
def check_node_health(ip, port):
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=ip, port=port, username=WORKER_SSH_USER,
                       password=WORKER_SSH_PASS, timeout=SSH_TIMEOUT,
                       allow_agent=False, look_for_keys=False)
        return 'alive'
    except Exception:
        return 'dead'
    finally:
        if client:
            client.close()

def job_health_check():
    logging.info("[Tâche 1] Exécution du Health Check...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, ip, ssh_port FROM nodes")
        nodes = cursor.fetchall()
        update_cursor = conn.cursor()
        for node in nodes:
            ip_to_use = resolve_worker_ip(node['ip'])
            status = check_node_health(ip_to_use, node['ssh_port'])
            update_cursor.execute(
                "UPDATE nodes SET status=%s, last_checked=%s WHERE id=%s",
                (status, datetime.now(), node['id'])
            )
        logging.info(f"[Tâche 1] Health Check terminé. {len(nodes)} noeuds vérifiés.")
    except Exception as e:
        logging.error(f"[Tâche 1] Erreur Health Check: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- Migration des noeuds morts ---
def job_migrate_dead_nodes():
    logging.info("[Tâche 2] Vérification des migrations...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM nodes WHERE status='dead' AND allocated=TRUE FOR UPDATE")
        dead_nodes = cursor.fetchall()
        if not dead_nodes:
            conn.rollback()
            return
        for node in dead_nodes:
            logging.info(f"[Tâche 2] Migration pour noeud {node['id']}...")
            cursor.execute("SELECT id, ip, ssh_port FROM nodes WHERE status='alive' AND allocated=FALSE LIMIT 1 FOR UPDATE")
            new_node = cursor.fetchone()
            if not new_node:
                logging.error(f"[Tâche 2] Aucun noeud sain disponible pour migration de {node['id']}")
                conn.rollback()
                continue
            cursor.execute("SELECT id, user_id FROM rentals WHERE node_id=%s AND active=TRUE", (node['id'],))
            rental = cursor.fetchone()
            if not rental:
                logging.error(f"[Tâche 2] Pas de rental actif pour {node['id']}")
                conn.rollback()
                continue
            user_id = rental['user_id']
            client_user = f"user_{user_id}"
            ip_to_use = resolve_worker_ip(new_node['ip'])
            success = run_ansible_task('create_user.yml', ip_to_use, new_node['ssh_port'], client_user, WORKER_SSH_PASS)
            if not success:
                logging.error(f"[Tâche 2] Échec Ansible pour migration vers {new_node['id']}")
                conn.rollback()
                continue
            cursor.execute("UPDATE nodes SET allocated=FALSE, allocated_to=NULL WHERE id=%s", (node['id'],))
            cursor.execute("UPDATE nodes SET allocated=TRUE, allocated_to=%s WHERE id=%s", (client_user, new_node['id']))
            conn.commit()
            logging.info(f"[Tâche 2] Migration réussie: {client_user} -> {new_node['id']}")
    except Exception as e:
        logging.error(f"[Tâche 2] Erreur migration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- Expiration des baux ---
def job_expire_leases():
    logging.info("[Tâche 3] Vérification des baux expirés...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)
        sql = """
        SELECT n.id AS node_id, n.ip, n.ssh_port, r.id AS rental_id, r.user_id
        FROM nodes n
        JOIN rentals r ON r.node_id = n.id
        WHERE n.allocated=TRUE AND r.active=TRUE AND r.leased_until <= NOW()
        FOR UPDATE
        """
        cursor.execute(sql)
        expired = cursor.fetchall()
        for row in expired:
            client_user = f"user_{row['user_id']}"
            ip_to_use = resolve_worker_ip(row['ip'])
            logging.info(f"[Tâche 3] Expiration de {client_user} sur noeud {row['node_id']}...")
            success = run_ansible_task('delete_user.yml', ip_to_use, row['ssh_port'], client_user)
            if success:
                cursor.execute("UPDATE nodes SET allocated=FALSE, allocated_to=NULL WHERE id=%s", (row['node_id'],))
                cursor.execute("UPDATE rentals SET active=FALSE WHERE id=%s", (row['rental_id'],))
                logging.info(f"[Tâche 3] Noeud {row['node_id']} libéré et rental {row['rental_id']} clos.")
            else:
                logging.error(f"[Tâche 3] Échec nettoyage Ansible pour noeud {row['node_id']}")
        conn.commit()
    except Exception as e:
        logging.error(f"[Tâche 3] Erreur expiration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- Main loop ---
if __name__ == "__main__":
    logging.info("--- Démarrage du Scheduler Orion-Dynamic ---")
    schedule.every(30).seconds.do(job_health_check)
    schedule.every(10).seconds.do(job_migrate_dead_nodes)
    schedule.every(1).minute.do(job_expire_leases)
    job_health_check()  # première exécution
    while True:
        schedule.run_pending()
        time.sleep(1)
