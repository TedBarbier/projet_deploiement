# Scénarios de Challenges CTF pour le Projet CyberINSA

Ce document présente 15 scénarios de challenges pour le projet CTF CyberINSA, répartis en trois niveaux de difficulté : Facile, Moyen et Difficile. Chaque scénario est conçu pour être cohérent et réaliste, couvrant différentes catégories de la cybersécurité.

---

## Niveau Facile (10 challenges)

**Scénario Général :** Une jeune startup nommée "InnovateTech" vient de lancer son site vitrine. Cependant, en raison de l'inexpérience de ses développeurs, plusieurs vulnérabilités de base ont été laissées dans leur infrastructure, offrant une porte d'entrée pour les étudiants débutants.

### 1. Accès Interdit
> **Catégorie :** Web  
> **Nom du challenge :** Accès Interdit  
> **Scénario :** Une page d'administration (`admin.php`) existe sur le site web de la startup, mais elle est simplement "protégée" par une directive dans le fichier `robots.txt`. Le défi consiste à ignorer cette directive, trouver et accéder à la page pour récupérer le premier flag.

### 2. L'employé bavard
> **Catégorie :** OSINT (Open Source Intelligence)  
> **Nom du challenge :** L'employé bavard  
> **Scénario :** Le PDG d'InnovateTech a partagé une photo de son bureau sur un réseau social professionnel. En inspectant minutieusement cette photo, on peut apercevoir un post-it collé sur son écran avec un mot de passe écrit dessus. Ce mot de passe sera la clé pour un autre challenge.

### 3. Message Secret
> **Catégorie :** Cryptographie  
> **Nom du challenge :** Message Secret  
> **Scénario :** Un message codé a été laissé en commentaire dans le code source HTML de la page d'accueil du site. Il s'agit d'un simple chiffrement par décalage (type César). Les étudiants devront identifier le chiffrement et le décoder pour obtenir le flag.

