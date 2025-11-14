#!/bin/sh

# Lancer l'agent d'enregistrement en arrière-plan
# Il ne s'exécute qu'une fois et s'arrête
python3 /usr/local/bin/agent.py &

# Lancer le serveur SSH au premier plan (processus principal)
echo "[ENTRYPOINT] Démarrage du serveur SSH..."
/usr/sbin/sshd -D -e