#import "@preview/silky-report-insa:0.5.2": *
#import "@preview/codly:1.3.0": *
#import "@preview/codly-languages:0.1.1": *
#import "@preview/colorful-boxes:1.4.3": *
#codly(languages: codly-languages)
#show: codly-init.with()

#show: doc => insa-document(
  "pfe",
  insa: "cvl",
  cover-top-left: [*Rapport de Projet : Déploiement & Infrastructure*],
  cover-middle-left: [
    *BARBIER Ted* \
    Département STI \
    4ème Année
  ],
  page-header: "Orion-Dynamic : PaaS à inventaire dynamique",
  include-back-cover: true,
  doc,
)

#set align(left)
#set heading(numbering: "1.1.1.")

#outline(indent: auto)
#pagebreak()

= Introduction

Dans le cadre de l'unité d'enseignement "Déploiement & Infrastructure as Code", ce projet vise à concevoir un prototype de *Platform as a Service* (PaaS) baptisé *Orion-Dynamic*.

L'objectif est de gérer dynamiquement un parc de machines (les "Workers"), de les louer à des clients pour une durée déterminée, et d'assurer la continuité de service même en cas de panne de matériel. Ce projet explore le concept d'*infrastructure opportuniste* : les ressources de calcul ne sont pas connues à l'avance par le serveur, mais s'enregistrent spontanément. Ce modèle répond aux besoins de plateformes modernes où les ressources peuvent être volatiles, géographiquement distribuées et éphémères.

= Architecture Système : Philosophie Micro-services

Le système repose sur une séparation stricte entre le plan de contrôle (*Control Plane*) et le plan de données (*Data Plane*).

== Le Data Plane : Les Workers "Opportunistes"
Le *Data Plane* est constitué des ressources de calcul. Dans notre simulation, ces machines sont représentées par des conteneurs légers (Alpine Linux) exécutant un serveur SSH.

Chaque Worker embarque un *Agent* (`agent.py`). Au démarrage de la machine, cet agent a pour unique responsabilité de contacter l'API centrale pour signaler sa présence.
- *Choix du mode Push* : Contrairement à une configuration statique où le serveur doit scanner le réseau (Pull), c'est ici le nœud qui "pousse" ses informations. Cela permet d'outrepasser les contraintes de NAT/Firewall et d'ajouter des capacités de calcul instantanément sans reconfigurer le serveur central.

== Le Control Plane : L'intelligence orchestrée
Le *Control Plane* est composé de plusieurs micro-services conteneurisés, chacun ayant une responsabilité unique :

/ *Reverse Proxy (Caddy)*: Sert d'API Gateway unique. Il assure l'équilibrage de charge (*Load Balancing*) vers les réplicas de l'API.
/ *API (Python/FastAPI)*: Le cœur réactif du système. Elle gère l'enregistrement des workers et les baux clients. Elle est strictement "Stateless".
/ *Scheduler*: Assure la cohérence de l'infrastructure. Il effectue les "Health Checks", les migrations et le nettoyage des baux expirés.
/ *Autoscaler*: Un service de régulation en boucle fermée qui ajuste le nombre de réplicas de l'API en fonction de la charge CPU réelle détectée sur le socket Docker.
/ *Base de Données (MariaDB)*: Stocke l'état du parc, les utilisateurs et les contrats de location (baux).

= Choix Techniques & Justifications Critiques

== Pourquoi Python et l'API REST ?
Le choix de Python s'est imposé pour sa rapidité de prototypage et sa compatibilité native avec les outils d'automatisation comme Ansible.
- *Justification REST* : Nous avons privilégié REST plutôt que gRPC ou des brokers de messages. Dans un contexte de PaaS ouvert, REST est le standard universel : n'importe quel terminal peut s'enregistrer via une simple requête HTTP JSON, garantissant une interopérabilité maximale.

== MariaDB : Le choix de l'intégrité transactionnelle
Le choix d'une base SQL (MariaDB) plutôt que NoSQL est dicté par le besoin de *cohérence forte*.
- *Problématique de concurrence* : Deux clients ne doivent jamais pouvoir louer la même machine simultanément.
- *Solution* : Les transactions ACID de MariaDB permettent de verrouiller l'état d'un worker lors de son allocation, rendant l'opération atomique.

