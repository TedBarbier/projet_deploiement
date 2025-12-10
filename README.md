# Orion-Dynamic

Orchestrateur de Ressources à Inventaire Dynamique et Gestion de Baux

## Objectif

Simuler une plateforme PaaS où les ressources (conteneurs/machines) **s'enregistrent automatiquement** auprès d'un Control Plane.  
L'orchestrateur gère :

- Les locations à durée déterminée (baux)
- La QoS avec migration automatique en cas de panne
- Le nettoyage des ressources à l'expiration des baux

---

## 🔹 Architecture

### Control Plane

- **Reverse Proxy (Caddy)** : HTTPS pour les clients, HTTP pour les agents. Fait du **Load Balancing** dynamique vers les réplicas d'API.
- **API Python** : Gère l’enregistrement des Workers et les locations. Scalable horizontalement (replicas gérés par l'autoscaler).
- **Autoscaler** : Service autonome qui monitore la charge CPU des conteneurs API via le socket Docker et ajuste le nombre de répliques (Scale Up/Down).
- **Scheduler** : Service scalable (supporte le multi-instance grâce au verrouillage `SKIP LOCKED`). Gère Health Check, migration et expiration.
- **MariaDB** : Base de données centralisée (Inventaire, Locations, Users).

### Data Plane

- **Workers** : Conteneurs ou machines simulées, avec SSH et un agent d’enregistrement (`agent.py`).

---

## Flowchart

```mermaid
flowchart LR
    Client["Client (curl/CLI)"]

    subgraph DATA["Monde Externe (Data Plane)"]
        Worker["Workers (Pré-existants)<br/>Agent 'agent.py'"]
    end

    subgraph CONTROL["Control Plane"]
        RP["Reverse Proxy (Caddy)<br/>:443 Client<br/>:80 Agent"]
        API["API Python (Scalable)<br/>:8080"]
        AS["Autoscaler<br/>(Docker Socket)"]
        SCHED["Scheduler"]
        DB[(MariaDB)]
    end

    %% Flux
    Worker -- "Flux 0: POST /api/workers/register" --> RP
    Client -->|"Flux 1: POST /api/rent" | RP
    RP -->|"Flux 2: Proxy → API (Load Balacing)" | API
    API -->|"Flux 3: API ↔ DB" | DB
    SCHED -->|"Flux 3: Scheduler ↔ DB" | DB
    AS -.->|"Monitors CPU & Scales"| API
    AS -.->|"Monitors CPU & Scales"| SCHED
    API -.->|"Flux 4: Provisioning SSH (Ansible)" | Worker
    SCHED -.->|"Flux 5: Health Check & Cleanup SSH (Ansible)" | Worker
```
## Fonctionnalités

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

## 🚀 Démonstrations

Le projet inclut deux scripts de démonstration pour valider les aspects dynamiques :

### 1. Autoscaling (`demo_autoscaling.sh`)
Simule une charge CPU sur les APIs pour déclencher le scaling horizontal.
```bash
cd orion-dynamic
./demo_autoscaling.sh
```
- Affiche les logs de l'Autoscaler qui détecte la charge.
- Montre Caddy redémarrant pour prendre en compte les nouveaux réplicas.
- Vérifie que les requêtes sont bien réparties (Load Balancing).

### 2. Scheduler Dynamique (`demo_scheduler.sh`)
Lance plusieurs instances de Scheduler pour traiter une file de tâches massives.
```bash
cd orion-dynamic
./demo_scheduler.sh
```
- Génère 300 locations dans la DB.
- Lance 3 schedulers en parallèle.
- Démontre l'efficacité du verrouillage `SKIP LOCKED` : aucune tâche n'est traitée deux fois, et la charge est répartie équitablement.

## ✅ Tests Unitaires

Une suite de tests complète (API, Scheduler, Autoscaler) est disponible.

**Pré-requis** :
```bash
cd orion-dynamic
pip install -r requirements-test.txt
```

**Lancer les tests** :
```bash
./run_unit_tests.sh
```
*Couverture : Auth, Locations, SSH Mock, Scaling Logic, Concurrence Scheduler.*

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

- **POST /api/reset**
  - Reset DB nodes/rentals (dev/admin only).
  - Headers : `Authorization: Bearer <token_admin>`
  - Retour : 
    ```json
    {"message": "DB reset OK"}
    ```

## Notes

- Pour les Workers Docker, `host.docker.internal` est utilisé à la place de l’IP interne Docker pour les connexions SSH depuis le Control Plane.
- La base MariaDB stocke :
  - `nodes` : état des Workers
  - `users` : utilisateurs
  - `rentals` : baux actifs

