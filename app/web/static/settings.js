for (const btn of document.querySelectorAll('[data-check]')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.check;
    const out = document.querySelector(`[data-check-result="${id}"]`);
    out.textContent = 'проверяю…';
    btn.disabled = true;
    try {
      const r = await fetch(`/settings/check/${id}`, {method: 'POST', credentials: 'same-origin'});
      const data = await r.json();
      out.textContent = (data.ok ? '✅ ' : '❌ ') + data.message;
    } catch (e) {
      out.textContent = '❌ ошибка запроса';
    } finally {
      btn.disabled = false;
    }
  });
}

document.getElementById('restart').addEventListener('click', async () => {
  if (!confirm('Перезапустить программу? Видео и распознавание прервутся примерно на 30 секунд.')) return;
  const out = document.getElementById('restart-result');
  try {
    await fetch('/settings/restart', {method: 'POST', credentials: 'same-origin'});
  } catch (e) { /* соединение рвётся при перезапуске — это нормально */ }
  out.textContent = 'Перезапускается… страница обновится сама.';
  setTimeout(() => location.reload(), 20000);
});

const driveBtn = document.getElementById('check-drive');
if (driveBtn) {
  driveBtn.addEventListener('click', async () => {
    const out = document.getElementById('check-drive-result');
    out.textContent = 'проверяю…';
    driveBtn.disabled = true;
    try {
      const r = await fetch('/settings/check-drive', {method: 'POST', credentials: 'same-origin'});
      const data = await r.json();
      out.textContent = (data.ok ? '✅ ' : '❌ ') + data.message;
    } catch (e) {
      out.textContent = '❌ ошибка запроса';
    } finally {
      driveBtn.disabled = false;
    }
  });
}
