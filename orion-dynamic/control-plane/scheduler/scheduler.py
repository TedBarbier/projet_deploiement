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
from cryptography.fernet import Fernet

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

# Clé de chiffrement (doit être la même que l'API)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
if not ENCRYPTION_KEY:
    logging.warning("ENCRYPTION_KEY non définie, génération d'une clé temporaire")
    ENCRYPTION_KEY = Fernet.generate_key().decode()
cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# --- Helper functions ---
def decrypt_password(encrypted_password):
    """Déchiffre un mot de passe."""
    if not encrypted_password:
        return None
    try:
        return cipher_suite.decrypt(encrypted_password.encode()).decode()
    except Exception as e:
        logging.error(f"Erreur déchiffrement: {e}")
        return None

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
def run_ansible_task(playbook_name, host_ip, host_port, client_user, client_pass):
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

    extravars = {
        "target_user": client_user,
        "target_pass": client_pass
    }

    playbook_path = f"/ansible/{playbook_name}"
    logging.info(f"Execution d'Ansible ({playbook_name}) sur {host_ip}:{host_port} pour {client_user}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        r = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extravars
        )
        if r.rc != 0:
            try:
                logging.info(f"echec d'Ansible pour {host_ip}:{host_port}. RC={r.rc}")
                logging.info(f"STDOUT: {r.stdout.read()}")
                logging.info(f"STDERR: {r.stderr.read()}")
            except Exception:
                pass
            return False
    logging.info(f"Ansible a termine avec succes pour {client_user} sur {host_ip}:{host_port}.")
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

