# Orion-Dynamic

Orchestrateur de Ressources à Inventaire Dynamique et Gestion de Baux

## Objectif

Simuler une plateforme PaaS où les ressources (conteneurs/machines) **s'enregistrent automatiquement** auprès d'un Control Plane.  
L'orchestrateur gère :

- Les locations à durée déterminée (baux)
- La QoS avec migration automatique en cas de panne
- Le nettoyage des ressources à l'expiration des baux

---

## 🔹 Architecture : Philosophie Micro-services

Le système repose sur une séparation stricte entre le **Control Plane** (Intelligence) et le **Data Plane** (Ressources).

### 1. Data Plane : Les Workers "Opportunistes" (Push Mode)
Contrairement aux architectures classiques où le serveur scanne le réseau (Pull), nous utilisons un **mode Push**.
- Chaque Worker embarque un `agent.py` léger.
- Au démarrage, l'agent contacte l'API pour signaler sa présence.
- **Avantage** : Permet de traverser les NAT/Firewalls et d'ajouter des capacités de calcul instantanément sans reconfigurer le serveur central.

### 2. Control Plane : L'intelligence orchestrée
Composé de micro-services stateless conteneurisés :

- **Reverse Proxy (Caddy)** : API Gateway unique. Gère le **Load Balancing** dynamique vers les réplicas d'API.
- **API (FastAPI)** : Cœur réactif et stateless. Gère l'enregistrement et les baux.
- **Scheduler** : Assure la cohérence (Health Check, Migration, Expiration). Utilise le verrouillage `SKIP LOCKED` pour la scalabilité.
- **Autoscaler** : Régulation en boucle fermée (PID) qui ajuste les réplicas d'API selon la charge CPU.
- **MariaDB** : Vérité terrain. Garantit l'intégrité via des transactions **ACID** strictes (essentiel pour éviter les doubles locations).
- **Ansible** : Moteur de sécurité. Isole les clients en créant/supprimant des utilisateurs éphémères sur les workers (garantie de nettoyage sans accès root).

---

## 💡 Choix Techniques & Résilience

### Pourquoi MariaDB & SQL ?
Pour la **Cohérence Forte**. Dans un système de location, deux clients ne doivent jamais obtenir la même ressource. Les transactions `SELECT ... FOR UPDATE` garantissent l'atomicité des allocations.

### Gestion de la Concurrence massive
Le Scheduler utilise `SELECT ... FOR UPDATE SKIP LOCKED`.
- Cela permet de lancer plusieurs instances du Scheduler en parallèle.
- Chaque instance "pioche" une tâche libre (ex: migration) sans bloquer les autres.

### Sécurité & Isolation
Nous ne donnons jamais d'accès `root` aux clients.
- **Provisioning** : Ansible crée un utilisateur UNIX dédié lors de la location.
- **Nettoyage** : À l'expiration ou après une panne, Ansible supprime cet utilisateur, garantissant qu'aucune donnée résiduelle ne persiste pour le client suivant.

### Pivot Technique : Vagrant vs Docker
Initialement prévu sur Vagrant pour une isolation totale, le projet a pivoté vers une architecture **Docker Native**.
- **Raison** : Instabilités majeures de la virtualisation imbriquée (Linux sur Vagrant sur macOS ARM64/Apple Silicon).
- **Bénéfice** : Docker Compose offre ici de meilleures performances et une portabilité immédiate sur toutes les architectures modernes.

---

## Flowchart

