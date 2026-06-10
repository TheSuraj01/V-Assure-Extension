(() => {
  if (window.__veevaRecorder) return;
  window.__veevaRecorder = true;

  const Config = {
    SKIP_TAGS: new Set(['html', 'body', 'head', 'script', 'style', 'meta', 'svg', 'path', 'circle', 'rect', 'g', 'defs', 'use', 'polygon', 'polyline', 'ellipse', 'line']),
    NOISE_PATTERNS: [
      /^delegate access/i,
      /^\d+$/,
      /^(true|false|null|undefined)$/i,
      /^[\s\W]+$/,
      // Session-timeout / notification banners: long sentences ending with punctuation
      /^.{55,}[.!?]$/,
      // Explicit Veeva session-timeout phrases
      /logged off vault/i,
      /will be logged off/i,
      /click here if you wish to continue/i,
    ],
    MIN_LABEL_LENGTH: 2,
    MAX_LABEL_LENGTH: 80,
    VEEVA_SELECTORS: ['label', '[class*="field-label"]', '[class*="form-label"]', '[class*="input-label"]', '[class*="vv-label"]', '[class*="label-text"]', 'legend', '[data-label]'],
    TYPE_NAMES: { button: 'button', link: 'link', tab: 'tab', 'menu-item': 'menu item', 'dropdown-option': 'dropdown option', checkbox: 'checkbox', radio: 'radio button', select: 'dropdown', input: 'input field', textarea: 'text area', toggle: 'toggle', 'file-upload': 'file upload', image: 'image', label: 'label' }
  };

  class DOMUtils {
    static cleanText(text) {
      return text ? text.replace(/\s+/g, ' ').replace(/[*✱†‡]/g, '').replace(/\u00a0/g, ' ').trim() : '';
    }

    static isNoisy(text) {
      if (!text || text.length < Config.MIN_LABEL_LENGTH || text.length > Config.MAX_LABEL_LENGTH) return true;
      return Config.NOISE_PATTERNS.some(p => p.test(text));
    }

    static classifyElement(el) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      const role = (el.getAttribute('role') || '').toLowerCase();
      const cls = typeof el.className === 'string' ? el.className : '';
      const title = (el.getAttribute('title') || '').toLowerCase();
      const aria = (el.getAttribute('aria-label') || '').toLowerCase();

      if (aria.includes('tab collection') || title.includes('tab collection') || aria.includes('user profile') || title.includes('user profile')) return 'button';
      if (cls.includes('tabcollection-menu-button') || cls.includes('tabcollection-menu')) return 'button';
      // Waffle icon (data-icon="waffle") is always the Tab Collections trigger
      if (el.getAttribute('data-icon') === 'waffle' || el.querySelector('[data-icon="waffle"]')) return 'button';
      if (el.querySelector('[aria-label*="Tab Collection" i], [title*="Tab Collection" i], [aria-label*="User Profile" i], [title*="User Profile" i]')) return 'button';

      if (tag === 'button' || role === 'button' || (tag === 'input' && ['submit', 'button', 'reset'].includes(type))) return 'button';
      if (tag === 'input') {
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'file') return 'file-upload';
        return 'input';
      }
      if (['select', 'textarea', 'a', 'label', 'img'].includes(tag)) return tag === 'a' ? 'link' : tag === 'img' ? 'image' : tag;
      if (['tab', 'menuitem', 'menu-item', 'option', 'checkbox', 'switch'].includes(role)) return role === 'switch' ? 'toggle' : role === 'menuitem' ? 'menu-item' : role === 'option' ? 'dropdown-option' : role;
      if (role === 'listbox' || role === 'combobox') return 'select';

      if (['span', 'div', 'li'].includes(tag)) {
        if (el.onclick || el.getAttribute('onclick') || el.getAttribute('ng-click') || el.getAttribute('(click)') || el.getAttribute('tabindex') || cls.match(/\b(btn|button|action|clickable|tab|option|item|link|trigger|toggle|menu)\b/i)) return 'button';
        const pCls = typeof el.parentElement?.className === 'string' ? el.parentElement.className : '';
        if (pCls.match(/\b(nav|menu|toolbar|tabs|actions|controls)\b/i)) return 'button';
      }
      return null;
    }

    static resolveLabel(el) {
      const title = el.getAttribute('title') || '';
      const aria = el.getAttribute('aria-label') || '';
      if (title.toLowerCase().includes('tab collection') || aria.toLowerCase().includes('tab collection')) return 'Tab Collection';
      if (title.toLowerCase().includes('user profile') || aria.toLowerCase().includes('user profile')) return 'User Profile';

      const child = el.querySelector('[aria-label*="Tab Collection" i], [title*="Tab Collection" i], [aria-label*="User Profile" i], [title*="User Profile" i]');
      if (child) {
        const cTitle = (child.getAttribute('title') || '').toLowerCase();
        const cAria = (child.getAttribute('aria-label') || '').toLowerCase();
        if (cTitle.includes('tab collection') || cAria.includes('tab collection')) return 'Tab Collections';
        if (cTitle.includes('user profile') || cAria.includes('user profile')) return 'User Profile';
      }

      if (aria && !this.isNoisy(aria)) return aria;

      if (el.id) {
        try {
          const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
          if (lbl) return this.cleanText(lbl.innerText);
        } catch { }
      }

      const ariaLabelledby = el.getAttribute('aria-labelledby');
      if (ariaLabelledby) {
        const parts = ariaLabelledby.split(/\s+/).map(id => {
          const ref = document.getElementById(id);
          return ref ? this.cleanText(ref.innerText) : '';
        }).filter(Boolean);
        if (parts.length) return parts.join(' ');
      }

      if (title && !this.isNoisy(title)) return title;

      if (['input', 'textarea'].includes(el.tagName.toLowerCase())) {
        const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder');
        if (ph && !this.isNoisy(ph)) return ph;
      }

      const parentLbl = el.closest('label');
      if (parentLbl) {
        const txt = this.cleanText(parentLbl.innerText.replace(el.value || '', ''));
        if (txt && !this.isNoisy(txt)) return txt;
      }

      const dataAttr = el.getAttribute('data-label') || el.getAttribute('data-name') || el.getAttribute('data-title');
      if (dataAttr && !this.isNoisy(dataAttr)) return dataAttr;

      const tag = el.tagName.toLowerCase();
      if (['button', 'a', 'li'].includes(tag)) {
        const txt = this.cleanText(el.innerText);
        if (txt && !this.isNoisy(txt) && txt.length <= Config.MAX_LABEL_LENGTH) return txt;
      }

      if (['span', 'div'].includes(tag)) {
        const txt = this.cleanText(el.innerText);
        if (txt && txt.length <= 30 && !this.isNoisy(txt) && !txt.includes('\n')) return txt;
      }

      let node = el.parentElement;
      for (let i = 0; i < 6 && node; i++) {
        for (const sel of Config.VEEVA_SELECTORS) {
          try {
            const found = node.querySelector(sel);
            if (found && found !== el && !found.contains(el) && !el.contains(found)) {
              const txt = this.cleanText(found.innerText);
              if (txt && !this.isNoisy(txt)) return txt;
            }
          } catch { }
        }
        let sib = el.previousElementSibling;
        while (sib) {
          if (!sib.querySelector('input,select,textarea,button')) {
            const txt = this.cleanText(sib.innerText);
            if (txt && !this.isNoisy(txt) && txt.length <= 50) return txt;
          }
          sib = sib.previousElementSibling;
        }
        node = node.parentElement;
      }

      const td = el.closest('td');
      if (td) {
        const idx = Array.from(td.parentElement?.children || []).indexOf(td);
        const th = td.closest('table')?.querySelector(`th:nth-child(${idx + 1})`);
        if (th) return this.cleanText(th.innerText);
      }

      return null;
    }

    static getIdentifiers(el) {
      const ids = {};
      if (el.id) ids.id = el.id;
      if (el.name) ids.name = el.name;
      const aria = el.getAttribute('aria-label');
      if (aria) ids.ariaLabel = aria;

      const veevaAttrs = {};
      for (const attr of el.attributes) {
        if (/^(data-vv|data-vault|data-action|data-object|data-type|data-id|data-test)/.test(attr.name)) veevaAttrs[attr.name] = attr.value;
      }
      if (Object.keys(veevaAttrs).length) ids.veevaData = veevaAttrs;
      return ids;
    }

    static getDropdownContext(el) {
      const tabColContainer = el.closest('[class*="tabcollection"]');
      if (tabColContainer) {
        const triggerBtn =
          tabColContainer.querySelector('[aria-label*="Tab Collection" i]') ||
          tabColContainer.querySelector('[class*="tabcollection-menu-button"]') ||
          document.querySelector('[class*="tabcollection-menu-button"]');
        if (triggerBtn) {
          const aria = triggerBtn.getAttribute('aria-label');
          if (aria && aria.toLowerCase().includes('tab collection')) return aria;
        }
        return 'Tab Collections';
      }

      const container = el.closest('[class*="dropdown"], [role="listbox"], [class*="picker"], [class*="menu-list"], [class*="options"], [class*="picklist"], [class*="vv-menu"], [class*="select2-"], [class*="chzn-drop"], [class*="k-list"]');
      if (!container) return null;

      const aria = (container.getAttribute('aria-label') || '').toLowerCase();
      const title = (container.getAttribute('title') || '').toLowerCase();
      if (aria.includes('tab collection') || title.includes('tab collection')) return 'Tab Collections';
      if (aria.includes('user profile') || title.includes('user profile')) return 'User Profile';

      if (container.id) {
        const trigger = document.querySelector(`[aria-controls="${CSS.escape(container.id)}"]`);
        if (trigger) {
          const t = this.cleanText(trigger.innerText) || trigger.getAttribute('aria-label');
          if (t && !this.isNoisy(t)) return t;
        }
      }
      return this.resolveLabel(container);
    }

    static hasDropdownInput(el) {
      const container = el.closest('[class*="dropdown"], [role="listbox"], [class*="picker"], [class*="menu-list"], [class*="options"], [class*="picklist"], [class*="vv-menu"], [class*="select2-"], [class*="chzn-drop"], [class*="k-list"], [class*="tabcollection"]');
      if (!container) return false;

      // Check inside container
      if (container.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])')) return true;

      // Check for related trigger/input using aria-controls or aria-owns
      if (container.id) {
        if (document.querySelector(`input[aria-controls="${CSS.escape(container.id)}"]`)) return true;
        if (document.querySelector(`input[aria-owns="${CSS.escape(container.id)}"]`)) return true;
      }

      // Check parent wrapper
      const parentWrap = container.closest('[class*="combo"], [class*="select"], [class*="lookup"], [class*="picker"]');
      if (parentWrap && parentWrap.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])')) return true;

      // Sometimes the input is a preceding sibling
      let prev = container.previousElementSibling;
      while (prev) {
        if (prev.tagName && prev.tagName.toLowerCase() === 'input' && !['hidden', 'radio', 'checkbox'].includes(prev.type)) return true;
        if (prev.querySelector && prev.querySelector('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])')) return true;
        prev = prev.previousElementSibling;
      }

      return false;
    }

    static findNearbyLabel(el) {
      if (!el) return null;
      let node = el;
      for (let i = 0; i < 6 && node; i++) {
        let prev = node.previousElementSibling;
        while (prev) {
          if (!prev.querySelector('input,select,textarea,button')) {
            const txt = this.cleanText(prev.innerText || prev.textContent || '');
            if (txt && !this.isNoisy(txt)) return txt;
          }
          prev = prev.previousElementSibling;
        }
        const parent = node.parentElement;
        if (parent && parent.previousElementSibling && !parent.previousElementSibling.querySelector('input,select,textarea,button')) {
          const txt = this.cleanText(parent.previousElementSibling.innerText || parent.previousElementSibling.textContent || '');
          if (txt && !this.isNoisy(txt)) return txt;
        }
        node = node.parentElement;
      }
      return null;
    }

    static isNavbarItem(el) {
      // Detects Veeva's top navigation bar links.
      // These are <a class="vv-navbar-link"> inside <ul class="vv-navbar-nav">.
      // They must NEVER be treated as dropdown selections.
      return !!(
        el.closest('ul.vv-navbar-nav') ||
        el.closest('[class*="vv-navbar-nav"]') ||
        el.closest('[class*="vv-navbar-item"]') ||
        (el.tagName && el.tagName.toLowerCase() === 'a' && el.classList.contains('vv-navbar-link'))
      );
    }

    static isTabCollectionMenuItem(el) {
      // Detects elements belonging to the Tab Collection Menu.
      // An element is a Tab Collection Menu Item when it — or any of its
      // ancestors — carries the attribute data-corgix-internal="MENU-ITEM".
      // This works regardless of element type, extra CSS classes, or nesting.
      return !!(
        el.getAttribute('data-corgix-internal') === 'MENU-ITEM' ||
        el.closest('[data-corgix-internal="MENU-ITEM"]')
      );
    }

    static isInsideVeevaDropdown(el) {
      // Tab Collection Menu items must never be treated as dropdown selections
      if (DOMUtils.isTabCollectionMenuItem(el)) return false;
      // Navbar items are never dropdown selections — guard first
      if (DOMUtils.isNavbarItem(el)) return false;

      const inStandardDropdown = !!el.closest('[role="listbox"], [role="option"], [role="menu"], [role="menuitem"], [class*="dropdown-menu"], [class*="dropdown-list"], [class*="option-list"], [class*="picklist"], [class*="autocomplete"], [class*="vv-menu"], [class*="vv-list"], [class*="select2-"], [class*="chzn-"], [class*="k-list"]');
      if (inStandardDropdown) return true;
      const tabColContainer = el.closest('[class*="tabcollection"]');
      if (tabColContainer) {
        const isTriggerBtn = !!(el.closest('[class*="tabcollection-menu-button"]') || (el.tagName.toLowerCase() === 'button' && (el.getAttribute('aria-label') || '').toLowerCase().includes('tab collection')));
        if (!isTriggerBtn) return true;
      }
      return false;
    }

    static findInteractiveParent(node) {
      if (!node) return null;
      const tabColBtn = node.closest('[class*="tabcollection-menu-button"], [aria-label*="Tab Collection" i]');
      if (tabColBtn) return tabColBtn;
      const clickable = node.closest('button, a, [role="button"], [role="tab"], [role="link"], [role="menuitem"], [role="option"], [class*="btn"], [class*="tab"], [onclick], [data-action], [data-test]');
      if (clickable) return clickable;

      let curr = node;
      for (let i = 0; i < 6 && curr && curr !== document.body; i++) {
        const title = (curr.getAttribute('title') || '').toLowerCase();
        const aria = (curr.getAttribute('aria-label') || '').toLowerCase();
        const cls = typeof curr.className === 'string' ? curr.className : '';

        if (title.includes('tab collection') || aria.includes('tab collection') ||
          title.includes('user profile') || aria.includes('user profile') ||
          cls.includes('tabcollection-menu-button')) {
          return curr;
        }
        const txt = (curr.innerText || curr.textContent || '').toLowerCase();
        if (txt.length < 50 && (txt.includes('tab collection') || txt.includes('user profile'))) {
          return curr;
        }
        curr = curr.parentElement;
      }
      return null;
    }
  }

  class StateManager {
    constructor() {
      this.steps = [];
      this.isRecording = false;
      this.stepCounter = 0;
      this.userStep = null;
    }

    async load() {
      return new Promise(resolve => {
        chrome.storage.local.get(['veevaSteps', 'veevaRecording', 'veevaCounter'], (res) => {
          this.steps = res.veevaSteps || [];
          this.stepCounter = res.veevaCounter || 0;
          this.isRecording = res.veevaRecording || false;
          resolve();
        });
      });
    }

    async save() {
      return new Promise(resolve => chrome.storage.local.set({ veevaSteps: this.steps, veevaRecording: this.isRecording, veevaCounter: this.stepCounter }, resolve));
    }

    async updateBadge() {
      try {
        await chrome.runtime.sendMessage({ type: 'BADGE', count: this.steps.length, recording: this.isRecording });
      } catch { }
    }

    async addStep(step) {
      if (!step) return null;
      const last = this.steps[this.steps.length - 1];
      if (last) {
        const dt = new Date(step.timestamp) - new Date(last.timestamp);
        if (dt < 400 && last.action === step.action && last.element.label === step.element.label) {
          const sameInput = (last.element.inputValue ?? '') === (step.element.inputValue ?? '');
          const sameSelect = (last.element.selectedValue ?? '') === (step.element.selectedValue ?? '');
          if (sameInput && sameSelect) return null;
        }
      }

      step.step = ++this.stepCounter;
      step.userStep = this.userStep;
      this.steps.push(step);

      await this.save();
      await this.updateBadge();
      return step;
    }

    async clear() {
      this.steps = [];
      this.stepCounter = 0;
      await this.save();
      await this.updateBadge();
    }
  }

  class ToastManager {
    constructor() {
      this.el = null;
      this.timer = null;
    }

    show(step) {
      if (!this.el) {
        this.el = document.createElement('div');
        this.el.id = '__veeva-recorder-toast';
        this.el.style.cssText = `position:fixed;bottom:20px;right:20px;z-index:2147483647;background:#0f172a;color:#e2e8f0;font:600 11px/1.4 'SF Mono',monospace;padding:9px 13px;border-radius:8px;border-left:3px solid #22d3ee;box-shadow:0 8px 24px rgba(0,0,0,0.5);pointer-events:none;transition:opacity 0.25s ease;max-width:300px;`;
        document.body.appendChild(this.el);
      }

      const colors = { click: '#22d3ee', enter: '#4ade80', select: '#f59e0b', screenshot: '#c084fc', step_marker: '#f472b6' };
      const color = colors[step.action] || '#94a3b8';
      const label = step.element.label || step.element.type;
      const val = step.element.inputValue || step.element.selectedValue || '';
      const hType = Config.TYPE_NAMES[step.element.type] || step.element.type;

      this.el.style.borderLeftColor = color;
      this.el.innerHTML = `<span style="color:${color};text-transform:uppercase;font-size:9px;letter-spacing:.08em;">#${step.step} ${step.action} · ${hType}</span><br><span style="color:#f1f5f9;">${step.userStep ? `[${step.userStep}] ` : ''}${label}${val ? ` → "${val}"` : ''}</span>`;
      this.el.style.opacity = '1';

      clearTimeout(this.timer);
      this.timer = setTimeout(() => { if (this.el) this.el.style.opacity = '0'; }, 2800);
    }
  }

  class UIController {
    constructor(stateManager) {
      this.state = stateManager;
      this.container = null;
      this.input = null;
      this.isDragging = false;
      this.initialX = 0;
      this.initialY = 0;
      this.xOffset = 0;
      this.yOffset = 0;
    }

    create() {
      if (this.container) return;
      this.container = document.createElement('div');
      this.container.id = '__veeva-recorder-ui';
      this.container.style.cssText = `position:fixed;top:20px;right:20px;z-index:2147483647;background:#080d14;border:1px solid #1e2d3e;border-radius:8px;padding:12px;display:none;flex-direction:column;gap:10px;font-family:'Outfit',sans-serif;color:#e2e8f0;box-shadow:0 8px 24px rgba(0,0,0,0.5);width:180px;cursor:move;user-select:none;`;

      this.container.addEventListener('mousedown', this.dragStart.bind(this));
      document.addEventListener('mouseup', this.dragEnd.bind(this));
      document.addEventListener('mousemove', this.drag.bind(this), { passive: true });

      const wrapper = document.createElement('div');
      wrapper.style.cssText = 'display:flex;flex-direction:column;gap:4px;';

      const label = document.createElement('label');
      label.innerText = 'Current Step:';
      label.style.cssText = 'font-size:11px;color:#6b8094;font-weight:600;';

      this.input = document.createElement('input');
      this.input.type = 'text';
      this.input.id = '__veeva-recorder-input';
      this.input.placeholder = 'e.g., Step 1';
      this.input.setAttribute('autocomplete', 'off');
      this.input.style.cssText = `background:#111e2e;border:1px solid #253545;color:#e2e8f0;padding:6px 8px;border-radius:4px;font-size:12px;outline:none;`;

      ['keydown', 'keyup', 'keypress', 'input'].forEach(evt => this.input.addEventListener(evt, e => e.stopPropagation()));

      this.input.addEventListener('change', async () => {
        const val = this.input.value.trim();
        this.state.userStep = val || null;
        if (val) {
          const step = await this.state.addStep({ timestamp: new Date().toISOString(), action: 'step_marker', element: { type: 'marker', label: val }, page: { url: window.location.href, title: document.title } });
          if (step) toast.show(step);
        }
      });

      wrapper.appendChild(label);
      wrapper.appendChild(this.input);

      const btn = document.createElement('button');
      btn.innerText = '📷 Screenshot';
      btn.style.cssText = `background:#253545;border:1px solid #1e2d3e;color:#e2e8f0;padding:8px 10px;border-radius:4px;font-size:12px;cursor:pointer;font-weight:600;transition:background 0.15s;`;
      btn.onmouseover = () => btn.style.background = '#1e2d3e';
      btn.onmouseout = () => btn.style.background = '#253545';

      btn.addEventListener('click', async (e) => {
        e.stopPropagation(); e.preventDefault();
        const step = await this.state.addStep({ timestamp: new Date().toISOString(), action: 'screenshot', element: { type: 'screenshot', label: 'Take screenshot of the page' }, page: { url: window.location.href, title: document.title } });
        if (step) toast.show(step);
      });

      this.container.appendChild(wrapper);
      this.container.appendChild(btn);
      document.body.appendChild(this.container);
    }

    dragStart(e) {
      if (['INPUT', 'BUTTON'].includes(e.target.tagName)) return;
      this.initialX = e.clientX - this.xOffset;
      this.initialY = e.clientY - this.yOffset;
      this.isDragging = true;
    }

    dragEnd() {
      this.isDragging = false;
    }

    drag(e) {
      if (!this.isDragging) return;
      e.preventDefault();
      this.xOffset = e.clientX - this.initialX;
      this.yOffset = e.clientY - this.initialY;
      requestAnimationFrame(() => {
        if (this.container) this.container.style.transform = `translate3d(${this.xOffset}px, ${this.yOffset}px, 0)`;
      });
    }

    updateVisibility() {
      if (this.container) this.container.style.display = this.state.isRecording ? 'flex' : 'none';
    }
  }

  class EventRecorder {
    constructor(stateManager, toastManager) {
      this.state = stateManager;
      this.toast = toastManager;
      this.inputValues = new WeakMap();
      this.capturedInputs = new WeakSet();
      this.bindEvents();
    }

    isIgnored(el) {
      return !el || el.id === '__veeva-recorder-ui' || el.closest('#__veeva-recorder-ui') || el.id === '__veeva-recorder-toast' || el.id === '__veeva-recorder-input';
    }

    buildStep(el, eventType, typedValue, originalTarget) {
      const type = DOMUtils.classifyElement(el);
      
      // Filter out decorative/meaningless step captures:
      // If type resolves to null AND the original target was an SVG/path/icon element, return null to skip.
      const isSvgOrIcon = originalTarget && (
        ['svg', 'path', 'circle', 'rect', 'g', 'defs', 'use', 'polygon', 'polyline', 'ellipse', 'line'].includes(originalTarget.tagName?.toLowerCase()) ||
        originalTarget.closest('svg') ||
        originalTarget.classList?.contains('icon') ||
        originalTarget.tagName?.toLowerCase() === 'i'
      );
      if (!type && isSvgOrIcon) return null;
      if (!type) return null;

      const ids = DOMUtils.getIdentifiers(el);
      const label = DOMUtils.resolveLabel(el);
      const placeholder = el.getAttribute('placeholder') || el.getAttribute('data-placeholder');
      const visibleText = DOMUtils.cleanText((el.value !== undefined ? el.value : (el.innerText || el.textContent)) || '');

      let bestLabel = label || placeholder || ids.ariaLabel || ids.name || ids.id || DOMUtils.findNearbyLabel(el);
      if (!bestLabel && DOMUtils.isNoisy(visibleText)) bestLabel = ids.name || ids.id || 'input field';

      // Known container label fallback if label is missing/generic
      const blocklist = ['input field', 'icon', 'svg', 'element'];
      let hasMeaningfulLabel = bestLabel && !blocklist.includes(bestLabel.toLowerCase().trim());
      
      if (!hasMeaningfulLabel) {
        const tabColContainer = el.closest('[class*="tabcollection"]');
        if (tabColContainer) {
          bestLabel = 'Tab Collections';
          hasMeaningfulLabel = true;
        } else {
          const userProfileContainer = el.closest('[class*="userprofile"], [class*="user-profile"]');
          if (userProfileContainer) {
            bestLabel = 'User Profile';
            hasMeaningfulLabel = true;
          }
        }
      }

      // Reject the step entirely if the resolved label is generic or noisy (e.g. session-timeout banners)
      if (!bestLabel || blocklist.includes(bestLabel.toLowerCase().trim()) || DOMUtils.isNoisy(bestLabel)) {
        return null;
      }

      const tag = el.tagName.toLowerCase();
      const action = (eventType === 'input' || (['input', 'textarea'].includes(tag) && typedValue !== undefined) || (el.isContentEditable && typedValue !== undefined)) ? 'enter' : (tag === 'select' || eventType === 'change' || eventType === 'select-click') ? 'select' : 'click';

      const step = {
        timestamp: new Date().toISOString(),
        action,
        element: Object.fromEntries(Object.entries({
          type, tag,
          label: bestLabel,
          placeholder,
          inputValue: typedValue !== undefined ? typedValue : null,
          selectedValue: (eventType === 'change' && tag === 'select') ? el.options[el.selectedIndex]?.text || el.value : null
        }).filter(([, v]) => v !== null && v !== '')),
        identifiers: ids,
        page: { url: window.location.href, title: document.title }
      };

      const ddCtx = DOMUtils.getDropdownContext(el);
      if (ddCtx && ddCtx !== bestLabel) {
        step.dropdownParent = ddCtx;
        step.element.hasInput = DOMUtils.hasDropdownInput(el);
      } else if (tag === 'select') {
        step.element.hasInput = false;
      }

      step._kb = this.generateKBEntry(step);
      return step;
    }

    generateKBEntry(step) {
      const el = step.element;
      const label = el.label || el.placeholder || el.type;
      const hType = Config.TYPE_NAMES[el.type] || 'element';
      let kbAction = step.action;
      if (step.action === 'click' && step.dropdownParent && el.selectedValue) {
        kbAction = 'select';
      }

      // ── Tab Collection Menu fast-path (Priority 1) ─────────────────────
      // Elements with context='tab_collection_menu' always produce a
      // deterministic output: "Click on <label> from tab collection menu."
      // The context field is forwarded to the server to skip the LLM.
      if (el.context === 'tab_collection_menu') {
        const menuLabel = label;
        let menuSlug = (menuLabel || 'menu').toLowerCase()
          .replace(/[^a-z0-9\s]/g, '').trim().replace(/\s+/g, '_').substring(0, 40);
        const input = { action: 'click', label: menuLabel, context: 'tab_collection_menu' };
        if (step.userStep) input.userStep = step.userStep;
        if (step.identifiers?.id) input.elementId = step.identifiers.id;
        if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;
        return {
          name: `tabmenu_${menuSlug}_veeva`,
          input,
          output: `Click on ${menuLabel} from tab collection menu.`,
        };
      }

      // ── Navbar fast-path (Priority 2) ──────────────────────────────────
      // Elements marked as navbar items always produce a deterministic output
      // in the format: "Click on <label> from navbar."
      // The context field is forwarded to the server so it can skip the LLM.
      if (el.context === 'navbar') {
        const navbarLabel = label;
        let navSlug = (navbarLabel || 'navbar').toLowerCase()
          .replace(/[^a-z0-9\s]/g, '').trim().replace(/\s+/g, '_').substring(0, 40);
        const input = { action: 'click', label: navbarLabel, context: 'navbar' };
        if (step.userStep) input.userStep = step.userStep;
        if (step.identifiers?.id) input.elementId = step.identifiers.id;
        if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;
        return {
          name: `navbar_${navSlug}_veeva`,
          input,
          output: `Click on ${navbarLabel} from navbar.`,
        };
      }

      const input = { action: kbAction, label };
      if (step.userStep) input.userStep = step.userStep;

      if (kbAction === 'enter') {
        input.value = el.inputValue || '<<value>>';
        if (el.placeholder) input.placeholder = el.placeholder;
      }
      if (kbAction === 'select') {
        input.selectedText = el.selectedValue || el.inputValue || '<<value>>';
        if (step.dropdownParent) input.dropdownLabel = step.dropdownParent;
        if (el.hasInput) input.hasInput = true;
      }
      if (kbAction === 'click' && step.dropdownParent) {
        input.dropdownLabel = step.dropdownParent;
      }
      if (step.identifiers?.id) input.elementId = step.identifiers.id;
      if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;

      let output = '';
      if (kbAction === 'click') {
        if (step.dropdownParent) {
          output = `Click the ${label} button in ${step.dropdownParent}.`;
        } else {
          output = `Click the ${label} ${hType}.`;
        }
      } else if (kbAction === 'enter') {
        output = `Enter <<${el.inputValue || 'value'}>> in the ${el.label || el.placeholder || 'input'} input field.`;
      } else if (kbAction === 'select') {
        const selectedVal = el.selectedValue || el.inputValue || 'value';
        const parentCtx = step.dropdownParent || el.label || 'dropdown';
        
        if (parentCtx === 'Tab Collections' || parentCtx === 'User Profile') {
          output = `Select ${selectedVal} from ${parentCtx}.`;
          input.label = parentCtx;
        } else if (el.hasInput) {
          output = `Enter ${selectedVal} and select ${selectedVal} from '${parentCtx}' dropdown.`;
        } else {
          output = `Select ${selectedVal} from '${parentCtx}' dropdown.`;
        }
      }

      let finalLabel = label;
      if (kbAction === 'select' && step.dropdownParent) {
        finalLabel = step.dropdownParent;
      }
      let slug = (finalLabel || el.type || 'element').toLowerCase().replace(/[^a-z0-9\s]/g, '').trim().replace(/\s+/g, '_').substring(0, 40);
      if (slug === 'tab_collections') slug = 'tab_collection';
      if (slug === 'user_profiles') slug = 'user_profile';

      if (kbAction === 'select') {
        const val = el.selectedValue || el.inputValue;
        if (val) {
          const valSlug = val.toLowerCase().replace(/[^a-z0-9\s]/g, '').trim().replace(/\s+/g, '_').substring(0, 30);
          slug = `select_${valSlug}_${slug}`;
        }
      }
      return { name: `${slug}_veeva`, input, output };
    }

    trackInput(el) {
      if (!el || this.inputValues.has(el)) return;
      const isCE = !!el.isContentEditable;
      const update = () => { try { this.inputValues.set(el, isCE ? (el.innerText || '') : (el.value || '')); } catch { } };
      update();
      ['input', 'compositionend', 'paste'].forEach(evt => el.addEventListener(evt, update, { passive: true, capture: true }));
    }

    async handleInteract(e) {
      if (!this.state.isRecording || !e.target || this.isIgnored(e.target)) return;
      let el = e.target;
      const tag = el.tagName.toLowerCase();
      const isInputEl = ['input', 'textarea', 'select'].includes(tag) || el.isContentEditable || el.closest('[contenteditable="true"]');

      if (!isInputEl) {
        const interactive = DOMUtils.findInteractiveParent(el);
        if (interactive) el = interactive;
        else if (Config.SKIP_TAGS.has(el.tagName.toLowerCase())) return;
      }

      if (e.type === 'blur' && isInputEl) {
        if (this.capturedInputs.has(el)) {
          this.capturedInputs.delete(el);
          this.inputValues.delete(el);
          return;
        }
        const val = this.inputValues.get(el) ?? (el.isContentEditable ? el.innerText : el.value);
        if (!val) return;
        const step = this.buildStep(el, 'input', val, e.target);
        if (step) this.toast.show(await this.state.addStep(step));
        this.inputValues.delete(el);
      } else if (e.type === 'change' && tag === 'select') {
        const step = this.buildStep(el, 'change', undefined, e.target);
        if (step) this.toast.show(await this.state.addStep(step));
      } else if (e.type === 'click' || e.type === 'mousedown') {
        if (isInputEl && tag !== 'select') return this.trackInput(el);
        if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) {
          if (e.type === 'mousedown') return;
          const step = this.buildStep(el, 'click', el.checked ? 'checked' : 'unchecked', e.target);
          if (step) this.toast.show(await this.state.addStep(step));
          return;
        }
        // Priority 1: Tab Collection Menu items (data-corgix-internal="MENU-ITEM")
        // Must be checked BEFORE navbar to honour the classification hierarchy.
        // Use e.target.closest() so nested child clicks are caught correctly.
        const tabMenuEl = e.target.closest('[data-corgix-internal="MENU-ITEM"]') ||
          (el.getAttribute && el.getAttribute('data-corgix-internal') === 'MENU-ITEM' ? el : null);
        if (tabMenuEl) {
          // Extract visible label from the menu item container (handles nested children)
          const rawText = DOMUtils.cleanText(tabMenuEl.innerText || tabMenuEl.textContent || '');
          const menuLabel = (rawText && !DOMUtils.isNoisy(rawText)) ? rawText : DOMUtils.resolveLabel(el);
          if (menuLabel) {
            const step = this.buildStep(el, 'click', undefined, e.target);
            if (step) {
              step.element.label = menuLabel;
              step.element.context = 'tab_collection_menu';
              step._kb = this.generateKBEntry(step);
              this.toast.show(await this.state.addStep(step));
            }
            return;
          }
        }
        // Priority 2: Navbar links (vv-navbar-link) → always a plain navigation click
        if (DOMUtils.isNavbarItem(el)) {
          const step = this.buildStep(el, 'click', undefined, e.target);
          if (step) {
            step.element.context = 'navbar';
            step._kb = this.generateKBEntry(step);
            this.toast.show(await this.state.addStep(step));
          }
          return;
        }
        if (DOMUtils.isInsideVeevaDropdown(el)) {
          const text = DOMUtils.cleanText(el.innerText || el.textContent || '');
          if (text && !DOMUtils.isNoisy(text)) {
            const step = this.buildStep(el, 'select-click', undefined, e.target);
            if (step) {
              step.action = 'select';
              step.element.selectedValue = text;
              const ddCtx = DOMUtils.getDropdownContext(el);
              step.dropdownParent = ddCtx || step.dropdownParent || DOMUtils.findNearbyLabel(el) || 'dropdown';
              step._kb = this.generateKBEntry(step);
              this.toast.show(await this.state.addStep(step));
              return;
            }
          }
        }
        const step = this.buildStep(el, 'click', undefined, e.target);
        if (step) this.toast.show(await this.state.addStep(step));
      }
    }

    async handleKeyDown(e) {
      if (!this.state.isRecording || !e.target || this.isIgnored(e.target)) return;
      const el = e.target;
      const tag = el.tagName.toLowerCase();
      if ((tag === 'input' || tag === 'textarea') && e.key === 'Enter') {
        if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) return;
        const val = this.inputValues.get(el) ?? el.value;
        if (!val) return;
        const step = this.buildStep(el, 'input', val, e.target);
        if (step) {
          this.toast.show(await this.state.addStep(step));
          this.capturedInputs.add(el);
          this.inputValues.delete(el);
        }
      }
    }

    bindEvents() {
      const handler = this.handleInteract.bind(this);
      ['mousedown', 'click', 'change', 'blur'].forEach(evt => document.addEventListener(evt, handler, true));
      document.addEventListener('keydown', this.handleKeyDown.bind(this), true);
      document.addEventListener('focusin', e => {
        if (this.state.isRecording && !this.isIgnored(e.target) && (['input', 'textarea'].includes((e.target.tagName || '').toLowerCase()) || e.target.isContentEditable)) {
          if (e.target.type !== 'checkbox' && e.target.type !== 'radio') this.trackInput(e.target);
        }
      }, true);
      const onGlobalInput = e => {
        if (e.target && !this.isIgnored(e.target) && (['input', 'textarea'].includes((e.target.tagName || '').toLowerCase()) || e.target.isContentEditable)) {
          this.trackInput(e.target);
          this.inputValues.set(e.target, e.target.isContentEditable ? (e.target.innerText || '') : (e.target.value || ''));
        }
      };
      ['input', 'paste'].forEach(evt => document.addEventListener(evt, onGlobalInput, true));
    }
  }

  const state = new StateManager();
  const toast = new ToastManager();
  const ui = new UIController(state);
  const recorder = new EventRecorder(state, toast);

  chrome.runtime.onMessage.addListener((msg, _, sendResponse) => {
    (async () => {
      try {
        switch (msg.type) {
          case 'START':
            state.isRecording = true;
            await state.save(); await state.updateBadge(); ui.updateVisibility();
            sendResponse({ ok: true });
            break;
          case 'STOP':
            state.isRecording = false;
            await state.save(); await state.updateBadge(); ui.updateVisibility();
            sendResponse({ ok: true });
            break;
          case 'RESTART':
            state.isRecording = false;
            await state.clear(); ui.updateVisibility();
            sendResponse({ ok: true });
            break;
          case 'GET_STATE':
            sendResponse({ steps: state.steps, isRecording: state.isRecording, stepCounter: state.stepCounter });
            break;
          case 'GET_KB':
            sendResponse({ kb: state.steps.map(s => s._kb).filter(Boolean) });
            break;
          case 'MEMORY_STORE': {
            // Build a memory-store step — no recording check needed (user-initiated)
            const storeStep = {
              timestamp: new Date().toISOString(),
              action: 'memory_store',
              element: {
                type: 'memory',
                label: msg.varName,
                inputValue: msg.value,
              },
              page: { url: window.location.href, title: document.title },
            };
            storeStep._kb = {
              name: `memory_store_${msg.varName.toLowerCase().replace(/[^a-z0-9]/g, '_')}_veeva`,
              input: { action: 'memory_store', label: msg.varName, value: msg.value },
              output: `Store the <<${msg.value}>> in the memory with the key <<${msg.varName}>>.`,
            };
            const added = await state.addStep(storeStep);
            if (added) toast.show(added);
            sendResponse({ ok: true, step: added });
            break;
          }
          case 'MEMORY_FETCH': {
            // Build a memory-fetch step
            const fetchStep = {
              timestamp: new Date().toISOString(),
              action: 'memory_fetch',
              element: {
                type: 'memory',
                label: msg.varName,
              },
              page: { url: window.location.href, title: document.title },
            };
            fetchStep._kb = {
              name: `memory_fetch_${msg.varName.toLowerCase().replace(/[^a-z0-9]/g, '_')}_veeva`,
              input: { action: 'memory_fetch', label: msg.varName },
              output: `Fetch the <<${msg.varName}>> value from the memory.`,
            };
            const fetched = await state.addStep(fetchStep);
            if (fetched) toast.show(fetched);
            sendResponse({ ok: true, step: fetched });
            break;
          }
        }
      } catch (e) {
        sendResponse({ error: e.message });
      }
    })();
    return true;
  });

  const init = async () => {
    await state.load();
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { ui.create(); ui.updateVisibility(); });
    else { ui.create(); ui.updateVisibility(); }
  };

  init();
})();