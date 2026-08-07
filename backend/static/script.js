const API_BASE_URL =
  window.location.port === '5500' || window.location.port === '5501'
    ? 'http://localhost:8000/api'
    : `${window.location.origin}/api`;

const categoriesContainer = document.getElementById('categories');
const emailInput = document.getElementById('email');
const successMessage = document.getElementById('successMessage');
const errorMessage = document.getElementById('errorMessage');
const emailError = document.getElementById('emailError');
const categoryError = document.getElementById('categoryError');
const categorySearch = document.getElementById('categorySearch');
const categorySearchEmpty = document.getElementById('categorySearchEmpty');
const submitBtn = document.getElementById('submitBtn');
const form = document.getElementById('subscribeForm');

document.addEventListener('DOMContentLoaded', () => {
  loadCategories();
  categorySearch?.addEventListener('input', () => filterCategories(categorySearch.value));
});

function readOptionalNumber(id) {
  const value = document.getElementById(id).value.trim();
  return value === '' ? null : Number(value);
}

async function loadCategories() {
  try {
    const response = await fetch(`${API_BASE_URL}/categories`);
    if (!response.ok) throw new Error('Failed to load categories');

    const categories = await response.json();
    if (!categories.length) {
      categoriesContainer.innerHTML = '<p class="loading">لا توجد تصنيفات متاحة حالياً.</p>';
      return;
    }

    categoriesContainer.innerHTML = '';
    categories.forEach((category) => {
      const wrapper = document.createElement('label');
      wrapper.className = 'category-item';
      wrapper.setAttribute('for', `cat-${category.id}`);

      wrapper.innerHTML = `
        <input type="checkbox" id="cat-${category.id}" value="${category.id}" name="categories">
        <span class="category-label">${category.name}</span>
      `;

      categoriesContainer.appendChild(wrapper);
    });
    filterCategories(categorySearch?.value || '');
  } catch (err) {
    console.error(err);
    categoriesContainer.innerHTML = '<p class="loading">تعذر تحميل التصنيفات. يرجى تحديث الصفحة.</p>';
  }
}
function filterCategories(query) {
  if (!categoriesContainer) return;
  const normalizedQuery = query.trim().toLocaleLowerCase();
  let visibleCount = 0;

  categoriesContainer.querySelectorAll('.category-item').forEach((item) => {
    const label = item.querySelector('.category-label')?.textContent || '';
    const visible = !normalizedQuery || label.toLocaleLowerCase().includes(normalizedQuery);
    item.hidden = !visible;
    if (visible) visibleCount += 1;
  });

  if (categorySearchEmpty) categorySearchEmpty.hidden = visibleCount > 0;
}


function setAlert(element, message) {
  if (!element) return;
  if (message) {
    element.textContent = message;
    element.classList.add('show');
  } else {
    element.classList.remove('show');
  }
}

