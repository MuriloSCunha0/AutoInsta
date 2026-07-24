/* Service worker do SandraoFlow — recebe Web Push e abre a notificação. */

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Chega um push do servidor -> mostra a notificação no aparelho.
self.addEventListener('push', (event) => {
    let dados = { title: 'SandraoFlow', body: '', url: '/' };
    try {
        if (event.data) dados = Object.assign(dados, event.data.json());
    } catch (e) {
        if (event.data) dados.body = event.data.text();
    }
    const options = {
        body: dados.body,
        icon: '/icon-192.png',
        badge: '/icon-192.png',
        data: { url: dados.url || '/' },
        vibrate: [80, 40, 80],
        tag: dados.tag || undefined,
    };
    event.waitUntil(self.registration.showNotification(dados.title, options));
});

// Tocar na notificação -> foca a aba aberta ou abre uma nova.
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const destino = (event.notification.data && event.notification.data.url) || '/';
    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((lista) => {
            for (const cliente of lista) {
                if ('focus' in cliente) {
                    cliente.navigate(destino);
                    return cliente.focus();
                }
            }
            return self.clients.openWindow(destino);
        })
    );
});
