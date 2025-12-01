-- Utiliser la base créée par Docker (via les variables d'environnement)
USE orion_db;

-- ===========================
--  TABLE DES NŒUDS (WORKERS)
-- ===========================
CREATE TABLE IF NOT EXISTS nodes (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- Infos fournies par l'agent
    hostname VARCHAR(255) NOT NULL,
    ip VARCHAR(45) NOT NULL,
    ssh_port INT NOT NULL,

    -- Géré par le Scheduler
    status ENUM('unknown', 'alive', 'dead') NOT NULL DEFAULT 'unknown',
    last_checked TIMESTAMP NULL,

    -- Alloué ou non (sera utilisé comme "lock" rapide)
    allocated BOOLEAN NOT NULL DEFAULT FALSE,

    -- Géré par un scheduler
    scheduler_id INT,

    UNIQUE KEY uq_node_inventory (hostname, ip, ssh_port)
);

-- Index pour la table nodes
CREATE INDEX idx_nodes_status ON nodes(status);
CREATE INDEX idx_nodes_allocated ON nodes(allocated);
CREATE INDEX idx_nodes_scheduler_id ON nodes(scheduler_id);

-- ===========================
--  TABLE DES UTILISATEURS
-- ===========================
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('user', 'admin') NOT NULL DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ===========================
--  TABLE DES LOCATIONS
--  (permet plusieurs locations, release, extend, historique)
-- ===========================
CREATE TABLE IF NOT EXISTS rentals (
    id INT AUTO_INCREMENT PRIMARY KEY,

    user_id INT NOT NULL,
    ssh_password VARCHAR(255) NULL,
    node_id INT NOT NULL,

    leased_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    leased_until TIMESTAMP NOT NULL,

    active BOOLEAN NOT NULL DEFAULT TRUE,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

-- Index pour la table rentals
CREATE INDEX idx_rentals_user_id ON rentals(user_id);
CREATE INDEX idx_rentals_node_id ON rentals(node_id);
CREATE INDEX idx_rentals_leased_until_active ON rentals(leased_until, active);

-- ===========================
--  TABLE DES SCHEDULERS
-- ===========================
CREATE TABLE IF NOT EXISTS scheduler_ranges (
    scheduler_id INT AUTO_INCREMENT PRIMARY KEY,
    start_id INT NOT NULL,
    end_id INT NOT NULL
);
