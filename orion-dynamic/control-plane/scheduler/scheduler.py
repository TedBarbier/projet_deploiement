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
import socket
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
SSH_TIMEOUT = 5

# Clé de chiffrement (doit être la même que l'API)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
if not ENCRYPTION_KEY:
    logging.warning("ENCRYPTION_KEY non définie, génération d'une clé temporaire")
    ENCRYPTION_KEY = Fernet.generate_key().decode()
cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# -----------------------
# Helper functions
# -----------------------
def decrypt_password(encrypted_password):
    """Déchiffre un mot de passe."""
    if not encrypted_password:
        return None
    try:
        return cipher_suite.decrypt(encrypted_password.encode()).decode()
    except Exception as e:
        logging.error(f"Erreur déchiffrement: {e}")
        return None

# -----------------------
# DB connection
# -----------------------
def get_db_connection(autocommit=False):
    try:
        return mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=autocommit
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
def check_socket(ip, port):
    try:
        if ip == 'host.docker.internal':
            # Resolve manually to be sure? Not needed if socket does it.
            pass
        sock = socket.create_connection((ip, port), timeout=SSH_TIMEOUT)
        sock.close()
        return True
    except Exception as e:
        # logging.warning(f"Socket connection failed for {ip}:{port} : {e}")
        return False

