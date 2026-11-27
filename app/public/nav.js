// Top navigation extras shared by every dashboard page: bot URL for Voice studio, unread-question badge.
fetch('/api/config').then((r) => r.json()).then((c) => { document.getElementById('voice-link').href = `${c.botUrl}/voice`; }).catch(() => {});
const badge = () => fetch('/api/offguard').then((r) => r.json()).then((q) => {
  const n = q.filter((x) => x.status === 'new').length, b = document.getElementById('q-badge');
  b.hidden = !n; b.textContent = n;
}).catch(() => {});
badge(); setInterval(badge, 10000);
