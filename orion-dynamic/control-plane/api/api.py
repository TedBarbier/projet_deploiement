#!/usr/bin/env python3
import os
import logging
import tempfile
import secrets
import string
import jwt
import bcrypt
from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone
import mysql.connector
from mysql.connector import errorcode
import ansible_runner
from functools import wraps
from cryptography.fernet import Fernet
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from marshmallow import Schema, fields, ValidationError

# -----------------------
# Logging & Flask
# -----------------------
gunicorn_logger = logging.getLogger('gunicorn.error')
app = Flask(__name__)
app.logger.handlers = gunicorn_logger.handlers
app.logger.setLevel(gunicorn_logger.level)

# -----------------------
# Config (env)
# -----------------------
DB_HOST = os.getenv('DB_HOST', 'db')
DB_USER = os.getenv('DB_USER', 'orion')
DB_PASS = os.getenv('DB_PASSWORD', 'orionpass')
DB_NAME = os.getenv('DB_NAME', 'orion_db')

WORKER_SSH_USER = os.getenv('WORKER_SSH_USER', 'root')
WORKER_SSH_PASS = os.getenv('WORKER_SSH_PASS', 'password')

JWT_SECRET = os.getenv('JWT_SECRET', 'change_me_in_prod')
JWT_EXPIRE_SECONDS = int(os.getenv('JWT_EXPIRE_SECONDS', '3600'))  # 1h default

# Clé de chiffrement pour les passwords SSH
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
if not ENCRYPTION_KEY:
    app.logger.warning("ENCRYPTION_KEY non définie, génération d'une clé temporaire (ne pas utiliser en prod !)")
    ENCRYPTION_KEY = Fernet.generate_key().decode()
cipher_suite = Fernet(ENCRYPTION_KEY.encode())

# -----------------------
# Password encryption helpers
# -----------------------
def encrypt_password(password):
    """Chiffre un mot de passe."""
    if not password:
        return None
    return cipher_suite.encrypt(password.encode()).decode()

def decrypt_password(encrypted_password):
    """Déchiffre un mot de passe."""
    if not encrypted_password:
        return None
    return cipher_suite.decrypt(encrypted_password.encode()).decode()

# -----------------------
# DB helper
# -----------------------
def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            autocommit=False
        )
        return conn
    except mysql.connector.Error as err:
        app.logger.error(f"Erreur de connexion a la DB: {err}")
        return None



# -----------------------
# Ansible runner (existing)
# -----------------------
def run_ansible_provision(playbook_name, host_ip, host_port, client_user, client_pass):
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
    app.logger.info(f"Execution d'Ansible ({playbook_name}) sur {host_ip}:{host_port} pour {client_user}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        r = ansible_runner.run(
            private_data_dir=tmpdir,
            playbook=playbook_path,
            inventory=inventory,
            extravars=extravars
        )
        if r.rc != 0:
            try:
                app.logger.error(f"echec d'Ansible pour {host_ip}:{host_port}. RC={r.rc}")
                app.logger.error(f"STDOUT: {r.stdout.read()}")
                app.logger.error(f"STDERR: {r.stderr.read()}")
            except Exception:
                pass
            return False
    app.logger.info(f"Ansible a termine avec succes pour {client_user} sur {host_ip}:{host_port}.")
    return True

# -----------------------
# Auth helpers / decorators
# -----------------------
def generate_jwt(user_id, username, role):
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=JWT_EXPIRE_SECONDS)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    # PyJWT v2 returns str, older returns bytes
    if isinstance(token, bytes):
        token = token.decode()
    return token

