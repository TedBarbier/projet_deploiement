#!/usr/bin/env python3
import os
import logging
import tempfile
import secrets
import string
import jwt
import bcrypt
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
import mysql.connector
from mysql.connector import errorcode
import ansible_runner
from functools import wraps

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
        "exp": datetime.utcnow() + timedelta(seconds=JWT_EXPIRE_SECONDS)
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
# Auth endpoints
# -----------------------
@app.route("/auth/signup", methods=["POST"])
def signup():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "user")
    if not username or not password:
        return jsonify({"error": "username et password requis"}), 400
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        cur = conn.cursor()
        # check existing
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            return jsonify({"error": "Utilisateur deja existant"}), 400
        # hash
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)", (username, pw_hash, role))
        conn.commit()
        return jsonify({"message": "Compte cree"}), 201
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

@app.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "username et password requis"}), 400
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
@app.route("/api/rent", methods=["POST"])
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
            WHERE status='alive' AND allocated=FALSE
            ORDER BY last_checked DESC
            LIMIT {count}
            FOR UPDATE
        """)
        nodes = cur.fetchall()

        if not nodes or len(nodes) < count:
            conn.rollback()
            return jsonify({"error": "Pas assez de workers libres", "found": len(nodes)}), 503

        allocated = []
        now = datetime.utcnow()
        lease_end = now + timedelta(hours=duration_hours)

        for node in nodes:
            node_id = node["id"]
            host_ip = node["ip"]
            port = node["ssh_port"]

            # Exception Docker : remplacer l'IP par host.docker.internal si IP interne Docker
            if host_ip.startswith("172.17."):
                host_ip = "host.docker.internal"

            client_pass = ssh_password_given or ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

            # Insert rental
            insert_rental = """
                INSERT INTO rentals (node_id, user_id, leased_from, leased_until, active)
                VALUES (%s, %s, %s, %s, TRUE)
            """
            cur.execute(insert_rental, (node_id, request.user["user_id"], now, lease_end))
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


@app.route("/api/release/<int:lease_id>", methods=["POST"])
@require_auth
def release_lease(lease_id):
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB non disponible"}), 500
    try:
        conn.start_transaction()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM leases WHERE id=%s FOR UPDATE", (lease_id,))
        lease = cur.fetchone()
        if not lease:
            conn.rollback()
            return jsonify({"error": "Lease introuvable"}), 404

        # permission check
        if request.user["role"] != "admin" and lease["user_id"] != request.user["user_id"]:
            conn.rollback()
            return jsonify({"error": "Pas la permission de liberer cette location"}), 403

        # get node details
        cur.execute("SELECT * FROM nodes WHERE id=%s FOR UPDATE", (lease["node_id"],))
        node = cur.fetchone()
        if not node:
            conn.rollback()
            return jsonify({"error": "Noeud introuvable"}), 404

        # Call ansible delete_user.yml to remove user on worker (best-effort)
        try:
            run_ansible_provision('delete_user.yml', node["hostname"], node["ssh_port"], lease["client_name"], lease.get("ssh_password"))
        except Exception as e:
            app.logger.warning(f"Cleanup Ansible may have failed: {e}")

        # mark lease released and update node
        cur.execute("UPDATE leases SET status=%s WHERE id=%s", ("released", lease_id))
        cur.execute("UPDATE nodes SET allocated=false, allocated_to=NULL, lease_end_at=NULL WHERE id=%s", (node["id"],))
        conn.commit()
        return jsonify({"message": "Lease released"}), 200
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

@app.route("/api/extend/<int:lease_id>", methods=["POST"])
@require_auth
def extend_lease(lease_id):
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
        cur.execute("SELECT * FROM leases WHERE id=%s FOR UPDATE", (lease_id,))
        lease = cur.fetchone()
        if not lease:
            conn.rollback()
            return jsonify({"error": "Lease introuvable"}), 404

        # permission check
        if request.user["role"] != "admin" and lease["user_id"] != request.user["user_id"]:
            conn.rollback()
            return jsonify({"error": "Pas la permission d'etendre cette location"}), 403

        if lease["status"] != "active":
            conn.rollback()
            return jsonify({"error": "La lease n'est pas active"}), 400

        new_end = lease["end_at"] + timedelta(hours=add_hours)
        cur.execute("UPDATE leases SET end_at=%s WHERE id=%s", (new_end, lease_id))
        # mirror in nodes.lease_end_at if desired
        cur.execute("UPDATE nodes SET lease_end_at=%s WHERE id=%s", (new_end, lease["node_id"]))
        conn.commit()
        return jsonify({"lease_id": lease_id, "new_end_at": new_end.isoformat()}), 200
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

@app.route("/api/nodes", methods=["GET"])
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
                       r.id as rental_id, r.user_id as rental_user_id, r.leased_from, r.leased_until, r.active
                FROM nodes n
                LEFT JOIN rentals r ON r.node_id = n.id AND r.active = TRUE
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
                    "leased_from": r["leased_from"].isoformat() if r["leased_from"] else None,
                    "leased_until": r["leased_until"].isoformat() if r["leased_until"] else None,
                    "active": bool(r["active"])
                }

        return jsonify(list(nodes.values())), 200

    except Exception as e:
        app.logger.error(f"Erreur list_nodes: {e}")
        return jsonify({"error": "Erreur serveur interne"}), 500
    finally:
        conn.close()

@app.route('/api/workers/register', methods=['POST'])
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


# -----------------------
# Health check for Caddy etc.
# -----------------------
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy"}), 200

# -----------------------
# Dev helper: reset DB nodes/leasing (kept for tests)
# -----------------------
@app.route('/api/reset', methods=['POST'])
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