### 4. Fuite de Données
> **Catégorie :** Réseau  
> **Nom du challenge :** Fuite de Données  
> **Scénario :** Une capture de trafic réseau (`capture.pcap`) est fournie. En analysant les paquets avec un outil comme Wireshark, les étudiants doivent trouver une requête HTTP contenant des identifiants (nom d'utilisateur et mot de passe) envoyés en clair.

### 5. L'image mystérieuse
> **Catégorie :** Stéganographie  
> **Nom du challenge :** L'image mystérieuse  
> **Scénario :** Le logo de l'entreprise affiché sur le site web cache des informations. Les étudiants devront utiliser des outils de stéganographie (comme steghide ou zsteg) pour extraire un fichier texte contenant le flag de l'image.

### 6. Injection SQL pour les nuls
> **Catégorie :** Web  
> **Nom du challenge :** Injection SQL pour les nuls  
> **Scénario :** La page de connexion du site est vulnérable à une injection SQL basique (`' OR '1'='1`). Les étudiants devront utiliser une charge utile simple pour contourner le formulaire d'authentification et accéder au panneau d'administration où se trouve le flag.

### 7. Droits d'accès
> **Catégorie :** Système  
> **Nom du challenge :** Droits d'accès  
> **Scénario :** Après avoir obtenu un accès initial à la machine, les étudiants découvrent un script appartenant à l'utilisateur `root` mais possédant des permissions d'exécution pour tous les utilisateurs. Ils devront analyser ce script pour comprendre comment il peut être exploité pour lire un fichier protégé, comme `/etc/shadow`.

### 8. Fichier supprimé
> **Catégorie :** Forensique  
> **Nom du challenge :** Fichier supprimé  
> **Scénario :** Une image disque d'une clé USB (`usb_dump.img`) est fournie. Un fichier contenant le flag a été effacé. Les étudiants devront utiliser des outils de récupération de données (comme `testdisk` ou `foremost`) pour retrouver et restaurer le fichier manquant.

### 9. Le mot de passe caché
> **Catégorie :** Reverse  
> **Nom du challenge :** Le mot de passe caché  
> **Scénario :** Un simple exécutable en ligne de commande demande un mot de passe pour afficher le flag. En utilisant la commande `strings` sur le binaire, les étudiants peuvent trouver le mot de passe en clair directement dans les chaînes de caractères du fichier.

### 10. Script d'authentification défaillant
> **Catégorie :** Programmation  
> **Nom du challenge :** Script d'authentification défaillant  
> **Scénario :** Un script Python simple gère une authentification locale. En analysant le code source, les étudiants remarqueront une faille logique dans la comparaison des mots de passe (par exemple, une comparaison de type faible ou une erreur de logique) qui leur permettra de s'authentifier sans connaître le mot de passe correct.

---

## Niveau Moyen (4 challenges)

**Scénario Général :** Après les premières intrusions, "InnovateTech" a tenté de renforcer sa sécurité. Les challenges se déroulent maintenant sur leur serveur de développement, où des vulnérabilités plus subtiles mais critiques persistent.

### 11. Cross-Site Scripting (XSS) Persistant
> **Catégorie :** Web  
> **Nom du challenge :** Cross-Site Scripting (XSS) Persistant  
> **Scénario :** Le blog de l'entreprise a une section de commentaires vulnérable à une attaque XSS stockée. Les étudiants doivent injecter un script qui vole le cookie de session de l'administrateur et l'envoie à un serveur qu'ils contrôlent. L'objectif est de récupérer le cookie de l'admin pour usurper son identité.

### 12. Escalade de privilèges via Sudo
> **Catégorie :** Système  
> **Nom du challenge :** Escalade de privilèges via Sudo  
> **Scénario :** Après avoir obtenu un accès utilisateur sur le serveur, la commande `sudo -l` révèle que l'utilisateur a le droit d'exécuter un programme spécifique (comme `find` ou `nmap`) en tant que `root` sans mot de passe. Les étudiants devront exploiter les fonctionnalités de ce programme pour obtenir un shell root.

### 13. Clé privée exposée
> **Catégorie :** Réseau & Cryptographie  
> **Nom du challenge :** Clé privée exposée  
> **Scénario :** Une nouvelle capture réseau (`traffic_dev.pcap`) est fournie. Elle contient une tentative de connexion SSH. En fouillant attentivement les autres paquets, les étudiants trouveront une clé privée RSA (`id_rsa`) qui a été accidentellement transférée via FTP. Ils devront extraire cette clé et l'utiliser pour se connecter au serveur en SSH.

### 14. Crackme
> **Catégorie :** Reverse & Programmation  
> **Nom du challenge :** Crackme  
> **Scénario :** Un programme binaire plus complexe que celui du niveau facile est fourni. Les étudiants devront le désassembler (avec `Ghidra` ou `gdb`) pour comprendre son algorithme de validation de mot de passe. L'objectif est de développer un petit script (key-gen) pour générer une clé valide qui débloquera le flag.

---

## Niveau Difficile (1 challenge en 5 étapes)

**Scénario Général :** "InnovateTech" a déployé une infrastructure réseau plus réaliste, simulant un environnement d'entreprise avec une DMZ et un réseau interne. L'objectif final est de pénétrer le réseau interne depuis le web et d'exfiltrer des données sensibles du serveur de fichiers.

### 15. Infiltration du Réseau d'Entreprise
> **Catégorie :** Multi-catégories (Web, Réseau, Système, Pivoting)  
> **Nom du challenge :** Infiltration du Réseau d'Entreprise  
> **Scénario :**
> *   **Étape 1 (Web - Compromission initiale) :** Le site web principal, hébergé sur une VM dans la DMZ, est vulnérable à une faille de téléversement de fichier. Les étudiants doivent exploiter cette vulnérabilité pour uploader un web-shell et obtenir un premier accès à la machine.
> *   **Étape 2 (Réseau & Pivoting - Découverte interne) :** Depuis le serveur web compromis, les étudiants découvrent une seconde interface réseau connectée à un LAN interne (ex: `192.168.1.0/24`). Ils doivent utiliser des outils de scan (`nmap`) et des techniques de pivoting (par exemple avec `ssh` ou `proxychains`) pour cartographier ce réseau et identifier les services ouverts.
> *   **Étape 3 (Système - Mouvement latéral) :** Le scan révèle un serveur de fichiers interne utilisant une version obsolète de Samba, vulnérable à une faille connue (comme EternalBlue/MS17-010 ou une autre CVE). Les étudiants doivent trouver et adapter un exploit public pour obtenir un accès sur cette deuxième machine.
> *   **Étape 4 (Système - Escalade de privilèges) :** L'accès obtenu sur le serveur de fichiers est celui d'un utilisateur à faibles privilèges. En fouillant le système, les étudiants découvrent une tâche `cron` mal configurée qui exécute un script avec les droits `root`. Ils doivent modifier ce script pour obtenir un shell root persistant.
> *   **Étape 5 (Forensique & Exfiltration - Objectif final) :** Le flag final est situé dans le répertoire personnel d'un administrateur sur le serveur de fichiers. Il est contenu dans un document sensible. Une fois les privilèges `root` obtenus, les étudiants doivent trouver ce document et exfiltrer le flag pour valider le challenge.
