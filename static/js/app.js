/* ============================================================
   AutoInsta — Main Application JavaScript
   ============================================================ */

(function () {
  'use strict';

  /* ── Diamond Rain Animation ─────────────────────────────── */
  function initDiamondRain() {
    const container = document.querySelector('.diamond-rain');
    if (!container) return;

    const DIAMOND_COUNT = 35;

    for (let i = 0; i < DIAMOND_COUNT; i++) {
      createDiamond(container);
    }
  }

  function createDiamond(container) {
    const diamond = document.createElement('span');
    diamond.className = 'diamond';

    const size = Math.random() * 6 + 4;          // 4-10px
    const left = Math.random() * 100;             // 0-100%
    const delay = Math.random() * 15;             // 0-15s
    const duration = Math.random() * 12 + 10;     // 10-22s
    const opacity = Math.random() * 0.10 + 0.04;  // 0.04-0.14

    diamond.style.cssText = `
      width: ${size}px;
      height: ${size}px;
      left: ${left}%;
      top: -20px;
      animation-delay: ${delay}s;
      animation-duration: ${duration}s;
      opacity: ${opacity};
    `;

    // Alternate colors
    const colors = [
      'rgba(168, 85, 247, 0.6)',
      'rgba(124, 58, 237, 0.5)',
      'rgba(232, 121, 249, 0.4)',
      'rgba(139, 92, 246, 0.5)',
    ];
    diamond.style.background = colors[Math.floor(Math.random() * colors.length)];

    container.appendChild(diamond);
  }

  /* ── Toast Notification System ──────────────────────────── */
  const TOAST_ICONS = {
    success: 'bi-check-circle-fill',
    error: 'bi-exclamation-circle-fill',
    warning: 'bi-exclamation-triangle-fill',
    info: 'bi-info-circle-fill',
  };

  const TOAST_TITLES = {
    success: 'Sucesso',
    error: 'Erro',
    warning: 'Atenção',
    info: 'Informação',
  };

  window.showToast = function (message, type = 'info', duration = 5000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    toast.innerHTML = `
      <i class="bi ${TOAST_ICONS[type] || TOAST_ICONS.info} toast-icon"></i>
      <div class="toast-content">
        <div class="toast-title">${TOAST_TITLES[type] || TOAST_TITLES.info}</div>
        <div class="toast-message">${message}</div>
      </div>
      <button class="toast-close" onclick="dismissToast(this.parentElement)">
        <i class="bi bi-x"></i>
      </button>
    `;

    container.appendChild(toast);

    // Auto-dismiss
    if (duration > 0) {
      setTimeout(() => {
        dismissToast(toast);
      }, duration);
    }
  };

  window.dismissToast = function (toastEl) {
    if (!toastEl || toastEl.classList.contains('toast-exit')) return;

    toastEl.classList.add('toast-exit');
    setTimeout(() => {
      toastEl.remove();
    }, 260);
  };

  /* ── Mobile Menu Toggle ─────────────────────────────────── */
  function initMobileMenu() {
    const toggle = document.getElementById('mobile-toggle');
    const sidebar = document.getElementById('side-nav');
    const overlay = document.getElementById('sidebar-overlay');

    if (!toggle || !sidebar) return;

    toggle.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      if (overlay) overlay.classList.toggle('show');
    });

    if (overlay) {
      overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('show');
      });
    }
  }

  /* ── Bootstrap Modal Relocation ─────────────────────────────
     Bootstrap appends the .modal-backdrop to <body>, but our modals
     are declared deep inside .app-shell / .main-content / .account-card.
     Those ancestors create stacking contexts (z-index) and containing
     blocks (backdrop-filter), which trap the modal *behind* the backdrop —
     the screen dims and nothing is clickable/typable. Moving every modal
     to be a direct child of <body> removes it from any trapping ancestor.
     Idempotent + dedupes by id so HTMX re-renders don't duplicate modals. */
  function relocateModals(root) {
    const scope = root || document;
    scope.querySelectorAll('.modal').forEach((modal) => {
      if (modal.parentElement === document.body) return;

      const id = modal.id;
      if (id) {
        const existing = document.querySelector('body > .modal#' + CSS.escape(id));
        if (existing && existing !== modal) {
          // A copy is already living at <body> level → drop this nested duplicate.
          modal.remove();
          return;
        }
      }
      document.body.appendChild(modal);
    });
  }
  window.relocateModals = relocateModals;

  /* ── Modal System (legacy custom modals) ────────────────── */
  window.openModal = function (modalId) {
    const modal = document.getElementById(modalId);
    const backdrop = document.getElementById(modalId + '-backdrop');

    if (modal) {
      modal.classList.add('show');
      document.body.style.overflow = 'hidden';

      // Auto-focus first input
      setTimeout(() => {
        const firstInput = modal.querySelector('input:not([type="hidden"]), textarea, select');
        if (firstInput) firstInput.focus();
      }, 100);
    }

    if (backdrop) {
      backdrop.classList.add('show');
    }
  };

  window.closeModal = function (modalId) {
    const modal = document.getElementById(modalId);
    const backdrop = document.getElementById(modalId + '-backdrop');

    if (modal) modal.classList.remove('show');
    if (backdrop) backdrop.classList.remove('show');
    document.body.style.overflow = '';
  };

  // Close modal on backdrop click
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-backdrop-custom')) {
      const modalId = e.target.id.replace('-backdrop', '');
      closeModal(modalId);
    }
  });

  // Close modal on ESC
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-custom.show').forEach((modal) => {
        closeModal(modal.id);
      });
    }
  });

  /* ── HTMX Event Listeners ──────────────────────────────── */
  function initHTMX() {
    // After an HTMX swap, check for modals and re-init
    document.addEventListener('htmx:afterSwap', (event) => {
      // Any modal that arrived in the swapped fragment must be moved to <body>.
      relocateModals(event.detail.target);

      // If the response contains a modal, open it
      const target = event.detail.target;
      const modal = target.querySelector('.modal-custom') || 
                    (target.classList.contains('modal-custom') ? target : null);
      if (modal) {
        openModal(modal.id);
      }

      // Challenge modal — auto-focus code input
      const challengeInput = target.querySelector('.challenge-input');
      if (challengeInput) {
        setTimeout(() => challengeInput.focus(), 100);
      }
    });

    // Error responses
    document.addEventListener('htmx:responseError', (event) => {
      const status = event.detail.xhr.status;
      let message = 'Ocorreu um erro inesperado. Tente novamente.';

      if (status === 401) message = 'Sessão expirada. Faça login novamente.';
      else if (status === 403) message = 'Você não tem permissão para esta ação.';
      else if (status === 404) message = 'Recurso não encontrado.';
      else if (status === 429) message = 'Muitas requisições. Aguarde um momento.';
      else if (status >= 500) message = 'Erro no servidor. Tente novamente mais tarde.';

      showToast(message, 'error');
    });

    // Show success toasts from HTMX response headers
    document.addEventListener('htmx:afterRequest', (event) => {
      const xhr = event.detail.xhr;
      if (!xhr) return;

      const toastMessage = xhr.getResponseHeader('X-Toast-Message');
      const toastType = xhr.getResponseHeader('X-Toast-Type') || 'success';

      if (toastMessage) {
        showToast(toastMessage, toastType);
      }
    });

    // Close modals via X-Close-Modal header
    document.addEventListener('htmx:afterRequest', (event) => {
      const xhr = event.detail.xhr;
      if (!xhr) return;

      const closeModalId = xhr.getResponseHeader('X-Close-Modal');
      if (closeModalId) {
        closeModal(closeModalId);
      }
    });
  }

  /* ── Account Status Polling ─────────────────────────────── */
  function initStatusPolling() {
    // Find all connecting account cards and ensure they have HTMX polling
    document.querySelectorAll('[data-status="connecting"]').forEach((card) => {
      if (!card.hasAttribute('hx-get')) {
        const accountId = card.dataset.accountId;
        if (accountId) {
          card.setAttribute('hx-get', `/instagram/${accountId}/status/`);
          card.setAttribute('hx-trigger', 'every 3s');
          card.setAttribute('hx-swap', 'outerHTML');
          // Re-process with htmx
          if (typeof htmx !== 'undefined') {
            htmx.process(card);
          }
        }
      }
    });
  }

  // Re-check after swaps
  document.addEventListener('htmx:afterSwap', () => {
    setTimeout(initStatusPolling, 100);
  });

  /* ── Active Nav Link ────────────────────────────────────── */
  function highlightActiveNav() {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.side-links a').forEach((link) => {
      const href = link.getAttribute('href');
      if (!href) return;

      // Exact match for dashboard, prefix match for others
      if (href === '/' && currentPath === '/') {
        link.classList.add('active');
      } else if (href !== '/' && currentPath.startsWith(href)) {
        link.classList.add('active');
      }
    });

    // Mobile nav
    document.querySelectorAll('.mobile-links a').forEach((link) => {
      const href = link.getAttribute('href');
      if (!href) return;

      if (href === '/' && currentPath === '/') {
        link.classList.add('active');
      } else if (href !== '/' && currentPath.startsWith(href)) {
        link.classList.add('active');
      }
    });
  }

  /* ── Init ───────────────────────────────────────────────── */
  function init() {
    initDiamondRain();
    initMobileMenu();
    initHTMX();
    initStatusPolling();
    highlightActiveNav();
    relocateModals();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
