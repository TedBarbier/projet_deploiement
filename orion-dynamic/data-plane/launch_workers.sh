#!/bin/bash
# (À lancer depuis le dossier data-plane/)

# --- Configuration ---
# Récupère le nombre de workers depuis le 1er argument, sinon 3 par défaut
WORKER_COUNT=${1:-3}
IMAGE_NAME="orion-dynamic-worker:latest"
BASE_SSH_PORT=22220 # On commencera à 22221 (22220 + 1)

# Sur Linux natif, utilisez : API_URL="http://$(ip -4 addr show docker0 | grep -Po 'inet \K[\d.]+')"
API_URL="https://host.docker.internal" # Port 443, géré par Caddy

# --- Build de l'image ---
echo "--- Construction de l'image Worker ($IMAGE_NAME) ---"
docker build -t $IMAGE_NAME ./worker

# --- Nettoyage ---
echo "--- Arrêt et suppression des anciens workers (worker-*) ---"
# Utilise un filtre de nom pour supprimer tous les anciens workers
docker rm -f $(docker ps -a -q --filter "name=worker-") 2>/dev/null

echo "--- Lancement de $WORKER_COUNT workers (Data Plane) ---"

# --- Boucle de Lancement ---
for i in $(seq 1 $WORKER_COUNT)
do
  # Formate le nom (worker-01, worker-02, etc.)
  WORKER_NAME=$(printf "worker-%02d" $i)
  
  # Calcule le port SSH mappé (22221, 22222, etc.)
  HOST_PORT=$((BASE_SSH_PORT + i))

  echo "Lancement de $WORKER_NAME sur le port $HOST_PORT..."

  docker run -d \
    --name $WORKER_NAME \
    -p $HOST_PORT:22 \
    --add-host=host.docker.internal:host-gateway \
    -e MY_HOST_PORT=$HOST_PORT \
    -e API_ENDPOINT=$API_URL \
    $IMAGE_NAME
done

echo "--- Data Plane Démarré ---"
docker ps -f "name=worker-"