### 1. 📋 Titre du Projet

**Projet "Orion-Static" : Orchestrateur de Ressources à Inventaire Statique et Haute Disponibilité**

* **Matière :** Déploiement & Infrastructure as Code (IaC)
* **Objectif :** Simuler une plateforme de location de ressources (conteneurs) basée sur un inventaire **connu et pré-défini**. La plateforme doit garantir la QoS en surveillant activement l'état des ressources et en migrant les clients en cas de panne.

---

### 2. 🎯 Contexte et Objectifs

Le but est de créer un système de location de ressources (PaaS) où le parc de "nœuds" (workers) est connu à l'avance par l'orchestrateur.

L'orchestrateur doit :
1.  **Gérer un inventaire statique** (défini en configuration).
2.  **Exposer une API sécurisée** (via un Reverse Proxy) pour les clients désirant louer une ressource.
3.  **Surveiller la santé (Health Check)** : Un service `Scheduler` doit activement sonder (via SSH) tous les nœuds (loués ou non) pour connaître leur état ("alive" / "dead").
4.  **Provisionner** les ressources à la demande (créer un utilisateur) en utilisant Ansible (appelé par l'API).
5.  **Gérer la résilience :** En cas de détection de panne d'un nœud loué, le `Scheduler` doit déclencher la migration du client vers un nœud sain.

---

### 3. 🏗️ Architecture Globale

Le système est composé de deux ensembles :
* **Le "Control Plane" (Réseau Docker "lab") :** Contient la logique de gestion.
    * `Reverse Proxy` (Caddy/Nginx) : Point d'entrée pour les clients humains.
    * `API` (Go/Gin) : Gère les demandes de location.
    * `Scheduler` (Go) : Gère la surveillance et la migration.
    * `Database` (MariaDB) : Stocke l'état de l'inventaire et des locations.
* **Le "Data Plane" (Hôte Docker) :**
    * `Workers` (Conteneurs Alpine) : Les ressources passives. Exécutent un serveur SSH et sont mappés sur des ports de l'hôte (ex: `22221 -> 22`).

---

### 4. 🌐 Schéma Réseau Détaillé (Parc Connu)

Ce schéma illustre le modèle à inventaire statique où le Control Plane initie les connexions vers les Workers.

```mermaid
flowchart LR
    %% Définir le style pour les connexions SSH en pointillés
    linkStyle 3 stroke-dasharray: 5 5
    linkStyle 4 stroke-dasharray: 5 5

    %% Entités Hors Réseau
    Client["Client (curl/CLI)"]

    %% Subgraph pour l'Hôte Docker (Data Plane)
    subgraph HOTE [Host (Docker Engine)]
        Workers["Workers (Alpine)<br/>Port 22221..N -> 22"]
    end

    %% Subgraph pour le Réseau de Contrôle (Control Plane)
    subgraph LAB [lab_network]
        direction LR
        RP["Reverse Proxy (Caddy)<br/>HTTPS :443"]
        API["Go API (Gin)<br/>:8080"]
        SCHED["Go Scheduler"]
        DB[(MariaDB)]

        RP -->|Flux 2: HTTP :8080| API
        API -->|Flux 3: SQL| DB
        SCHED -->|Flux 3: SQL| DB
    end

    %% Connexions Externes vers Internes
    Client -->|Flux 1: HTTPS :443| RP

    %% Connexions du Control Plane vers le Data Plane (SSH)
    API -.->|Flux 4: Provisioning<br/>(SSH via host.docker.internal)| Workers
    SCHED -.->|Flux 5: Health Check<br/>(SSH via host.docker.internal)| Workers
```
**Explication des flux :**

* **Flux 1 (Location Client) :** Le `Client` envoie `POST /api/rent` au `Reverse Proxy` (point d'entrée public).
* **Flux 2 (Proxy Pass) :** Le `Proxy` transfère la requête à l'`API (Go/Gin)`.
* **Flux 3 (État) :** L'`API` et le `Scheduler` lisent/écrivent constamment dans la `Database` (MariaDB) pour connaître/modifier l'état des nœuds et des locations.
* **Flux 4 (Provisioning) :** L'`API` (suite à une location) initie une connexion **SSH sortante** (ex: `ssh host.docker.internal:22221`) vers le `Worker` pour le provisionner (via Ansible).
* **Flux 5 (Health Check / QoS) :** Le `Scheduler` initie des connexions **SSH sortantes** (ex: `ssh host.docker.internal:22221`, `...:22222`, etc.) pour vérifier la santé de **tous** les `Workers` (loués ou non).

---

### 5. 🛠️ Fonctionnalités Détaillées

#### 5.1. IaC et Déploiement
* **En tant qu'Admin,** je veux un `docker-compose.yml` qui déploie le "Control Plane" (Proxy, API, Scheduler, DB).
* **En tant qu'Admin,** je veux un script séparé (ou un autre Compose) pour lancer le "Data Plane" (les N `Workers` Alpine avec leurs ports SSH mappés).
* L'inventaire des Workers (ex: `host.docker.internal:22221`) est fourni à l'API et au Scheduler (ex: via un fichier de config ou des variables d'env.).

#### 5.2. API (Go/Gin)
* **`POST /api/rent` :** (Appelé par le Client, via Proxy)
    1.  Interroge la `DB` pour trouver un `Worker` avec `status = 'alive'` ET `allocated = false`.
    2.  Si aucun n'est trouvé, renvoie 503 (Service Unavailable).
    3.  Si trouvé, marque le Worker comme `allocated = true` dans la DB.
    4.  Appelle **Ansible** (Flux 4) pour provisionner ce Worker.
    5.  Renvoie les détails de connexion au client.

#### 5.3. Scheduler (Go) - La QoS
* **Tâche 1 : Health Check (Toutes les 30s)**
    1.  Itère sur **TOUT** l'inventaire des Workers (de la DB ou config).
    2.  Pour chaque Worker, tente une connexion SSH (Flux 5).
    3.  Si succès : `UPDATE nodes SET status = 'alive', last_checked = NOW()`.
    4.  Si échec : `UPDATE nodes SET status = 'dead', last_checked = NOW()`.
* **Tâche 2 : Migration (Toutes les 10s)**
    1.  Cherche dans la DB les `Workers` avec `status = 'dead'` ET `allocated = true`.
    2.  Pour chaque cas (une "panne client") :
        a. Trouve un *nouveau* Worker (`status = 'alive'`, `allocated = false`).
        b. Si pas de nouveau Worker dispo, log l'erreur (client en panne).
        c. Si trouvé :
            i. Appelle **Ansible** (Flux 4) pour provisionner le *nouveau* Worker.
            ii. Met à jour la DB (ancienne location libérée, nouvelle créée).
            iii. (Optionnel) Notifie le client du changement d'IP.

---

### 6. 📦 Livrables Attendus

1.  **Code Source :** Le code Go pour l'API et le Scheduler.
2.  **Fichiers IaC :**
    * `docker-compose.yml` pour le "Control Plane".
    * `Dockerfile` pour l'API (Go, Ansible).
    * `Dockerfile` pour le Scheduler (Go).
    * `Dockerfile` pour le Worker (Alpine, `openssh-server`).
    * Fichier de configuration Nginx/Caddy pour le Proxy.
3.  **Scripts d'Automatisation :**
    * Playbooks Ansible (ex: `create_user.yml`, `delete_user.yml`).
    * Script de lancement des Workers.
4.  **Documentation :** Un `README.md` expliquant l'architecture, comment lancer le Control Plane et le Data Plane, et comment simuler une panne (ex: `docker stop worker_1`).è
