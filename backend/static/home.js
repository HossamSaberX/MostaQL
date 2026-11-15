// Home page: subscribe, then swap the form for a clear "what next" state.
import {
  api, busy, categoryPicker, categoriesRule, channelsRule, emailRule, formStatus,
  linkEmailDelivery, numberRules, readSettings, validator,
} from 'app';

const form = document.getElementById('subscribeForm');
const done = document.getElementById('done');
const email = form.elements.email;
const picker = categoryPicker(document.getElementById('categoriesField'));
const status = formStatus(form);
const telegram = document.getElementById('doneTelegram');
const telegramHint = document.getElementById('doneTelegramHint');

linkEmailDelivery(form);
const validate = validator([emailRule(email), channelsRule(form), categoriesRule(picker), ...numberRules(form)]);

function showDone(data, settings) {
  // Telegram-only signups still get an email (it holds the manage link), but Telegram is the next step.
  const kind = !settings.receive_email && data.telegram_link ? 'telegram' : 'created';

  for (const block of done.querySelectorAll('[data-done]')) block.hidden = block.dataset.done !== kind;
  for (const slot of done.querySelectorAll('[data-email]')) slot.textContent = data.email;
  telegram.hidden = !data.telegram_link;
  if (data.telegram_link) telegram.href = data.telegram_link;
  telegramHint.hidden = !(kind === 'created' && data.telegram_link);

  form.hidden = true;
  done.hidden = false;
  done.querySelector(`[data-done="${kind}"] h2`).focus();
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  status.clear();
  if (!validate()) return;

  const button = form.querySelector('button[type="submit"]');
  const settings = readSettings(form);
  busy(button, true, 'جارٍ الاشتراك…');
  try {
    const data = await api('/subscribe', {
      method: 'POST',
      body: { email: email.value.trim(), category_ids: picker.selected(), ...settings },
    });
    showDone(data, settings);
  } catch (error) {
    status.error(error.message);
  } finally {
    busy(button, false);
  }
});

document.getElementById('changeEmail').addEventListener('click', () => {
  done.hidden = true;
  form.hidden = false;
  email.focus();
  email.select();
});
