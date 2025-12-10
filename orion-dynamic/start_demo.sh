#!/bin/bash

# Configuration de l'environnement
# Ajout des chemins Docker communs pour macOS au PATH
export PATH=$PATH:/usr/local/bin:/Applications/Docker.app/Contents/Resources/bin

# Définition de l'ID du scheduler s'il n'est pas déjà défini
export SCHEDULER_ID=${SCHEDULER_ID:-1}

echo "=================================================="
echo "   Démarrage de Orion-Dynamic (Demo Mode)"
echo "=================================================="

# 1. Démarrer le Control Plane
echo ""
echo ">>> [1/2] Démarrage du Control Plane (Docker Compose)..."
echo "--------------------------------------------------------"
if docker compose up -d --build; then
    echo "✅ Control Plane démarré avec succès."
else
    echo "❌ Erreur lors du démarrage du Control Plane."
    exit 1
fi

# 2. Démarrer le Data Plane (Workers)
echo ""
echo ">>> [2/2] Démarrage du Data Plane (Workers)..."
echo "--------------------------------------------------------"
if [ -d "data-plane" ]; then
    cd data-plane
    
    if [ -f "launch_workers.sh" ]; then
        chmod +x launch_workers.sh
        ./launch_workers.sh 15
        
        # Revenir au dossier parent
        cd ..
    else
        echo "❌ Erreur: launch_workers.sh introuvable dans data-plane/"
        exit 1
    fi
else
    echo "❌ Erreur: Dossier data-plane/ introuvable."
    exit 1
fi

echo ""
echo "=================================================="
echo "🎉 Démonstration prête !"
echo "=================================================="
echo "Workers : Démarrés et enregistrés (check logs)"
echo "Accès   : https://localhost"
echo "Monitor API : docker logs -f orion-autoscaler-api"
echo "Monitor Sched: docker logs -f orion-autoscaler-scheduler"
echo "=================================================="
