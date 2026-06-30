(() => {
  const API = {
    searchFood: '/api/search-food',
    analyzeImage: '/api/analyze-image-nutrition',
    calculateRecommendation: '/api/calculate-recommendation',
    calculateCustomRecipes: '/api/calculate-custom-recipes',
    updateImageLabel: '/api/update-image-food-label',
    users: '/api/user-records',
    userDetail: (id) => `/api/user-records/${encodeURIComponent(id)}`,
    dailyIntake: (id) => `/api/daily-intake/${encodeURIComponent(id)}`,
    downloadStl: (name) => `/download-stl/${encodeURIComponent(name)}`,
    downloadObj: (name) => `/download-obj/${encodeURIComponent(name)}`,
    downloadStlZip: '/download-stl-zip',
    health: '/health',
  };

  const LS_KEYS = {
    activeUserId: 'ui.activeUserId',
    settings: 'ui.settings',
  };

  const todayKey = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };

  const deepCopy = (v) => JSON.parse(JSON.stringify(v));
  const round = (n, d = 2) => Number((Number(n || 0)).toFixed(d));
  
  const formatNutrition = (calories, carbs, protein, fat) => {
    return `${round(calories, 1)} kcal | Carbs ${round(carbs, 2)}g | Protein ${round(protein, 2)}g | Fat ${round(fat, 2)}g`;
  };

  const shortId = (id) => {
    const v = String(id || '').trim();
    if (v.length <= 12) return v;
    return `${v.slice(0, 8)}...${v.slice(-4)}`;
  };
  
  const DIET_SCALE = [
    [0.50 / 4.1, 0.20 / 4.1, 0.30 / 8.8],
    [0.60 / 4.1, 0.20 / 4.1, 0.20 / 8.8],
    [0.20 / 4.1, 0.30 / 4.1, 0.50 / 8.8],
    [0.28 / 4.1, 0.39 / 4.1, 0.33 / 8.8],
  ];

  class NutritionUI {
    constructor() {
      this.state = {
        users: [],
        activeUserId: localStorage.getItem(LS_KEYS.activeUserId) || '',
        activeUser: null,
        profile: {},
        entriesByDate: {},
        selectedDate: todayKey(),
        recommendation: null,
          recommendationStatus: '',
        customRecipes: null,
        pendingFoodPreview: null,
        pendingImagePreview: null,
        calendarMonth: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
        historyUndo: [],
        historyRedo: [],
        settings: {
          autosave: true,
          confirmDelete: true,
          sidebarCollapsed: false,
          insightCollapsed: false,
          ...JSON.parse(localStorage.getItem(LS_KEYS.settings) || '{}'),
        },
        recommendationRequestKey: '',
        recommendationInFlight: null,
        activeUserSyncToken: 0,
      };

      this.bindDom();
      this.bindEvents();
      this.boot();
    }

    bindDom() {
      this.navItems = Array.from(document.querySelectorAll('.nav-item'));
      this.pages = Array.from(document.querySelectorAll('.page'));
      this.tabs = Array.from(document.querySelectorAll('.tab'));
      this.tabPanels = Array.from(document.querySelectorAll('.tab-panel'));

      this.activeUserName = document.getElementById('active-user-name');
      this.toggleLeftNavBtn = document.getElementById('toggle-left-nav');
      this.toggleRightPanelBtn = document.getElementById('toggle-right-panel');
      this.openUserManagerBtn = document.getElementById('open-user-manager');
      this.quickSaveDayBtn = document.getElementById('quick-save-day');

      this.summary = {
        calories: document.getElementById('summary-calories'),
        carbs: document.getElementById('summary-carbs'),
        protein: document.getElementById('summary-protein'),
        fat: document.getElementById('summary-fat'),
      };

      this.targetProgressView = document.getElementById('target-progress-view');
      this.dashboardUserSummary = document.getElementById('dashboard-user-summary');
      this.dashboardIntakeList = document.getElementById('dashboard-intake-list');
      this.recommendationSnapshot = document.getElementById('recommendation-snapshot-content');
      this.goRecommendations = document.getElementById('go-recommendations');
      this.goCalendar = document.getElementById('go-calendar');

      this.foodSearchForm = document.getElementById('food-search-form');
      this.foodSearchInput = document.getElementById('food-search-input');
      this.foodSearchPreview = document.getElementById('food-search-preview');

      this.directMacroForm = document.getElementById('direct-macro-form');
      this.previewMacroBtn = document.getElementById('preview-macro');
      this.macroPreview = document.getElementById('macro-preview');
      this.macroFields = {
        carbs: document.getElementById('macro-carbs'),
        protein: document.getElementById('macro-protein'),
        fat: document.getElementById('macro-fat'),
        calories: document.getElementById('macro-calories'),
      };

      this.imageAnalysisForm = document.getElementById('image-analysis-form');
      this.mealImage = document.getElementById('meal-image');
      this.imageUploadPreview = document.getElementById('image-upload-preview');
      this.imageUploadPreviewImg = document.getElementById('image-upload-preview-img');
      this.imageHints = document.getElementById('image-hints');
      this.imageContainerSize = document.getElementById('image-container-size');
      this.imageAnalysisResult = document.getElementById('image-analysis-result');

      this.timeline = document.getElementById('intake-timeline');
      this.historyList = document.getElementById('history-list');
      this.undoBtn = document.getElementById('undo-action');
      this.redoBtn = document.getElementById('redo-action');
      this.clearDayBtn = document.getElementById('clear-day');

      this.runRecommendationBtn = document.getElementById('run-recommendation');
      this.recommendationLoadingIndicator = document.getElementById('recommendation-loading-indicator');
      this.recommendationLoadingText = document.getElementById('recommendation-loading-text');
      this.recommendationRefreshHint = document.getElementById('recommendation-refresh-hint');
      this.energyGoalView = document.getElementById('energy-goal-view');
      this.nutrientGapView = document.getElementById('nutrient-gap-view');
      this.suggestedCombosView = document.getElementById('suggested-combos-view');
      this.printingModelsView = document.getElementById('printing-models-view');

      this.customFoodText = document.getElementById('custom-food-text');
      this.runCustomRecipesBtn = document.getElementById('run-custom-recipes');
      this.customRecipesRuntimeStatus = document.getElementById('custom-recipes-runtime-status');
      this.customRecipesView = document.getElementById('custom-recipes-view');
      this.customRecipeCard = document.getElementById('custom-recipe-card');
      this.customRecipeContent = document.getElementById('custom-recipe-content');
      this.customRecipeStatus = document.getElementById('custom-recipe-status');
      this.toggleCustomRecipeBtn = document.getElementById('toggle-custom-recipe');

      this.calendarPrev = document.getElementById('calendar-prev');
      this.calendarNext = document.getElementById('calendar-next');
      this.calendarMonthLabel = document.getElementById('calendar-month-label');
      this.calendarGrid = document.getElementById('calendar-grid');
      this.calendarDayDetail = document.getElementById('calendar-day-detail');

      this.createUserName = document.getElementById('create-user-name');
      this.createUserSubmit = document.getElementById('create-user-submit');
      this.userListView = document.getElementById('user-list-view');
      this.profileDropdown = document.getElementById('profile-dropdown');
      this.profileForm = document.getElementById('profile-form');
      this.profileOwnerLabel = document.getElementById('profile-owner-label');
      this.saveProfileBtn = document.getElementById('save-profile');

      this.autosaveToggle = document.getElementById('autosave-toggle');
      this.confirmDeleteToggle = document.getElementById('confirm-delete-toggle');

      this.entryEditModal = document.getElementById('entry-edit-modal');
      this.entryEditContent = document.getElementById('entry-edit-content');
      this.closeEntryModal = document.getElementById('close-entry-modal');

      this.foodPreviewEditModal = document.getElementById('food-preview-edit-modal');
      this.foodPreviewEditContent = document.getElementById('food-preview-edit-content');
      this.closeFoodPreviewModal = document.getElementById('close-food-preview-modal');
      this.previewFoodLabel = document.getElementById('preview-food-label');
      this.previewCalories = document.getElementById('preview-calories');
      this.previewCarbs = document.getElementById('preview-carbs');
      this.previewProtein = document.getElementById('preview-protein');
      this.previewFat = document.getElementById('preview-fat');
      this.confirmFoodAfterEdit = document.getElementById('confirm-food-after-edit');

      this.confirmationModal = document.getElementById('confirmation-modal');
      this.confirmationTitle = document.getElementById('confirmation-title');
      this.confirmationMessage = document.getElementById('confirmation-message');
      this.confirmationCancelBtn = document.getElementById('confirmation-cancel');
      this.confirmationConfirmBtn = document.getElementById('confirmation-confirm');
      this.toastContainer = document.getElementById('toast-container');
    }

    bindEvents() {
      this.navItems.forEach((btn) => btn.addEventListener('click', () => this.openPage(btn.dataset.page)));
      this.tabs.forEach((btn) => btn.addEventListener('click', () => this.openTab(btn.dataset.logTab)));

      document.querySelectorAll('[data-jump-tab]').forEach((btn) => {
        btn.addEventListener('click', () => {
          this.openPage('log');
          this.openTab(btn.dataset.jumpTab);
        });
      });

      this.openUserManagerBtn.addEventListener('click', () => this.openPage('users'));
      this.quickSaveDayBtn.addEventListener('click', () => this.saveDayToBackend());
      this.toggleLeftNavBtn.addEventListener('click', () => this.toggleSidebar());
      this.toggleRightPanelBtn.addEventListener('click', () => this.toggleInsightPanel());
      this.goRecommendations.addEventListener('click', () => this.openPage('recommendations'));
      this.goCalendar.addEventListener('click', () => this.openPage('calendar'));

      this.foodSearchForm.addEventListener('submit', (e) => this.handleFoodSearch(e));

      this.previewMacroBtn.addEventListener('click', () => this.previewMacroDelta());
      this.directMacroForm.addEventListener('submit', (e) => this.handleMacroSubmit(e));

      this.imageAnalysisForm.addEventListener('submit', (e) => this.handleImageAnalyze(e));
      this.mealImage.addEventListener('change', () => this.renderUploadedImagePreview());

      this.undoBtn.addEventListener('click', () => this.undo());
      this.redoBtn.addEventListener('click', () => this.redo());
      this.clearDayBtn.addEventListener('click', () => this.clearDay());

      if (this.runRecommendationBtn) {
        this.runRecommendationBtn.addEventListener('click', () => this.runRecommendation());
      }
      this.runCustomRecipesBtn.addEventListener('click', () => this.runCustomRecipes());
      this.toggleCustomRecipeBtn.addEventListener('click', () => this.toggleCustomRecipeSection());

      this.calendarPrev.addEventListener('click', () => this.shiftCalendar(-1));
      this.calendarNext.addEventListener('click', () => this.shiftCalendar(1));

      this.createUserSubmit.addEventListener('click', () => this.createUser());
      if (this.saveProfileBtn) {
        this.saveProfileBtn.addEventListener('click', () => this.saveProfile());
      }

      this.autosaveToggle.checked = !!this.state.settings.autosave;
      this.confirmDeleteToggle.checked = !!this.state.settings.confirmDelete;
      this.autosaveToggle.addEventListener('change', () => this.updateSettings());
      this.confirmDeleteToggle.addEventListener('change', () => this.updateSettings());

      this.closeEntryModal.addEventListener('click', () => this.closeEntryEditor());
      this.entryEditModal.addEventListener('click', (e) => {
        if (e.target === this.entryEditModal) this.closeEntryEditor();
      });

      this.closeFoodPreviewModal.addEventListener('click', () => this.closeFoodPreviewEditor());
      this.foodPreviewEditModal.addEventListener('click', (e) => {
        if (e.target === this.foodPreviewEditModal) this.closeFoodPreviewEditor();
      });
      this.confirmFoodAfterEdit.addEventListener('click', () => this.commitPendingFoodWithEdits());

      this.confirmationCancelBtn.addEventListener('click', () => this.closeConfirmation());
      this.confirmationConfirmBtn.addEventListener('click', () => this.confirmConfirmation());
      this.confirmationModal.addEventListener('click', (e) => {
        if (e.target === this.confirmationModal) this.closeConfirmation();
      });

      window.addEventListener('resize', () => this.handleWindowResize());
    }

    async boot() {
      this.applySidebarState();
      await this.loadUsers();
      
      // Auto-select latest used account if no active user is selected
      if (!this.state.activeUser && this.state.users.length > 0) {
        let latestUserId = null;
        let latestTime = 0;
        
        for (const user of this.state.users) {
          const timeStr = localStorage.getItem(`lastUserTime.${user.user_id}`);
          const time = timeStr ? parseInt(timeStr, 10) : 0;
          if (time > latestTime) {
            latestTime = time;
            latestUserId = user.user_id;
          }
        }
        
        if (latestUserId) {
          const latestUser = this.state.users.find((u) => u.user_id === latestUserId);
          if (latestUser) {
            this.setActiveUser(latestUser);
            await this.loadDailyHistoryForMonth();
            this.renderAll();
            return;
          }
        }
      }
      
      if (!this.state.activeUser) {
        this.openPage('users');
      }
      this.renderAll();
      if (this.state.activeUserId) {
        await this.loadDailyHistoryForMonth();
      }
    }

    // Custom confirmation modal (replaces window.confirm)
    showConfirmation(title, message) {
      return new Promise((resolve) => {
        this.confirmationTitle.textContent = title;
        this.confirmationMessage.textContent = message;
        this.confirmationModal.removeAttribute('hidden');
        this.confirmationConfirmBtn.focus();

        this._pendingConfirmCallback = resolve;
      });
    }

    closeConfirmation() {
      this.confirmationModal.setAttribute('hidden', '');
      if (this._pendingConfirmCallback) {
        this._pendingConfirmCallback(false);
        this._pendingConfirmCallback = null;
      }
    }

    confirmConfirmation() {
      this.confirmationModal.setAttribute('hidden', '');
      if (this._pendingConfirmCallback) {
        this._pendingConfirmCallback(true);
        this._pendingConfirmCallback = null;
      }
    }

    // Toast notification system (replaces alert)
    showToast(message, type = 'info', duration = 3000) {
      const toast = document.createElement('div');
      toast.className = `toast ${type}`;
      toast.textContent = message;
      this.toastContainer.appendChild(toast);

      if (duration > 0) {
        setTimeout(() => {
          toast.classList.add('closing');
          toast.addEventListener('animationend', () => toast.remove(), { once: true });
        }, duration);
      }
      return toast;
    }

    entriesForSelectedDate() {
      return this.state.entriesByDate[this.state.selectedDate] || [];
    }

    calculateTotals(entries = this.entriesForSelectedDate()) {
      return entries.reduce((acc, item) => {
        acc.calories += Number(item.nutrition.calories || 0);
        acc.carbs += Number(item.nutrition.carbs || 0);
        acc.protein += Number(item.nutrition.protein || 0);
        acc.fat += Number(item.nutrition.fat || 0);
        return acc;
      }, { calories: 0, carbs: 0, protein: 0, fat: 0 });
    }

    toNumber(v, fallback = 0) {
      const n = Number(v);
      return Number.isFinite(n) ? n : fallback;
    }

    buildTargetsFromProfile() {
      const p = this.state.profile || {};
      const gender = this.toNumber(p.gender, 0);
      const age = this.toNumber(p.age, 0);
      const height = this.toNumber(p.height, 0);
      const weight = this.toNumber(p.weight, 0);
      const activity = this.toNumber(p.activity, 0);
      const diet = this.toNumber(p.diet, 0);

      if (age <= 0 || height <= 0 || weight <= 0) return null;
      if (![0, 1].includes(gender)) return null;
      if (![0, 1, 2, 3].includes(activity)) return null;
      if (![0, 1, 2, 3].includes(diet)) return null;

      const rmr = gender === 0
        ? (9.99 * weight) + (6.25 * height) - (4.92 * age) + 5
        : (9.99 * weight) + (6.25 * height) - (4.92 * age) - 161;

      const activityFactor = [1.2, 1.375, 1.55, 1.725][activity];
      const calories = rmr * activityFactor;
      const [carbScale, proteinScale, fatScale] = DIET_SCALE[diet];

      return {
        calories,
        carbohydrate_intake: calories * carbScale,
        protein_intake: calories * proteinScale,
        fat_intake: calories * fatScale,
      };
    }

    getTargetModel() {
      if (this.state.recommendation) {
        return this.state.recommendation;
      }
      return this.buildTargetsFromProfile();
    }

    targetProgressRowHtml(label, current, target, unit, keyClass) {
      const safeTarget = Number(target || 0);
      const safeCurrent = Number(current || 0);
      const ratio = safeTarget > 0 ? safeCurrent / safeTarget : 0;
      const pct = Math.max(0, ratio * 100);
      const clamped = Math.min(100, pct);
      const exceeded = safeTarget > 0 && safeCurrent > safeTarget;
      const overBy = exceeded ? safeCurrent - safeTarget : 0;

      return `
        <div class="target-progress-row ${exceeded ? 'is-exceeded' : ''}">
          <div class="target-progress-head">
            <span>${label}</span>
            <strong>${round(safeCurrent, 2)} / ${round(safeTarget, 2)} ${unit}</strong>
          </div>
          <div class="target-progress-track" role="progressbar" aria-label="${label} progress" aria-valuemin="0" aria-valuemax="${round(safeTarget, 2)}" aria-valuenow="${round(safeCurrent, 2)}">
            <div class="target-progress-fill ${keyClass}${exceeded ? ' warning-shine' : ''}" style="width: ${clamped.toFixed(1)}%;"></div>
          </div>
          <div class="target-progress-meta">
            <span>${round(pct, 1)}%</span>
            ${exceeded ? `<span class="target-over">Over by ${round(overBy, 2)} ${unit}</span>` : ''}
          </div>
        </div>
      `;
    }

    pushHistory(prevState, nextState, actionLabel) {
      this.state.historyUndo.push({ prevState, nextState, actionLabel });
      this.state.historyRedo = [];
    }

    applyStatePatch(nextEntriesByDate, actionLabel) {
      const prev = deepCopy(this.state.entriesByDate);
      this.state.entriesByDate = nextEntriesByDate;
      const next = deepCopy(this.state.entriesByDate);
      this.pushHistory(prev, next, actionLabel);
      this.renderAll();
      if (this.state.settings.autosave) this.saveDayToBackend();
    }

    renderAll() {
      this.renderActiveUser();
      this.renderUsers();
      this.renderProfile();
      this.renderCustomRecipeSection();
      this.renderRecommendationRefreshHint();
      this.renderSummary();
      this.renderTimeline();
      this.renderDashboard();
      this.renderHistory();
      this.renderRecommendationPanels();
      this.renderCalendar();
    }

    getRecommendationRequestKey() {
      const totals = this.calculateTotals();
      const profile = this.state.profile || {};
      return JSON.stringify({
        userId: this.state.activeUserId,
        profile,
        totals: {
          calories: round(totals.calories, 3),
          carbs: round(totals.carbs, 3),
          protein: round(totals.protein, 3),
          fat: round(totals.fat, 3),
        },
      });
    }

    isRecommendationStale() {
      if (!this.state.recommendation || !this.state.recommendationRequestKey) return false;
      return this.state.recommendationRequestKey !== this.getRecommendationRequestKey();
    }

    renderRecommendationRefreshHint() {
      if (!this.recommendationRefreshHint) return;
      const shouldShow = this.state.recommendation && this.isRecommendationStale();
      this.recommendationRefreshHint.hidden = !shouldShow;
      if (shouldShow) {
        this.recommendationRefreshHint.textContent = 'You have added new foods since your last recommendation. Generate updated recommendations to refresh goals, gaps, and model outputs.';
      } else {
        this.recommendationRefreshHint.textContent = '';
      }
    }

    hasPreferredFoods() {
      return !!String(this.state.profile?.preferred_foods || '').trim();
    }

    renderCustomRecipeSection() {
      if (!this.customRecipeCard) return;

      if (typeof this.state.customRecipeCollapsed !== 'boolean') {
        this.state.customRecipeCollapsed = this.hasPreferredFoods();
      }

      const collapsed = !!this.state.customRecipeCollapsed;
      this.customRecipeCard.classList.toggle('is-collapsed', collapsed);
      this.toggleCustomRecipeBtn.textContent = collapsed ? 'Show' : 'Hide';

      const preferredFoods = String(this.state.profile?.preferred_foods || '').trim();
      if (collapsed && preferredFoods) {
        this.customRecipeStatus.hidden = false;
        this.customRecipeStatus.textContent = `Hidden because profile already has preferred foods: ${preferredFoods}`;
      } else if (collapsed) {
        this.customRecipeStatus.hidden = false;
        this.customRecipeStatus.textContent = 'Custom recipe builder is hidden.';
      } else {
        this.customRecipeStatus.hidden = true;
        this.customRecipeStatus.textContent = '';
      }
    }

    toggleCustomRecipeSection() {
      this.state.customRecipeCollapsed = !this.state.customRecipeCollapsed;
      this.renderCustomRecipeSection();
    }

    buildInlineProfileEditor(user, active) {
      const u = user?.user_info || {};
      const userId = this.escape(user?.user_id || '');
      const summaryRowHtml = `
        <div class="user-card-main">
          <div><strong>${this.escape(u.name || user?.user_id || '')}</strong><div class="muted">${this.escape(user?.user_id || '')}</div></div>
          <div class="stack-actions">
            <button class="btn-secondary slim ${active ? 'current-state' : ''}" data-user-action="switch" data-user-id="${userId}" ${active ? 'disabled' : ''}>${active ? 'Current' : 'Switch'}</button>
            <button class="btn-secondary slim danger" data-user-action="delete" data-user-id="${userId}">Delete</button>
          </div>
        </div>
      `;
      return `
        <details class="profile-dropdown inline-profile-dropdown" ${active ? 'open' : ''}>
          <summary class="profile-dropdown-summary profile-user-summary">${summaryRowHtml}</summary>
          <div class="profile-dropdown-body">
            <form class="profile-grid" data-profile-form="${userId}">
              <label>Username
                <input type="text" name="name" value="${this.escape(u.name || '')}" required>
              </label>
              <label>Gender
                <select name="gender" required>
                  <option value="">Select</option>
                  <option value="0" ${String(u.gender ?? '') === '0' ? 'selected' : ''}>Male</option>
                  <option value="1" ${String(u.gender ?? '') === '1' ? 'selected' : ''}>Female</option>
                </select>
              </label>
              <label>Age
                <input type="number" name="age" min="1" value="${this.escape(u.age ?? '')}" required>
              </label>
              <label>Height (cm)
                <input type="number" name="height" step="0.1" min="1" value="${this.escape(u.height ?? '')}" required>
              </label>
              <label>Weight (kg)
                <input type="number" name="weight" step="0.1" min="1" value="${this.escape(u.weight ?? '')}" required>
              </label>
              <label>Activity Level
                <select name="activity" required>
                  <option value="">Select</option>
                  <option value="0" ${String(u.activity ?? '') === '0' ? 'selected' : ''}>Sedentary</option>
                  <option value="1" ${String(u.activity ?? '') === '1' ? 'selected' : ''}>Low Active</option>
                  <option value="2" ${String(u.activity ?? '') === '2' ? 'selected' : ''}>Active</option>
                  <option value="3" ${String(u.activity ?? '') === '3' ? 'selected' : ''}>Very Active</option>
                </select>
              </label>
              <label>Diet
                <select name="diet" required>
                  <option value="">Select</option>
                  <option value="0" ${String(u.diet ?? '') === '0' ? 'selected' : ''}>Balanced</option>
                  <option value="1" ${String(u.diet ?? '') === '1' ? 'selected' : ''}>Low Fat</option>
                  <option value="2" ${String(u.diet ?? '') === '2' ? 'selected' : ''}>Low Carb</option>
                  <option value="3" ${String(u.diet ?? '') === '3' ? 'selected' : ''}>High Protein</option>
                </select>
              </label>
              <label>Preference
                <select name="preference" required>
                  <option value="">Select</option>
                  <option value="0" ${String(u.preference ?? '') === '0' ? 'selected' : ''}>Meat-based</option>
                  <option value="1" ${String(u.preference ?? '') === '1' ? 'selected' : ''}>Plant-based</option>
                </select>
              </label>
              <label>Preferred Foods (optional)
                <input type="text" name="preferred_foods" value="${this.escape(u.preferred_foods || '')}" placeholder="e.g., tofu, oats, broccoli">
              </label>
            </form>
            <div class="inline-form-row">
              <button type="button" class="btn-primary" data-save-profile data-user-id="${userId}">Save Profile</button>
            </div>
          </div>
        </details>
      `;
    }

    renderActiveUser() {
      const active = this.state.activeUser;
      this.activeUserName.textContent = active ? (active.user_info?.name || active.user_id || 'Unknown') : 'Guest';
      
      if (active) {
        const name = active.user_info?.name || active.user_id || 'Unknown';
        const userId = active.user_id;
        this.dashboardUserSummary.innerHTML = `${name} <span class="user-id-display">(${this.escape(shortId(userId))})</span>`;
        this.dashboardUserSummary.classList.remove('is-disabled');
      } else {
        this.dashboardUserSummary.textContent = 'No active user yet. Create one in Users / Profiles.';
        this.dashboardUserSummary.classList.remove('is-disabled');
      }
    }

    renderSummary() {
      const t = this.calculateTotals();
      if (this.summary.calories) this.summary.calories.textContent = round(t.calories, 1);
      if (this.summary.carbs) this.summary.carbs.textContent = round(t.carbs, 2);
      if (this.summary.protein) this.summary.protein.textContent = round(t.protein, 2);
      if (this.summary.fat) this.summary.fat.textContent = round(t.fat, 2);

      const r = this.getTargetModel();
      if (!r) {
        this.targetProgressView.textContent = 'Save a valid profile (age, height, weight, gender, activity, diet) to show target progress.';
        return;
      }
      const progressData = [
        { label: 'Calories', current: t.calories, target: r.calories, unit: 'kcal', keyClass: 'calories' },
        { label: 'Carbs', current: t.carbs, target: r.carbohydrate_intake, unit: 'g', keyClass: 'carbs' },
        { label: 'Protein', current: t.protein, target: r.protein_intake, unit: 'g', keyClass: 'protein' },
        { label: 'Fat', current: t.fat, target: r.fat_intake, unit: 'g', keyClass: 'fat' },
      ];
      this.targetProgressView.innerHTML = `
        ${progressData.map((it) => this.targetProgressRowHtml(it.label, it.current, it.target, it.unit, it.keyClass)).join('')}
      `;
    }

    renderTimeline() {
      const entries = this.entriesForSelectedDate();
      const html = entries.length
        ? entries.map((entry) => this.entryCardHtml(entry)).join('')
        : '<div class="empty-state">No entries yet for this day.</div>';
      this.timeline.innerHTML = html;
      this.timeline.querySelectorAll('[data-action]').forEach((btn) => {
        btn.addEventListener('click', (e) => this.handleEntryAction(e.currentTarget));
      });
    }

    renderDashboard() {
      this.dashboardIntakeList.innerHTML = this.timeline.innerHTML;
      this.dashboardIntakeList.querySelectorAll('[data-action]').forEach((btn) => {
        btn.addEventListener('click', (e) => this.handleEntryAction(e.currentTarget));
      });

      if (!this.state.recommendation) {
        this.recommendationSnapshot.classList.add('is-empty');
        this.recommendationSnapshot.textContent = 'No recommendation generated yet.';
      } else {
        this.recommendationSnapshot.classList.remove('is-empty');
        const r = this.state.recommendation;
        this.recommendationSnapshot.innerHTML = `
          <div>Daily Goal: <strong>${round(r.calories, 1)} kcal</strong></div>
          <div>Need: ${formatNutrition(r.calories_needed || 0, r.carbohydrate_needed, r.protein_needed, r.fat_needed)}</div>
        `;
      }

      this.drawTrend();
    }

    getTrendCanvas() {
      return document.getElementById('trend-canvas');
    }

    resizeTrendCanvas(canvas) {
      if (!canvas) return null;
      const cssWidth = Math.max(1, canvas.clientWidth || canvas.parentElement?.clientWidth || 0);
      const cssHeight = Math.max(1, canvas.clientHeight || Math.round(cssWidth * 0.32));
      const pixelRatio = window.devicePixelRatio || 1;
      const nextWidth = Math.max(1, Math.round(cssWidth * pixelRatio));
      const nextHeight = Math.max(1, Math.round(cssHeight * pixelRatio));

      if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
        canvas.width = nextWidth;
        canvas.height = nextHeight;
      }

      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      }

      return {
        width: cssWidth,
        height: cssHeight,
        ctx,
      };
    }

    drawTrend() {
      const canvas = this.getTrendCanvas();
      if (!canvas) return;
      const sizing = this.resizeTrendCanvas(canvas);
      if (!sizing || !sizing.ctx) return;
      const { width, height, ctx } = sizing;

      ctx.clearRect(0, 0, width, height);
      const values = [];
      for (let i = 6; i >= 0; i--) {
        const d = new Date();
        d.setDate(d.getDate() - i);
        const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        const totals = this.calculateTotals(this.state.entriesByDate[key] || []);
        values.push(totals.calories || 0);
      }
      const max = Math.max(1, ...values);
      ctx.strokeStyle = '#0d8f6f';
      ctx.lineWidth = 3;
      ctx.beginPath();
      values.forEach((v, idx) => {
        const x = 15 + (idx * (width - 30) / 6);
        const y = height - 15 - ((v / max) * (height - 30));
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    handleWindowResize() {
      this.drawTrend();
    }

    renderHistory() {
      const entries = this.entriesForSelectedDate().slice().sort((a, b) => b.ts - a.ts);
      this.historyList.innerHTML = entries.length
        ? entries.map((e) => this.entryCardHtml(e)).join('')
        : '<div class="empty-state">No history for selected date.</div>';
      this.historyList.querySelectorAll('[data-action]').forEach((btn) => {
        btn.addEventListener('click', (ev) => this.handleEntryAction(ev.currentTarget));
      });
    }

    setRecommendationLoading(isLoading, text = 'Loading recommendations...') {
      if (!this.recommendationLoadingIndicator) return;
      this.recommendationLoadingIndicator.hidden = !isLoading;
      this.recommendationLoadingIndicator.classList.toggle('active', isLoading);
      this.recommendationLoadingIndicator.classList.remove('done', 'error');
      if (this.recommendationLoadingText) {
        this.recommendationLoadingText.textContent = text;
      }
    }

    setRecommendationFinished(text = 'Recommendations finished.') {
      if (!this.recommendationLoadingIndicator) return;
      this.recommendationLoadingIndicator.hidden = false;
      this.recommendationLoadingIndicator.classList.remove('active', 'error');
      this.recommendationLoadingIndicator.classList.add('done');
      if (this.recommendationLoadingText) {
        this.recommendationLoadingText.textContent = text;
      }
    }

    setRecommendationFailed(text = 'Recommendation failed.') {
      if (!this.recommendationLoadingIndicator) return;
      this.recommendationLoadingIndicator.hidden = false;
      this.recommendationLoadingIndicator.classList.remove('active', 'done');
      this.recommendationLoadingIndicator.classList.add('error');
      if (this.recommendationLoadingText) {
        this.recommendationLoadingText.textContent = text;
      }
    }

    renderRecommendationPanels() {
      const r = this.state.recommendation;
      if (!r) {
        const msg = this.state.recommendationStatus || 'No recommendation yet.';
        this.energyGoalView.textContent = msg;
        this.nutrientGapView.textContent = msg;
        this.suggestedCombosView.textContent = msg;
        this.printingModelsView.textContent = this.state.recommendationStatus || 'No printable outputs yet.';
        return;
      }

      const totals = this.calculateTotals();
      this.energyGoalView.innerHTML = `<strong>${round(r.calories, 1)} kcal</strong> target  current ${round(totals.calories, 1)} kcal`;

      this.nutrientGapView.innerHTML = `
        <table class="gap-table">
          <thead><tr><th>Nutrient</th><th>Target</th><th>Current</th><th>Need</th></tr></thead>
          <tbody>
            <tr><td>Carbs</td><td>${round(r.carbohydrate_intake, 2)}g</td><td>${round(totals.carbs, 2)}g</td><td>${round(r.carbohydrate_needed, 2)}g</td></tr>
            <tr><td>Protein</td><td>${round(r.protein_intake, 2)}g</td><td>${round(totals.protein, 2)}g</td><td>${round(r.protein_needed, 2)}g</td></tr>
            <tr><td>Fat</td><td>${round(r.fat_intake, 2)}g</td><td>${round(totals.fat, 2)}g</td><td>${round(r.fat_needed, 2)}g</td></tr>
          </tbody>
        </table>
      `;

      const combos = Array.isArray(r.best_matches) ? r.best_matches : [];
      const comboAdvice = r.best_match_advice || {};
      this.suggestedCombosView.innerHTML = combos.length
        ? combos.map((c, i) => {
            const foods = (c.foods || []).map((f) => `${f.name} ${f.gram}g`).join(', ');
            return `<div class="combo-card"><strong>Option ${i + 1}</strong><div>${foods}</div><small>Supplies: Carbs ${round(c.supplied?.carbs || 0, 2)}g | Protein ${round(c.supplied?.protein || 0, 2)}g | Fat ${round(c.supplied?.fat || 0, 2)}g</small></div>`;
          }).join('') + (comboAdvice.suggested_foods?.length ? `<div class="combo-note">Suggested additions: ${comboAdvice.suggested_foods.join(', ')}</div>` : '')
        : 'No combination suggestions.';

      const results = Array.isArray(r.results) ? r.results : [];
      const modelCards = [];
      results.forEach((res, idx) => {
        const foods = res[0] || [];
        const folderName = res[4] || '';
        const objName = res[5] || '';
        const files = foods.filter((f) => f.mesh);
        if (!files.length && !objName) return;
        modelCards.push(`
          <div class="model-card">
            <strong>Option ${idx + 1}</strong>
            <div>${files.map((f) => `${f.name} (${f.x || '-'} x ${f.y || '-'} x ${f.z || '-'} mm)`).join('<br>')}</div>
            <div class="inline-form-row">
              <button class="btn-secondary slim" data-download-zip="${idx}">Download ZIP</button>
              <button class="btn-secondary slim" data-download-stl="${idx}">Open STL List</button>
              ${objName ? `<button class="btn-secondary slim" data-download-obj="${idx}">Download OBJ (stacked)</button>` : ''}
            </div>
            ${folderName ? `<small>Folder hint: ${folderName}</small>` : ''}
          </div>
        `);
      });
      this.printingModelsView.innerHTML = modelCards.length ? modelCards.join('') : 'No model assets in this recommendation.';
      this.printingModelsView.querySelectorAll('[data-download-zip]').forEach((btn) => btn.addEventListener('click', () => this.downloadStlZip(Number(btn.dataset.downloadZip))));
      this.printingModelsView.querySelectorAll('[data-download-stl]').forEach((btn) => btn.addEventListener('click', () => this.openStlList(Number(btn.dataset.downloadStl))));
      this.printingModelsView.querySelectorAll('[data-download-obj]').forEach((btn) => btn.addEventListener('click', () => this.downloadObj(Number(btn.dataset.downloadObj))));
    }

    renderUsers() {
      if (!this.state.users.length) {
        this.userListView.innerHTML = '<div class="empty-state">No users. Create one above.</div>';
        return;
      }
      this.userListView.innerHTML = this.state.users.map((u) => {
        const active = this.state.activeUserId === u.user_id;
        return `
          <div class="user-card ${active ? 'active' : ''}">
            ${this.buildInlineProfileEditor(u, active)}
          </div>
        `;
      }).join('');
      this.userListView.querySelectorAll('[data-save-profile]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const userId = btn.dataset.userId;
          const form = this.userListView.querySelector(`form[data-profile-form="${userId}"]`);
          this.saveProfile(userId, form);
        });
      });
      this.userListView.querySelectorAll('[data-user-action]').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.handleUserAction(btn.dataset.userAction, btn.dataset.userId);
        });
      });
    }

    renderProfile() {
      // Profiles are now rendered and editable per user row in renderUsers().
    }

    renderCalendar() {
      const month = this.state.calendarMonth;
      this.calendarMonthLabel.textContent = month.toLocaleString(undefined, { month: 'long', year: 'numeric' });
      const first = new Date(month.getFullYear(), month.getMonth(), 1);
      const startOffset = first.getDay();
      const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
      const cells = [];
      for (let i = 0; i < startOffset; i++) cells.push('<div class="calendar-cell empty"></div>');
      for (let d = 1; d <= daysInMonth; d++) {
        const key = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const totals = this.calculateTotals(this.state.entriesByDate[key] || []);
        const hasRec = !!this.state.entriesByDate[key]?.some((e) => e.recommendationSnapshot);
        const isSelected = key === this.state.selectedDate;
        cells.push(`
          <button class="calendar-cell ${isSelected ? 'selected' : ''}" data-date="${key}" aria-pressed="${isSelected ? 'true' : 'false'}">
            <strong>${d}</strong>
            <span>${round(totals.calories, 1)} kcal</span>
            <small>${hasRec ? 'Rec ✓' : ''}</small>
          </button>
        `);
      }
      this.calendarGrid.innerHTML = cells.join('');
      this.calendarGrid.querySelectorAll('[data-date]').forEach((btn) => btn.addEventListener('click', () => this.selectDate(btn.dataset.date)));
      this.renderCalendarDetail();
    }

    renderCalendarDetail() {
      const key = this.state.selectedDate;
      const entries = this.state.entriesByDate[key] || [];
      const totals = this.calculateTotals(entries);
      this.calendarDayDetail.innerHTML = `
        <div><strong>${key}</strong></div>
        <div>${formatNutrition(totals.calories, totals.carbs, totals.protein, totals.fat)}</div>
        <div>${entries.length} entries</div>
      `;
    }

    entryCardHtml(entry) {
      const n = entry.nutrition || {};
      return `
        <article class="timeline-item">
          <div class="timeline-head">
            <span class="source-badge source-${entry.source}">${entry.source}</span>
            <strong>${entry.label}</strong>
            <small>${new Date(entry.ts).toLocaleTimeString()}</small>
          </div>
          <div class="macro-line">${formatNutrition(n.calories, n.carbs, n.protein, n.fat)}</div>
          <div class="stack-actions">
            <button class="btn-secondary slim" data-action="edit" data-id="${entry.id}">Edit</button>
            <button class="btn-secondary slim" data-action="delete" data-id="${entry.id}">Delete</button>
            <button class="btn-secondary slim" data-action="duplicate" data-id="${entry.id}">Duplicate</button>
          </div>
        </article>
      `;
    }

    async handleEntryAction(btn) {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      const entries = this.entriesForSelectedDate();
      const idx = entries.findIndex((e) => e.id === id);
      if (idx < 0) return;
      const entry = entries[idx];

      if (action === 'edit') return this.openEntryEditor(entry);
      if (action === 'duplicate') {
        const next = deepCopy(this.state.entriesByDate);
        const clone = deepCopy(entry);
        clone.id = `entry-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
        clone.ts = Date.now();
        next[this.state.selectedDate] = [...entries, clone];
        return this.applyStatePatch(next, 'duplicate');
      }
      if (action === 'delete') {
        if (this.state.settings.confirmDelete) {
          const confirmed = await this.showConfirmation(
            'Delete Entry?',
            'Delete this entry? You can undo.'
          );
          if (!confirmed) return;
        }
        const next = deepCopy(this.state.entriesByDate);
        next[this.state.selectedDate].splice(idx, 1);
        return this.applyStatePatch(next, 'delete');
      }
    }

    openEntryEditor(entry) {
      this.entryEditModal.hidden = false;
      const n = entry.nutrition || { calories: 0, carbs: 0, protein: 0, fat: 0 };
      const imageDetails = Array.isArray(entry.details) ? entry.details : [];
      const isImageEntry = entry.source === 'image';

      const imageRowsHtml = imageDetails.length
        ? imageDetails.map((f, idx) => `
          <tr data-edit-food-idx="${idx}">
            <td><input type="text" class="edit-food-name" value="${this.escape(f.food_name || 'unknown food')}" placeholder="food name"></td>
            <td><input type="number" step="0.1" class="edit-food-weight" value="${round(f.weight_g || 0, 1)}"></td>
            <td><input type="number" step="0.1" class="edit-food-calories" value="${round(f.calories || 0, 1)}"></td>
            <td><input type="number" step="0.01" class="edit-food-carbs" value="${round(f.carbs || 0, 2)}"></td>
            <td><input type="number" step="0.01" class="edit-food-protein" value="${round(f.protein || 0, 2)}"></td>
            <td><input type="number" step="0.01" class="edit-food-fat" value="${round(f.fat || 0, 2)}"></td>
            <td><button type="button" class="btn-delete-food" data-delete-edit-food="${idx}">Delete</button></td>
          </tr>
        `).join('')
        : '<tr><td colspan="7" class="muted">No food rows yet.</td></tr>';

      this.entryEditContent.innerHTML = `
        <div class="edit-grid">
          <label>Label<input id="edit-label" type="text" value="${this.escape(entry.label)}"></label>
          <label>Calories<input id="edit-calories" type="number" step="0.1" value="${round(n.calories, 1)}"></label>
          <label>Carbs (g)<input id="edit-carbs" type="number" step="0.1" value="${round(n.carbs, 2)}"></label>
          <label>Protein (g)<input id="edit-protein" type="number" step="0.1" value="${round(n.protein, 2)}"></label>
          <label>Fat (g)<input id="edit-fat" type="number" step="0.1" value="${round(n.fat, 2)}"></label>
        </div>
        ${isImageEntry ? `
          <div class="inline-form-row" style="margin-top:8px;">
            <strong>Image Food Details</strong>
            <button id="add-edit-food-row" type="button" class="btn-secondary slim">Add Food Row</button>
          </div>
          <div class="inline-form-row">
            <strong>Whole Nutrition Zoom</strong>
            <input id="entry-scale-factor" type="number" step="0.05" min="0.1" value="1.10" style="width:110px;">
            <button id="entry-scale-down" type="button" class="btn-secondary slim">Zoom Out</button>
            <button id="entry-scale-up" type="button" class="btn-secondary slim">Zoom In</button>
            <button id="entry-scale-apply" type="button" class="btn-secondary slim">Apply Factor</button>
          </div>
          <table class="food-edit-table">
            <thead>
              <tr>
                <th>Food</th>
                <th>Mass(g)</th>
                <th>Calories</th>
                <th>Carbs</th>
                <th>Protein</th>
                <th>Fat</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="entry-edit-food-body">
              ${imageRowsHtml}
            </tbody>
          </table>
          <div id="entry-edit-food-totals" class="muted"></div>
        ` : ''}
        <div class="modal-actions">
          <button id="cancel-entry-edit" class="btn-secondary">Cancel</button>
          <button id="save-entry-edit" class="btn-primary">Save Changes</button>
        </div>
      `;

      const recalcEditorTotals = () => {
        if (!isImageEntry) return;
        const rows = Array.from(this.entryEditContent.querySelectorAll('#entry-edit-food-body tr[data-edit-food-idx]'));
        let total = { calories: 0, carbs: 0, protein: 0, fat: 0 };
        rows.forEach((row) => {
          total.calories += Number(row.querySelector('.edit-food-calories')?.value || 0);
          total.carbs += Number(row.querySelector('.edit-food-carbs')?.value || 0);
          total.protein += Number(row.querySelector('.edit-food-protein')?.value || 0);
          total.fat += Number(row.querySelector('.edit-food-fat')?.value || 0);
        });
        document.getElementById('edit-calories').value = round(total.calories, 1);
        document.getElementById('edit-carbs').value = round(total.carbs, 2);
        document.getElementById('edit-protein').value = round(total.protein, 2);
        document.getElementById('edit-fat').value = round(total.fat, 2);
        const totalsView = this.entryEditContent.querySelector('#entry-edit-food-totals');
        if (totalsView) {
          totalsView.textContent = `Detail Totals: ${formatNutrition(total.calories, total.carbs, total.protein, total.fat)}`;
        }
      };

      if (isImageEntry) {
        const body = this.entryEditContent.querySelector('#entry-edit-food-body');
        const addBtn = this.entryEditContent.querySelector('#add-edit-food-row');
        const scaleInput = this.entryEditContent.querySelector('#entry-scale-factor');
        const scaleRows = (factor) => {
          const f = Number(factor || 0);
          if (!Number.isFinite(f) || f <= 0) {
            this.showToast('Scale factor must be greater than 0.', 'warning');
            return;
          }
          const rows = Array.from(this.entryEditContent.querySelectorAll('#entry-edit-food-body tr[data-edit-food-idx]'));
          rows.forEach((row) => {
            const map = ['weight', 'calories', 'carbs', 'protein', 'fat'];
            map.forEach((key) => {
              const el = row.querySelector(`.edit-food-${key}`);
              if (!el) return;
              el.value = round(Number(el.value || 0) * f, 2);
            });
          });
          recalcEditorTotals();
        };

        const downBtn = this.entryEditContent.querySelector('#entry-scale-down');
        const upBtn = this.entryEditContent.querySelector('#entry-scale-up');
        const applyBtn = this.entryEditContent.querySelector('#entry-scale-apply');
        if (downBtn) downBtn.addEventListener('click', () => scaleRows(0.9));
        if (upBtn) upBtn.addEventListener('click', () => scaleRows(1.1));
        if (applyBtn) applyBtn.addEventListener('click', () => scaleRows(Number(scaleInput?.value || 1)));

        if (addBtn && body) {
          addBtn.addEventListener('click', () => {
            const nextIdx = body.querySelectorAll('tr[data-edit-food-idx]').length;
            const row = document.createElement('tr');
            row.setAttribute('data-edit-food-idx', String(nextIdx));
            row.innerHTML = `
              <td><input type="text" class="edit-food-name" value="new food" placeholder="food name"></td>
              <td><input type="number" step="0.1" class="edit-food-weight" value="0"></td>
              <td><input type="number" step="0.1" class="edit-food-calories" value="0"></td>
              <td><input type="number" step="0.01" class="edit-food-carbs" value="0"></td>
              <td><input type="number" step="0.01" class="edit-food-protein" value="0"></td>
              <td><input type="number" step="0.01" class="edit-food-fat" value="0"></td>
              <td><button type="button" class="btn-delete-food" data-delete-edit-food="${nextIdx}">Delete</button></td>
            `;
            const empty = body.querySelector('td[colspan="7"]');
            if (empty) empty.parentElement.remove();
            body.appendChild(row);
            recalcEditorTotals();
          });

          body.addEventListener('click', (ev) => {
            const btn = ev.target.closest('[data-delete-edit-food]');
            if (!btn) return;
            const row = btn.closest('tr[data-edit-food-idx]');
            if (row) row.remove();
            if (!body.querySelector('tr[data-edit-food-idx]')) {
              body.innerHTML = '<tr><td colspan="7" class="muted">No food rows yet.</td></tr>';
            }
            recalcEditorTotals();
          });

          body.addEventListener('input', (ev) => {
            if (ev.target && ev.target.className && String(ev.target.className).startsWith('edit-food-')) {
              recalcEditorTotals();
            }
          });
        }
        recalcEditorTotals();
      }

      document.getElementById('cancel-entry-edit').addEventListener('click', () => this.closeEntryEditor());
      document.getElementById('save-entry-edit').addEventListener('click', () => {
        const next = deepCopy(this.state.entriesByDate);
        const entries = next[this.state.selectedDate] || [];
        const idx = entries.findIndex((e) => e.id === entry.id);
        if (idx < 0) return;
        entries[idx].label = document.getElementById('edit-label').value.trim() || entries[idx].label;
        entries[idx].nutrition = {
          calories: Number(document.getElementById('edit-calories').value || 0),
          carbs: Number(document.getElementById('edit-carbs').value || 0),
          protein: Number(document.getElementById('edit-protein').value || 0),
          fat: Number(document.getElementById('edit-fat').value || 0),
        };

        if (isImageEntry) {
          const detailRows = Array.from(this.entryEditContent.querySelectorAll('#entry-edit-food-body tr[data-edit-food-idx]'));
          entries[idx].details = detailRows.map((row, rowIdx) => ({
            region_id: rowIdx + 1,
            food_name: String(row.querySelector('.edit-food-name')?.value || '').trim() || 'unknown food',
            weight_g: Number(row.querySelector('.edit-food-weight')?.value || 0),
            calories: Number(row.querySelector('.edit-food-calories')?.value || 0),
            carbs: Number(row.querySelector('.edit-food-carbs')?.value || 0),
            protein: Number(row.querySelector('.edit-food-protein')?.value || 0),
            fat: Number(row.querySelector('.edit-food-fat')?.value || 0),
          }));
        }

        this.closeEntryEditor();
        this.applyStatePatch(next, 'edit');
      });
    }

    closeEntryEditor() {
      this.entryEditModal.hidden = true;
      this.entryEditContent.innerHTML = '';
    }

    undo() {
      const a = this.state.historyUndo.pop();
      if (!a) return;
      this.state.historyRedo.push(a);
      this.state.entriesByDate = deepCopy(a.prevState);
      this.renderAll();
      if (this.state.settings.autosave) this.saveDayToBackend();
    }

    redo() {
      const a = this.state.historyRedo.pop();
      if (!a) return;
      this.state.historyUndo.push(a);
      this.state.entriesByDate = deepCopy(a.nextState);
      this.renderAll();
      if (this.state.settings.autosave) this.saveDayToBackend();
    }

    async clearDay() {
      const confirmed = await this.showConfirmation(
        'Clear Day?',
        'Clear all entries for selected day? Undo is available.'
      );
      if (!confirmed) return;
      const next = deepCopy(this.state.entriesByDate);
      next[this.state.selectedDate] = [];
      this.applyStatePatch(next, 'clear-day');
    }

    async handleFoodSearch(e) {
      e.preventDefault();
      const text = this.foodSearchInput.value.trim();
      if (!text) return;
      this.foodSearchPreview.textContent = 'Searching...';

      // Backend endpoint integration: /api/search-food
      const r = await fetch(API.searchFood, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ food_input: text }),
      });
      const data = await r.json();
      if (!r.ok) {
        this.foodSearchPreview.innerHTML = `<div class="error">${this.escape(data.error || 'Search failed')}</div>`;
        return;
      }

      this.state.pendingFoodPreview = {
        source: 'text',
        label: data.original_input || text,
        nutrition: data.nutrition || { calories: 0, carbs: 0, protein: 0, fat: 0 },
        details: data.individual_foods || [],
      };
      this.foodSearchPreview.innerHTML = `
        <div class="preview-card">
          <div><strong>Preview</strong> ${this.escape(text)}</div>
          <div>${formatNutrition(this.state.pendingFoodPreview.nutrition.calories, this.state.pendingFoodPreview.nutrition.carbs, this.state.pendingFoodPreview.nutrition.protein, this.state.pendingFoodPreview.nutrition.fat)}</div>
          <div class="preview-actions">
            <button id="confirm-food-add" class="btn-primary slim">Confirm Add</button>
            <button id="edit-food-add" class="btn-secondary slim">Edit</button>
          </div>
        </div>
      `;
      document.getElementById('confirm-food-add').addEventListener('click', () => this.commitPendingFood());
      document.getElementById('edit-food-add').addEventListener('click', () => this.openFoodPreviewEditor());
    }

    openFoodPreviewEditor() {
      if (!this.state.pendingFoodPreview) return;
      
      const p = this.state.pendingFoodPreview;
      this.previewFoodLabel.textContent = `Editing: ${p.label}`;
      this.previewCalories.value = round(p.nutrition.calories, 2);
      this.previewCarbs.value = round(p.nutrition.carbs, 2);
      this.previewProtein.value = round(p.nutrition.protein, 2);
      this.previewFat.value = round(p.nutrition.fat, 2);
      
      this.foodPreviewEditModal.removeAttribute('hidden');
    }

    closeFoodPreviewEditor() {
      this.foodPreviewEditModal.setAttribute('hidden', '');
    }

    commitPendingFoodWithEdits() {
      if (!this.state.pendingFoodPreview) return;
      
      // Update nutrition values from the edit modal
      this.state.pendingFoodPreview.nutrition = {
        calories: this.toNumber(this.previewCalories.value, 0),
        carbs: this.toNumber(this.previewCarbs.value, 0),
        protein: this.toNumber(this.previewProtein.value, 0),
        fat: this.toNumber(this.previewFat.value, 0),
      };
      
      this.closeFoodPreviewEditor();
      this.commitPendingFood();
    }

    commitPendingFood() {
      if (!this.state.pendingFoodPreview) return;
      const p = this.state.pendingFoodPreview;
      const next = deepCopy(this.state.entriesByDate);
      const arr = next[this.state.selectedDate] || [];
      arr.push({
        id: `entry-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: Date.now(),
        source: 'text',
        label: p.label,
        nutrition: {
          calories: Number(p.nutrition.calories || 0),
          carbs: Number(p.nutrition.carbs || 0),
          protein: Number(p.nutrition.protein || 0),
          fat: Number(p.nutrition.fat || 0),
        },
        details: p.details,
      });
      next[this.state.selectedDate] = arr;
      this.foodSearchInput.value = '';
      this.foodSearchPreview.innerHTML = '';
      this.state.pendingFoodPreview = null;
      this.applyStatePatch(next, 'add-food');
    }

    previewMacroDelta() {
      const vals = Object.fromEntries(Object.entries(this.macroFields).map(([k, el]) => [k, Number(el.value || 0)]));
      this.macroPreview.innerHTML = `<div class="preview-card">Delta preview: Calories ${round(vals.calories, 1)}  Carbs ${round(vals.carbs, 2)}  Protein ${round(vals.protein, 2)}  Fat ${round(vals.fat, 2)}</div>`;
    }

    async handleMacroSubmit(e) {
      e.preventDefault();
      const pairs = [
        ['carbs', this.macroFields.carbs.value],
        ['protein', this.macroFields.protein.value],
        ['fat', this.macroFields.fat.value],
        ['calories', this.macroFields.calories.value],
      ].filter(([, v]) => String(v || '').trim() !== '' && Number(v) !== 0);

      if (!pairs.length) {
        this.macroPreview.innerHTML = '<div class="error">Enter at least one non-zero value.</div>';
        return;
      }

      const summed = { carbs: 0, protein: 0, fat: 0, calories: 0 };
      for (const [name, value] of pairs) {
        let query = '';
        if (name === 'calories') query = `${value} kcal`;
        else query = `${value} ${name}`;

        // Backend endpoint integration: /api/search-food supports direct macro syntax
        const r = await fetch(API.searchFood, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ food_input: query }),
        });
        const data = await r.json();
        if (!r.ok) {
          this.macroPreview.innerHTML = `<div class="error">${this.escape(data.error || `Failed on ${name}`)}</div>`;
          return;
        }
        summed.calories += Number(data.nutrition?.calories || 0);
        summed.carbs += Number(data.nutrition?.carbs || 0);
        summed.protein += Number(data.nutrition?.protein || 0);
        summed.fat += Number(data.nutrition?.fat || 0);
      }

      const next = deepCopy(this.state.entriesByDate);
      const arr = next[this.state.selectedDate] || [];
      arr.push({
        id: `entry-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: Date.now(),
        source: 'macros',
        label: 'Direct macro adjustment',
        nutrition: summed,
      });
      next[this.state.selectedDate] = arr;
      Object.values(this.macroFields).forEach((el) => { el.value = ''; });
      this.macroPreview.innerHTML = '';
      this.applyStatePatch(next, 'add-macro');
    }

    async handleImageAnalyze(e) {
      e.preventDefault();
      if (!this.mealImage.files[0]) {
        this.imageAnalysisResult.innerHTML = '<div class="error">Select an image first.</div>';
        return;
      }

      const fd = new FormData();
      fd.append('image', this.mealImage.files[0]);
      if (this.imageHints.value.trim()) fd.append('food_hints', this.imageHints.value.trim());
      if (this.imageContainerSize.value.trim()) fd.append('plate_diameter_cm', this.imageContainerSize.value.trim());

      this.imageAnalysisResult.textContent = 'Analyzing image...';

      // Backend endpoint integration: /api/analyze-image-nutrition
      const r = await fetch(API.analyzeImage, { method: 'POST', body: fd });
      const data = await r.json();
      if (!r.ok) {
        this.imageAnalysisResult.innerHTML = `<div class="error">${this.escape(data.error || 'Image analyze failed')}</div>`;
        return;
      }

      this.state.pendingImagePreview = {
        imageOrigin: data.image_origin,
        analysisSource: data.analysis_source,
        foodItems: (data.food_items || []).map((f, i) => ({ ...f, region_id: f.region_id || i + 1 })),
      };
      this.renderImagePreviewEditor();
    }

    renderUploadedImagePreview() {
      if (!this.imageUploadPreview || !this.imageUploadPreviewImg) return;
      const file = this.mealImage?.files?.[0];
      if (!file) {
        this.imageUploadPreview.hidden = true;
        this.imageUploadPreviewImg.removeAttribute('src');
        return;
      }
      this.imageUploadPreviewImg.src = URL.createObjectURL(file);
      this.imageUploadPreview.hidden = false;
    }

    renderImagePreviewEditor() {
      const p = this.state.pendingImagePreview;
      if (!p) return;

      const itemsHtml = p.foodItems.length
        ? p.foodItems.map((f, idx) => `
            <tr data-food-idx="${idx}">
              <td class="food-name-col"><input type="text" class="input-food-name" data-food-idx="${idx}" value="${this.escape(f.food_name || 'unknown food')}" placeholder="food name"></td>
              <td><input type="number" step="0.1" class="input-weight" data-food-idx="${idx}" value="${f.weight_g || 0}" placeholder="weight(g)"></td>
              <td><input type="number" step="0.1" class="input-calories" data-food-idx="${idx}" value="${f.calories || 0}" placeholder="kcal"></td>
              <td><input type="number" step="0.01" class="input-carbs" data-food-idx="${idx}" value="${f.carbs || 0}" placeholder="C"></td>
              <td><input type="number" step="0.01" class="input-protein" data-food-idx="${idx}" value="${f.protein || 0}" placeholder="P"></td>
              <td><input type="number" step="0.01" class="input-fat" data-food-idx="${idx}" value="${f.fat || 0}" placeholder="F"></td>
              <td><button type="button" class="btn-delete-food" data-food-idx="${idx}">Delete</button></td>
            </tr>
          `).join('')
        : '<tr><td colspan="7" class="muted">No food items detected.</td></tr>';

      this.imageAnalysisResult.innerHTML = `
        <div class="preview-card">
          <div><strong>Image Analysis Result</strong>  ${this.escape(p.analysisSource || 'unknown')}</div>
          <div class="inline-form-row">
            <strong>Whole Nutrition Zoom</strong>
            <input id="image-scale-factor" type="number" step="0.05" min="0.1" value="1.10" style="width:110px;">
            <button id="image-scale-down" class="btn-secondary slim" type="button">Zoom Out</button>
            <button id="image-scale-up" class="btn-secondary slim" type="button">Zoom In</button>
            <button id="image-scale-apply" class="btn-secondary slim" type="button">Apply Factor</button>
          </div>
          <table class="food-edit-table">
            <thead>
              <tr>
                <th>Food</th>
                <th>Mass(g)</th>
                <th>Calories</th>
                <th>Carbs</th>
                <th>Protein</th>
                <th>Fat</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              ${itemsHtml}
            </tbody>
          </table>
          <div class="inline-form-row">
            <button id="refresh-image-totals" class="btn-secondary slim">Refresh Totals</button>
            <button id="confirm-image-add" class="btn-primary slim">Confirm Add to Timeline</button>
          </div>
          <div id="image-total-preview"></div>
        </div>
      `;

      // Event listeners for input changes
      this.imageAnalysisResult.querySelectorAll('[class^="input-"]').forEach(input => {
        input.addEventListener('change', () => this.updateImageFoodItem(input));
      });

      // Event listeners for delete buttons
      this.imageAnalysisResult.querySelectorAll('.btn-delete-food').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          this.deleteImageFoodItem(parseInt(btn.dataset.foodIdx));
        });
      });

      // Refresh totals button
      document.getElementById('refresh-image-totals').addEventListener('click', (e) => {
        e.preventDefault();
        this.recalculateImageTotals();
      });

      // Whole scaling controls
      const scaleInput = this.imageAnalysisResult.querySelector('#image-scale-factor');
      const runScale = (factor) => this.applyImageScale(factor);
      document.getElementById('image-scale-down').addEventListener('click', () => runScale(0.9));
      document.getElementById('image-scale-up').addEventListener('click', () => runScale(1.1));
      document.getElementById('image-scale-apply').addEventListener('click', () => {
        const factor = Number(scaleInput?.value || 1);
        runScale(factor);
      });

      // Confirm add button
      document.getElementById('confirm-image-add').addEventListener('click', () => this.commitImagePreview());
      this.recalculateImageTotals();
    }

    applyImageScale(factor) {
      const p = this.state.pendingImagePreview;
      if (!p) return;
      const f = Number(factor || 0);
      if (!Number.isFinite(f) || f <= 0) {
        this.showToast('Scale factor must be greater than 0.', 'warning');
        return;
      }

      const rows = Array.from(this.imageAnalysisResult.querySelectorAll('tr[data-food-idx]'));
      rows.forEach((row, idx) => {
        const weightEl = row.querySelector('.input-weight');
        const calEl = row.querySelector('.input-calories');
        const carbEl = row.querySelector('.input-carbs');
        const proteinEl = row.querySelector('.input-protein');
        const fatEl = row.querySelector('.input-fat');

        const nextWeight = round(Number(weightEl?.value || p.foodItems[idx]?.weight_g || 0) * f, 2);
        const nextCal = round(Number(calEl?.value || p.foodItems[idx]?.calories || 0) * f, 2);
        const nextCarb = round(Number(carbEl?.value || p.foodItems[idx]?.carbs || 0) * f, 2);
        const nextProtein = round(Number(proteinEl?.value || p.foodItems[idx]?.protein || 0) * f, 2);
        const nextFat = round(Number(fatEl?.value || p.foodItems[idx]?.fat || 0) * f, 2);

        if (weightEl) weightEl.value = nextWeight;
        if (calEl) calEl.value = nextCal;
        if (carbEl) carbEl.value = nextCarb;
        if (proteinEl) proteinEl.value = nextProtein;
        if (fatEl) fatEl.value = nextFat;

        if (p.foodItems[idx]) {
          p.foodItems[idx].weight_g = nextWeight;
          p.foodItems[idx].calories = nextCal;
          p.foodItems[idx].carbs = nextCarb;
          p.foodItems[idx].protein = nextProtein;
          p.foodItems[idx].fat = nextFat;
        }
      });

      this.recalculateImageTotals();
      this.showToast(`Applied nutrition zoom x${round(f, 2)} to all foods.`, 'success');
    }

    updateImageFoodItem(input) {
      const p = this.state.pendingImagePreview;
      if (!p) return;
      const idx = parseInt(input.dataset.foodIdx);
      const field = input.classList[0].replace('input-', '');
      const fieldMap = {
        'food-name': 'food_name',
        weight: 'weight_g',
        calories: 'calories',
        carbs: 'carbs',
        protein: 'protein',
        fat: 'fat'
      };
      if (p.foodItems[idx]) {
        if (field === 'food-name') {
          p.foodItems[idx].food_name = String(input.value || '').trim() || p.foodItems[idx].food_name || 'unknown food';
        } else {
          p.foodItems[idx][fieldMap[field]] = parseFloat(input.value) || 0;
        }
      }
      this.recalculateImageTotals();
    }

    deleteImageFoodItem(idx) {
      const p = this.state.pendingImagePreview;
      if (!p) return;
      p.foodItems.splice(idx, 1);
      this.renderImagePreviewEditor();
    }

    recalculateImageTotals() {
      const p = this.state.pendingImagePreview;
      if (!p) return;
      let total = { calories: 0, carbs: 0, protein: 0, fat: 0 };
      
      // Read from form inputs if they exist
      this.imageAnalysisResult.querySelectorAll('tr[data-food-idx]').forEach(row => {
        const idx = parseInt(row.dataset.foodIdx);
        if (p.foodItems[idx]) {
          const weightVal = parseFloat(row.querySelector('.input-weight')?.value || p.foodItems[idx].weight_g || 0);
          const caloriesVal = parseFloat(row.querySelector('.input-calories')?.value || p.foodItems[idx].calories || 0);
          const carbsVal = parseFloat(row.querySelector('.input-carbs')?.value || p.foodItems[idx].carbs || 0);
          const proteinVal = parseFloat(row.querySelector('.input-protein')?.value || p.foodItems[idx].protein || 0);
          const fatVal = parseFloat(row.querySelector('.input-fat')?.value || p.foodItems[idx].fat || 0);
          
          total.calories += caloriesVal;
          total.carbs += carbsVal;
          total.protein += proteinVal;
          total.fat += fatVal;
        }
      });
      
      document.getElementById('image-total-preview').innerHTML = `<strong>Totals:</strong> ${formatNutrition(total.calories, total.carbs, total.protein, total.fat)}`;
      return total;
    }

    async commitImagePreview() {
      const p = this.state.pendingImagePreview;
      if (!p) return;
      
      // Sync form values back to state before committing
      this.imageAnalysisResult.querySelectorAll('tr[data-food-idx]').forEach(row => {
        const idx = parseInt(row.dataset.foodIdx);
        if (p.foodItems[idx]) {
          p.foodItems[idx].food_name = String(row.querySelector('.input-food-name')?.value || p.foodItems[idx].food_name || 'unknown food').trim() || 'unknown food';
          p.foodItems[idx].weight_g = parseFloat(row.querySelector('.input-weight')?.value || p.foodItems[idx].weight_g || 0);
          p.foodItems[idx].calories = parseFloat(row.querySelector('.input-calories')?.value || p.foodItems[idx].calories || 0);
          p.foodItems[idx].carbs = parseFloat(row.querySelector('.input-carbs')?.value || p.foodItems[idx].carbs || 0);
          p.foodItems[idx].protein = parseFloat(row.querySelector('.input-protein')?.value || p.foodItems[idx].protein || 0);
          p.foodItems[idx].fat = parseFloat(row.querySelector('.input-fat')?.value || p.foodItems[idx].fat || 0);
        }
      });

      this.recalculateImageTotals();
      const total = p.foodItems.reduce((a, f) => {
        a.calories += Number(f.calories || 0);
        a.carbs += Number(f.carbs || 0);
        a.protein += Number(f.protein || 0);
        a.fat += Number(f.fat || 0);
        return a;
      }, { calories: 0, carbs: 0, protein: 0, fat: 0 });

      // Backend endpoint integration marker: /api/update-image-food-label
      // We keep advanced per-region relabeling flow available via /upload_image route.

      const next = deepCopy(this.state.entriesByDate);
      const arr = next[this.state.selectedDate] || [];
      arr.push({
        id: `entry-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: Date.now(),
        source: 'image',
        label: `Image meal (${p.foodItems.length} items)`,
        nutrition: total,
        details: deepCopy(p.foodItems),
      });
      next[this.state.selectedDate] = arr;
      this.state.pendingImagePreview = null;
      this.imageAnalysisResult.innerHTML = '';
      this.mealImage.value = '';
      this.renderUploadedImagePreview();
      this.imageHints.value = '';
      this.imageContainerSize.value = '';
      this.applyStatePatch(next, 'add-image');
    }

    async runRecommendation(options = {}) {
      const suppressAlerts = !!options.suppressAlerts;
      const force = !!options.force;
      if (!this.state.activeUserId) {
        if (!suppressAlerts) this.showToast('Create or switch user first.', 'info');
        this.state.recommendationStatus = 'No active user. Please create or switch user first.';
        this.renderRecommendationPanels();
        this.setRecommendationFailed('No active user selected.');
        return;
      }
      const totals = this.calculateTotals();
      const profile = this.state.profile || {};
      const requestKey = this.getRecommendationRequestKey();

      // Skip repeat requests for identical inputs to keep recommendation refresh responsive.
      if (!force && this.state.recommendation && this.state.recommendationRequestKey === requestKey) {
        this.state.recommendationStatus = '';
        this.renderRecommendationPanels();
        this.setRecommendationFinished('Recommendations are up to date.');
        return;
      }

      if (this.state.recommendationInFlight && !force) {
        return;
      }

      this.state.recommendationStatus = 'Calculating latest recommendation...';
      this.setRecommendationLoading(true, 'Calculating latest recommendation...');
      this.renderRecommendationPanels();

      const btn = this.runRecommendationBtn;
      const hasBtn = !!btn;
      const originalLabel = hasBtn ? btn.textContent : '';
      if (hasBtn) {
        btn.disabled = true;
        btn.textContent = 'Calculating...';
      }

      try {
        this.state.recommendationInFlight = requestKey;
        // Backend endpoint integration: /api/calculate-recommendation
        const r = await fetch(API.calculateRecommendation, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: this.state.activeUserId,
            user_info: profile,
            daily_nutrition: totals,
          }),
        });

        const contentType = String(r.headers.get('content-type') || '');
        let data = null;
        if (contentType.includes('application/json')) {
          data = await r.json();
        } else {
          const text = await r.text();
          try {
            data = JSON.parse(text);
          } catch (_) {
            data = { error: text || 'Unexpected server response.' };
          }
        }

        if (!r.ok) {
          const msg = data?.error || 'Recommendation failed';
          this.state.recommendationStatus = msg;
          this.renderRecommendationPanels();
          this.setRecommendationFailed(msg);
          if (!suppressAlerts) this.showToast(msg, 'error');
          return;
        }

        if (!data || !data.recommendation) {
          const msg = 'Recommendation response was empty. Please try again.';
          this.state.recommendationStatus = msg;
          this.renderRecommendationPanels();
          this.setRecommendationFailed(msg);
          if (!suppressAlerts) this.showToast(msg, 'error');
          return;
        }

        this.state.recommendation = data.recommendation || null;
        this.state.recommendationRequestKey = requestKey;
        this.state.recommendationStatus = '';
        this.renderAll();
        this.setRecommendationFinished('Recommendations finished.');
      } catch (err) {
        this.state.recommendationStatus = `Failed to calculate recommendation: ${err?.message || 'Unknown error'}`;
        this.renderRecommendationPanels();
        this.setRecommendationFailed(this.state.recommendationStatus);
        if (!suppressAlerts) {
          this.showToast(`Failed to calculate recommendation: ${err?.message || 'Unknown error'}`, 'error');
        }
      } finally {
        this.state.recommendationInFlight = null;
        if (hasBtn) {
          btn.disabled = false;
          btn.textContent = originalLabel;
        }
      }
    }

    async runCustomRecipes() {
      if (!this.state.activeUserId) {
        this.showToast('Switch to a user first.', 'info');
        return;
      }
      const text = this.customFoodText.value.trim();
      if (!text) {
        this.showToast('Enter desired foods first.', 'info');
        return;
      }

      if (this.runCustomRecipesBtn.disabled) {
        return;
      }

      const totals = this.calculateTotals();
      const originalLabel = this.runCustomRecipesBtn.textContent;
      this.runCustomRecipesBtn.disabled = true;
      this.runCustomRecipesBtn.textContent = 'Generating...';
      if (this.customRecipesRuntimeStatus) {
        this.customRecipesRuntimeStatus.hidden = false;
        this.customRecipesRuntimeStatus.classList.remove('is-error');
        this.customRecipesRuntimeStatus.classList.add('is-busy');
        this.customRecipesRuntimeStatus.textContent = 'Generating recipes... please wait.';
      }

      try {
        // Backend endpoint integration: /api/calculate-custom-recipes
        const r = await fetch(API.calculateCustomRecipes, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_info: this.state.profile,
            daily_nutrition: totals,
            food_text: text,
          }),
        });
        const data = await r.json();
        if (!r.ok) {
          this.customRecipesView.innerHTML = `<div class="error">${this.escape(data.error || 'Failed')}</div>`;
          if (this.customRecipesRuntimeStatus) {
            this.customRecipesRuntimeStatus.classList.remove('is-busy');
            this.customRecipesRuntimeStatus.classList.add('is-error');
            this.customRecipesRuntimeStatus.textContent = 'Failed to generate recipes. Please try again.';
          }
          return;
        }

        this.state.customRecipes = data;
        this.customRecipesView.innerHTML = (data.recipes || []).map((rec) => `
          <div class="recipe-card">
            <strong>${this.escape(rec.title || 'Recipe')}</strong>
            <div>${(rec.foods || []).map((f) => `${this.escape(f.name)} ${round(f.gram, 2)}g`).join(', ')}</div>
            <small>Supplies: Carbs ${round(rec.supplied?.carbs || 0, 2)}g | Protein ${round(rec.supplied?.protein || 0, 2)}g | Fat ${round(rec.supplied?.fat || 0, 2)}g</small>
          </div>
        `).join('') || '<div class="empty-state">No recipes generated.</div>';

        if (this.customRecipesRuntimeStatus) {
          this.customRecipesRuntimeStatus.classList.remove('is-busy', 'is-error');
          this.customRecipesRuntimeStatus.textContent = 'Recipe generation complete.';
        }
      } catch (err) {
        this.customRecipesView.innerHTML = `<div class="error">${this.escape(err?.message || 'Failed')}</div>`;
        if (this.customRecipesRuntimeStatus) {
          this.customRecipesRuntimeStatus.classList.remove('is-busy');
          this.customRecipesRuntimeStatus.classList.add('is-error');
          this.customRecipesRuntimeStatus.textContent = 'Failed to generate recipes. Please try again.';
        }
      } finally {
        this.runCustomRecipesBtn.disabled = false;
        this.runCustomRecipesBtn.textContent = originalLabel;
      }
    }

    async downloadStlZip(optionIndex) {
      const rec = this.state.recommendation;
      const result = rec?.results?.[optionIndex];
      if (!result) return;
      const files = (result[0] || []).map((f) => f.mesh).filter(Boolean);
      if (!files.length) return;
      const r = await fetch(API.downloadStlZip, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files, folder_name: result[4] || `option_${optionIndex + 1}` }),
      });
      if (!r.ok) {
        this.showToast('ZIP download failed', 'error');
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${result[4] || `option_${optionIndex + 1}`}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }

    downloadObj(optionIndex) {
      const rec = this.state.recommendation;
      const result = rec?.results?.[optionIndex];
      const objName = result?.[5];
      if (!objName) {
        this.showToast('OBJ file is not available for this option.', 'info');
        return;
      }
      window.open(API.downloadObj(objName), '_blank', 'noopener');
    }

    openStlList(optionIndex) {
      const rec = this.state.recommendation;
      const result = rec?.results?.[optionIndex];
      if (!result) return;
      const files = (result[0] || []).map((f) => f.mesh).filter(Boolean);
      if (!files.length) return;
      const links = files.map((f) => `<a href="${API.downloadStl(f)}" target="_blank" rel="noopener">${this.escape(f)}</a>`).join('<br>');
      this.printingModelsView.insertAdjacentHTML('beforeend', `<div class="model-links">${links}</div>`);
    }

    async loadUsers() {
      const r = await fetch(API.users);
      if (!r.ok) return;
      const data = await r.json();
      this.state.users = data.records || [];
      await this.syncActiveUserFromBackend();
    }

    async fetchUserRecord(userId) {
      if (!userId) return null;
      const r = await fetch(API.userDetail(userId));
      if (!r.ok) return null;
      const data = await r.json();
      return data.record || null;
    }

    async syncActiveUserFromBackend() {
      const activeUserId = this.state.activeUserId;
      const token = ++this.state.activeUserSyncToken;

      if (!activeUserId) {
        this.state.activeUser = null;
        this.state.profile = {};
        return;
      }

      const record = await this.fetchUserRecord(activeUserId);
      if (token !== this.state.activeUserSyncToken || this.state.activeUserId !== activeUserId) {
        return;
      }

      if (record && record.user_id === activeUserId) {
        this.state.activeUser = record;
        this.state.profile = { ...(record.user_info || {}) };
      } else {
        this.state.activeUser = this.state.users.find((u) => u.user_id === activeUserId) || null;
        this.state.profile = { ...(this.state.activeUser?.user_info || {}) };
        if (!this.state.activeUser) {
          this.state.activeUserId = '';
          localStorage.removeItem(LS_KEYS.activeUserId);
        }
      }

      await this.loadDayFromBackend(this.state.selectedDate);
    }

    setActiveUser(userRecord) {
      const nextUserId = userRecord?.user_id || '';
      this.state.activeUser = userRecord;
      this.state.activeUserId = nextUserId;
      this.state.profile = { ...(userRecord?.user_info || {}) };
      this.state.customRecipeCollapsed = this.hasPreferredFoods();
      localStorage.setItem(LS_KEYS.activeUserId, this.state.activeUserId);
      // Store timestamp of last used account
      if (nextUserId) {
        localStorage.setItem(`lastUserTime.${nextUserId}`, Date.now().toString());
      }
      this.state.recommendation = null;
      this.state.customRecipes = null;
      const token = ++this.state.activeUserSyncToken;
      this.syncActiveUserFromBackend().then(() => {
        if (token !== this.state.activeUserSyncToken || this.state.activeUserId !== nextUserId) return;
        this.renderAll();
        this.loadDailyHistoryForMonth();
      });
    }

    async createUser() {
      const name = this.createUserName.value.trim();
      if (!name) return;

      // Backend endpoint integration: /api/user-records
      const r = await fetch(API.users, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const data = await r.json();
      if (!r.ok) {
        this.showToast(data.error || 'Create user failed', 'error');
        return;
      }
      this.state.users.push(data.user);
      this.createUserName.value = '';
      this.setActiveUser(data.user);
      this.showToast('User created successfully', 'success');
      this.renderAll();
    }

    async handleUserAction(action, userId) {
      const user = this.state.users.find((u) => u.user_id === userId);
      if (!user) return;
      if (action === 'switch') {
        this.setActiveUser(user);
        this.renderAll();
        return;
      }
      if (action === 'delete') {
        const confirmed = await this.showConfirmation(
          'Delete User?',
          'Delete this user and all their data?'
        );
        if (!confirmed) return;

        // Backend endpoint integration: /api/user-records/<user_id>
        const r = await fetch(API.userDetail(userId), { method: 'DELETE' });
        if (!r.ok) {
          this.showToast('Delete failed', 'error');
          return;
        }
        this.state.users = this.state.users.filter((u) => u.user_id !== userId);
        if (this.state.activeUserId === userId) {
          this.state.activeUserId = '';
          this.state.activeUser = null;
          this.state.profile = {};
          this.state.entriesByDate = {};
          localStorage.removeItem(LS_KEYS.activeUserId);
        }
        this.showToast('User deleted successfully', 'success');
        this.renderAll();
      }
    }

    async saveProfile(userId, formEl) {
      const targetUserId = userId || this.state.activeUserId;
      if (!targetUserId) {
        this.showToast('Select user first.', 'info');
        return;
      }
      if (!formEl) {
        this.showToast('Profile form not found.', 'error');
        return;
      }
      const formData = new FormData(formEl);
      const nextProfile = Object.fromEntries(formData.entries());
      nextProfile.name = (nextProfile.name || '').trim();
      if (!nextProfile.name) {
        this.showToast('Username is required.', 'info');
        return;
      }
      if (this.state.activeUserId === targetUserId) {
        this.state.profile = nextProfile;
      }

      // Backend endpoint integration: /api/user-records
      const r = await fetch(API.users, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: targetUserId, user_info: nextProfile }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        this.showToast(d.error || 'Profile save failed', 'error');
        return;
      }
      const refreshed = await this.fetchUserRecord(targetUserId);
      if (refreshed && refreshed.user_id === targetUserId) {
        const u = this.state.users.find((x) => x.user_id === targetUserId);
        if (u) u.user_info = { ...(refreshed.user_info || {}) };
        if (this.state.activeUserId === targetUserId) {
          this.state.activeUser = refreshed;
          this.state.profile = { ...(refreshed.user_info || {}) };
          this.state.customRecipeCollapsed = this.hasPreferredFoods();
        }
      }
      this.showToast('Profile saved successfully', 'success');
      this.renderAll();
    }

    async saveDayToBackend() {
      if (!this.state.activeUserId) return;
      const totals = this.calculateTotals();
      const recSnapshot = this.state.recommendation ? {
        calories: this.state.recommendation.calories,
        carbs: this.state.recommendation.carbohydrate_intake,
        protein: this.state.recommendation.protein_intake,
        fat: this.state.recommendation.fat_intake,
      } : {};

      // Backend endpoint integration: /api/daily-intake/<user_id>
      const r = await fetch(API.dailyIntake(this.state.activeUserId), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ daily_nutrition: totals, recommended: recSnapshot }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        console.error('Save daily failed', d.error || r.statusText);
      }
    }

    async loadDayFromBackend(day) {
      if (!this.state.activeUserId) return;
      // Backend endpoint integration: /api/daily-intake/<user_id>
      const r = await fetch(API.dailyIntake(this.state.activeUserId));
      if (!r.ok) return;
      const data = await r.json();
      const history = data.history || [];
      const entry = history.find((h) => h.date === day);
      if (!this.state.entriesByDate[day]) this.state.entriesByDate[day] = [];
      if (entry && (!this.state.entriesByDate[day] || this.state.entriesByDate[day].length === 0)) {
        this.state.entriesByDate[day] = [{
          id: `entry-import-${Date.now()}`,
          ts: Date.now(),
          source: 'text',
          label: 'Imported daily total',
          nutrition: {
            calories: Number(entry.nutrition?.calories || 0),
            carbs: Number(entry.nutrition?.carbs || 0),
            protein: Number(entry.nutrition?.protein || 0),
            fat: Number(entry.nutrition?.fat || 0),
          },
          recommendationSnapshot: entry.recommended || null,
        }];
      }
      this.renderAll();
    }

    async loadDailyHistoryForMonth() {
      if (!this.state.activeUserId) return;
      const r = await fetch(API.dailyIntake(this.state.activeUserId));
      if (!r.ok) return;
      const data = await r.json();
      const history = data.history || [];
      history.forEach((h) => {
        if (!this.state.entriesByDate[h.date] || this.state.entriesByDate[h.date].length === 0) {
          this.state.entriesByDate[h.date] = [{
            id: `entry-h-${h.date}`,
            ts: Date.now(),
            source: 'text',
            label: 'Saved daily summary',
            nutrition: {
              calories: Number(h.nutrition?.calories || 0),
              carbs: Number(h.nutrition?.carbs || 0),
              protein: Number(h.nutrition?.protein || 0),
              fat: Number(h.nutrition?.fat || 0),
            },
            recommendationSnapshot: h.recommended || null,
          }];
        }
      });
      this.renderAll();
    }

    shiftCalendar(deltaMonth) {
      this.state.calendarMonth = new Date(this.state.calendarMonth.getFullYear(), this.state.calendarMonth.getMonth() + deltaMonth, 1);
      this.renderCalendar();
    }

    selectDate(key) {
      this.state.selectedDate = key;
      this.loadDayFromBackend(key);
      this.renderAll();
    }

    openPage(pageKey) {
      this.navItems.forEach((n) => n.classList.toggle('active', n.dataset.page === pageKey));
      this.pages.forEach((p) => p.classList.toggle('active', p.id === `page-${pageKey}`));
      if (pageKey === 'recommendations') {
        if (!this.state.recommendation) {
          this.runRecommendation({ suppressAlerts: true });
        } else if (this.isRecommendationStale()) {
          this.setRecommendationFinished('Recommendations finished.');
          this.renderRecommendationRefreshHint();
        } else {
          this.setRecommendationFinished('Recommendations finished.');
          this.renderRecommendationRefreshHint();
        }
      } else if (this.recommendationLoadingIndicator) {
        this.recommendationLoadingIndicator.hidden = true;
      }
    }

    openTab(tabKey) {
      this.tabs.forEach((t) => t.classList.toggle('active', t.dataset.logTab === tabKey));
      this.tabPanels.forEach((p) => p.classList.toggle('active', p.id === `tab-${tabKey}`));
    }

    updateSettings() {
      this.state.settings.autosave = this.autosaveToggle.checked;
      this.state.settings.confirmDelete = this.confirmDeleteToggle.checked;
      localStorage.setItem(LS_KEYS.settings, JSON.stringify(this.state.settings));
    }

    applySidebarState() {
      const isCollapsed = !!this.state.settings.sidebarCollapsed;
      const isInsightCollapsed = !!this.state.settings.insightCollapsed;
      document.body.classList.toggle('sidebar-collapsed', isCollapsed);
      document.body.classList.toggle('insight-collapsed', isInsightCollapsed);
      if (this.toggleLeftNavBtn) {
        this.toggleLeftNavBtn.classList.toggle('is-collapsed', isCollapsed);
        this.toggleLeftNavBtn.title = isCollapsed ? 'Show menu' : 'Hide menu';
        this.toggleLeftNavBtn.setAttribute('aria-label', isCollapsed ? 'Show side menu' : 'Hide side menu');
      }
      if (this.toggleRightPanelBtn) {
        this.toggleRightPanelBtn.classList.toggle('is-collapsed', isInsightCollapsed);
        this.toggleRightPanelBtn.title = isInsightCollapsed ? 'Show insights' : 'Hide insights';
        this.toggleRightPanelBtn.setAttribute('aria-label', isInsightCollapsed ? 'Show insights panel' : 'Hide insights panel');
      }
    }

    toggleSidebar() {
      this.state.settings.sidebarCollapsed = !this.state.settings.sidebarCollapsed;
      this.applySidebarState();
      localStorage.setItem(LS_KEYS.settings, JSON.stringify(this.state.settings));
    }

    toggleInsightPanel() {
      this.state.settings.insightCollapsed = !this.state.settings.insightCollapsed;
      this.applySidebarState();
      localStorage.setItem(LS_KEYS.settings, JSON.stringify(this.state.settings));
    }

    escape(v) {
      return String(v || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }
  }

  window.addEventListener('DOMContentLoaded', () => {
    window.appUI = new NutritionUI();
  });
})();
