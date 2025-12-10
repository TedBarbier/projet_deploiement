#import "@preview/silky-report-insa:0.5.2": *
#import "@preview/codly:1.3.0": *
#import "@preview/codly-languages:0.1.1": *
#import "@preview/colorful-boxes:1.4.3": *
#codly(languages: codly-languages)
#show: codly-init.with()

#show: doc => insa-document(
  "pfe",
  insa: "cvl",
  cover-top-left: [*Rapport projet déploiement*],
  cover-middle-left: [
    *BARBIER Ted*


    Département STI
  ],
  page-header: "Rapport projet déploiement",
  include-back-cover: true,
  doc,
)

#set align(left)
#set heading(numbering: "1.1.1.")
#counter(page).update(2)

#outline()
#pagebreak()

= Introduction

Dans le cadre de l'unité d'enseignement "Déploiement & Infrastructure as Code", il nous a été demandé de concevoir et de réaliser une infrastructure simulant un fournisseur de *Platform as a Service* (PaaS). L'objectif principal est de créer un système capable de gérer dynamiquement un parc de machines (les "Workers"), de les louer à des clients pour une durée déterminée, et d'assurer la continuité de service même en cas de panne de matériel.

Le projet, baptisé *Orion-Dynamic* dans mon cas, se distingue par son approche de découverte automatique des ressources : contrairement à une configuration statique où le serveur connaît à l'avance ses nœuds, ici, c'est aux machines de s'enregistrer spontanément auprès du système central.

= Architecture & Concept

Le système repose sur une séparation claire entre le plan de contrôle (*Control Plane*) et le plan de données (*Data Plane*).

== Le Data Plane : Les Workers
Le *Data Plane* est constitué de l'ensemble des ressources de calcul disponibles à la location. Dans notre simulation, ces "machines" sont représentées par des conteneurs légers (Alpine Linux) exécutant un serveur SSH.

Chaque Worker embarque un *Agent* (script Python `agent.py`). Au démarrage de la machine, cet agent a pour unique responsabilité de contacter l'API centrale pour signaler sa présence, transmettant ses informations de connexion (IP, port SSH, nom d'hôte). Cette approche "Push" (l'agent s'annonce) plutôt que "Pull" (le serveur scanne le réseau) offre une grande flexibilité : n'importe quel nœud, quel que soit son réseau, peut rejoindre le cluster tant qu'il peut joindre l'API.

== Le Control Plane : Le Cerveau
Le *Control Plane* orchestre l'ensemble du système. Il est composé de plusieurs micro-services conteneurisés :

/ *Reverse Proxy (Caddy)*: Le point d'entrée unique. Il sécurise les accès (terminaison TLS simulée), route le trafic (HTTP vers les agents, HTTPS vers les clients) et assure l'équilibrage de charge (*Load Balancing*) vers les réplicas de l'API.
/ *API (Python)*: Le cœur réactif du système. Elle expose les points d'entrée pour l'enregistrement des workers et la gestion des locations par les clients. Elle est conçue pour être "Stateless" afin de pouvoir être dupliquée horizontalement selon la charge.
/ *Scheduler*: Le gestionnaire de tâches de fond. Indépendant de l'API, il boucle en permanence pour effectuer trois missions critiques :
  - *Health Check* : Vérifier que chaque worker est vivant (ping SSH).
  - *Migration* : Si un worker loué ne répond plus, déplacer immédiatement son client vers un noeud sain.
  - *Expiration* : Libérer les ressources dont le bail est terminé.
/ *Autoscaler*: Un service autonome qui surveille la consommation CPU des conteneurs de l'API et ajuste dynamiquement leur nombre (scaling horizontal) via le socket Docker.
/ *Base de Données (MariaDB)*: La source de vérité unique, stockant l'inventaire des machines, les utilisateurs et les baux actifs.

= Choix Techniques & Implémentation

== Infrastructure as Code (IaC) & Provisioning
L'ensemble de l'environnement de développement est défini dans un fichier `docker-compose.yml`, garantissant la reproductibilité.

Pour la configuration des Workers, nous avons choisi d'utiliser *Ansible*. Lorsqu'une location est validée ou qu'une migration est nécessaire, ce n'est pas l'API qui exécute des commandes SSH brutes, mais un playbook Ansible (`create_user.yml` ou `delete_user.yml`). Cela assure une idempotence et une gestion propre des erreurs lors de la création ou de la suppression des comptes utilisateurs sur les machines louées.

== Gestion de la Concurrence : SKIP LOCKED
Un défi majeur dans les systèmes distribués est d'éviter que deux processus ne traitent la même tâche simultanément (ex: deux schedulers essayant de migrer le même client).