```mermaid
flowchart TD
    %% --- Styles ---
    classDef client fill:#ffecb3,stroke:#ff6f00,stroke-width:2px,color:black;
    classDef control fill:#e1f5fe,stroke:#01579b,stroke-width:2px,color:black;
    classDef worker fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px,color:black;
    classDef db fill:#fff9c4,stroke:#fbc02d,stroke-width:2px,color:black;

    %% --- Noeuds ---
    Client["Client (curl/CLI)"]:::client

    subgraph CONTROL["Control Plane"]
        direction TB
        
        %% Niveau 1 : Entrée et Autoscaler alignés
        RP["Reverse Proxy (Caddy)<br/>:443 Client | :80 Agent"]:::control
        AS["Autoscaler<br/>(Docker Socket)"]:::control
        
        %% Astuce : Lien invisible pour placer AS à droite de RP
        RP ~~~ AS

        %% Niveau 2 : Services
        API["API Python (Scalable)<br/>:8080"]:::control
        SCHED["Scheduler"]:::control
        
        %% Niveau 3 : Base de données
        DB[("MariaDB")]:::db
    end

    subgraph DATA["Monde Externe (Data Plane)"]
        Worker["Workers (Pré-existants)<br/>Agent 'agent.py'"]:::worker
    end

    %% --- Flux (Edges) ---
    
    %% Flux Entrants
    Client -->|"Flux 1:<br/>POST /api/rent"| RP
    Worker -->|"Flux 0:<br/>POST /api/workers/register"| RP

    %% Flux Internes Control Plane
    %% J'ai mis 3 tirets (--->) ici pour allonger la flèche et faire de la place au texte
    RP --->|"Flux 2: Proxy → API<br/>(Load Balancing)"| API
    
    API <-->|"Flux 3:<br/>Read/Write"| DB
    SCHED <-->|"Flux 3:<br/>Polling Tasks"| DB
    
    %% Scaling (Lignes pointillées pour ne pas surcharger)
    AS -.->|"Monitors CPU<br/>& Scales"| API
    AS -.->|"Monitors CPU<br/>& Scales"| SCHED

    %% Flux Sortants vers Workers
    API -.->|"Flux 4: Provisioning SSH<br/>(Ansible)"| Worker
    SCHED -.->|"Flux 5: Health Check<br/>& Cleanup SSH"| Worker
```# Fonctionnalités

### Agent (`agent.py`)

- Lit les variables d’environnement (`MY_HOST_PORT`, `API_ENDPOINT`)
- Enregistre le Worker via `POST /api/workers/register`
- Réessaye en boucle si le Control Plane n’est pas prêt
- Envoie `hostname`, `ip`, et `ssh_port`

### API

- **POST /api/workers/register** : enregistre ou met à jour un Worker
- **POST /api/rent** : loue un ou plusieurs Workers pour une durée définie
  - Retourne les infos SSH pour le client
  - Marque `allocated = true` dans la base de données

### Scheduler

- **Health Check** : ping SSH tous les Workers
- **Migration** : déplace les clients d’un Worker mort vers un Worker sain
- **Expiration des baux** : déprovisionne et libère automatiquement les Workers

---

## Déploiement

1. **Control Plane**

```bash
docker-compose up -d
```
2. **Workers (Data Plane)**

```bash
./launch_workers.sh
```
3. Agent

- Se lance automatiquement au démarrage du Worker
- Lit les variables d’environnement (`MY_HOST_PORT`, `API_ENDPOINT`)
- Enregistre le Worker auprès du Control Plane via `POST /api/workers/register`
- Gère les réessais en cas d’échec
- Envoie `hostname`, `ip`, et `ssh_port`

## Fichiers importants

- `docker-compose.yml` : orchestration Control Plane
- `Dockerfile` pour API et Scheduler
- `Dockerfile` pour Workers (Alpine + SSH + Agent)
- `control-plane/autoscaler/` : Code et Dockerfile de l'autoscaler
- `Caddyfile` : configuration du Reverse Proxy
- `init.sql` : initialisation de la base MariaDB
- `playbooks/` : Ansible pour `create_user.yml` et `delete_user.yml`
- `launch_workers.sh` : script pour déployer plusieurs Workers

## 🚀 Démonstration Complète (`full_demo.sh`)

Le projet inclut un script de démonstration complet qui joue un scénario réaliste couvrant toutes les fonctionnalités :

1.  **Cleanup** : Nettoie l'environnement.
2.  **Infrastructure** : Démarre le Control Plane et attend que l'API soit prête.
3.  **Scaling & Load Balancing** :
    *   Scale l'API et le Scheduler à 3 réplicas.
    *   Démontre le Round-Robin du Load Balancer.
4.  **Authentification & Rôles** :
    *   Crée un Admin et un User.
    *   Vérifie les connexions et les tokens JWT.
5.  **Location & Self-Healing** :
    *   L'utilisateur loue un nœud.
    *   **Simulation de panne** : Le worker loué est arrêté brutalement.
    *   **Migration** : Le Scheduler détecte la pane et migre l'utilisateur vers un nouveau nœud automatiquement.
    *   **Resurrection** : Le worker mort est redémarré.
    *   **Cleanup de sécurité** : Le Scheduler détecte le retour du worker et supprime immédiatement le compte utilisateur qui y était (pour éviter tout accès non autorisé).
6.  **Cycle de vie** :
    *   Location -> Extension de bail -> Libération anticipée.
    *   Vérification que les ressources sont bien libérées.

