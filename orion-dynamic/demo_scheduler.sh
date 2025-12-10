#!/bin/bash

# Configuration
DB_CONTAINER="orion-db"
DB_USER="root"
# We'll fetch the password from env inside the script if needed, or assume hardcoded from docker-compose if simpler for demo
# In docker-compose.yml: MARIADB_ROOT_PASSWORD=${DB_ROOT_PASSWORD} (usually 'root' in dev or similar)
# Found via docker exec: supersecretroot
pass="supersecretroot"

# Worker Config
WORKER_COUNT=30
IMAGE_NAME="orion-dynamic-worker:latest"
BASE_SSH_PORT=22220
# Use host.docker.internal for workers to reach API
API_URL="http://host.docker.internal" 
# Note: Workers register themselves via API on startup.

echo "=================================================="
echo "   DEMO SCHEDULER & WORK QUEUE (REAL WORKERS)"
echo "=================================================="
echo ""

echo ">>> 0. Pausing Autoscaler (to prevent interference)..."
docker pause orion-autoscaler-scheduler
echo "✅ Autoscaler paused."
echo ""

echo ">>> 1. Scaling up to 3 Scheduler instances..."
docker compose up -d --scale scheduler=3 --no-recreate scheduler
echo "✅ Scaled to 3 instances."
echo ""

echo ">>> 2. Building Worker Image..."
# Build executed from root (context ./data-plane/worker) ? No, context is ./data-plane/worker
# We are in orion-dynamic/ folder. data-plane is likely ../data-plane ?
# Wait, user path: .../deploiement/mac/projet_deploiement/orion-dynamic
# data-plane is sibling? No, let's check file structure.
# User opened: .../orion-dynamic/data-plane/launch_workers.sh
# So data-plane is IN orion-dynamic folder?
if [ -d "data-plane" ]; then
    docker build -t $IMAGE_NAME ./data-plane/worker
else
    # Fallback/Guess
    docker build -t $IMAGE_NAME ../data-plane/worker
fi
echo "✅ Image built."
echo ""

echo ">>> 3. Launching $WORKER_COUNT Real Workers..."
# Cleanup old workers first
docker rm -f $(docker ps -a -q --filter "name=worker-") 2>/dev/null

for i in $(seq 1 $WORKER_COUNT); do
    WORKER_NAME=$(printf "worker-%02d" $i)
    HOST_PORT=$((BASE_SSH_PORT + i))
    
    # Run container (similar to launch_workers.sh)
    # Pass host.docker.internal binding
    docker run -d \
        --name $WORKER_NAME \
        -p $HOST_PORT:22 \
        --add-host=host.docker.internal:host-gateway \
        -e MY_HOST_PORT=$HOST_PORT \
        -e API_ENDPOINT=$API_URL \
        $IMAGE_NAME > /dev/null
done
echo "✅ $WORKER_COUNT workers launched & registering..."
echo ""

echo ">>> 4. Watching Logs (Ctrl+C to stop)..."
echo "You should see 3 Schedulers processing these real containers."
echo "The workers are registering via API, appearing in DB, and Schedulers check them."
echo "---------------------------------------------------"

# Stream logs for 60 seconds then stop
# Use --since 0s to show only current activity
docker compose logs --since 0s -f scheduler | grep --line-buffered "Checking" &
PID=$!
sleep 60
kill $PID 2>/dev/null
wait $PID 2>/dev/null

echo ""
echo "---------------------------------------------------"
echo ">>> 5. Cleaning up Real Workers..."
docker rm -f $(docker ps -a -q --filter "name=worker-") 2>/dev/null
# Also clean DB? Workers might remain as 'dead' or 'unknown' until manual clean, 
# or we can wipe nodes table:
docker exec $DB_CONTAINER mysql -u$DB_USER -p$pass -D orion_db -e "DELETE FROM nodes;" 2>/dev/null
echo "✅ Workers removed."

echo ">>> 6. Reactivating Autoscaler..."
docker unpause orion-autoscaler-scheduler
echo "✅ Autoscaler unpaused."
