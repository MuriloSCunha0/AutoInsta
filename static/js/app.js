/* ============================================================
   AutoInsta — Main Application JavaScript
   ============================================================ */

(function () {
  'use strict';

  /* ── Theme Toggler ─────────────────────────────── */
  function initThemeToggle() {
    const toggleBtn = document.getElementById('theme-toggle');
    const toggleIcon = document.getElementById('theme-icon');
    if (!toggleBtn) return;

    function updateIcon() {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      if(toggleIcon) toggleIcon.className = isDark ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
    }

    updateIcon();

    toggleBtn.addEventListener('click', () => {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      const newTheme = isDark ? 'light' : 'dark';
      
      document.documentElement.setAttribute('data-theme', newTheme);
      localStorage.setItem('theme', newTheme);
      updateIcon();
    });
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
    // After an HTMX swap, auto-focus challenge input if present
    document.addEventListener('htmx:afterSwap', (event) => {
      // Any modal that arrived in the swapped fragment must be moved to <body>.
      relocateModals(event.detail.target);

      const target = event.detail.target;
      // Foca o campo de código do painel de verificação inline quando ele
      // aparece após um swap (o atributo autofocus só age no load inicial).
      const codeInput = target.querySelector('.verify-code-input, .challenge-input');
      if (codeInput) {
        setTimeout(() => codeInput.focus(), 100);
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

    // Close the enclosing Bootstrap modal after a successful submit from within it.
    // Replaces the inline hx-on handlers that used to live on the modal <form>.
    // 204 = validation error (see instagram views _toast) → keep the modal open.
    document.addEventListener('htmx:afterRequest', (event) => {
      const xhr = event.detail.xhr;
      if (!xhr || xhr.status < 200 || xhr.status >= 300 || xhr.status === 204) return;

      const el = event.detail.elt;
      const modalEl = el && el.closest ? el.closest('.modal') : null;
      if (modalEl && window.bootstrap) {
        const inst = bootstrap.Modal.getInstance(modalEl) || bootstrap.Modal.getOrCreateInstance(modalEl);
        inst.hide();
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
  /* Active state is now handled server-side via Django url_name matching.
     No JS-based prefix matching needed (which caused multiple items
     to be highlighted when URLs shared prefixes like /instagram/). */

  /* ── Password reveal ("olhinho") ────────────────────────────
     Delegated so it works for fields that arrive later via HTMX
     (ex.: o modal de adicionar conta). Alterna o type do input e a
     classe .is-visible no wrapper .pw-field (a CSS troca o ícone). */
  document.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('.pw-toggle') : null;
    if (!btn) return;
    e.preventDefault();
    const field = btn.closest('.pw-field');
    const input = field && field.querySelector('input');
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    field.classList.toggle('is-visible', show);
    btn.setAttribute('aria-label', show ? 'Ocultar senha' : 'Mostrar senha');
    input.focus();
  });

  /* ── Sidebar Scroll Persistence ────────────────────────── */
  function preserveSidebarScroll() {
    const sidebar = document.querySelector('.side-nav');
    if (!sidebar) return;

    // Restore scroll position
    const scrollPos = sessionStorage.getItem('sidebar-scroll-pos');
    if (scrollPos) {
      // Use setTimeout to ensure DOM is fully rendered before scrolling
      setTimeout(() => {
        sidebar.scrollTop = parseInt(scrollPos, 10);
      }, 50);
    }

    // Save scroll position on scroll
    sidebar.addEventListener('scroll', () => {
      sessionStorage.setItem('sidebar-scroll-pos', sidebar.scrollTop);
    });

    // Also save just before leaving the page to be absolutely sure
    window.addEventListener('beforeunload', () => {
      sessionStorage.setItem('sidebar-scroll-pos', sidebar.scrollTop);
    });
  }

  /* ── Sidebar Collapse (desktop) ─────────────────────────── */
  function initSidebarCollapse() {
    const btn = document.getElementById('desk-toggle');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const collapsed = document.documentElement.getAttribute('data-nav') === 'collapsed';
      if (collapsed) {
        document.documentElement.removeAttribute('data-nav');
        localStorage.removeItem('nav-collapsed');
      } else {
        document.documentElement.setAttribute('data-nav', 'collapsed');
        localStorage.setItem('nav-collapsed', '1');
      }
    });
  }

  /* ── Command Palette (busca rápida ⌘K / Ctrl+K) ─────────── */
  function initCommandPalette() {
    const overlay = document.getElementById('cmdk-overlay');
    const input = document.getElementById('cmdk-input');
    const list = document.getElementById('cmdk-list');
    const empty = document.getElementById('cmdk-empty');
    const trigger = document.getElementById('cmdk-trigger');
    if (!overlay || !input || !list) return;

    // Monta os itens a partir dos links da sidebar (rótulo + ícone + href).
    const items = [];
    document.querySelectorAll('.side-links a').forEach((a) => {
      const label = (a.querySelector('.nav-text')?.textContent || a.textContent || '').trim();
      const icon = a.querySelector('i')?.className || 'bi bi-arrow-right';
      const href = a.getAttribute('href');
      if (label && href) {
        items.push({ label, icon, href });
        a.dataset.label = label; // usado no tooltip da sidebar colapsada
      }
    });

    let active = 0;

    function render() {
      const q = input.value.toLowerCase().trim();
      const matches = items.filter((it) => it.label.toLowerCase().includes(q));
      if (active >= matches.length) active = Math.max(matches.length - 1, 0);
      list.innerHTML = matches.map((it, i) =>
        '<li class="' + (i === active ? 'active' : '') + '"><a href="' + it.href + '">' +
        '<i class="' + it.icon + '"></i>' + it.label + '</a></li>'
      ).join('');
      empty.hidden = matches.length > 0;
    }

    function open() {
      overlay.classList.add('open');
      overlay.setAttribute('aria-hidden', 'false');
      input.value = '';
      active = 0;
      render();
      setTimeout(() => input.focus(), 30);
    }
    function close() {
      overlay.classList.remove('open');
      overlay.setAttribute('aria-hidden', 'true');
    }

    if (trigger) trigger.addEventListener('click', open);

    input.addEventListener('input', () => { active = 0; render(); });

    input.addEventListener('keydown', (e) => {
      const rows = list.querySelectorAll('li');
      if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, rows.length - 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        const link = list.querySelector('li.active a') || list.querySelector('li a');
        if (link) window.location.href = link.getAttribute('href');
      }
    });

    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        overlay.classList.contains('open') ? close() : open();
      } else if (e.key === 'Escape' && overlay.classList.contains('open')) {
        close();
      }
    });
  }

  /* ── Barra de progresso global (navegação + HTMX) ───────── */
  function initProgressBar() {
    const bar = document.getElementById('app-progress');
    if (!bar) return;
    let timer = null;

    function start() {
      clearInterval(timer);
      bar.classList.add('active');
      let w = 8;
      bar.style.width = w + '%';
      timer = setInterval(() => {
        w = Math.min(w + Math.random() * 8, 90);
        bar.style.width = w + '%';
      }, 300);
    }
    function done() {
      clearInterval(timer);
      bar.style.width = '100%';
      setTimeout(() => {
        bar.classList.remove('active');
        bar.style.width = '0';
      }, 250);
    }

    window.addEventListener('beforeunload', start);
    document.addEventListener('htmx:beforeRequest', start);
    document.addEventListener('htmx:afterRequest', done);
    document.addEventListener('htmx:responseError', done);
  }

  /* ── Init ───────────────────────────────────────────────── */
  function init() {
    initThemeToggle();
    initMobileMenu();
    initSidebarCollapse();
    initCommandPalette();
    initProgressBar();
    initHTMX();
    initStatusPolling();
    relocateModals();
    preserveSidebarScroll();
  }

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