### Lancer la démo

```bash
cd orion-dynamic
./full_demo.sh
```

> **Note** : Le script est interactif et vous guidera étape par étape.

## fichiers importants

- `full_demo.sh` : Script de démonstration principal.
- `start_demo.sh` : Script utilitaire pour lancer l'infrastructure.
- `docker-compose.yml` : Orchestration Control Plane.
- `Dockerfile` pour API et Scheduler.
- `control-plane/autoscaler/` : Code de l'autoscaler.
- `Caddyfile` : Configuration du Reverse Proxy.

## ✅ Tests Unitaires

Une suite de tests complète (**58 tests**, couverture ~82%) couvre l'ensemble des composants critiques :
- **Autoscaler (90%)** : Scaling, Commandes Docker, Gestion d'erreurs.
- **API (86%)** : Endpoints, Authentification, cas limites et erreurs DB.
- **Scheduler (76%)** : Boucle principale robuste, Health Check, Migration, Expiration.

Les tests utilisent `pytest` avec un mock complet de la base de données et d'Ansible, permettant une exécution rapide et isolée.

### Lancer les tests

```bash
cd orion-dynamic
./run_unit_tests.sh
```
Ce script configure automatiquement un environnement virtuel (`venv_test`), installe les dépendances et lance les tests avec un rapport de couverture.

## API Endpoints et Commandes

### Authentification

- **POST /api/signup**
  - Crée un utilisateur.
  - Body JSON : 
    ```json
    {"username":"user", "password":"pass"}
    ```
  - Retour : 
    ```json
    {"message":"Compte créé"}
    ```
    ou erreur.

- **POST /api/login**
  - Connecte un utilisateur et retourne un JWT.
  - Body JSON : 
    ```json
    {"username":"user", "password":"pass"}
    ```
  - Retour : 
    ```json
    {"token": "<JWT>"}
    ```

### Gestion des Workers / Location

- **POST /api/rent**
  - Loue un ou plusieurs Workers.
  - Headers : `Authorization: Bearer <token>`
  - Body JSON : 
    ```json
    {"duration_hours": 2, "count": 1, "ssh_password": "optionnel"}
    ```
  - Retour : liste des locations :
    ```json
    [
      {
        "rental_id": 2,
        "host_ip": "192.168.0.10",
        "ssh_port": 22221,
        "client_user": "alice",
        "client_pass": "motdepasse123",
        "leased_until": "2025-11-15T21:46:30.988892"
      }
    ]
    ```

- **POST /api/release/<rental_id>**
  - Libère un bail existant.
  - Headers : `Authorization: Bearer <token>`
  - Retour : 
    ```json
    {"message":"Lease released"}
    ```

- **POST /api/extend/<rental_id>**
  - Prolonge un bail.
  - Headers : `Authorization: Bearer <token>`
  - Body JSON : 
    ```json
    {"additional_hours": 1}
    ```
  - Retour : 
    ```json
    {"lease_id": 2, "new_end_at": "2025-11-15T22:46:30.988892"}
    ```

- **GET /api/nodes**
  - Liste les Workers et leurs locations.
  - Headers : `Authorization: Bearer <token>`
  - Retour : liste des nœuds avec infos de bail si actif :
    ```json
    [
      {
        "node_id": 1,
        "hostname": "worker1",
        "ssh_port": 22221,
        "status": "alive",
        "allocated": true,
        "lease": {
          "rental_id": 2,
          "user_id": 1,
          "leased_from": "2025-11-15T20:46:30.988892",
          "leased_until": "2025-11-15T21:46:30.988892",
          "active": true
        }
      }
    ]
    ```

- **POST /api/workers/register**
  - Appelé par l’agent des Workers.
  - Body JSON : 
    ```json
    {"hostname":"host", "ip":"1.2.3.4", "ssh_port":2222}
    ```
  - Retour : 
    ```json
    {"message": "Worker enregistré avec succès"}
    ```
    ou 
    ```json
    {"message": "Worker déjà enregistré"}
    ```

- **GET /api/health**
  - Vérifie l’état du serveur.
  - Retour : 
    ```json
    {"status": "healthy"}
    ```



## Notes

- Pour les Workers Docker, `host.docker.internal` est utilisé à la place de l’IP interne Docker pour les connexions SSH depuis le Control Plane.
- La base MariaDB stocke :
  - `nodes` : état des Workers
  - `users` : utilisateurs
  - `rentals` : baux actifs

