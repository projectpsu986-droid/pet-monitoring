// NOTE: ไม่เรียก skipWaiting/clients.claim แบบบังคับ
// เพื่อหลีกเลี่ยงอาการหน้าเว็บ "รีเฟรช/รีเซ็ต" ในบาง browser ตอน service worker ถูกอัปเดต
self.addEventListener("install", () => {
  // default install flow
});

self.addEventListener("activate", (event) => {
  // default activate flow
  event.waitUntil(Promise.resolve());
});

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "Notification", body: event.data.text() };
  }

  const title = data.title || "Pet Alert";
  const options = {
    body: data.body || "มีการแจ้งเตือนใหม่",
    icon: "/icon.png",
    badge: "/icon.png",
    data: data.url || "/"
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data || "/";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(url) && "focus" in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(url);
      }
    })
  );
});
