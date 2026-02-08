/**
 * Authentication JavaScript - Login functionality
 * Handles both email/password and Google OAuth login
 */

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

    // Disable button during request
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
            
            // Store user data in localStorage
            localStorage.setItem("user", JSON.stringify(body.user));
            
            // Redirect to dashboard
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
        console.error("Login error:", err);
        msgEl.className = "message text-danger";
        msgEl.innerText = "❌ Impossible de joindre le serveur. Vérifiez que le backend est démarré.";
        loginBtn.disabled = false;
        loginBtn.innerText = "Se connecter";
    });
}

/**
 * Initiate Google OAuth login flow
 * Redirects user to Google's authorization page
 */
function loginGoogle() {
    // Show loading message
    const msgEl = document.getElementById("msg");
    if (msgEl) {
        msgEl.className = "message text-info";
        msgEl.innerText = "🔄 Redirection vers Google...";
    }
    
    // Redirect to backend OAuth endpoint
    // The backend will handle the OAuth flow and redirect back with user data
    window.location.href = "http://127.0.0.1:5000/api/auth/google";
}

/**
 * Check if user is already logged in
 * Redirect to dashboard if authenticated
 */
function checkAuth() {
    const user = localStorage.getItem("user");
    if (user) {
        try {
            const userData = JSON.parse(user);
            if (userData.id && userData.email) {
                // User is authenticated, redirect to dashboard
                window.location.href = "dashboard.html";
            }
        } catch (e) {
            // Invalid user data, clear it
            localStorage.removeItem("user");
        }
    }
}

/**
 * Logout function
 * Clears user data and redirects to login
 */
function logout() {
    localStorage.clear();
    sessionStorage.clear();
    const url = (window.location.origin && window.location.origin !== "null") ? window.location.origin + "/index.html" : "index.html";
    window.location.replace(url);
}

// Auto-check authentication status when page loads
// Uncomment if you want to auto-redirect authenticated users
// window.addEventListener('DOMContentLoaded', checkAuth);