def check_node_health(ip, port):
    # Pre-check TCP socket before Paramiko
    if not check_socket(ip, port):
        return 'dead'

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
    conn = get_db_connection(autocommit=True) # Explicit autocommit
    if not conn:
        return
    try:
        # We need a transaction for SELECT ... FOR UPDATE
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)

        # Work Queue: Select nodes that need checking (older than 30s)
        # Using SKIP LOCKED to allow multiple schedulers to pick different nodes
        cursor.execute("""
            SELECT id, ip, ssh_port 
            FROM nodes 
            WHERE 
                (last_checked IS NULL OR last_checked < NOW() - INTERVAL 5 SECOND)
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
            # Use a NEW connection for updates to ensure they are committed independently
            update_conn = get_db_connection(autocommit=True)
            if update_conn:
                update_cursor = update_conn.cursor()
                for node in nodes:
                    ip_to_use = resolve_worker_ip(node['ip'])
                    status = check_node_health(ip_to_use, node['ssh_port'])
                    logging.info(f"Node {node['id']} ({node['ip']} -> {ip_to_use}) -> {status}")
                    
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
            try:
                reassign_rental_on_node_failure(node['id'], cursor)
                conn.commit()
            except Exception as e:
                logging.error(f"Failed to migrate node {node['id']}: {e}")
                conn.rollback()
        
        # Metadata or logic to mark as processed? 
        # reassign_rental_on_node_failure likely updates the rental/node state,
        # effectively removing it from this query's result set for next time.
        # conn.commit() # This commit is now handled inside the loop for each node.

    except Exception as e:
        logging.error(f"[Tâche 2] Erreur migration : {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()



def reassign_rental_on_node_failure(dead_node_id, cursor):
    """
    Déplace les locations actives du nœud mort vers un autre nœud.
    Marque le nœud mort comme "dirty" (needs_cleanup=TRUE).
    Met à jour la DB via le cursor fourni (faisant partie de la transaction appelante).
    """
    logging.info(f"[Tâche 2] Réattribution pour le nœud {dead_node_id}...")
    
    try:
        # 1. Identifier les locations actives sur ce nœud (Lock row if needed, but we hold Node lock)
        # Using the passed cursor (reusing transaction/connection)

        cursor.execute("SELECT * FROM rentals WHERE node_id=%s AND active=TRUE FOR UPDATE", (dead_node_id,))
        affected_rentals = cursor.fetchall()
        
        if not affected_rentals:
            # Pas de locations actives, on marque juste le nœud comme non alloué mais toujours mort
            # (Le health check le passera en alive s'il revient)
            # Pas de locations actives, on marque juste le nœud comme non alloué mais dirty
            # (Le health check le passera en alive s'il revient, mais il devra être nettoyé)
            cursor.execute("UPDATE nodes SET allocated=FALSE, needs_cleanup=TRUE WHERE id=%s", (dead_node_id,))
            # conn.commit() # Caller will commit
            return

        logging.info(f"Migration de {len(affected_rentals)} locations depuis le nœud {dead_node_id}...")
        
        # 2. Trouver des nœuds de remplacement
        # On cherche autant de nœuds que nécessaire
        needed = len(affected_rentals)
        cursor.execute(f"""
            SELECT * FROM nodes 
            WHERE status='alive' AND allocated=FALSE AND id != %s
            LIMIT {needed}
            FOR UPDATE SKIP LOCKED
        """, (dead_node_id,))
        replacements = cursor.fetchall()
        logging.info(f"Trouvé {len(replacements)} nœuds de remplacement (besoin: {needed})")

        if len(replacements) < needed:
            logging.error(f"Pas assez de nœuds libres pour migrer tout le monde (besoin: {needed}, dispo: {len(replacements)})")
            # On migre ce qu'on peut ? Ou on fail tout ?
            # Pour l'instant, on migre ce qu'on peut.
        
        # 3. Effectuer la migration
        for i, rental in enumerate(affected_rentals):
            if i >= len(replacements):
                logging.error(f"Impossible de migrer la location {rental['id']} (user {rental['user_id']}): plus de nœud dispo.")
                continue

            new_node = replacements[i]
            
            # Option A: On met à jour la location existante (simple)
            # MAIS on perd la trace que l'user était sur l'ancien nœud pour le cleanup !
            # Option B: On ferme l'ancienne location (active=FALSE) et on en crée une nouvelle.
            # C'est mieux pour l'historique et pour le cleanup du nœud mort.
            
            # Fermer l'ancienne location
            # On note qu'elle a été interrompue/migrée ? Active=FALSE suffit.
            cursor.execute("UPDATE rentals SET active=FALSE WHERE id=%s", (rental['id'],))
            
            # Créer la nouvelle location
            # On garde les mêmes infos (user, dates, password)
            sql_insert = """
                INSERT INTO rentals (node_id, user_id, leased_from, leased_until, active, ssh_password)
                VALUES (%s, %s, %s, %s, TRUE, %s)
            """
            cursor.execute(sql_insert, (
                new_node['id'], 
                rental['user_id'], 
                rental['leased_from'], 
                rental['leased_until'], 
                rental['ssh_password']
            ))
            new_rental_id = cursor.lastrowid
            
            # Marquer le nouveau nœud comme alloué
            cursor.execute("UPDATE nodes SET allocated=TRUE WHERE id=%s", (new_node['id'],))
            
            # Provisioning du nouveau nœud
            # On doit le faire ici (ou via une queue). On le fait synchrone (bloquant mais simple).
            # Mais c'est critique.
            # Récupérer username
            cursor.execute("SELECT username FROM users WHERE id=%s", (rental['user_id'],))
            user_row = cursor.fetchone()
            client_user = user_row['username']
            
            ip_to_use = resolve_worker_ip(new_node['ip'])
            client_pass = decrypt_password(rental['ssh_password'])
            
            # Lancer Ansible en background ou synchrone ? 
            # Le scheduler est monothread ici, ça va bloquer.
            success = run_ansible_task('create_user.yml', ip_to_use, new_node['ssh_port'], client_user, client_pass)
            if success:
                logging.info(f"Migration réussie: Rental {rental['id']} -> Nouveau Rental {new_rental_id} sur Node {new_node['id']}")
            else:
                logging.error(f"Migration partielle: Provisioning échoué sur le nouveau nœud {new_node['id']}")
                # On laisse quand même le rental actif ? On risque d'avoir un user sans accès.
        
        # 4. Marquer l'ancien nœud comme non alloué et dirty
        cursor.execute("UPDATE nodes SET allocated=FALSE, needs_cleanup=TRUE WHERE id=%s", (dead_node_id,))
        
        # conn.commit() # Caller will commit

    except Exception as e:
        logging.error(f"Erreur reassign_rental_on_node_failure: {e}")
        # La transaction sera rollback par l'appelant
        raise e
    # finally: # No connection to close here, it's managed by the caller
    #     if conn and conn.is_connected():
    #         conn.close()


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
    logging.info("[Tâche 4] Nettoyage des nœuds ressuscités (dirty)...")
    conn = get_db_connection()
    if not conn:
        return
    try:
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)
        # Sélectionner les nœuds 'alive' mail marqués comme 'dirty' (needs_cleanup)
        cursor.execute("SELECT * FROM nodes WHERE status='alive' AND needs_cleanup=TRUE FOR UPDATE SKIP LOCKED")
        nodes = cursor.fetchall()
        
        for node in nodes:
            logging.info(f"[Tâche 4] Traitement du nœud dirty {node['id']} ({node['hostname']})...")
            
            # Chercher TOUS les utilisateurs distincts ayant eu une location sur ce nœud
            # On veut nettoyer tout historique potentiel.
            cursor.execute("""
                SELECT DISTINCT u.username, r.ssh_password
                FROM rentals r
                JOIN users u ON r.user_id = u.id
                WHERE r.node_id=%s
            """, (node['id'],))
            rentals = cursor.fetchall()
            
            node_cleanup_success = True
            
            if not rentals:
                 logging.info(f"[Tâche 4] Aucun utilisateur trouvé dans l'historique pour le nœud {node['id']}. Marquage comme clean.")
            
            for rental in rentals:
                client_user = rental['username']
                ip_to_use = resolve_worker_ip(node['ip'])
                
                # Déchiffrer le password (s'il y en a plusieurs, on prend celui du tuple courant)
                # Note: Si un user a eu plusieurs passwords, le playbook forcera la suppression de toute façon par username.
                client_pass = decrypt_password(rental.get('ssh_password'))
                
                # Supprimer l'utilisateur du nœud ressuscité
                success = run_ansible_task('delete_user.yml', ip_to_use, node['ssh_port'], 
                                          client_user, client_pass or "")
                if success:
                    logging.info(f"[Tâche 4] Utilisateur {client_user} supprimé du nœud {node['id']}")
                else:
                    logging.error(f"[Tâche 4] Échec suppression utilisateur {client_user} sur le nœud {node['id']}")
                    node_cleanup_success = False
            
            # Si tout s'est bien passé (ou s'il n'y avait rien à faire), on marque le nœud comme propre
            if node_cleanup_success:
                cursor.execute("UPDATE nodes SET needs_cleanup=FALSE WHERE id=%s", (node['id'],))
                logging.info(f"[Tâche 4] Nœud {node['id']} nettoyé et marqué comme CLEAN (disponible).")
            else:
                logging.warning(f"[Tâche 4] Nœud {node['id']} toujours DIRTY suite à des erreurs.")

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

# --- Main loop ---
def main():
    logging.info("--- Démarrage du Scheduler Orion-Dynamic (Work Queue Mode) ---")
    schedule.every(2).seconds.do(job_health_check)
    schedule.every(2).seconds.do(job_migrate_dead_nodes)
    schedule.every(10).seconds.do(job_expire_leases)
    schedule.every(2).seconds.do(job_cleanup_resurrected_nodes)
    job_health_check()  # première exécution
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Erreur dans la boucle principale: {e}")
        time.sleep(1)

if __name__ == "__main__":
    main()