def decode_jwt(token):
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Token manquant ou mal forme"}), 401
        token = auth.split(" ", 1)[1]
        try:
            payload = decode_jwt(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expire"}), 401
        except Exception:
            return jsonify({"error": "Token invalide"}), 401

        # Attach user info to request
        request.user = {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
            "role": payload.get("role", "user")
        }
        return f(*args, **kwargs)
    return wrapper

def require_admin(f):
    @wraps(f)
    @require_auth
    def wrapper(*args, **kwargs):
        if request.user.get("role") != "admin":
            return jsonify({"error": "Acces admin requis"}), 403
        return f(*args, **kwargs)
    return wrapper

# -----------------------
# Utility DB helpers
# -----------------------
def get_user_by_username(conn, username):
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, username, password_hash, role FROM users WHERE username=%s", (username,))
    user = cur.fetchone()
    cur.close()
    return user

def get_user_by_id(conn, user_id):
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, username, role FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    cur.close()
    return user

# -----------------------
# Schemas de validation
# -----------------------
class SignupSchema(Schema):
    username = fields.Str(required=True, validate=lambda x: len(x) >= 3)
    password = fields.Str(required=True, validate=lambda x: len(x) >= 6)

class LoginSchema(Schema):
    username = fields.Str(required=True)
    password = fields.Str(required=True)

signup_schema = SignupSchema()
login_schema = LoginSchema()

# -----------------------
# Auth endpoints avec validation
# -----------------------
@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json() or {}
    try:
        validated_data = signup_schema.load(data)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400

    username = validated_data["username"]
    password = validated_data["password"]

    role = "user"  # on force toujours le rôle utilisateur pour la sécurité

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500

    try:
        cur = conn.cursor()
        # Vérifier si l'utilisateur existe déjà
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            return jsonify({"error": "Utilisateur déjà existant"}), 400

        # Hachage du mot de passe
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        # Insérer l'utilisateur avec le rôle forcé
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, pw_hash, role)
        )
        conn.commit()
        return jsonify({"message": "Compte créé avec succès"}), 201

    except Exception as e:
        app.logger.error(f"Erreur signup: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        cur.close()
        conn.close()

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    try:
        validated_data = login_schema.load(data)
    except ValidationError as err:
        return jsonify({"error": err.messages}), 400

    username = validated_data["username"]
    password = validated_data["password"]

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        user = get_user_by_username(conn, username)
        if not user:
            return jsonify({"error": "Utilisateur inconnu"}), 401
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return jsonify({"error": "Mot de passe incorrect"}), 401
        token = generate_jwt(user["id"], user["username"], user.get("role", "user"))
        return jsonify({"token": token}), 200
    except Exception as e:
        app.logger.error(f"Erreur login: {e}")
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        conn.close()

# -----------------------
# Core: rent multiple, release, extend, nodes
# -----------------------
@app.route("/rent", methods=["POST"])
@require_auth
def rent_nodes():
    """
    Body:
    {
      "duration_hours": 2,
      "count": 1,
      "ssh_password": "chosen_by_user"  # optional; if not provided API generates one per node
    }
    """
    import secrets, string
    from datetime import datetime, timedelta

    data = request.get_json() or {}
    client_name = request.user["username"]

    # Vérification de la durée
    try:
        duration_hours = int(data.get("duration_hours", 0))
        if duration_hours <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"error": "duration_hours invalide"}), 400

    # Vérification du nombre de noeuds
    try:
        count = int(data.get("count", 1))
        if count < 1:
            count = 1
    except Exception:
        count = 1

    ssh_password_given = data.get("ssh_password")  # optional

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500

    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)

        # Récupérer les noeuds libres
        cur.execute(f"""
            SELECT * FROM nodes
            WHERE status='alive' AND allocated=FALSE AND needs_cleanup=FALSE
            ORDER BY last_checked DESC
            LIMIT {count}
            FOR UPDATE
        """)
        nodes = cur.fetchall()

        if not nodes or len(nodes) < count:
            conn.rollback()
            return jsonify({"error": "Pas assez de workers libres", "found": len(nodes)}), 503

        allocated = []
        now = datetime.now(timezone.utc)
        lease_end = now + timedelta(hours=duration_hours)

        for node in nodes:
            node_id = node["id"]
            host_ip = node["ip"]
            port = node["ssh_port"]

            # Exception Docker : remplacer l'IP par host.docker.internal si IP interne Docker
            if host_ip.startswith("172.17."):
                host_ip = "host.docker.internal"

            client_pass = ssh_password_given or ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

            # Chiffrer le mot de passe avant stockage
            encrypted_pass = encrypt_password(client_pass)

            # Insert rental avec password chiffré
            insert_rental = """
                INSERT INTO rentals (node_id, user_id, leased_from, leased_until, active, ssh_password)
                VALUES (%s, %s, %s, %s, TRUE, %s)
            """
            cur.execute(insert_rental, (node_id, request.user["user_id"], now, lease_end, encrypted_pass))
            rental_id = cur.lastrowid
            cur.execute("UPDATE nodes SET allocated=TRUE WHERE id=%s", (node_id,))
            
            # Provisioning
            success = run_ansible_provision(
                playbook_name='create_user.yml',
                host_ip=host_ip,
                host_port=port,
                client_user=client_name,
                client_pass=client_pass
            )

            if not success:
                app.logger.error(f"Provisioning failed for node {node_id}, rolling back transaction")
                conn.rollback()
                return jsonify({"error": "Échec du provisioning; transaction annulée"}), 500

            allocated.append({
                "rental_id": rental_id,
                "host_ip": "host.docker.internal" if node["ip"].startswith("172.17.") else node["ip"],
                "ssh_port": port,
                "client_user": client_name,
                "client_pass": client_pass,
                "leased_until": lease_end.isoformat(),
            })

        conn.commit()
        return jsonify({"allocated": allocated}), 200

    except Exception as e:
        app.logger.error(f"Erreur interne rent_nodes: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        try:
            cur.close()
        except:
            pass
        conn.close()


@app.route("/release/<int:rental_id>", methods=["POST"])
@require_auth
def release_lease(rental_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        
        # 1. Récupérer le rental avec les infos du user
        cur.execute("""
            SELECT r.*, u.username, n.ip, n.hostname, n.ssh_port
            FROM rentals r
            JOIN users u ON r.user_id = u.id
            JOIN nodes n ON r.node_id = n.id
            WHERE r.id = %s
            FOR UPDATE
        """, (rental_id,))
        rental = cur.fetchone()
        
        if not rental:
            conn.rollback()
            return jsonify({"error": "Lease introuvable"}), 404

        # 2. Vérifier les permissions
        if request.user["role"] != "admin" and rental["user_id"] != request.user["user_id"]:
            conn.rollback()
            return jsonify({"error": "Pas la permission"}), 403

        # 3. Vérifier que le rental est encore actif
        if not rental["active"]:
            conn.rollback()
            return jsonify({"error": "Ce bail est déjà libéré"}), 400

        # 4. Déprovisionner via Ansible (best-effort)
        client_user = rental["username"]  # Le username du user
        host_ip = rental["ip"]
        if host_ip.startswith("172.17."):
            host_ip = "host.docker.internal"
        
        # Déchiffrer le password pour le cleanup (si stocké)
        client_pass = ""
        if rental.get("ssh_password"):
            try:
                client_pass = decrypt_password(rental["ssh_password"])
            except Exception as e:
                app.logger.warning(f"Impossible de déchiffrer le password: {e}")
        
        # Supprimer l'utilisateur via Ansible
        try:
            run_ansible_provision(
                'delete_user.yml', 
                host_ip, 
                rental["ssh_port"], 
                client_user, 
                client_pass  # Password déchiffré (ou vide si non disponible)
            )
        except Exception as e:
            app.logger.warning(f"Cleanup Ansible a échoué (non bloquant): {e}")

        # 5. Désactiver le rental et libérer le nœud
        cur.execute("UPDATE rentals SET active = FALSE WHERE id = %s", (rental_id,))
        cur.execute("UPDATE nodes SET allocated = FALSE WHERE id = %s", (rental["node_id"],))
        
        conn.commit()
        return jsonify({"message": "Lease libérée avec succès"}), 200
        
    except Exception as e:
        app.logger.error(f"Erreur release_lease: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        try:
            cur.close()
        except:
            pass
        conn.close()

@app.route("/extend/<int:rental_id>", methods=["POST"])
@require_auth
def extend_lease(rental_id):
    data = request.get_json() or {}
    try:
        add_hours = int(data.get("additional_hours", 0))
        if add_hours <= 0:
            return jsonify({"error": "additional_hours doit être > 0"}), 400
    except Exception:
        return jsonify({"error": "additional_hours invalide"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        
        # 1. Récupérer le rental
        cur.execute("SELECT * FROM rentals WHERE id=%s FOR UPDATE", (rental_id,))
        rental = cur.fetchone()
        
        if not rental:
            conn.rollback()
            return jsonify({"error": "Lease introuvable"}), 404

        # 2. Vérifier les permissions
        if request.user["role"] != "admin" and rental["user_id"] != request.user["user_id"]:
            conn.rollback()
            return jsonify({"error": "Pas la permission"}), 403

        # 3. Vérifier que le rental est actif
        if not rental["active"]:
            conn.rollback()
            return jsonify({"error": "Ce bail n'est pas actif"}), 400

        # 4. Calculer la nouvelle date de fin
        new_end = rental["leased_until"] + timedelta(hours=add_hours)
        
        # 5. Mettre à jour
        cur.execute("UPDATE rentals SET leased_until = %s WHERE id = %s", (new_end, rental_id))
        
        conn.commit()
        return jsonify({
            "message": "Bail prolongé avec succès",
            "rental_id": rental_id, 
            "new_leased_until": new_end.isoformat()
        }), 200
        
    except Exception as e:
        app.logger.error(f"Erreur extend_lease: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        try:
            cur.close()
        except:
            pass
        conn.close()

@app.route("/nodes", methods=["GET"])
@require_auth
def list_nodes():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        cur = conn.cursor(dictionary=True)

        if request.user["role"] == "admin":
            cur.execute("""
                SELECT n.id as node_id, n.hostname, n.ssh_port, n.status, n.allocated,
                       r.id as rental_id, r.user_id as rental_user_id, r.leased_from, r.leased_until, r.active,
                       u.username as renter_username
                FROM nodes n
                LEFT JOIN rentals r ON r.node_id = n.id AND r.active = TRUE
                LEFT JOIN users u ON r.user_id = u.id
            """)
        else:
            cur.execute("""
                SELECT n.id as node_id, n.hostname, n.ssh_port, n.status, n.allocated,
                       r.id as rental_id, r.user_id as rental_user_id, r.leased_from, r.leased_until, r.active
                FROM nodes n
                LEFT JOIN rentals r ON r.node_id = n.id AND r.active = TRUE
                WHERE r.user_id = %s
            """, (request.user["user_id"],))

        rows = cur.fetchall()
        cur.close()

        if not rows:
            return jsonify({"message": "Vous n'avez actuellement aucune location de node."}), 200

        nodes = {}
        for r in rows:
            nid = r["node_id"]
            if nid not in nodes:
                nodes[nid] = {
                    "node_id": nid,
                    "hostname": r["hostname"],
                    "ssh_port": r["ssh_port"],
                    "status": r["status"],
                    "allocated": bool(r["allocated"]),
                    "lease": None
                }

            if r.get("rental_id"):
                nodes[nid]["lease"] = {
                    "rental_id": r["rental_id"],
                    "user_id": r["rental_user_id"],
                    "renter_username": r.get("renter_username"),
                    "leased_from": r["leased_from"].isoformat() if r["leased_from"] else None,
                    "leased_until": r["leased_until"].isoformat() if r["leased_until"] else None,
                    "active": bool(r["active"]),
                }

        return jsonify(list(nodes.values())), 200

    except Exception as e:
        app.logger.error(f"Erreur list_nodes: {e}")
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        conn.close()

@app.route('/workers/register', methods=['POST'])
def register_worker():
    """
    Appelé par les agents 'agent.py' au démarrage.
    Enregistre un worker dans la base de données avec hostname, IP et port SSH.
    """
    data = request.get_json()
    if not data or 'hostname' not in data or 'ip' not in data or 'ssh_port' not in data:
        return jsonify({"error": "Données JSON manquantes: 'hostname', 'ip' et 'ssh_port' requis"}), 400

    hostname = data['hostname']
    ip = data['ip']
    ssh_port = data['ssh_port']
    

    sql = "INSERT INTO nodes (hostname, ip, ssh_port, status) VALUES (%s, %s, %s, 'unknown')"
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Connexion à la base de données impossible"}), 500

        cursor = conn.cursor()
        cursor.execute(sql, (hostname, ip, ssh_port))
        conn.commit()

        app.logger.info(f"Nouveau worker enregistré : {hostname} ({ip}):{ssh_port}")
        return jsonify({"message": "Worker enregistré avec succès"}), 201

    except mysql.connector.Error as err:
        if err.errno == errorcode.ER_DUP_ENTRY:
            app.logger.warning(f"Worker {hostname} ({ip}):{ssh_port} déjà enregistré.")
            return jsonify({"message": "Worker déjà enregistré"}), 409
        else:
            app.logger.error(f"Erreur DB lors de l'enregistrement: {err}")
            if conn:
                conn.rollback()
            return jsonify({"error": f"Erreur base de données: {err}"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

@app.route('/lease/<int:rental_id>/password', methods=['GET'])
@require_auth
def get_ssh_password(rental_id):

    # Récupérer l'utilisateur actuel
    current_user = request.user["user_id"]

    conn = get_db_connection()
    if not conn:
        app.logger.error("Connexion à la base de données impossible")
        return jsonify({"error": "DB non disponible"}), 500

    # Rechercher le lease correspondant dans la base de données
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT r.*, u.username, n.ip, n.hostname, n.ssh_port
            FROM rentals r
            JOIN users u ON r.user_id = u.id
            JOIN nodes n ON r.node_id = n.id
            WHERE r.id = %s
        """, (rental_id,))
        rental = cur.fetchone()
        cur.close()
    except Exception as e:
        app.logger.error(f"Erreur lors de la récupération du lease: {e}")
        return jsonify({"error": "Erreur serveur interne"}), 500

    if not rental:
        app.logger.warning(f"Lease introuvable pour rental_id: {rental_id}")
        return jsonify({"error": "Lease introuvable."}), 404

    # Vérifier si l'utilisateur est autorisé à accéder à ce lease
    if rental["user_id"] != current_user:
        app.logger.warning(f"Accès non autorisé pour l'utilisateur {current_user} sur rental_id: {rental_id}")
        return jsonify({"error": "Accès non autorisé."}), 403

    # Retourner le mot de passe SSH
    ssh_password = rental.get("ssh_password")
    if ssh_password:
        try:
            ssh_password = decrypt_password(ssh_password)
        except Exception as e:
            app.logger.warning(f"Impossible de déchiffrer le mot de passe SSH: {e}")
            ssh_password = None

    return jsonify({"ssh_password": ssh_password}), 200


# -----------------------
# Health check for Caddy etc.
# -----------------------
@app.route('/health', methods=['GET'])
def health_check():
    import socket
    return jsonify({"status": "healthy", "hostname": socket.gethostname()}), 200

# -----------------------
# Dev helper: reset DB nodes/leasing (kept for tests)
# -----------------------
@app.route('/reset', methods=['POST'])
@require_admin
def reset_database():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM rentals;")
        cur.execute("ALTER TABLE rentals AUTO_INCREMENT = 1;")
        cur.execute("DELETE FROM nodes;")
        cur.execute("ALTER TABLE nodes AUTO_INCREMENT = 1;")
        conn.commit()
        return jsonify({"message": "DB reset OK"}), 200
    except Exception as e:
        app.logger.error(f"Erreur reset_database: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        conn.close()

# -----------------------
# Startup main
# -----------------------

if __name__ == "__main__":
    app.logger.info("--- Demarrage du serveur Flask en mode DEBUG ---")
    app.run(host='0.0.0.0', port=8080, debug=True)

@app.route("/rent/test", methods=["POST"])
@require_auth
def rent_test_node():
    """
    Endpoint pour tester la location d'un nœud pour 2 minutes.
    """
    import secrets, string
    from datetime import datetime, timedelta

    client_name = request.user["username"]

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500

    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)

        # Récupérer un seul nœud libre
        cur.execute("""
            SELECT * FROM nodes
            WHERE status='alive' AND allocated=FALSE AND needs_cleanup=FALSE
            ORDER BY last_checked DESC
            LIMIT 1
            FOR UPDATE
        """)
        node = cur.fetchone()

        if not node:
            conn.rollback()
            return jsonify({"error": "Pas de nœud libre disponible"}), 503

        now = datetime.utcnow()
        lease_end = now + timedelta(minutes=2)

        node_id = node["id"]
        host_ip = node["ip"]
        port = node["ssh_port"]

        # Exception Docker : remplacer l'IP par host.docker.internal si IP interne Docker
        if host_ip.startswith("172.17."):
            host_ip = "host.docker.internal"

        client_pass = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

        # Chiffrer le mot de passe avant stockage
        encrypted_pass = encrypt_password(client_pass)

        # Insert rental avec password chiffré
        insert_rental = """
            INSERT INTO rentals (node_id, user_id, leased_from, leased_until, active, ssh_password)
            VALUES (%s, %s, %s, %s, TRUE, %s)
        """
        cur.execute(insert_rental, (node_id, request.user["user_id"], now, lease_end, encrypted_pass))
        cur.execute("UPDATE nodes SET allocated=TRUE WHERE id=%s", (node_id,))
        rental_id = cur.lastrowid

        # Provisioning
        success = run_ansible_provision(
            playbook_name='create_user.yml',
            host_ip=host_ip,
            host_port=port,
            client_user=client_name,
            client_pass=client_pass
        )

        if not success:
            app.logger.error(f"Provisioning failed for node {node_id}, rolling back transaction")
            conn.rollback()
            return jsonify({"error": "Échec du provisioning; transaction annulée"}), 500

        conn.commit()
        return jsonify({
            "rental_id": rental_id,
            "host_ip": host_ip,
            "ssh_port": port,
            "client_user": client_name,
            "client_pass": client_pass,
            "leased_until": lease_end.isoformat(),
        }), 200

    except Exception as e:
        app.logger.error(f"Erreur interne rent_test_node: {e}")
        try:
            conn.rollback()
        except:
            pass
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        try:
            cur.close()
        except:
            pass
        conn.close()


