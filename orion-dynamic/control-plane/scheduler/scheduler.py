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

# --- Configuration du Logging ---
logging.basicConfig(level=logging.INFO, format='[SCHEDULER] %(asctime)s: %(message)s')

# --- Lecture des variables d'environnement ---
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

WORKER_SSH_USER = os.getenv('WORKER_SSH_USER', 'root')
WORKER_SSH_PASS = os.getenv('WORKER_SSH_PASS', 'password')
SSH_TIMEOUT = 5 # Secondes

# --- Helper 1: Connexion à la Base de Données ---
def get_db_connection():
    """Crée et retourne une nouvelle connexion à la DB."""
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=True # Plus simple pour les updates unitaires
        )
        return conn
    except mysql.connector.Error as err:
        logging.error(f"Erreur de connexion à la DB: {err}")
        return None

# --- Helper 2: Exécution d'Ansible (Migration & Nettoyage) ---
def run_ansible_task(playbook_name, host_ip, host_port, client_user, client_pass=None):
    """
    Exécute un playbook Ansible pour provisionner (migration) ou
    dé-provisionner (expiration) un utilisateur.
    """
    
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
    
    # Variables à passer au playbook
    extravars = {
        "target_user": client_user
    }
    
    # Uniquement pour create_user (migration)
    if client_pass:
        extravars["target_pass"] = client_pass

    playbook_path = f"/ansible/{playbook_name}"
    
    logging.info(f"Exécution d'Ansible ({playbook_name}) sur {host_ip}:{host_port} pour l'utilisateur {client_user}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        r = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extravars
        )
        
        if r.rc != 0:
            logging.error(f"Échec d'Ansible ({playbook_name}) pour {host_ip}:{host_port}. RC={r.rc}")
            return False
            
    logging.info(f"Ansible ({playbook_name}) a terminé avec succès pour {client_user}.")
    return True

# --- Helper 3: Health Check SSH (Paramiko) ---
def check_node_health(hostname, port):
    """Tente une connexion SSH rapide pour valider la santé."""
    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=hostname,
            port=port,
            username=WORKER_SSH_USER,
            password=WORKER_SSH_PASS,
            timeout=SSH_TIMEOUT,
            allow_agent=False,
            look_for_keys=False
        )
        return 'alive'
    except Exception as e:
        # logging.warning(f"Health check échec pour {hostname}:{port} - {e}")
        return 'dead'
    finally:
        if client:
            client.close()

