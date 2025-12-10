const API = "";  // ex: "http://localhost:8080" si nécessaire

function saveToken(token) {
    localStorage.setItem("jwt", token);
}

function getToken() {
    return localStorage.getItem("jwt");
}

function logout() {
    localStorage.removeItem("jwt");
    location.reload();
}

function showLoadingIndicator() {
    const loadingDiv = document.createElement("div");
    loadingDiv.id = "loading-indicator";
    loadingDiv.style.position = "fixed";
    loadingDiv.style.top = "50%";
    loadingDiv.style.left = "50%";
    loadingDiv.style.transform = "translate(-50%, -50%)";
    loadingDiv.style.padding = "10px 20px";
    loadingDiv.style.backgroundColor = "rgba(0, 0, 0, 0.8)";
    loadingDiv.style.color = "white";
    loadingDiv.style.borderRadius = "5px";
    loadingDiv.style.zIndex = "1000";
    loadingDiv.textContent = "Chargement...";
    document.body.appendChild(loadingDiv);
}

function hideLoadingIndicator() {
    const loadingDiv = document.getElementById("loading-indicator");
    if (loadingDiv) {
        document.body.removeChild(loadingDiv);
    }
}



async function login() {
    let username = document.getElementById("username").value;
    let password = document.getElementById("password").value;

    const res = await api("/api/login", "POST", { username, password });

    if (res.token) {
        saveToken(res.token);
        startDashboard(username);
    } else {
        document.getElementById("login-error").innerText = res.error || "Erreur";
    }
}

async function register() {
    const username = document.getElementById("register-username").value;
    const password = document.getElementById("register-password").value;

    const res = await api("/api/signup", "POST", {
        username,
        password
    });

    const resultElement = document.getElementById("register-result");
    if (res.error) {
        resultElement.textContent = res.error || "Erreur";
        resultElement.style.color = "red";
        return;
    }

    resultElement.style.color = "green";
    resultElement.textContent = "Compte créé ! Tu peux te connecter.";
}

function startDashboard(username) {
    document.getElementById("login-box").style.display = "none";
    document.getElementById("dashboard").style.display = "block";
    document.getElementById("user-name").innerText = username;

    loadNodes();
}

// Stocker les données actuelles pour comparaison
let currentNodes = null;

async function api(path, method = "GET", body = null) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();

    if (token) headers["Authorization"] = "Bearer " + token;

    console.log("Appel API:", {
        url: API + path,
        method,
        headers,
        body: body ? JSON.stringify(body) : null
    }); // Log des détails de l'appel API

    showLoadingIndicator();
    try {
        const res = await fetch(API + path, {
            method,
            headers,
            body: body ? JSON.stringify(body) : null
        });

        if (!res.ok) {
            if (res.status === 401) {
                console.warn("Session expirée ou non autorisée (401). Déconnexion...");
                logout();
                return { error: "Session expirée" };
            }
            console.error(`Erreur réseau: ${res.status} ${res.statusText}`);
            throw new Error(`Erreur réseau: ${res.status} ${res.statusText}`);
        }

        return await res.json();
    } catch (error) {
        console.error("Erreur lors de l'appel API:", error);
        return { error: "Une erreur réseau est survenue. Veuillez réessayer plus tard." };
    } finally {
        hideLoadingIndicator();
    }
}

