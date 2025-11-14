import os
import logging
import tempfile
import secrets
import string
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import mysql.connector
from mysql.connector import errorcode
import ansible_runner

# --- Configuration du Logging ---
# On utilise le logger de Flask au lieu de basicConfig
gunicorn_logger = logging.getLogger('gunicorn.error')
app = Flask(__name__)
app.logger.handlers = gunicorn_logger.handlers
app.logger.setLevel(gunicorn_logger.level)

# --- Lecture des variables d'environnement ---
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')

WORKER_SSH_USER = os.getenv('WORKER_SSH_USER', 'root')
WORKER_SSH_PASS = os.getenv('WORKER_SSH_PASS', 'password')

# --- Helper 1: Connexion à la Base de Données ---
def get_db_connection():
    """Crée et retourne une nouvelle connexion à la DB."""
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME
        )
        return conn
    except mysql.connector.Error as err:
        app.logger.error(f"Erreur de connexion à la DB: {err}")
        return None

# --- Helper 2: Exécution d'Ansible ---
def run_ansible_provision(playbook_name, host_ip, host_port, client_user, client_pass):
    """Exécute un playbook Ansible pour provisionner un utilisateur."""
    
    # Inventaire "en mémoire" pour Ansible
    inventory = {
        'all': {
            'hosts': {
                'target_node': { # Nom logique
                    'ansible_host': host_ip,
                    'ansible_port': host_port,
                    'ansible_user': WORKER_SSH_USER,
                    'ansible_password': WORKER_SSH_PASS,
                    'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
                }
            }
        }
    }
    
    # Variables à passer au playbook (create_user.yml)
    extravars = {
        "target_user": client_user,
        "target_pass": client_pass
    }
    
    playbook_path = f"/ansible/{playbook_name}"
    
    app.logger.info(f"Exécution d'Ansible ({playbook_name}) sur {host_ip}:{host_port} pour l'utilisateur {client_user}...")

    # Utilise un répertoire temporaire pour les artefacts d'Ansible
    with tempfile.TemporaryDirectory() as tmpdir:
        r = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extravars
        )
        
        if r.rc != 0:
            app.logger.error(f"Échec d'Ansible pour {host_ip}:{host_port}. RC={r.rc}")
            app.logger.error(f"STDOUT: {r.stdout.read()}")
            app.logger.error(f"STDERR: {r.stderr.read()}")
            return False
            
    app.logger.info(f"Ansible a terminé avec succès pour {client_user}.")
    return True

# --- Endpoint 0: Health Check (pour Caddy) --- (NOUVEAU)
@app.route('/api/health', methods=['GET'])
def health_check():
    """Endpoint de santé simple pour le reverse proxy."""
    # On pourrait ajouter un check de la DB ici, mais gardons simple
    return jsonify({"status": "healthy"}), 200

