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
const submitBtn = document.getElementById('submitBtn');
const form = document.getElementById('subscribeForm');

document.addEventListener('DOMContentLoaded', () => {
  loadCategories();
});

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
  } catch (err) {
    console.error(err);
    categoriesContainer.innerHTML = '<p class="loading">تعذر تحميل التصنيفات. يرجى تحديث الصفحة.</p>';
  }
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

  submitBtn.disabled = true;
  submitBtn.textContent = 'جاري الإرسال...';

  try {
    const response = await fetch(`${API_BASE_URL}/subscribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: emailInput.value.trim(),
        category_ids: selectedCategories,
      }),
    });

    const data = await response.json();
    if (response.ok) {
      toggleGlobalAlert(successMessage, data.message || 'تم الاشتراك بنجاح');
      form.reset();
      
      // Show Telegram button if token is present
      const telegramSection = document.getElementById('telegramSection');
      const telegramBtn = document.getElementById('telegramBtn');
      const resetSection = document.getElementById('resetSection');
      
      if (data.token && typeof telegramBotUsername !== 'undefined' && telegramBotUsername) {
          telegramBtn.href = `https://t.me/${telegramBotUsername}?start=${data.token}`;
          
          // Logic to check if user is already linked could be improved by returning 'is_linked' flag from backend
          // For now, we can infer based on the message content or just show a generic "Connect/Open" message
          if (data.message && data.message.includes('تحديث')) {
             document.getElementById('telegramBtnText').textContent = 'فتح تيليجرام';
          }

          telegramSection.style.display = 'block';
          // Hide form after success to focus on next steps
          form.style.display = 'none';
          document.querySelector('.subtitle').textContent = 'تم تسجيل طلبك بنجاح';
          
          if (resetSection) {
            resetSection.style.display = 'block';
            document.getElementById('resetBtn').onclick = () => window.location.reload();
          }
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

