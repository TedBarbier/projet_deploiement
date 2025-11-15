-- Utiliser la base créée par Docker (via les variables d'environnement)
USE orion_db;

-- ===========================
--  TABLE DES NŒUDS (WORKERS)
-- ===========================
CREATE TABLE IF NOT EXISTS nodes (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- Infos fournies par l'agent
    hostname VARCHAR(255) NOT NULL,
    ssh_port INT NOT NULL,

    -- Géré par le Scheduler
    status ENUM('unknown', 'alive', 'dead') NOT NULL DEFAULT 'unknown',
    last_checked TIMESTAMP NULL,

    -- Alloué ou non (sera utilisé comme "lock" rapide)
    allocated BOOLEAN NOT NULL DEFAULT FALSE,

    UNIQUE KEY uq_node_inventory (hostname, ssh_port)
);

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
    node_id INT NOT NULL,

    leased_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    leased_until TIMESTAMP NOT NULL,

    active BOOLEAN NOT NULL DEFAULT TRUE,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);