async function loadNodes() {
    const token = getToken();
    if (!token) return;

    const res = await api("/api/nodes");
    const container = document.getElementById("nodes");

    if (res.error) {
        container.innerHTML = "<p style='color:red'>" + res.error + "</p>";
        return;
    }

    // Vérifier si res est un tableau
    if (!Array.isArray(res) || res.length === 0) {
        container.innerHTML = "<p>Aucun nœud disponible.</p>";
        return;
    }

    // Comparer les nouvelles données avec les données actuelles
    if (JSON.stringify(res) !== JSON.stringify(currentNodes)) {
        currentNodes = res; // Mettre à jour les données actuelles
        container.innerHTML = ""; // Réinitialiser le conteneur

        const now = new Date();
        console.log("Heure actuelle (locale) :", now.toString());

        res.forEach(node => {
            let nodeDiv = document.querySelector(`.node[data-id='${node.node_id}']`);

            if (!nodeDiv) {
                nodeDiv = document.createElement("div");
                nodeDiv.className = "node";
                nodeDiv.setAttribute("data-id", node.node_id);
                container.appendChild(nodeDiv);
            }

            const leaseEnd = node.lease ? new Date(node.lease.leased_until + 'Z') : null; // Ajouter 'Z' pour indiquer UTC
            if (leaseEnd) {
                // Convertir l'heure UTC en heure locale
                const localLeaseEnd = leaseEnd.toLocaleString('fr-FR', {
                    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone, // Fuseau horaire local détecté automatiquement
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit'
                });

                console.log(`Nœud ${node.node_id} - Fin du bail (locale) :`, localLeaseEnd);

                nodeDiv.innerHTML = `
                    <b>Node ${node.node_id}</b><br>
                    Hostname: ${node.hostname}<br>
                    Port SSH: ${node.ssh_port}<br>
                    Status: ${node.status}<br>
                    Allocated: ${node.allocated}<br>
                    ${node.lease ? `
                        <p><b>Lease ${node.lease.rental_id}</b><br>
                        Jusqu'à (heure locale) : ${localLeaseEnd}</p>
                        <p><b>Mot de passe SSH :</b> <button onclick="fetchPassword(${node.lease.rental_id}, this)">Afficher</button></p>
                        <button onclick="release(${node.lease.rental_id})">Release</button>
                        <button onclick="showExtendForm(${node.lease.rental_id})">Extend</button>
                    ` : `<i>Libre</i>`}
                `;
            }
        });
    }
}

async function fetchPassword(rentalId, button) {
    try {
        const res = await api(`/api/lease/${rentalId}/password`); // Correction de l'interpolation de rentalId

        if (res.error) {
            console.error("Erreur API:", res.error);
            alert("Erreur: " + res.error);
            return;
        }

        // Afficher le mot de passe dans le DOM
        const passwordElement = document.createElement("span");
        passwordElement.textContent = res.ssh_password;
        button.parentNode.replaceChild(passwordElement, button);
    } catch (error) {
        console.error("Erreur lors de la récupération du mot de passe:", error);
        alert("Une erreur est survenue lors de la récupération du mot de passe. Veuillez vérifier votre connexion réseau ou réessayer plus tard.");
    }
}

// Stocker les données actuelles pour comparaison
let currentData = null;

// Fonction pour comparer les données
function hasDataChanged(newData) {
    return JSON.stringify(currentData) !== JSON.stringify(newData);
}

// Mettre à jour les données et afficher un message si nécessaire
async function refreshData() {
    const token = getToken();
    if (!token) {
        console.warn("Utilisateur non connecté. Pas de mise à jour.");
        return;
    }

    try {
        const response = await fetch("/api/nodes", {
            headers: { Authorization: `Bearer ${token}` },
        });

        if (response.ok) {
            const newData = await response.json();

            // S'assurer que newData est bien un tableau
            if (Array.isArray(newData)) {
                if (hasDataChanged(newData)) {
                    // Vérifier les changements significatifs
                    const significantChanges = newData.filter((newNode, index) => {
                        const currentNode = currentData ? currentData[index] : null;
                        return (
                            !currentNode ||
                            newNode.status !== currentNode.status ||
                            newNode.node_id !== currentNode.node_id
                        );
                    });

                    if (significantChanges.length > 0) {
                        currentData = newData; // Mettre à jour les données actuelles
                        updateDashboard(newData); // Fonction pour mettre à jour l'interface
                    }
                }
            }
        } else {
            if (response.status === 401) {
                logout();
                return;
            }
            console.error("Erreur lors de la récupération des données.");
        }
    } catch (error) {
        console.error("Erreur réseau lors de la mise à jour des données.", error);
    }
}

