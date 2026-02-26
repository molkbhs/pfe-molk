/**
 * contact.js - Gestion formulaire contact (EmailJS + fallback backend)
 */

const EMAILJS_PUBLIC_KEY = 'BYgRqzNshOFw_Jh93';
const EMAILJS_SERVICE_ID = 'service_x0iueln';
const EMAILJS_TEMPLATE_ID = 'template_n5x8kt3';

function setFeedback(message, ok = true) {
  const box = document.getElementById('successMessage');
  if (!box) return;
  box.textContent = (ok ? 'OK: ' : 'Erreur: ') + message;
  box.style.background = ok ? 'rgba(72, 187, 120, 0.2)' : 'rgba(245, 101, 101, 0.2)';
  box.style.borderColor = ok ? '#48BB78' : '#f56565';
  box.classList.add('show');
  setTimeout(() => box.classList.remove('show'), 5000);
}

async function tryBackend(formData) {
  const res = await fetch('/api/contact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(formData)
  });
  if (!res.ok) throw new Error('backend_unavailable');
  const data = await res.json();
  if (!data.success) throw new Error(data.error || 'backend_failed');
  return true;
}

async function tryEmailJs(emailData) {
  if (!window.emailjs || typeof window.emailjs.send !== 'function') {
    throw new Error('emailjs_not_loaded');
  }
  const out = await window.emailjs.send(EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, emailData);
  if (!out || out.status !== 200) {
    throw new Error('emailjs_failed');
  }
  return true;
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('contactForm');
  if (!form) return;

  if (window.emailjs && typeof window.emailjs.init === 'function') {
    window.emailjs.init(EMAILJS_PUBLIC_KEY);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const button = form.querySelector('button[type="submit"]');
    const originalText = button ? button.textContent : 'Envoyer le message';

    const formData = {
      name: document.getElementById('name')?.value.trim() || '',
      email: document.getElementById('email')?.value.trim() || '',
      phone: document.getElementById('phone')?.value.trim() || '',
      subject: document.getElementById('subject')?.value || '',
      message: document.getElementById('message')?.value.trim() || '',
      newsletter: document.getElementById('newsletter')?.checked ? 'Oui' : 'Non'
    };

    if (!formData.name || !formData.email || !formData.subject || !formData.message) {
      setFeedback('Veuillez remplir tous les champs obligatoires', false);
      return;
    }

    if (button) {
      button.disabled = true;
      button.textContent = 'Envoi en cours...';
      button.classList.add('loading');
    }

    const emailData = {
      user_name: formData.name,
      user_email: formData.email,
      user_phone: formData.phone,
      subject: formData.subject,
      message: formData.message,
      newsletter: formData.newsletter
    };

    try {
      try {
        await tryBackend(formData);
      } catch (_) {
        await tryEmailJs(emailData);
      }

      form.reset();
      setFeedback('Merci ! Votre message a été envoyé avec succès.');
    } catch (err) {
      console.error('Contact error:', err);
      setFeedback('Impossible d\'envoyer le message. Vérifiez EmailJS (clé/service/template).', false);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalText;
        button.classList.remove('loading');
      }
    }
  });

  const observerOptions = { threshold: 0.1, rootMargin: '0px 0px -100px 0px' };
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }
    });
  }, observerOptions);

  document.querySelectorAll('.form-group').forEach((el) => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'all 0.6s ease-out';
    observer.observe(el);
  });
});

