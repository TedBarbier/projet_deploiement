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

async function api(path, method="GET", body=null) {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();

    if (token) headers["Authorization"] = "Bearer " + token;

    const res = await fetch(API + path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null
    });

    return res.json();
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

function startDashboard(username) {
    document.getElementById("login-box").style.display = "none";
    document.getElementById("dashboard").style.display = "block";
    document.getElementById("user-name").innerText = username;

    loadNodes();
}

async function loadNodes() {
    const res = await api("/api/nodes");
    const container = document.getElementById("nodes");
    container.innerHTML = "";

    if (res.error) {
        container.innerHTML = "<p style='color:red'>" + res.error + "</p>";
        return;
    }

    res.forEach(node => {
        const div = document.createElement("div");
        div.className = "node";

        div.innerHTML = `
            <b>Node ${node.node_id}</b><br>
            Hostname: ${node.hostname}<br>
            Port SSH: ${node.ssh_port}<br>
            Status: ${node.status}<br>
            Allocated: ${node.allocated}<br>
            ${node.lease ? `
                <p><b>Lease ${node.lease.rental_id}</b><br>
                Jusqu'à : ${node.lease.leased_until}</p>
                <button onclick="release(${node.lease.rental_id})">Release</button>
                <button onclick="extend(${node.lease.rental_id})">Extend</button>
            ` : `<i>Libre</i>`}
        `;

        container.appendChild(div);
    });
}

async function rent() {
    const hours = parseInt(document.getElementById("rent-hours").value);
    const count = parseInt(document.getElementById("rent-count").value);

    const res = await api("/api/rent", "POST", {
        duration_hours: hours,
        count: count
    });

    alert(JSON.stringify(res, null, 2));
    loadNodes();
}

async function release(id) {
    const res = await api(`/api/release/${id}`, "POST");
    alert(JSON.stringify(res, null, 2));
    loadNodes();
}

async function extend(id) {
    const res = await api(`/api/extend/${id}`, "POST", {
        additional_hours: 1
    });
    alert(JSON.stringify(res, null, 2));
    loadNodes();
}
