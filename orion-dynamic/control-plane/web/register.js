async function register() {
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;

    const res = await fetch("/api/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
    });

    const data = await res.json();

    if (!res.ok) {
        document.getElementById("result").textContent = data.error || "Erreur";
        return;
    }

    document.getElementById("result").style.color = "green";
    document.getElementById("result").textContent = "Compte créé ! Tu peux te connecter.";
}

