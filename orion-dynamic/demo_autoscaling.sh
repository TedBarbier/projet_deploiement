#!/bin/bash

# Configuration
API_URL="https://localhost/api/health"
DURATION=60

echo "=================================================="
echo "   DEMO AUTOSCALING & LOAD BALANCING (Live)"
echo "=================================================="
echo ""
echo ">>> 0. Pausing Autoscaler (to prevent immediate scale-down)..."
docker pause orion-autoscaler-api
echo "✅ Autoscaler paused."
echo ""

echo ">>> 1. Scaling up to 3 API instances..."
docker compose up -d --scale api=3 --no-recreate api
echo "✅ Scaled to 3 instances."
echo ""

echo ">>> 2. Waiting for containers to be ready (10s)..."
sleep 10

echo ""
echo ">>> 3. Listing Active API Containers:"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep "orion-dynamic-api"
echo ""

echo ">>> 4. Testing Load Balancing (Round-Robin)"
echo "Envoi de 10 requêtes /health pour voir qui répond..."
echo "---------------------------------------------------"

for i in {1..10}; do
    # Curl silencieux (-s), insecure (-k)
    RESPONSE=$(curl -s -k "$API_URL")
    
    # Tentative d'extraction du hostname (ID du conteneur)
    ID=$(echo "$RESPONSE" | grep -o '"hostname": *"[^"]*"' | cut -d'"' -f4)

    if [ -z "$ID" ]; then
        echo "Req #$i -> ⚠️  Pas de réponse (conteneur en démarrage ?)"
    else
        # Résolution du nom via Docker
        NAME=$(docker ps --filter "id=$ID" --format "{{.Names}}")
        # Fallback si nom non trouvé
        if [ -z "$NAME" ]; then NAME=$ID; fi
        
        echo "Req #$i -> Répondu par : $NAME"
    fi
    sleep 0.5
done

echo "---------------------------------------------------"
echo "✅ Démo Load Balancing terminée."
echo ""
echo ">>> 5. Reactivating Autoscaler..."
docker unpause orion-autoscaler-api
echo "✅ Autoscaler unpaused. Watch it kill the extra containers soon!"