# --- Endpoint 1: Enregistrement des Workers (pour les Agents) ---
@app.route('/api/workers/register', methods=['POST'])
def register_worker():
    """
    Appelé par les agents 'agent.py' au démarrage.
    Enregistre un worker dans la base de données.
    """
    data = request.get_json()
    if not data or 'hostname' not in data or 'ssh_port' not in data:
        return jsonify({"error": "Données JSON manquantes: 'hostname' et 'ssh_port' requis"}), 400

    hostname = data['hostname']
    ssh_port = data['ssh_port']
    
    # On insère le noeud avec le statut 'unknown'.
    # Le Scheduler (Tâche 1) devra le valider.
    sql = "INSERT INTO nodes (hostname, ssh_port, status) VALUES (%s, %s, 'unknown')"
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Connexion à la base de données impossible"}), 500
            
        cursor = conn.cursor()
        cursor.execute(sql, (hostname, ssh_port))
        conn.commit()
        
        app.logger.info(f"Nouveau worker enregistré : {hostname}:{ssh_port}")
        return jsonify({"message": "Worker enregistré avec succès"}), 201 # 201 Created

    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_DUP_ENTRY:
            # Si le worker existe déjà, c'est OK (l'agent a peut-être redémarré)
            app.logger.warning(f"Worker {hostname}:{ssh_port} déjà enregistré (Conflit).")
            return jsonify({"message": "Worker déjà enregistré"}), 409 # 409 Conflict
        else:
            app.logger.error(f"Erreur DB lors de l'enregistrement: {err}")
            if conn:
                conn.rollback()
            return jsonify({"error": f"Erreur base de données: {err}"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

# --- Endpoint 2: Location de Nœud (pour les Clients) ---
@app.route('/api/rent', methods=['POST'])
def rent_node():
    """
    Appelé par les clients pour louer un noeud.
    Doit être transactionnel pour éviter les "race conditions".
    """
    # --- VRAIE LOGIQUE (le test est fini) ---
    app.logger.info("!!!!!!!!!! Requête /api/rent reçue !!!!!!!!!!")
    
    data = request.get_json()
    try:
        client_id = data['client_id']
        duration_hours = int(data['duration_hours'])
    except Exception:
        return jsonify({"error": "Données JSON manquantes: 'client_id' et 'duration_hours' requis"}), 400

    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Connexion à la base de données impossible"}), 500
        
        # --- Début de la TRANSACTION ---
        conn.start_transaction()
        cursor = conn.cursor(dictionary=True) # dictionary=True pour get les résultats en dict

        # 1. Trouver et VERROUILLER un nœud disponible
        sql_find = """
            SELECT * FROM nodes
            WHERE status = 'alive' AND allocated = false
            ORDER BY last_checked DESC
            LIMIT 1
            FOR UPDATE
        """
        cursor.execute(sql_find)
        node = cursor.fetchone()

        if not node:
            # Aucun nœud disponible
            app.logger.warning("Aucun worker 'alive' et 'disponible' trouvé pour une location.")
            conn.rollback()
            return jsonify({"error": "Aucun service disponible (pas de worker libre)"}), 503

        app.logger.info(f"Nœud {node['id']} ({node['hostname']}:{node['ssh_port']}) trouvé pour {client_id}.")

        # 2. Calculer le bail et mettre à jour le nœud
        lease_end = datetime.now() + timedelta(hours=duration_hours)
        sql_update = """
            UPDATE nodes
            SET allocated = true, allocated_to = %s, lease_end_at = %s
            WHERE id = %s
        """
        cursor.execute(sql_update, (client_id, lease_end, node['id']))
        
        # 3. Provisionner le nœud (Ansible)
        alphabet = string.ascii_letters + string.digits
        client_pass = ''.join(secrets.choice(alphabet) for i in range(16))
        
        success = run_ansible_provision(
            playbook_name='create_user.yml',
            host_ip=node['hostname'],
            host_port=node['ssh_port'],
            client_user=client_id,
            client_pass=client_pass
        )

        if not success:
            # Si Ansible échoue, on annule TOUT
            app.logger.error(f"Échec du provisioning Ansible pour {node['id']}. Rollback.")
            conn.rollback()
            return jsonify({"error": "Échec du provisioning du worker"}), 500
        
        # 4. TOUT a réussi -> COMMIT
        conn.commit()
        
        app.logger.info(f"Location réussie. Nœud {node['id']} alloué à {client_id} jusqu'à {lease_end}.")
        
        # 5. Renvoyer les détails de connexion au client
        return jsonify({
            "message": "Nœud alloué avec succès",
            "node_id": node['id'],
            "connect_host": node['hostname'],
            "connect_port": node['ssh_port'],
            "client_user": client_id,
            "client_pass": client_pass, # Ne pas faire ça en production, mais OK pour le labo
            "lease_end_at": lease_end.isoformat()
        }), 200

    except Exception as e:
        app.logger.error(f"Erreur interne lors de la location: {e}")
        if conn:
            conn.rollback() # Annuler en cas d'erreur
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

    

# --- BLOC DE DÉMARRAGE (LA CORRECTION) ---
if __name__ == "__main__":
    app.logger.info("--- Démarrage du serveur Flask en mode DEBUG ---")
    app.run(host='0.0.0.0', port=8080, debug=True)

@app.route('/api/reset', methods=['POST'])
def reset_database():
    """
    Vider les tables nodes et éventuellement les locations pour repartir à zéro.
    ATTENTION: uniquement pour tests, pas en production !
    """
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Connexion à la base de données impossible"}), 500

        cursor = conn.cursor()
        
        # Vider les tables
        cursor.execute("DELETE FROM nodes;")
        cursor.execute("ALTER TABLE nodes AUTO_INCREMENT = 1;")  # Reset ID
        conn.commit()
        
        app.logger.info("Base de données réinitialisée avec succès.")
        return jsonify({"message": "Base de données réinitialisée."}), 200

    except Exception as e:
        app.logger.error(f"Erreur lors de la réinitialisation: {e}")
        if conn:
            conn.rollback()
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