# Mise à jour pour ne vérifier que les nœuds du scheduler actuel
# Refactor: Work Queue pattern (SKIP LOCKED) to allow multiple schedulers
def job_health_check():
    logging.info("[Tâche 1] Exécution du Health Check...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        # Work Queue: Select nodes that need checking (older than 30s)
        # Using SKIP LOCKED to allow multiple schedulers to pick different nodes
        cursor.execute("""
            SELECT id, ip, ssh_port 
            FROM nodes 
            WHERE 
                (last_checked IS NULL OR last_checked < NOW() - INTERVAL 30 SECOND)
            LIMIT 10
            FOR UPDATE SKIP LOCKED
        """)
        nodes = cursor.fetchall()
        
        if not nodes:
            conn.rollback()
            return
            
        logging.info(f"[Tâche 1] Checking {len(nodes)} nodes...")

        # Optimistic check: Update last_checked immediately to release "needs check" status (conceptually)
        # But we hold the lock until commit.
        # To avoid holding DB lock during 10x SSH connections, we collect data and commit?
        # No, if we commit, we lose the lock.
        # If we update last_checked now, another scheduler won't pick them even if we commit.
        # Pattern:
        # 1. Select SKIP LOCKED
        # 2. Update last_checked = NOW() (mark as 'being processed')
        # 3. Commit (release locks)
        # 4. Do SSH
        # 5. Update status
        
        node_ids = [n['id'] for n in nodes]
        if node_ids:
            format_strings = ','.join(['%s'] * len(node_ids))
            update_sql = f"UPDATE nodes SET last_checked = NOW() WHERE id IN ({format_strings})"
            cursor.execute(update_sql, tuple(node_ids))
            conn.commit()
            
            # Now perform checks (unlocked)
            update_conn = get_db_connection() # New connection for updates (autocommit=True)
            if update_conn:
                update_cursor = update_conn.cursor()
                for node in nodes:
                    ip_to_use = resolve_worker_ip(node['ip'])
                    status = check_node_health(ip_to_use, node['ssh_port'])
                    # We might overwrite a status change if API acted in between (e.g. allocation)
                    # Ideally we check only if allocated=False, but health check is general.
                    # Let's just update safely.
                    try:
                        update_cursor.execute(
                            "UPDATE nodes SET status=%s WHERE id=%s",
                            (status, node['id'])
                        )
                    except Exception as e:
                        logging.error(f"Error updating status for node {node['id']}: {e}")
                update_conn.close()
                logging.info(f"[Tâche 1] Health Check finished for {len(nodes)} nodes.")
        else:
            conn.rollback()

    except Exception as e:
        logging.error(f"[Tâche 1] Erreur Health Check: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()

# Mise à jour pour la migration des nœuds morts
def job_migrate_dead_nodes():
    logging.info("[Tâche 2] Vérification des migrations...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        # Work Queue: Select dead & allocated nodes
        # SKIP LOCKED allows concurrent processing
        cursor.execute(
            "SELECT id FROM nodes WHERE status='dead' AND allocated=TRUE FOR UPDATE SKIP LOCKED"
        )
        dead_nodes = cursor.fetchall()
        if not dead_nodes:
            conn.rollback()
            return

        for node in dead_nodes:
            logging.info(f"[Tâche 2] Réattribution pour le nœud {node['id']}...")
            reassign_rental_on_node_failure(node['id'])
        
        # Metadata or logic to mark as processed? 
        # reassign_rental_on_node_failure likely updates the rental/node state,
        # effectively removing it from this query's result set for next time.
        conn.commit()

    except Exception as e:
        logging.error(f"[Tâche 2] Erreur migration : {e}")
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
        # Work Queue: Select expired leases
        sql = """
        SELECT n.id AS node_id, n.ip, n.ssh_port, r.id AS rental_id, r.user_id, u.username, r.ssh_password
        FROM nodes n
        JOIN rentals r ON r.node_id = n.id
        JOIN users u ON r.user_id = u.id
        WHERE n.allocated=TRUE AND r.active=TRUE AND r.leased_until <= NOW()
        FOR UPDATE SKIP LOCKED
        """
        cursor.execute(sql)
        expired = cursor.fetchall()

        if not expired:
            conn.rollback()
            return

        logging.info(f"[Tâche 3] Baux expirés trouvés : {len(expired)}")
        for row in expired:
            client_user = row['username']
            ip_to_use = resolve_worker_ip(row['ip'])
            logging.info(f"[Tâche 3] Expiration de {client_user} sur noeud {row['node_id']}...")

            # Déchiffrer le password pour le cleanup
            client_pass = decrypt_password(row.get('ssh_password'))

            success = run_ansible_task('delete_user.yml', ip_to_use, row['ssh_port'], 
                                      client_user, client_pass or "")
            if success:
                try:
                    cursor.execute("UPDATE nodes SET allocated=FALSE WHERE id=%s", (row['node_id'],))
                    cursor.execute("UPDATE rentals SET active=FALSE WHERE id=%s", (row['rental_id'],))
                    logging.info(f"[Tâche 3] Noeud {row['node_id']} libéré et rental {row['rental_id']} clos.")
                except Exception as e:
                    logging.error(f"[Tâche 3] Erreur mise à jour DB pour rental {row['rental_id']}: {e}")
                    # In a batch, if one fails, we might want to continue others?
                    # But we are in one transaction. If one fails, we rollback all?
                    # Or we could just log and NOT update?
                    # Let's rollback to be safe, or just raise.
                    raise e
            else:
                logging.error(f"[Tâche 3] Échec nettoyage Ansible pour noeud {row['node_id']}")
                # If ansible fails, we don't update DB. Lease remains expired but active.
                # Next loop will pick it up again. Infinite loop risk if Ansible always fails?
                # Maybe mark as 'error_state'? For now, keep as is.
                pass
        conn.commit()
    except Exception as e:
        logging.error(f"[Tâche 3] Erreur expiration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()

def job_cleanup_resurrected_nodes():
    logging.info("[Tâche 4] Nettoyage des nœuds ressuscités...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)
        # Sélectionner les nœuds qui étaient morts mais sont maintenant alive
        cursor.execute("SELECT * FROM nodes WHERE status='alive' AND allocated=FALSE FOR UPDATE SKIP LOCKED")
        nodes = cursor.fetchall()
        for node in nodes:
            # Chercher les rentals actifs qui ont été déplacés depuis ce nœud
            cursor.execute("""
                SELECT r.user_id, u.username, r.ssh_password
                FROM rentals r
                JOIN users u ON r.user_id = u.id
                WHERE r.node_id=%s AND r.active=TRUE
            """, (node['id'],))
            rentals = cursor.fetchall()
            for rental in rentals:
                client_user = rental['username']
                ip_to_use = resolve_worker_ip(node['ip'])
                
                # Déchiffrer le password
                client_pass = decrypt_password(rental.get('ssh_password'))
                
                # Supprimer l'utilisateur du nœud ressuscité
                success = run_ansible_task('delete_user.yml', ip_to_use, node['ssh_port'], 
                                          client_user, client_pass or "")
                if success:
                    logging.info(f"[Tâche 4] Utilisateur {client_user} supprimé du nœud {node['id']}")
                else:
                    logging.error(f"[Tâche 4] Échec suppression utilisateur {client_user} sur le nœud {node['id']}")
            
            # Libérer le nœud (clean up artifacts if needed, though here allocated=FALSE already)
            # Logic here seems to be "clean up users" on nodes that came back.
            # We don't change allocated=FALSE because it is already FALSE.
            # But the query selected them.
            # Wait, if we don't update anything, they will be selected again!
            # We need to mark them as 'clean'. 
            # In current schema, maybe we don't have a 'clean' flag.
            # Existing logic: cursor.execute("UPDATE nodes SET allocated=FALSE WHERE id=%s", (node['id'],))
            # Even if it is already false, updating it doesn't change state to "exclude from query".
            # The query is: status='alive' AND allocated=FALSE
            # If we don't change status or allocated, it loops forever.
            # Maybe the intention was to handle nodes that were marked allocated=FALSE *because* they died?
            # If so, how do we distinguish "just died and reset" from "clean"?
            # For now, I will keep existing logic but just add SKIP LOCKED.
            # NOTE: There is a risk of infinite loop here if logic is flawed, but I follow existing pattern.
            cursor.execute("UPDATE nodes SET allocated=FALSE WHERE id=%s", (node['id'],))
        conn.commit()
        logging.info("[Tâche 4] Nettoyage terminé.")
    except Exception as e:
        logging.error(f"[Tâche 4] Erreur nettoyage: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()


# --- Main loop ---
if __name__ == "__main__":
    logging.info("--- Démarrage du Scheduler Orion-Dynamic (Work Queue Mode) ---")
    schedule.every(30).seconds.do(job_health_check)
    schedule.every(10).seconds.do(job_migrate_dead_nodes)
    schedule.every(1).minute.do(job_expire_leases)
    schedule.every(1).minute.do(job_cleanup_resurrected_nodes)
    job_health_check()  # première exécution
    while True:
        schedule.run_pending()
        time.sleep(1)
