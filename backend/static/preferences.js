// Preferences page: one form for the settings, immediate add/remove for past clients,
// and a native dialog before stopping everything.
import {
  api, busy, categoryPicker, categoriesRule, channelsRule, fillSettings, formStatus,
  linkEmailDelivery, numberRules, readSettings, say, showFieldError, clearFieldError, validator,
} from 'app';

const $ = (id) => document.getElementById(id);
const token = $('prefs').dataset.token;
const tokenPath = encodeURIComponent(token);
const form = $('settingsForm');
const picker = categoryPicker($('categoriesField'));
const status = formStatus(form);
const syncDelivery = linkEmailDelivery(form);
const validate = validator([channelsRule(form), categoriesRule(picker), ...numberRules(form)]);
const pageStatus = $('clientStatus');
let clients = [];

function render(data) {
  $('prefsEmail').textContent = data.email;
  fillSettings(form, data);
  syncDelivery();
  picker.set(data.category_ids || []);

  const active = !data.unsubscribed;
  $('statusBadge').classList.toggle('badge--off', !active);
  $('statusBadge').querySelector('[data-icon-on]').hidden = !active;
  $('statusBadge').querySelector('[data-icon-off]').hidden = active;
  $('statusText').textContent = active ? 'التنبيهات مفعّلة' : 'التنبيهات متوقفة';
  $('sentThisWeek').textContent = data.sent_this_week_text;
  $('resumeButton').hidden = active;
  $('stopSection').hidden = !active;

  $('telegramLinked').hidden = !data.telegram_linked;
  $('telegramNotLinked').hidden = data.telegram_linked || !data.telegram_link;
  if (data.telegram_link) $('telegramLink').href = data.telegram_link;

  clients = data.followed_clients || [];
  renderClients();
}

function renderClients() {
  $('clientList').replaceChildren(...clients.map((client) => {
    const item = document.createElement('li');
    const name = client.label || client.profile_url;
    const link = document.createElement('a');
    if (/^https?:\/\//i.test(client.profile_url)) link.href = client.profile_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = name;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button button--danger';
    remove.textContent = 'احذف';
    remove.setAttribute('aria-label', `احذف ${name}`);
    remove.addEventListener('click', () => removeClient(client.id));
    item.append(link, remove);
    return item;
  }));
  $('clientEmpty').hidden = clients.length > 0;
}

async function load() {
  try {
    render(await api(`/preferences/${tokenPath}`));
    $('prefsBody').hidden = false;
  } catch (error) {
    $('prefsLoadError').textContent = error.message;
  } finally {
    $('prefsLoading').hidden = true;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  status.clear();
  if (!validate()) return;

  const button = form.querySelector('button[type="submit"]');
  busy(button, true, 'جارٍ الحفظ…');
  try {
    const data = await api('/preferences', {
      method: 'POST',
      body: { token, category_ids: picker.selected(), ...readSettings(form) },
    });
    render(data);
    status.success(data.message);
  } catch (error) {
    status.error(error.message);
  } finally {
    busy(button, false);
  }
});

$('resumeButton').addEventListener('click', async (event) => {
  const button = event.currentTarget;
  $('resumeError').textContent = '';
  busy(button, true, 'جارٍ التفعيل…');
  try {
    const data = await api(`/resubscribe/${tokenPath}`, { method: 'POST' });
    busy(button, false);
    render(data);
    say(pageStatus, data.message);
    $('stopButton').focus(); // the resume button is gone; keep focus on a visible control
  } catch (error) {
    busy(button, false);
    say($('resumeError'), error.message);
  }
});

// ---- stopping everything ------------------------------------------------------------------

const dialog = $('stopDialog');
$('stopButton').addEventListener('click', () => dialog.showModal());

dialog.addEventListener('close', async () => {
  const choice = dialog.returnValue;
  dialog.returnValue = '';
  if (choice !== 'stop') return;
  try {
    const data = await api(`/unsubscribe/${tokenPath}`, { method: 'POST' });
    render(data);
    say(pageStatus, data.message);
    $('resumeButton').focus(); // the stop button is gone; keep focus on the next useful action
  } catch (error) {
    say($('resumeError'), error.message);
    $('stopButton').focus();
  }
});

// ---- past clients -------------------------------------------------------------------------

const clientForm = $('clientForm');
const clientUrl = $('clientUrl');
clientUrl.setAttribute('aria-invalid', 'false');
clientUrl.addEventListener('input', () => clearFieldError(clientUrl));

clientForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearFieldError(clientUrl);
  const url = clientUrl.value.trim();
  if (!url) {
    showFieldError(clientUrl, 'الصق رابط ملف العميل من مستقل.');
    clientUrl.focus();
    return;
  }

  const button = clientForm.querySelector('button[type="submit"]');
  busy(button, true, 'جارٍ الإضافة…');
  try {
    const data = await api('/preferences/clients', {
      method: 'POST',
      body: { token, profile_url: url, label: $('clientLabel').value.trim() || null },
    });
    clients = [...clients.filter((client) => client.id !== data.client.id), data.client];
    renderClients();
    clientForm.reset();
    say(pageStatus, data.message);
  } catch (error) {
    showFieldError(clientUrl, error.message);
    clientUrl.focus();
  } finally {
    busy(button, false);
  }
});

async function removeClient(id) {
  try {
    const data = await api(`/preferences/clients/${id}?token=${tokenPath}`, { method: 'DELETE' });
    clients = clients.filter((client) => client.id !== id);
    renderClients();
    say(pageStatus, data.message);
    clientUrl.focus();
  } catch (error) {
    showFieldError(clientUrl, error.message);
  }
}

load();
