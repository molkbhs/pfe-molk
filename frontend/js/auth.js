function login() {
    const emailInput = document.getElementById("email");
    const passwordInput = document.getElementById("password");
    const msgEl = document.getElementById("msg");
    const loginBtn = document.getElementById("loginBtn");

    // Validation
    if (!emailInput.value.trim() || !passwordInput.value.trim()) {
        msgEl.className = "message text-info";
        msgEl.innerText = "⚠️ Veuillez remplir tous les champs";
        return;
    }

    // Désactiver le bouton pendant la requête
    loginBtn.disabled = true;
    loginBtn.innerHTML = '<span class="loading"></span> Connexion...';
    msgEl.className = "message";
    msgEl.innerText = "";

    fetch("http://127.0.0.1:5000/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            email: emailInput.value,
            password: passwordInput.value
        })
    })
    .then(res => res.json().then(body => ({status: res.status, body})))
    .then(({status, body}) => {
        if (status === 200 && body.user) {
            msgEl.className = "message text-success";
            msgEl.innerText = "✅ Connexion réussie ! Redirection...";
            localStorage.setItem("user", JSON.stringify(body.user));
            setTimeout(() => {
                window.location.href = "dashboard.html";
            }, 1500);
        } else {
            msgEl.className = "message text-danger";
            msgEl.innerText = "❌ " + (body.error || 'Erreur de connexion');
            loginBtn.disabled = false;
            loginBtn.innerText = "Se connecter";
        }
    })
    .catch(err => {
        msgEl.className = "message text-danger";
        msgEl.innerText = "❌ Impossible de joindre le serveur";
        loginBtn.disabled = false;
        loginBtn.innerText = "Se connecter";
    });
}