== Sécurité, Isolation et Monétisation via Ansible
Le but du service est de louer une ressource tout en garantissant qu'elle reste sous notre contrôle technique (condition sine qua non pour la facturation).
- *Stratégie* : Nous utilisons Ansible pour créer un utilisateur temporaire avec des droits restreints.
- *Justification* : Plutôt que de donner un accès root (risqué), nous isolons le client. Ansible assure l'idempotence : si la création échoue, le système peut retenter sans corrompre la machine. À l'expiration du bail, Ansible supprime l'utilisateur, garantissant que la ressource redevient disponible pour un nouveau cycle de facturation.

= Analyse de la Stratégie de Développement : Le Cas Vagrant

Une phase importante du projet a concerné la mise en place d'une *parité d'environnement* via Vagrant.

== L'intention : Un environnement universel
L'objectif était d'utiliser Vagrant pour créer une machine virtuelle (VM) "bac à sable" standardisée. Cette VM devait héberger l'intégralité du projet (Docker, API, Workers) afin de garantir que n'importe quel développeur, qu'il soit sur Windows, Linux ou macOS, travaille sur une configuration bit-à-bit identique.

== L'impasse technique : Conflit d'architecture (ARM vs x86)
La tentative a été abandonnée après environ deux heures de tests infructueux.
- *Cause du blocage* : Le développement s'est déroulé sur une architecture *Apple Silicon (ARM64)*. La virtualisation d'images Linux standard (souvent optimisées pour x86_64) au sein de Vagrant a provoqué des instabilités majeures, des plantages du processus de provisionnement et des timeouts I/O lors de l'installation de Docker.
- *Décision d'ingénierie* : Le choix a été fait de pivoter vers une approche *Native Docker Compose*.
- *Leçon retenue* : Si la virtualisation (Vagrant) offre une isolation supérieure, elle introduit une couche de complexité (virtualisation imbriquée) parfois incompatible avec les nouvelles architectures matérielles. La conteneurisation native s'est révélée être un compromis plus pragmatique et performant.

= Implémentation de la Résilience

== Gestion de la Concurrence massive : SKIP LOCKED
Un défi majeur est d'éviter que deux instances du Scheduler ne traitent le même worker simultanément (ex: deux migrations pour une même panne).
- *Solution technique* : Utilisation de la clause SQL `SELECT ... FOR UPDATE SKIP LOCKED`.
- *Analyse* : Cela permet de paralléliser le Control Plane. Chaque instance "pioche" une tâche libre sans bloquer les autres instances. C'est ce qui permet au système de supporter des milliers de nœuds sans ralentissement.

== Cycle de vie d'une ressource (WorkFlow)
+ *Découverte* : Le Worker s'annonce via `POST /api/workers/register`.
+ *Allocation* : Le client appelle `POST /api/rent`. L'API sélectionne un nœud, exécute le playbook Ansible et renvoie les accès.
+ *Surveillance* : Le Scheduler effectue un Health Check (Ping SSH) toutes les 30s. Après 3 échecs, la *Migration* est déclenchée : le client est déplacé sur un nœud sain de manière transparente.
+ *Libération/Extension* : Le client peut prolonger son bail via `/api/extend` ou laisser le Scheduler nettoyer la machine à l'échéance.

= Conclusion

Le projet *Orion-Dynamic* valide la possibilité de construire un service PaaS robuste sur un inventaire volatil. Les choix techniques effectués privilégient la résilience (Migration auto), la scalabilité (Autoscaling) et l'intégrité (SQL/Ansible). Le pivot stratégique de Vagrant vers Docker illustre la capacité d'adaptation nécessaire à la gestion de projets complexes sur des architectures hétérogènes.

#pagebreak()
= Annexes Techniques

== Détails des Endpoints API
- `POST /api/signup` : Création de compte utilisateur.
- `POST /api/login` : Authentification et génération de JWT.
- `POST /api/rent` : Location de workers (allocation dynamique).
- `GET /api/nodes` : Monitoring en temps réel du parc.

== Structure du Projet
- `/control-plane` : Micro-services Python et Dockerfiles.
- `/playbooks` : Logic Ansible d'isolation utilisateur.
- `Caddyfile` : Configuration du Load Balancer.
- `launch_workers.sh` : Script de scalabilité du Data Plane.
