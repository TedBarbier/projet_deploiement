#!/bin/bash

# --- Configuration ---
WORKER_NAME="worker-02"   # nom exact du container docker du worker

echo "[*] Crash test : arrêt du worker '$WORKER_NAME'..."

# Stop brutal
docker kill "$WORKER_NAME" >/dev/null 2>&1

if [ $? -ne 0 ]; then
    echo "[!] Erreur : impossible de tuer le worker '$WORKER_NAME'"
    exit 1
fi

echo "[+] Worker '$WORKER_NAME' stoppé brutalement !"

sleep 2

echo "[*] Vérification…"
docker ps | grep "$WORKER_NAME" > /dev/null

if [ $? -eq 0 ]; then
    echo "[!] Le worker est encore en vie (??)"
else
    echo "[+] Confirmé : worker down. Le scheduler devrait récupérer et réattribuer un autre worker."
fi
