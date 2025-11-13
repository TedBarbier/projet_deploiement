### 1. 📋 Titre du Projet (Mise à jour)

**Projet "Orion-Sec" : Orchestrateur de Ressources Dynamiques à Haute Disponibilité avec Périphérie Sécurisée**

* **Matière :** Déploiement & Infrastructure as Code (IaC)
* **Objectif :** Simuler une plateforme PaaS capable de "louer" des environnements (conteneurs), en garantissant la QoS (migration automatique) et la sécurité via un **Reverse Proxy** en frontal.

---

### 2. 🎯 Contexte et Objectifs

Les objectifs de base restent les mêmes :
1.  Gérer un inventaire de ressources via "Call Home".
2.  Surveiller la santé des ressources via "Heartbeat".
3.  Provisionner les ressources avec Ansible.
4.  Garantir la résilience par la migration automatique.

L'objectif **supplémentaire** (et crucial) est :
5.  **Sécuriser l'accès :** L'API de l'Orchestrateur ne doit **jamais** être exposée directement. Tout le trafic (clients et workers) doit passer par un Reverse Proxy (Nginx) qui sert de point d'entrée unique.

---

### 3. 🏗️ Architecture Globale (Mise à jour)

Le système est composé d'un réseau Docker unique contenant **trois** types de services :

* **1. Reverse Proxy (ex: Nginx)**
    * **Rôle :** Le "Bouclier". C'est le **seul** service exposé à l'extérieur (ex: sur le port `8080`).
    * Il reçoit 100% du trafic entrant (locations clients, enregistrements workers).
    * Il gère le routage interne vers l'API et peut gérer la terminaison SSL, le rate limiting, etc.

* **2. Orchestrateur (Application API - ex: Flask/Python)**
    * **Rôle :** Le "Cerveau". Ce conteneur n'expose **aucun** port. Il est inaccessible de l'extérieur.
    * Il contient la logique métier (API REST), la connexion à la DB (SQLite), le moteur Ansible, et le Scheduler de santé.

* **3. Nœuds (Workers - ex: Alpine+SSH)**
    * **Rôle :** Le "Pool de ressources".
    * Chaque conteneur exécute un serveur SSH (pour Ansible) et un script "Agent" (pour le Call Home/Heartbeat).

---

### 4. 🌐 Schéma Réseau Détaillé (avec Reverse Proxy)

Ce schéma illustre les flux de communication.
```
```
+---------------------------------------------------------------------------------------+
| HÔTE (Votre Machine)                                                                  |
| Port 8080 (exposé)                                                                    |
+---------------------------------------------------------------------------------------+
      |
      | (Flux 1: Location Client) - POST /api/rent
      | (Flux 2: Call Home Worker) - POST /api/register
      |
+---------------------------------------------------------------------------------------+
| Réseau Docker ("orion_net" - ex: 172.20.0.0/16)                                       |
|                                                                                       |
|     +---------------------------+                                                     |
|     | Reverse Proxy (Nginx)     |                                                     |
|     | DNS: "manager_proxy"      |                                                     |
|     | IP: 172.20.0.2            |                                                     |
|     | (Port 80 exposé *interne*) |                                                     |
|     +---------------------------+                                                     |
|           |                                                                           |
| (Flux 1b/2b) | Routage interne (proxy_pass)                                            |
| (HTTP)       v                                                                        |
|     +---------------------------+       (Flux 3: Provisioning)                        |
|     | Orchestrateur API (Flask) | -----------------> (SSH, par IP) -> +----------------+
|     | DNS: "manager_app"        | (Ansible)                           | Worker 1       |
|     | IP: 172.20.0.3            |                                     | IP: 172.20.0.4 |
|     | (Aucun port exposé)       | -----------------> (SSH, par IP) -> +----------------+
|     +---------------------------+ (Ansible)                           | Worker 2       |
|                                                                       | IP: 172.20.0.5 |
|                                                                       +----------------+
|                                                                                       |
+---------------------------------------------------------------------------------------+
```
```
**Explication des flux :**

* **Flux 1 (Location Client) :**
    * Un client externe envoie `POST http://<votre_ip>:8080/api/rent`.
    * Le **Proxy** (Nginx) reçoit cette requête.
    * Le Proxy la transfère en interne à `http://manager_app:5000/api/rent`.
    * L'**API App** la traite, appelle Ansible (Flux 3) et répond.

* **Flux 2 (Call Home Worker) :**
    * Un Worker démarre. Son script agent envoie `POST http://manager_proxy:80/api/register`.
    * *Note :* Le worker utilise le nom DNS du *proxy* et le port *interne* de Nginx (port 80).
    * Le **Proxy** reçoit la requête et la transfère à `http://manager_app:5000/api/register`.

* **Flux 3 (Provisioning Ansible) :**
    * Ce flux est initié par l'**API App** (suite à une location).
    * Il ne passe **pas** par le proxy. C'est l'API App qui se connecte *directement* en SSH à l'IP du Worker (ex: `ssh 172.20.0.4`).

---

### 5. 🛠️ Fonctionnalités Détaillées (Mises à jour)

#### 5.1. IaC et Déploiement
* **En tant qu'Admin,** je veux un `docker-compose.yml` qui déploie **trois services** : `manager_proxy`, `manager_app`, et `worker`.
* Le service `manager_proxy` sera le seul à exposer un port (ex: `8080:80`).
* Le service `manager_app` ne doit exposer aucun port.
* Je veux pouvoir scaler le service `worker` avec `docker-compose up --scale worker=N`.

#### 5.2. Gestion du Pool (Workers)
* **En tant que Worker,** au démarrage, je dois exécuter un script agent.
* **En tant que Worker,** je dois m'enregistrer ("Call Home") en contactant le **reverse proxy** sur son nom de service interne (ex: `http://manager_proxy/api/register`).
* **En tant que Worker,** je dois envoyer un "Heartbeat" (ex: `http://manager_proxy/api/heartbeat`) toutes les 30 secondes.

*(Les sections 5.3 (API) et 5.4 (Résilience) ne changent pas dans leur logique métier, elles sont juste "protégées" par le proxy).*

---

### 6. 📦 Livrables Attendus (Mis à jour)

1.  **Code Source :** Le code de l'API de l'Orchestrateur (ex: `manager_app`).
2.  **Fichiers IaC :**
    * `docker-compose.yml` (définissant les 3 services et le réseau).
    * `Dockerfile` pour le service `manager_app` (installant Python, Flask, Ansible, SQLite...).
    * `Dockerfile` pour le service `worker` (installant `openssh-server` + script d'agent).
    * **Nouveau :** Un fichier de configuration pour le `manager_proxy` (ex: `nginx.conf`) qui gère le `proxy_pass` vers `manager_app`.
3.  **Scripts d'Automatisation :**
    * Les Playbooks Ansible (ex: `create_user.yml`, `delete_user.yml`).
    * Le script "agent" (bash ou python) pour le "Call Home" et le "Heartbeat".
4.  **Documentation :** Un `README.md` expliquant l'architecture (avec le proxy) et comment simuler un flux complet (lancement, location, panne).
