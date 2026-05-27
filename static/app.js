/**
 * Alertle-V2 — Frontend JS
 * Shared utilities used across all pages.
 */

/**
 * Fetch wrapper that parses JSON and throws on non-OK responses.
 */
async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 120)}`);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    return res.json();
  }
  return res.text();
}

/**
 * Open a modal by ID.
 */
function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('hidden');
}

/**
 * Close a modal by ID.
 */
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

function toggleNav() {
  document.getElementById('nav-links')?.classList.toggle('open');
}

// Close modals when clicking outside of them; close mobile nav when link clicked
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
  if (e.target.classList.contains('nav-link')) {
    document.getElementById('nav-links')?.classList.remove('open');
  }
});

// Close modals on Escape key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(el => {
      el.classList.add('hidden');
    });
  }
});
