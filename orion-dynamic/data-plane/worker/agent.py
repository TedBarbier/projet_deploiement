#!/usr/bin/env python3
import os
import requests
import time
import sys
import logging
import socket

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='[AGENT] %(asctime)s: %(message)s')

# Lire les variables d'environnement
HOST_PORT = os.getenv('MY_HOST_PORT')
API_ENDPOINT = os.getenv('API_ENDPOINT', 'https://host.docker.internal')
HOSTNAME = os.getenv('MY_HOSTNAME', os.uname()[1])

def get_host_ip():
    """Récupère l'IP locale de la machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # connexion fictive pour déterminer l'IP locale
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def register_worker():
    if not HOST_PORT:
        logging.error("Erreur: MY_HOST_PORT n'est pas défini. Abandon.")
        sys.exit(1)

    ip_address = get_host_ip()
    url = f"{API_ENDPOINT}/api/workers/register"
    payload = {
        "hostname": HOSTNAME,
        "ip": ip_address,
        "ssh_port": int(HOST_PORT)
        
    }

    logging.info(f"Tentative d'enregistrement auprès de {url} avec {payload}...")

    max_retries = 10
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=5, verify=False)
            
            if response.status_code in (200, 201):
                logging.info("Enregistrement réussi !")
                return True
            # 409 Conflict = Déjà enregistré (ex: après un redémarrage du worker)
            elif response.status_code == 409: 
                 logging.info("Noeud déjà enregistré (Conflit 409). C'est OK.")
                 return True
            else:
                logging.warning(f"Échec de l'enregistrement (Code: {response.status_code})... Réessai dans 10s.")
        
        except requests.exceptions.ConnectionError:
            logging.warning(f"Impossible de joindre l'API à {url}. Control Plane démarré ? Réessai dans 10s.")
        
        except Exception as e:
            logging.error(f"Erreur inattendue: {e}")

        time.sleep(10)
    
    logging.error("Échec de l'enregistrement après plusieurs tentatives.")
    return False

if __name__ == "__main__":
    # Attendre un peu que le réseau docker soit prêt
    time.sleep(5) 
    register_worker()
