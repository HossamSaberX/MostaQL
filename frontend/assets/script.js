// API Base URL - change in production
// For local dev with Go Live extension, API runs on port 8000
const API_BASE_URL = window.location.port === '5500' || window.location.port === '5501'
    ? 'http://localhost:8000/api'  // Go Live extension port
    : window.location.origin + '/api';  // Production with nginx

// Load categories on page load
document.addEventListener('DOMContentLoaded', () => {
    loadCategories();
});

// Fetch and display categories
async function loadCategories() {
    const grid = document.getElementById('categoriesGrid');
    
    try {
        const response = await fetch(`${API_BASE_URL}/categories`);
        
        if (!response.ok) {
            throw new Error('Failed to load categories');
        }
        
        const categories = await response.json();
        
        if (categories.length === 0) {
            grid.innerHTML = '<p style="text-align: center; color: #e74c3c;">لا توجد تخصصات متاحة حالياً</p>';
            return;
        }
        
        // Clear loading message
        grid.innerHTML = '';
        
        // Create checkbox for each category
        categories.forEach(category => {
            const categoryItem = document.createElement('div');
            categoryItem.className = 'category-item';
            
            categoryItem.innerHTML = `
                <input 
                    type="checkbox" 
                    id="cat-${category.id}" 
                    name="categories" 
                    value="${category.id}"
                >
                <label for="cat-${category.id}">${category.name}</label>
            `;
            
            grid.appendChild(categoryItem);
        });
        
    } catch (error) {
        console.error('Error loading categories:', error);
        grid.innerHTML = '<p style="text-align: center; color: #e74c3c;">حدث خطأ في تحميل التخصصات. يرجى تحديث الصفحة.</p>';
    }
}

// Show message
function showMessage(message, type = 'success') {
    const messageBox = document.getElementById('messageBox');
    messageBox.textContent = message;
    messageBox.className = `message ${type} show`;
    
    // Auto-hide after 5 seconds for success messages
    if (type === 'success') {
        setTimeout(() => {
            messageBox.classList.remove('show');
        }, 5000);
    }
}

// Handle form submission
document.getElementById('subscribeForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const submitBtn = document.getElementById('submitBtn');
    const email = document.getElementById('email').value;
    
    // Get selected categories
    const selectedCategories = Array.from(
        document.querySelectorAll('input[name="categories"]:checked')
    ).map(checkbox => parseInt(checkbox.value));
    
    // Validation
    if (selectedCategories.length === 0) {
        showMessage('يرجى اختيار تخصص واحد على الأقل', 'error');
        return;
    }
    
    if (selectedCategories.length > 10) {
        showMessage('يمكنك اختيار 10 تخصصات كحد أقصى', 'error');
        return;
    }
    
    // Disable submit button
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="loading"></span> جاري الإرسال...';
    
    try {
        const response = await fetch(`${API_BASE_URL}/subscribe`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                email: email,
                category_ids: selectedCategories
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showMessage(data.message + ' ✓', 'success');
            
            // Reset form after 2 seconds
            setTimeout(() => {
                document.getElementById('subscribeForm').reset();
            }, 2000);
        } else {
            // Handle errors
            const errorMessage = data.detail || 'حدث خطأ أثناء الاشتراك';
            showMessage(errorMessage, 'error');
        }
        
    } catch (error) {
        console.error('Subscription error:', error);
        showMessage('حدث خطأ في الاتصال. يرجى المحاولة مرة أخرى.', 'error');
    } finally {
        // Re-enable submit button
        submitBtn.disabled = false;
        submitBtn.textContent = 'اشترك الآن';
    }
});

