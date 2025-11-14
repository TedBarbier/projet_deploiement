-- Utiliser la base de données créée par les variables d'environnement
USE orion_db;

-- Créer la table pour notre inventaire (VIDE)
CREATE TABLE IF NOT EXISTS nodes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    -- Infos fournies par l'agent
    hostname VARCHAR(255) NOT NULL,
    ssh_port INT NOT NULL,
    
    -- Géré par le Scheduler (Tâche 1)
    status ENUM('unknown', 'alive', 'dead') NOT NULL DEFAULT 'unknown',
    last_checked TIMESTAMP NULL,
    
    -- Géré par l'API (location) et le Scheduler (expiration/migration)
    allocated BOOLEAN NOT NULL DEFAULT FALSE,
    allocated_to VARCHAR(100) NULL,
    lease_end_at TIMESTAMP NULL DEFAULT NULL,
    
    -- La contrainte d'unicité est critique
    UNIQUE KEY uq_node_inventory (hostname, ssh_port)
);