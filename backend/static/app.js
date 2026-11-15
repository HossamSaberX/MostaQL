// Shared by every page: API calls, messages, field errors, the settings form and category chips.
// Scraped text (category names, client labels) only ever goes in through textContent.

export const API_BASE = ['5500', '5501'].includes(window.location.port)
  ? 'http://localhost:8000/api'
  : `${window.location.origin}/api`;

const NETWORK_ERROR = 'تعذر الاتصال. تحقق من الإنترنت ثم حاول مرة أخرى.';
const SERVER_ERROR = 'حدث خطأ غير متوقع. حاول مرة أخرى بعد قليل.';
const TOO_MANY = 'محاولات كثيرة. انتظر قليلًا ثم حاول مرة أخرى.';

/** JSON request to the API. Throws an Error whose message is ready to show, in Arabic. */
export async function api(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(NETWORK_ERROR);
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    // an empty or non-JSON body; the status code decides
  }
  if (!response.ok) {
    if (response.status === 429) throw new Error(TOO_MANY);
    throw new Error(typeof data?.detail === 'string' ? data.detail : SERVER_ERROR);
  }
  return data ?? {};
}

/** Put text in a live region so it is announced even when the same words repeat. */
export function say(element, text) {
  if (!element) return;
  element.textContent = '';
  if (text) setTimeout(() => { element.textContent = text; }, 50);
}

/** The error and success lines of a form (see the form_status macro). */
export function formStatus(form) {
  const error = form.querySelector('[data-form-error]');
  const success = form.querySelector('[data-form-success]');
  return {
    error(text) { success.textContent = ''; say(error, text); },
    success(text) { error.textContent = ''; say(success, text); },
    clear() { error.textContent = ''; success.textContent = ''; },
  };
}

/** Disable a button while its request runs, with a short "working" label. */
export function busy(button, on, label) {
  if (on) {
    button.dataset.label = button.textContent;
    button.textContent = label;
    button.disabled = true;
  } else {
    if (button.dataset.label) button.textContent = button.dataset.label;
    button.disabled = false;
  }
}

// ---- field errors -------------------------------------------------------------------------
// Each control names its error element in data-error. The error is listed in
// aria-describedby only while shown, and errors that appear on blur are announced
// through one polite live region, a moment later so they do not talk over the next field.

let announcer;
let announceTimer;

function announce(text) {
  if (!announcer) {
    announcer = document.createElement('div');
    announcer.className = 'visually-hidden';
    announcer.setAttribute('aria-live', 'polite');
    document.body.append(announcer);
  }
  clearTimeout(announceTimer);
  announcer.textContent = '';
  announceTimer = setTimeout(() => { announcer.textContent = text; }, 500);
}

function linkError(control, shown) {
  const errorId = control.dataset.error;
  const ids = (control.getAttribute('aria-describedby') || '').split(/\s+/).filter((id) => id && id !== errorId);
  if (shown) ids.push(errorId);
  if (ids.length) control.setAttribute('aria-describedby', ids.join(' '));
  else control.removeAttribute('aria-describedby');
}

const isField = (control) => control.matches('input, textarea, select');

function controlName(control) {
  const label = isField(control) ? control.labels?.[0] : control.querySelector('legend');
  return label ? label.textContent.replace(/\s+/g, ' ').trim() : '';
}

export function showFieldError(control, message, { spoken = false } = {}) {
  const error = document.getElementById(control.dataset.error);
  if (!error) return;
  error.textContent = message;
  error.hidden = false;
  if (isField(control)) control.setAttribute('aria-invalid', 'true');
  linkError(control, true);
  if (spoken) announce(`${controlName(control)}: ${message}`);
}

export function clearFieldError(control) {
  const error = document.getElementById(control.dataset.error);
  if (!error || error.hidden) return;
  error.hidden = true;
  error.textContent = '';
  if (isField(control)) control.setAttribute('aria-invalid', 'false');
  linkError(control, false);
}

function focusControl(control) {
  control.closest('details')?.setAttribute('open', '');
  const target = isField(control)
    ? control
    : [...control.querySelectorAll('input:not(:disabled)')].find((input) => !input.closest('[hidden]'));
  (target || control).focus();
}

/**
 * Wire rules of the form { control, check: () => 'message' or '' }. Errors show on blur
 * (once the control was edited) and on submit, and clear as soon as the value is fixed.
 * Returns validate(): true when everything passes; otherwise focuses the first error.
 */
export function validator(rules) {
  const edited = new WeakSet();
  for (const { control, check } of rules) {
    if (isField(control)) control.setAttribute('aria-invalid', 'false');
    const recheck = () => {
      edited.add(control);
      if (!check()) clearFieldError(control);
    };
    control.addEventListener('input', recheck);
    control.addEventListener('change', recheck);
    control.addEventListener('focusout', (event) => {
      if (!edited.has(control) || control.contains(event.relatedTarget)) return;
      const message = check();
      if (message) showFieldError(control, message, { spoken: true });
    });
  }
  return () => {
    let first = null;
    for (const { control, check } of rules) {
      const message = check();
      if (message) {
        showFieldError(control, message);
        first ??= control;
      } else {
        clearFieldError(control);
      }
    }
    if (first) focusControl(first);
    return !first;
  };
}