function toggleGlobalAlert(element, message) {
  if (!element) return;
  if (message) {
    element.textContent = message;
    element.classList.add('show');
  } else {
    element.classList.remove('show');
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  toggleGlobalAlert(successMessage, '');
  toggleGlobalAlert(errorMessage, '');
  setAlert(emailError, '');
  setAlert(categoryError, '');

  const selectedCategories = Array.from(
    document.querySelectorAll('input[name="categories"]:checked')
  ).map((checkbox) => Number(checkbox.value));

  if (!emailInput.validity.valid) {
    setAlert(emailError, 'يرجى إدخال بريد إلكتروني صالح');
    return;
  }

  if (!selectedCategories.length) {
    setAlert(categoryError, 'يجب اختيار تصنيف واحد على الأقل');
    return;
  }

  if (selectedCategories.length > 10) {
    setAlert(categoryError, 'يمكنك اختيار 10 تصنيفات كحد أقصى');
    return;
  }

  const receiveEmail = document.getElementById('receiveEmail').checked;
  const receiveTelegram = document.getElementById('receiveTelegram').checked;
  const minHiringRate = readOptionalNumber('minHiringRate');
  const minBudgetUsd = readOptionalNumber('minBudgetUsd');
  const maxProjectAgeMinutes = readOptionalNumber('maxProjectAgeMinutes');
  const requireProjectsInProgress = document.getElementById('requireProjectsInProgress').checked;
  const requireOngoingCommunications = document.getElementById('requireOngoingCommunications').checked;
  const requireVerifiedClient = document.getElementById('requireVerifiedClient').checked;

  if (!receiveEmail && !receiveTelegram) {
    toggleGlobalAlert(errorMessage, 'يجب اختيار طريقة إشعار واحدة على الأقل');
    return;
  }

  submitBtn.disabled = true;
  submitBtn.textContent = 'جاري الاشتراك...';

  try {
    const response = await fetch(`${API_BASE_URL}/subscribe`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        email: emailInput.value,
        category_ids: selectedCategories,
        receive_email: receiveEmail,
        receive_telegram: receiveTelegram,
        min_hiring_rate: minHiringRate,
        require_projects_in_progress: requireProjectsInProgress,
        require_ongoing_communications: requireOngoingCommunications,
        min_budget_usd: minBudgetUsd,
        require_verified_client: requireVerifiedClient,
        max_project_age_minutes: maxProjectAgeMinutes,
      }),
    });

    const data = await response.json();
    if (response.ok) {
      const receiveEmailChecked = document.getElementById('receiveEmail').checked;
      const receiveTelegramChecked = document.getElementById('receiveTelegram').checked;
      
      const telegramSection = document.getElementById('telegramSection');
      const telegramBtn = document.getElementById('telegramBtn');
      const resetSection = document.getElementById('resetSection');
      const subtitle = document.querySelector('.subtitle');
      
      form.style.display = 'none';
      
      const isUpdate = (data.status === 'updated' || data.status === 'reactivated') || 
                       (data.message && (data.message.includes('تحديث') || data.message.includes('عودة')));
      
      if (isUpdate) {
          toggleGlobalAlert(successMessage, data.message);
          
          if (receiveTelegramChecked && data.token && typeof telegramBotUsername !== 'undefined' && telegramBotUsername) {
              telegramBtn.href = `https://t.me/${telegramBotUsername}?start=${data.token}`;
              document.getElementById('telegramBtnText').textContent = 'فتح تيليجرام';
              telegramSection.style.display = 'block';
          }
      } else {
          toggleGlobalAlert(successMessage, '');
          
          if (receiveTelegramChecked && data.token && typeof telegramBotUsername !== 'undefined' && telegramBotUsername) {
              telegramBtn.href = `https://t.me/${telegramBotUsername}?start=${data.token}`;
              document.getElementById('telegramBtnText').textContent = 'تفعيل تنبيهات تيليجرام فوراً';
              telegramSection.style.display = 'block';
          }
          
          if (receiveEmailChecked) {
              subtitle.textContent = receiveTelegramChecked 
                ? 'تم إرسال رسالة التفعيل. يرجى التحقق من بريدك الإلكتروني أو تفعيل تيليجرام فوراً'
                : 'تم إرسال رسالة التفعيل. يرجى التحقق من بريدك الإلكتروني';
          } else if (receiveTelegramChecked) {
              subtitle.textContent = 'تم تسجيل طلبك. يمكنك تفعيل تنبيهات تيليجرام فوراً';
          } else {
              subtitle.textContent = data.message || 'تم الاشتراك بنجاح';
          }
      }
      
      if (resetSection) {
        resetSection.style.display = 'block';
        document.getElementById('resetBtn').onclick = () => window.location.reload();
      }
      
    } else {
      toggleGlobalAlert(errorMessage, data.detail || 'حدث خطأ أثناء الاشتراك');
    }
  } catch (err) {
    console.error(err);
    toggleGlobalAlert(errorMessage, 'حدث خطأ في الاتصال. يرجى المحاولة لاحقاً.');
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'اشترك الآن';
  }
});

