function register() {
    const usernameInput = document.getElementById("username");
    const emailInput = document.getElementById("email");
    const passwordInput = document.getElementById("password");
    const msgEl = document.getElementById("msg");
    const registerBtn = document.getElementById("registerBtn");

    // Validation
    if (!usernameInput.value.trim() || !emailInput.value.trim() || !passwordInput.value.trim()) {
        msgEl.className = "message text-info";
        msgEl.innerText = "⚠️ Veuillez remplir tous les champs";
        return;
    }

    if (passwordInput.value.length < 6) {
        msgEl.className = "message text-info";
        msgEl.innerText = "⚠️ Le mot de passe doit contenir au moins 6 caractères";
        return;
    }

    // Désactiver le bouton pendant la requête
    registerBtn.disabled = true;
    registerBtn.innerHTML = '<span class="loading"></span> Création du compte...';
    msgEl.className = "message";
    msgEl.innerText = "";

    const payload = {
        username: usernameInput.value,
        email: emailInput.value,
        password: passwordInput.value
    };

    fetch("http://127.0.0.1:5000/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    })
    .then(res => res.json().then(body => ({status: res.status, body})))
    .then(({status, body}) => {
        if (status === 201 && body.user) {
            msgEl.className = "message text-success";
            msgEl.innerText = "✅ Inscription réussie ! Redirection vers connexion...";
            setTimeout(() => {
                window.location.href = "index.html";
            }, 1500);
        } else {
            msgEl.className = "message text-danger";
            msgEl.innerText = "❌ " + (body.error || 'Erreur lors de l\'inscription');
            registerBtn.disabled = false;
            registerBtn.innerText = "Créer un compte";
        }
    })
    .catch(err => {
        msgEl.className = "message text-danger";
        msgEl.innerText = "❌ Impossible de joindre le serveur";
        registerBtn.disabled = false;
        registerBtn.innerText = "Créer un compte";
    });
}