async function rent() {
    const hours = parseInt(document.getElementById("rent-hours").value);
    const count = parseInt(document.getElementById("rent-count").value);
    const customPassword = document.getElementById("rent-password").value;

    const body = {
        duration_hours: hours,
        count: count
    };

    // Ajouter le password personnalisé si fourni
    if (customPassword && customPassword.trim() !== "") {
        body.ssh_password = customPassword.trim();
    }

    const res = await api("/api/rent", "POST", body);

    if (res.error) {
        alert("Erreur: " + res.error);
    } else {
        // Afficher les infos de connexion SSH
        let message = "✅ Location réussie !\n\n";
        res.allocated.forEach(rental => {
            message += `=== Nœud ${rental.rental_id} ===\n`;
            message += `IP: ${rental.host_ip}\n`;
            message += `Port SSH: ${rental.ssh_port}\n`;
            message += `Utilisateur: ${rental.client_user}\n`;
            message += `Mot de passe: ${rental.client_pass}\n`;
            message += `Expire le: ${new Date(rental.leased_until).toLocaleString()}\n\n`;
            message += `Commande SSH:\nssh ${rental.client_user}@${rental.host_ip} -p ${rental.ssh_port}\n\n`;
        });
        alert(message);

        // Réinitialiser le champ password
        document.getElementById("rent-password").value = "";
    }

    loadNodes();
}

async function release(id) {
    const res = await api(`/api/release/${id}`, "POST");

    if (res.error) {
        alert("Erreur: " + res.error);
        return;
    }

    // Supprimer le nœud du DOM
    const nodeElement = document.querySelector(`.node[data-id='${id}']`);
    if (nodeElement) {
        nodeElement.remove();
    }

    alert("Nœud libéré avec succès.");
}

async function extend(id, additionalHours) {
    console.log(`Début de l'exécution de la fonction extend pour le node ID: ${id}`);
    console.log(`Heures supplémentaires demandées: ${additionalHours}`);

    const res = await api(`/api/extend/${id}`, "POST", {
        additional_hours: additionalHours
    });

    console.log("Réponse de l'API:", res);
    alert(JSON.stringify(res, null, 2));
    loadNodes();
    console.log("Fin de l'exécution de la fonction extend");
}

function showRegister() {
    const registerBox = document.getElementById("register-box");
    const loginBox = document.getElementById("login-box");

    if (registerBox && loginBox) {
        loginBox.style.display = "none";
        registerBox.style.display = "block";
    } else {
        console.error("L'élément 'register-box' ou 'login-box' est introuvable dans le DOM.");
    }
}

function showLogin() {
    const registerBox = document.getElementById("register-box");
    const loginBox = document.getElementById("login-box");

    if (registerBox && loginBox) {
        loginBox.style.display = "block";
        registerBox.style.display = "none";
    } else {
        console.error("L'élément 'register-box' ou 'login-box' est introuvable dans le DOM.");
    }
}

document.getElementById("password").addEventListener("keypress", function (event) {
    if (event.key === "Enter") {
        login();
    }
});

// Vérifier le token au chargement de la page
window.addEventListener("load", () => {
    const token = getToken();
    if (token) {
        try {
            const payload = JSON.parse(atob(token.split(".")[1]));
            const now = Math.floor(Date.now() / 1000);

            if (payload.exp > now) {
                startDashboard(payload.username);
            } else {
                logout();
            }
        } catch (e) {
            console.error("Token invalide", e);
            logout();
        }
    }
});

// Déconnexion automatique après 15 minutes d'inactivité
let inactivityTimeout;
function resetInactivityTimeout() {
    clearTimeout(inactivityTimeout);
    inactivityTimeout = setTimeout(() => {
        alert("Session expirée en raison d'une inactivité prolongée.");
        logout();
    }, 15 * 60 * 1000); // 15 minutes
}

// Réinitialiser le timer d'inactivité sur les événements utilisateur
["mousemove", "keydown", "click"].forEach(event => {
    window.addEventListener(event, resetInactivityTimeout);
});

// Initialiser le timer d'inactivité au chargement de la page
resetInactivityTimeout();

// Ajout d'un rafraîchissement automatique des nœuds
setInterval(loadNodes, 30000); // Rafraîchit toutes les 30 secondes

