function renderInbox(list, items) {
  if (!items.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = 'никого';
    list.replaceChildren(li);
    return;
  }
  list.replaceChildren(...items.map(v => {
    const li = document.createElement('li');
    const b = document.createElement('b');
    b.className = 'plate';
    b.textContent = v.plate;
    li.append(b, ` с ${v.entered_at}, уже ${v.duration}`);
    return li;
  }));
}

async function refresh() {
  let data;
  try {
    const r = await fetch('/api/status', {credentials: 'same-origin'});
    if (r.status === 401) { location.href = '/login'; return; }
    data = await r.json();
  } catch (e) {
    return;
  }
  const alertBox = document.getElementById('archive-alert');
  if (alertBox) {
    const a = data.archive;
    const problems = [];
    if (a) {
      for (const [cid, s] of Object.entries(a.cameras)) {
        if (s.state === 'error') problems.push(`Запись ${s.name || cid}: ${s.error} (с ${s.since})`);
      }
      if (a.upload_error) problems.push(`Загрузка на Google Диск: ${a.upload_error} (с ${a.upload_error_at})`);
      if (a.low_space) problems.push(`Мало места на диске: ${a.free_gb} ГБ`);
      if (a.cleanup_error) problems.push(`Очистка архива: ${a.cleanup_error}`);
    }
    alertBox.textContent = problems.join(' · ');
    alertBox.hidden = problems.length === 0;
  }
  for (const section of document.querySelectorAll('[data-camera]')) {
    const id = section.dataset.camera;
    const st = data.cameras[id];
    const badge = section.querySelector('[data-status]');
    if (!st || st.online === null) {
      badge.textContent = '';
      badge.className = 'badge';
    } else if (st.online) {
      badge.textContent = 'в сети';
      badge.className = 'badge ok';
    } else {
      badge.textContent = 'нет сигнала' + (st.offline_since ? ' с ' + st.offline_since : '');
      badge.className = 'badge bad';
    }
    const list = section.querySelector('[data-inbox]');
    if (list) renderInbox(list, data.in_box.filter(v => v.camera_id === id));
  }
}

for (const btn of document.querySelectorAll('[data-fullscreen]')) {
  btn.addEventListener('click', () => {
    const video = btn.closest('[data-camera]').querySelector('.video');
    (video.requestFullscreen || video.webkitRequestFullscreen).call(video);
  });
}

refresh();
setInterval(refresh, 5000);