Pour résoudre cela sans verrouillage global bloquant, nous utilisons la clause SQL `SELECT ... FOR UPDATE SKIP LOCKED` dans le Scheduler. Cela permet de récupérer les tâches (workers à vérifier) qui ne sont pas *déjà* verrouillées par une autre instance du scheduler. Grâce à cette technique, nous pouvons lancer autant d'instances du Scheduler que nécessaire pour paralléliser la charge de travail sans risque de collision.

== Scalabilité Automatique (Autoscaling)
Plutôt que d'utiliser des outils lourds comme Kubernetes pour ce projet, nous avons implémenté un *Autoscaler* léger en Python. Il interroge périodiquement les métriques des conteneurs via l'API Docker.
- Si la charge CPU moyenne des APIs dépasse 70%, il ajoute un réplica.
- Si elle descend sous 20%, il en retire un.
Caddy détecte automatiquement ces changements DNS et redistribue le trafic, rendant l'opération transparente pour l'utilisateur.

== Alternative Abandonnée : Vagrant
Dans une première itération, nous avons tenté d'isoler complètement l'environnement de développement dans une Machine Virtuelle (VM) gérée par *Vagrant*. L'objectif était de fournir un environnement totalement reproductible, indépendant de l'OS hôte (Mac/Linux/Windows).

Nous avons configuré un `Vagrantfile` utilisant une box `hashicorp-education/ubuntu-24-04` avec 4GB de RAM et 2 vCPUs. Le provisionnement par script Shell devait installer Docker et lancer le projet automatiquement :

```ruby
Vagrant.configure("2") do |config|
  config.vm.box = "hashicorp-education/ubuntu-24-04"
  # Forward ports for Caddy and API
  config.vm.network "forwarded_port", guest: 443, host: 8443
  config.vm.provision "shell", inline: <<-SHELL
    # Installation Docker & Compose
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg ...
    docker compose up -d
  SHELL
end
```

Cependant, cette approche a été abandonnée suite à des instabilités majeures : le processus de provisionnement "crashait" systématiquement lors de l'installation des paquets ou du lancement des conteneurs (problèmes probables de timeouts I/O). Face à ces blocages techniques qui ralentissaient le développement, nous avons privilégié l'utilisation directe de Docker Compose sur l'hôte, plus performante et fiable dans notre contexte.

= Fonctionnement du Projet

Le cycle de vie typique d'une ressource dans *Orion-Dynamic* suit ces étapes :

+ *Enregistrement Spontané* : Un nouveau Worker démarre. Son agent envoie une requête `POST /api/register`. Il apparaît immédiatement comme "libre" dans l'inventaire.
+ *Location (Allocation)* : Un client authentifié demande une machine pour 2 heures via `POST /api/rent`. L'API sélectionne un nœud libre, exécute le playbook Ansible pour créer un compte utilisateur temporaire, et renvoie les identifiants de connexion au client.
+ *Surveillance & Résilience* : Le Scheduler "ping" la machine toutes les 30 secondes. Si le client éteint accidentellement sa machine (simulation de panne), le Scheduler le détecte. Il trouve une nouvelle machine libre, y recrée le compte utilisateur (Migration), et met à jour la base de données. Le client perd sa connexion courante mais peut se reconnecter immédiatement sur la nouvelle IP fournie par l'API.
+ *Fin de Bail* : À l'heure de fin prévue, le Scheduler déclenche le "nettoyage". Le compte utilisateur est supprimé via Ansible, et la machine est marquée comme "libre" pour un futur client.

= Améliorations & Perspectives

== Réalisations Avancées
Au-delà du cahier des charges basique, nous avons intégré :
- Un *Load Balancing* dynamique avec Caddy.
- Un *Autoscaling* fonctionnel des instances API.
- Une gestion robuste de la *concurrence* base de données.

== Vers la Production
Pour transformer ce POC (*Proof of Concept*) en solution de production réelle, plusieurs axes seraient à prioriser :
- *Sécurité* : Actuellement, le socket Docker est monté dans l'autoscaler, ce qui est risqué. Il faudrait utiliser une API sécurisée ou un orchestrateur dédié. De plus, la gestion des secrets (clés SSH) devrait passer par un coffre-fort numérique (type HashiCorp Vault).
- *Orchestration* : Migrer le Control Plane vers *Kubernetes* permettrait de bénéficier nativement de l'autoscaling, des *Liveness Probes* et de la gestion des configurations, remplaçant avantageusement nos scripts "maison".
- *Observabilité* : Remplacer les logs texte par une stack *Prometheus + Grafana* pour visualiser en temps réel la charge, le nombre de locations et l'état de santé du parc.

= Conclusion

Ce projet a permis de mettre en pratique les concepts fondamentaux du déploiement moderne : conteneurisation, orchestration, interaction API/Agent et automatisation via Ansible. L'architecture réactive mise en place offre une base solide et résiliente, capable de s'adapter à la charge et aux pannes, répondant ainsi pleinement aux exigences d'un service PaaS moderne.