// Appeler la fonction de rafraîchissement périodiquement
setInterval(refreshData, 30000); // Toutes les 30 secondes

// Fonction pour mettre à jour le tableau des nœuds
function updateDashboard(nodes) {
    const tableBody = document.getElementById("nodes-table-body");
    tableBody.innerHTML = ""; // Réinitialiser le contenu du tableau

    nodes.forEach(node => {
        const row = document.createElement("tr");

        row.innerHTML = `
            <td>${node.node_id}</td>
            <td>${node.hostname}</td>
            <td>${node.status}</td>
            <td>${node.ssh_port}</td>
            <td>${node.lease?.ssh_password || "N/A"}</td>
        `;

        tableBody.appendChild(row);
    });
}

// Ajout de logs supplémentaires pour le débogage
function showExtendForm(nodeId) {
    console.log("Affichage du formulaire pour le nœud :", nodeId); // Log pour débogage

    // Créer une popup pour le formulaire
    const popup = document.createElement("div");
    popup.id = "extend-popup";
    popup.style.position = "fixed";
    popup.style.top = "50%";
    popup.style.left = "50%";
    popup.style.transform = "translate(-50%, -50%)";
    popup.style.padding = "20px";
    popup.style.backgroundColor = "white";
    popup.style.boxShadow = "0 4px 8px rgba(0, 0, 0, 0.2)";
    popup.style.borderRadius = "8px";
    popup.style.zIndex = "1000";

    popup.innerHTML = `
        <h3>Étendre la location</h3>
        <label for="extend-hours">Durée d'extension (en heures) :</label>
        <input type="number" id="extend-hours" min="1" placeholder="Entrez le nombre d'heures">
        <div style="margin-top: 10px;">
            <button id="confirm-extend-button" data-node-id="${nodeId}">Confirmer</button>
            <button id="cancel-extend-button">Annuler</button>
        </div>
    `;

    document.body.appendChild(popup);
}

// Gestionnaire d'événements délégué pour le bouton Confirmer
document.body.addEventListener("click", (event) => {
    if (event.target && event.target.id === "confirm-extend-button") {
        console.log("Bouton Confirmer cliqué");
        const nodeId = event.target.getAttribute("data-node-id");
        const additionalHours = parseInt(document.getElementById("extend-hours").value);
        console.log("Durée saisie :", additionalHours);
        if (isNaN(additionalHours) || additionalHours <= 0) {
            alert("Veuillez entrer une durée valide.");
            return;
        }
        console.log("Appel de la fonction extend avec nodeId:", nodeId, "et additionalHours:", additionalHours);
        extend(nodeId, additionalHours);
        const popup = document.getElementById("extend-popup");
        if (popup) {
            document.body.removeChild(popup); // Supprimer la popup après confirmation
        }
    }

    if (event.target && event.target.id === "cancel-extend-button") {
        const popup = document.getElementById("extend-popup");
        if (popup) {
            document.body.removeChild(popup); // Supprimer la popup si annulé
        }
    }
});

// Ajout d'une fonction pour vérifier si l'utilisateur est admin
function isAdmin() {
    const token = getToken();
    if (!token) return false;

    try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        return payload.role === "admin"; // Supposons que le rôle est stocké dans le champ "role"
    } catch (e) {
        console.error("Erreur lors de la vérification du rôle admin", e);
        return false;
    }
}

// Exemple d'utilisation pour afficher le bouton Bail spécial 2 minutes
function renderSpecialButton() {
    const container = document.getElementById("special-buttons"); // Conteneur pour les boutons spéciaux
    if (!container) return;

    if (isAdmin()) {
        const specialButton = document.createElement("button");
        specialButton.textContent = "Bail spécial 2 minutes";
        specialButton.onclick = () => {
            // Logique pour le bail spécial
            alert("Bail spécial activé pour 2 minutes !");
        };
        container.appendChild(specialButton);
    }
}

// Appeler la fonction après le chargement du tableau de bord
window.addEventListener("load", () => {
    renderSpecialButton();
});