# --- TÂCHE 1: Health Check (Toutes les 30s) ---
def job_health_check():
    """
    Itère sur TOUS les noeuds de la DB et met à jour leur statut.
    """
    logging.info("[Tâche 1] Exécution du Health Check...")
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return

        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, hostname, ssh_port FROM nodes")
        nodes = cursor.fetchall()
        
        update_cursor = conn.cursor()
        
        for node in nodes:
            status = check_node_health(node['hostname'], node['ssh_port'])
            
            # Met à jour le statut et l'heure de vérification
            sql_update = "UPDATE nodes SET status = %s, last_checked = %s WHERE id = %s"
            update_cursor.execute(sql_update, (status, datetime.now(), node['id']))
            
        logging.info(f"[Tâche 1] Health Check terminé. {len(nodes)} noeuds vérifiés.")

    except Exception as e:
        logging.error(f"[Tâche 1] Erreur lors du Health Check: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- TÂCHE 2: Migration (Toutes les 10s) ---
def job_migrate_dead_nodes():
    """
    Cherche les noeuds alloués mais morts et migre le client.
    """
    logging.info("[Tâche 2] Vérification des migrations...")
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return
        
        # On utilise une transaction car une migration implique 3 updates
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True)
        
        # 1. Trouver les clients en panne
        sql_find_dead = "SELECT * FROM nodes WHERE status = 'dead' AND allocated = true FOR UPDATE"
        cursor.execute(sql_find_dead)
        dead_leases = cursor.fetchall()

        if not dead_leases:
            conn.rollback() # Annule le "FOR UPDATE"
            return # Rien à faire

        logging.warning(f"[Tâche 2] {len(dead_leases)} location(s) en panne détectée(s) !")

        for lease in dead_leases:
            logging.info(f"[Tâche 2] Migration de {lease['allocated_to']} (depuis noeud {lease['id']})...")
            
            # 2. Trouver un nouveau noeud sain
            sql_find_new = """
                SELECT id, hostname, ssh_port FROM nodes
                WHERE status = 'alive' AND allocated = false AND id != %s
                LIMIT 1
                FOR UPDATE
            """
            cursor.execute(sql_find_new, (lease['id'],))
            new_node = cursor.fetchone()

            if not new_node:
                logging.error(f"[Tâche 2] ÉCHEC MIGRATION: Aucun noeud sain disponible pour {lease['allocated_to']} !")
                conn.rollback() # Annule tout pour ce client, réessaiera plus tard
                continue # Passe au client suivant

            logging.info(f"[Tâche 2] Nouveau noeud {new_node['id']} trouvé pour {lease['allocated_to']}.")

            # 3. Provisionner le nouveau noeud (Ansible)
            # On doit récupérer le mot de passe (ou en générer un nouveau si non stocké)
            # Pour ce labo, on suppose que client_id EST l'utilisateur et on utilise un mot de passe connu ou stocké
            # Ici, on triche : on utilise le mot de passe root, car on ne stocke pas le mdp client
            # En production: on utiliserait des clés SSH ou un service type Vault
            # Allez, pour le labo, on va juste utiliser le mot de passe root comme mdp client :)
            client_pass_migration = WORKER_SSH_PASS 
            
            success = run_ansible_task(
                playbook_name='create_user.yml',
                host_ip=new_node['hostname'],
                host_port=new_node['ssh_port'],
                client_user=lease['allocated_to'],
                client_pass=client_pass_migration # On réutilise le mdp root
            )
            
            if not success:
                logging.error(f"[Tâche 2] ÉCHEC MIGRATION: Ansible a échoué sur {new_node['id']}. Rollback.")
                conn.rollback()
                continue

            # 4. Mettre à jour la DB
            # 4a. Libérer l'ancien noeud (mort)
            sql_free_old = "UPDATE nodes SET allocated = false, allocated_to = NULL, lease_end_at = NULL WHERE id = %s"
            cursor.execute(sql_free_old, (lease['id'],))
            
            # 4b. Allouer le nouveau noeud (en gardant la fin du bail d'origine)
            sql_alloc_new = "UPDATE nodes SET allocated = true, allocated_to = %s, lease_end_at = %s WHERE id = %s"
            cursor.execute(sql_alloc_new, (lease['allocated_to'], lease['lease_end_at'], new_node['id']))

            conn.commit()
            logging.info(f"[Tâche 2] MIGRATION RÉUSSIE: {lease['allocated_to']} est sur {new_node['id']}.")

    except Exception as e:
        logging.error(f"[Tâche 2] Erreur lors de la migration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- TÂCHE 3: Expiration des Baux (Toutes les minutes) ---
def job_expire_leases():
    """
    Cherche les baux expirés, lance le nettoyage (Ansible) et libère le noeud.
    """
    logging.info("[Tâche 3] Vérification des baux expirés...")
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return

        cursor = conn.cursor(dictionary=True)
        
        # 1. Trouver les baux expirés
        sql_find_expired = "SELECT * FROM nodes WHERE allocated = true AND lease_end_at <= NOW()"
        cursor.execute(sql_find_expired)
        expired_leases = cursor.fetchall()

        if not expired_leases:
            return # Rien à faire
        
        logging.info(f"[Tâche 3] {len(expired_leases)} bail(baux) expiré(s) trouvé(s).")
        
        for lease in expired_leases:
            logging.info(f"[Tâche 3] Expiration de {lease['allocated_to']} sur noeud {lease['id']}...")

            # 2. Nettoyer le noeud (Ansible delete_user)
            success = run_ansible_task(
                playbook_name='delete_user.yml',
                host_ip=lease['hostname'],
                host_port=lease['ssh_port'],
                client_user=lease['allocated_to']
            )
            
            if not success:
                logging.error(f"[Tâche 3] ÉCHEC NETTOYAGE: Ansible a échoué sur {lease['id']}. Le noeud reste alloué.")
                # On ne libère pas le noeud, on réessaiera
                continue 
            
            # 3. Libérer le noeud dans la DB
            sql_free = "UPDATE nodes SET allocated = false, allocated_to = NULL, lease_end_at = NULL WHERE id = %s"
            cursor.execute(sql_free, (lease['id'],))
            logging.info(f"[Tâche 3] Nettoyage réussi. Noeud {lease['id']} est maintenant libre.")

    except Exception as e:
        logging.error(f"[Tâche 3] Erreur lors de l'expiration: {e}")
    finally:
        if conn and conn.is_connected():
            conn.close()

# --- Boucle Principale du Scheduler ---
if __name__ == "__main__":
    logging.info("--- Démarrage du Scheduler Orion-Dynamic ---")
    
    # Définir les intervalles
    schedule.every(30).seconds.do(job_health_check)
    schedule.every(10).seconds.do(job_migrate_dead_nodes)
    schedule.every(1).minute.do(job_expire_leases)
    
    # Exécuter une fois au démarrage pour peupler le statut
    logging.info("Exécution du premier Health Check au démarrage...")
    job_health_check()

    while True:
        schedule.run_pending()
        time.sleep(1)