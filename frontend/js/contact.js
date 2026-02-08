/**
 * Contact Form Handler
 * Handles form submission and communicates with backend API
 */

document.getElementById('contactForm').addEventListener('submit', function(e) {
    e.preventDefault();
    
    const button = this.querySelector('button[type="submit"]');
    const originalText = button.textContent;
    const successMessage = document.getElementById('successMessage');
    
    // Gather form data
    const formData = {
        name: document.getElementById('name').value.trim(),
        email: document.getElementById('email').value.trim(),
        phone: document.getElementById('phone').value.trim(),
        subject: document.getElementById('subject').value,
        message: document.getElementById('message').value.trim(),
        newsletter: document.getElementById('newsletter').checked
    };
    
    // Validate form
    if (!formData.name || !formData.email || !formData.subject || !formData.message) {
        showError('Veuillez remplir tous les champs obligatoires');
        return;
    }
    
    // Disable button and show loading state
    button.textContent = 'Envoi en cours...';
    button.disabled = true;
    successMessage.classList.remove('show');
    
    // Send to backend API
    fetch('http://127.0.0.1:5000/api/contact', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json().then(data => ({status: response.status, data})))
    .then(({status, data}) => {
        if (status === 201 || data.success) {
            // Success - show success message
            successMessage.textContent = '✓ Merci ! Votre message a été envoyé avec succès. Nous vous répondrons bientôt.';
            successMessage.style.background = 'rgba(72, 187, 120, 0.2)';
            successMessage.style.borderColor = '#48BB78';
            successMessage.classList.add('show');
            
            // Reset form
            document.getElementById('contactForm').reset();
            
            // Auto-hide success message after 5 seconds
            setTimeout(() => {
                successMessage.classList.remove('show');
            }, 5000);
        } else {
            // Error from server
            showError(data.error || 'Une erreur est survenue lors de l\'envoi du message');
        }
        
        // Re-enable button
        button.textContent = originalText;
        button.disabled = false;
    })
    .catch(error => {
        console.error('Error:', error);
        showError('Impossible de joindre le serveur. Veuillez vérifier que le backend est démarré.');
        
        // Re-enable button
        button.textContent = originalText;
        button.disabled = false;
    });
});

/**
 * Show error message
 */
function showError(message) {
    const successMessage = document.getElementById('successMessage');
    successMessage.textContent = '❌ ' + message;
    successMessage.style.background = 'rgba(245, 101, 101, 0.2)';
    successMessage.style.borderColor = '#f56565';
    successMessage.classList.add('show');
    
    // Auto-hide error message after 5 seconds
    setTimeout(() => {
        successMessage.classList.remove('show');
    }, 5000);
}

// Add smooth animations on scroll (existing code)
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -100px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
        }
    });
}, observerOptions);

// Observe all form groups
document.querySelectorAll('.form-group').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'all 0.6s ease-out';
    observer.observe(el);
});
