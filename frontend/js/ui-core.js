// ui-core.js - Système Central UI & Auth
window.API_BASE = window.location.origin.includes('localhost') || window.location.origin.includes('127.0.0.1')
    ? 'http://127.0.0.1:5000'
    : window.location.origin;

window.UICore = {
    // ── AUTH ──────────────────────────────────────────────────
    getUser: function() {
        return JSON.parse(localStorage.getItem('user') || 'null');
    },

    requireUser: function() {
        const user = this.getUser();
        if (!user) {
            window.location.replace('index.html');
            return null;
        }
        return user;
    },

    logout: function() {
        localStorage.clear();
        sessionStorage.clear();
        window.location.replace('index.html');
    },

    bindLogout: function() {
        document.getElementById('logoutNav')?.addEventListener('click', (e) => {
            e.preventDefault();
            this.logout();
        });
    },

    // ── USER UI ───────────────────────────────────────────────
    hydrateUserPill: function(user, options = {}) {
        const nameId = options.nameId || 'navUser';
        const avatarId = options.avatarId || 'userAvatar';
        
        // Main Pill Name
        const navUser = document.getElementById(nameId);
        let displayName = user.username || user.email;
        if (user.firstname || user.lastname) {
            displayName = `${user.firstname || ''} ${user.lastname || ''}`.trim();
        }
        if (navUser) navUser.textContent = displayName;
        
        // Initials calculation
        const initials = ((user.firstname || '')[0] || '') + ((user.lastname || '')[0] || (user.username || '')[0] || 'U');
        const initialsStr = initials.toUpperCase().slice(0, 2);

        // Main Pill Avatar
        const avatarEl = document.getElementById(avatarId);
        if (avatarEl) avatarEl.textContent = initialsStr;

        // NEW: Dropdown Menu Elements
        const menuAvatarLg = document.getElementById('menuUserAvatarLg');
        if (menuAvatarLg) menuAvatarLg.textContent = initialsStr;

        const menuName = document.getElementById('menuUserName');
        if (menuName) menuName.textContent = displayName;

        const menuEmail = document.getElementById('menuUserEmail');
        if (menuEmail) menuEmail.textContent = user.email || user.username;
        
        // Contextual updates
        const welcome = document.getElementById('welcome');
        if (welcome) welcome.textContent = `Bienvenue, ${user.firstname || user.username} !`;
    },

    initUserPill: function() {
        const user = this.getUser();
        if (user) {
            this.hydrateUserPill(user);
            this.initAdminLink(user.role);

            // Toggle menu on click
            const pill = document.getElementById('userPill');
            const menu = document.getElementById('userMenu');
            if (pill && menu) {
                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    menu.classList.toggle('show');
                });

                // Close menu when clicking outside
                window.addEventListener('click', (e) => {
                    if (!pill.contains(e.target)) {
                        menu.classList.remove('show');
                    }
                });
            }
        }
    },

    initAdminLink: function(role) {
        const adminItem = document.getElementById('navAdminItem');
        if (adminItem) {
            adminItem.style.display = (role === 'admin') ? 'list-item' : 'none';
        }
    },

    refreshUserProfile: async function() {
        const user = this.getUser();
        if (!user) return null;
        try {
            const res = await fetch(`${window.API_BASE}/api/profile`, {
                headers: { 'Authorization': `Bearer ${user.token || ''}` }
            });
            if (res.ok) {
                const fresh = await res.json();
                const updated = { ...user, ...fresh };
                localStorage.setItem('user', JSON.stringify(updated));
                this.hydrateUserPill(updated);
                return updated;
            }
        } catch (e) {
            console.error('[UICore] Failed to refresh profile', e);
        }
        return user;
    }
};

// Auto-init si l'utilisateur est là
document.addEventListener('DOMContentLoaded', () => {
    if (window.UICore.getUser()) {
        window.UICore.initUserPill();
    }
});
