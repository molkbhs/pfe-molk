/**
 * theme-manager.js
 * Gère le thème clair/sombre de l'application.
 */
window.ThemeManager = {
    STORAGE_KEY: 'app_theme',

    resolveTheme: function() {
        const saved = localStorage.getItem(this.STORAGE_KEY);
        if (saved) return saved;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    },

    applyTheme: function(theme, options = {}) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(this.STORAGE_KEY, theme);
        
        // Mettre à jour les icônes/labels si fournis
        if (options.iconId) {
            const icon = document.getElementById(options.iconId);
            if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
        }
        if (options.labelId) {
            const label = document.getElementById(options.labelId);
            if (label) label.textContent = theme === 'dark' ? 'Mode Clair' : 'Mode Sombre';
        }
    },

    toggle: function(options = {}) {
        const current = this.resolveTheme();
        const next = current === 'dark' ? 'light' : 'dark';
        this.applyTheme(next, options);
        return next;
    },

    initThemeToggle: function(options = {}) {
        const btn = document.getElementById(options.buttonId || 'themeToggle');
        if (btn) {
            btn.addEventListener('click', () => this.toggle(options));
        }
        
        // Appliquer le thème initial au chargement
        const initial = this.resolveTheme();
        this.applyTheme(initial, options);
        return initial;
    }
};

// Initialisation globale au chargement
document.addEventListener('DOMContentLoaded', () => {
    // Si la page a un bouton par défaut #themeToggle
    if (document.getElementById('themeToggle')) {
        window.ThemeManager.initThemeToggle({
            buttonId: 'themeToggle',
            iconId: 'themeIcon',
            labelId: 'themeLabel'
        });
    }
});
