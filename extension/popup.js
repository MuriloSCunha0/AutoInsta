/* AutoInsta Connector — lê o cookie sessionid do instagram.com e envia à
   plataforma junto do token de conexão do usuário. Nenhuma senha trafega. */

const tokenEl = document.getElementById('token');
const urlEl = document.getElementById('connectUrl');
const btn = document.getElementById('connectBtn');
const statusEl = document.getElementById('status');
const pill = document.getElementById('sessionPill');

let currentSessionId = null;

function showStatus(msg, kind) {
  statusEl.textContent = msg;
  statusEl.className = 'status show ' + kind;
}

function readSessionId() {
  // O cookie sessionid vive no domínio .instagram.com (httpOnly).
  // A API chrome.cookies consegue lê-lo com a permissão "cookies".
  chrome.cookies.get({ url: 'https://www.instagram.com', name: 'sessionid' }, (cookie) => {
    if (cookie && cookie.value) {
      currentSessionId = cookie.value;
      pill.textContent = '✔ Sessão do Instagram detectada';
      pill.className = 'pill on';
      updateButton();
    } else {
      currentSessionId = null;
      pill.textContent = '✖ Faça login em instagram.com primeiro';
      pill.className = 'pill off';
      updateButton();
    }
  });
}

function updateButton() {
  btn.disabled = !(currentSessionId && tokenEl.value.trim() && urlEl.value.trim());
}

// Restaura token/URL salvos.
chrome.storage.local.get(['token', 'connectUrl'], (data) => {
  if (data.token) tokenEl.value = data.token;
  if (data.connectUrl) urlEl.value = data.connectUrl;
  updateButton();
});

tokenEl.addEventListener('input', updateButton);
urlEl.addEventListener('input', updateButton);

btn.addEventListener('click', async () => {
  const token = tokenEl.value.trim();
  const connectUrl = urlEl.value.trim();
  if (!currentSessionId || !token || !connectUrl) return;

  // Persiste token/URL para as próximas vezes.
  chrome.storage.local.set({ token, connectUrl });

  btn.disabled = true;
  showStatus('Enviando sessão…', 'ok');

  try {
    const resp = await fetch(connectUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, sessionid: currentSessionId }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      showStatus(data.message || 'Conta conectada! Volte à plataforma.', 'ok');
    } else {
      showStatus(data.error || ('Erro (' + resp.status + ').'), 'err');
    }
  } catch (e) {
    showStatus('Falha de rede. Confira a URL da plataforma.', 'err');
  } finally {
    updateButton();
  }
});

readSessionId();