// ---- shared rules -------------------------------------------------------------------------

export function emailRule(input) {
  return {
    control: input,
    check() {
      const value = input.value.trim();
      if (!value) return 'اكتب بريدك الإلكتروني.';
      return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? '' : 'اكتب بريدًا صحيحًا، مثل name@example.com.';
    },
  };
}

export function channelsRule(form) {
  const field = form.querySelector('#channelsField');
  const { receive_email: email, receive_telegram: telegram } = form.elements;
  return {
    control: field,
    check: () => (email.checked || telegram.checked ? '' : 'اختر طريقة واحدة على الأقل لاستلام التنبيهات.'),
  };
}

export function categoriesRule(picker) {
  return {
    control: picker.field,
    check: () => (picker.selected().length ? '' : 'اختر تصنيفًا واحدًا على الأقل.'),
  };
}

export function numberRules(form) {
  return [...form.querySelectorAll('input[type="number"]')].map((input) => ({
    control: input,
    check() {
      const { badInput, rangeOverflow, rangeUnderflow } = input.validity;
      if (!(badInput || rangeOverflow || rangeUnderflow)) return '';
      return input.max ? `اكتب رقمًا من ${input.min} إلى ${input.max}.` : `اكتب رقمًا لا يقل عن ${input.min}.`;
    },
  }));
}

// ---- the settings form (shared by home and preferences) -----------------------------------

const CHECKBOXES = [
  'receive_email',
  'receive_telegram',
  'require_verified_client',
  'require_projects_in_progress',
  'require_ongoing_communications',
];
const NUMBERS = ['min_hiring_rate', 'min_budget_usd', 'max_project_age_minutes'];
const WORD_LISTS = ['keywords_include', 'keywords_exclude'];

export function readSettings(form) {
  const fields = form.elements;
  const settings = { email_delivery: fields.email_delivery.value || 'instant' };
  for (const name of CHECKBOXES) settings[name] = fields[name].checked;
  for (const name of NUMBERS) {
    const value = fields[name].value.trim();
    settings[name] = value === '' ? null : Number(value);
  }
  for (const name of WORD_LISTS) settings[name] = fields[name].value;
  return settings;
}

export function fillSettings(form, data) {
  const fields = form.elements;
  fields.email_delivery.value = data.email_delivery || 'instant';
  for (const name of CHECKBOXES) fields[name].checked = Boolean(data[name]);
  for (const name of NUMBERS) fields[name].value = data[name] ?? '';
  for (const name of WORD_LISTS) fields[name].value = data[name] ?? '';
}

/** Show the email-timing choice only while email is checked. Returns the sync function. */
export function linkEmailDelivery(form) {
  const email = form.elements.receive_email;
  const group = form.querySelector('[data-email-delivery]');
  const sync = () => { group.hidden = !email.checked; };
  email.addEventListener('change', sync);
  sync();
  return sync;
}

// ---- category chips ---------------------------------------------------------------------

/** Chips with a live "N من 10" counter and a search box, inside the categories_field macro. */
export function categoryPicker(field) {
  const max = Number(field.dataset.max) || 10;
  const chips = field.querySelector('[data-chips]');
  const search = field.querySelector('input[type="search"]');
  const empty = field.querySelector('[data-search-empty]');
  const counter = field.querySelector('output');
  let wanted = new Set();

  const inputs = () => [...chips.querySelectorAll('input')];
  const selected = () => inputs().filter((input) => input.checked).map((input) => Number(input.value));

  function update() {
    const count = selected().length;
    counter.textContent = count >= max ? `${count} من ${max} (الحد الأقصى)` : `${count} من ${max}`;
    for (const input of inputs()) input.disabled = !input.checked && count >= max;
  }

  function filter() {
    const query = search.value.trim().toLocaleLowerCase('ar');
    let visible = 0;
    for (const chip of chips.querySelectorAll('.chip')) {
      const show = !query || chip.textContent.toLocaleLowerCase('ar').includes(query);
      chip.hidden = !show;
      if (show) visible += 1;
    }
    empty.hidden = visible > 0 || !query;
  }

  function chip(category) {
    const label = document.createElement('label');
    label.className = 'chip';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = 'category_ids';
    input.value = String(category.id);
    input.checked = wanted.has(category.id);
    const name = document.createElement('span');
    name.textContent = category.name;
    label.append(input, name);
    return label;
  }

  chips.addEventListener('change', update);
  search.addEventListener('input', filter);

  const ready = api('/categories')
    .then((categories) => {
      if (!categories.length) {
        chips.querySelector('p').textContent = 'لا توجد تصنيفات الآن. حدّث الصفحة بعد قليل.';
        return;
      }
      chips.replaceChildren(...categories.map(chip));
      update();
      filter();
    })
    .catch(() => {
      chips.querySelector('p').textContent = 'تعذر تحميل التصنيفات. حدّث الصفحة.';
    });

  return {
    field,
    ready,
    selected,
    set(ids) {
      wanted = new Set(ids.map(Number));
      for (const input of inputs()) input.checked = wanted.has(Number(input.value));
      update();
    },
  };
}
