// "Manage your subscription": mail a manage link. The reply is the same for every address.
import { api, busy, emailRule, formStatus, validator } from './app.js?v=5';

const form = document.getElementById('requestForm');
const email = form.elements.email;
const status = formStatus(form);
const validate = validator([emailRule(email)]);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  status.clear();
  if (!validate()) return;

  const button = form.querySelector('button[type="submit"]');
  busy(button, true, 'جارٍ الإرسال…');
  try {
    const data = await api('/unsubscribe-request', { method: 'POST', body: { email: email.value.trim() } });
    status.success(data.message);
  } catch (error) {
    status.error(error.message);
  } finally {
    busy(button, false);
  }
});
