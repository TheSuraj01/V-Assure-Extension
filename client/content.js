/**
 * Veeva Vault Action Recorder — Content Script
 * ─────────────────────────────────────────────
 * Captures user interactions (clicks, typed input, dropdown selections,
 * screenshots, manual step markers, memory store/fetch) on Veeva Vault
 * pages and converts each one into:
 *   1. A structured "step" record (for the extension's own step list / UI)
 *   2. A "KB entry" (name/input/output) describing the action in natural
 *      language, intended for a downstream knowledge-base / LLM pipeline.
 *
 * This file is loaded as a single classic (non-module) content script, so
 * everything is wrapped in one IIFE and guarded against double-injection.
 *
 * Section map:
 *   1. CONSTANTS         — static config, selectors, noise patterns
 *   2. TEXT UTILITIES     — pure string helpers
 *   3. DOM CLASSIFICATION — element type / label / context resolution
 *   4. STEP FACTORY        — turns a DOM interaction into a step + KB entry
 *   5. RECORDING STATE     — chrome.storage-backed state container
 *   6. TOAST UI             — on-page "last action recorded" feedback
 *   7. CONTROL PANEL UI     — draggable panel (step marker input, screenshot)
 *   8. EVENT RECORDER       — DOM event listeners → StepFactory → state/UI
 *   9. RUNTIME MESSAGING    — chrome.runtime message handling
 *  10. BOOTSTRAP             — wires everything together and starts it
 */
(() => {
  'use strict';

  // Guard against the content script being injected more than once.
  if (window.__veevaRecorder) return;
  window.__veevaRecorder = true;


  /** Tag names that should never be treated as interactive/recordable elements. */
  const SKIP_TAGS = new Set([
    'html', 'body', 'head', 'script', 'style', 'meta',
    'svg', 'path', 'circle', 'rect', 'g', 'defs', 'use', 'polygon', 'polyline', 'ellipse', 'line',
  ]);

  /**
   * Patterns that mark a candidate label as noise rather than a genuine
   * field/button label — e.g. raw booleans, IDs, or Veeva's session-timeout
   * banner text, which would otherwise get picked up as a "label".
   */
  const NOISE_PATTERNS = [
    /^delegate access/i,
    /^\d+$/,
    /^(true|false|null|undefined)$/i,
    /^[\s\W]+$/,
    /^.{55,}[.!?]$/, // long sentences ending in punctuation (banners/notifications)
    /logged off vault/i,
    /will be logged off/i,
    /click here if you wish to continue/i,
  ];

  const MIN_LABEL_LENGTH = 2;
  const MAX_LABEL_LENGTH = 80;

  /** Selectors checked, in order, when hunting for a nearby Veeva field label. */
  const VEEVA_LABEL_SELECTORS = [
    'label',
    '[class*="field-label"]',
    '[class*="form-label"]',
    '[class*="input-label"]',
    '[class*="vv-label"]',
    '[class*="label-text"]',
    'legend',
    '[data-label]',
  ];

  /** Selector matching any kind of dropdown/listbox/picker container used across Veeva UIs. */
  const DROPDOWN_CONTAINER_SELECTOR = [
    '[class*="dropdown"]', '[role="listbox"]', '[class*="picker"]', '[class*="menu-list"]',
    '[class*="options"]', '[class*="picklist"]', '[class*="vv-menu"]', '[class*="select2-"]',
    '[class*="chzn-drop"]', '[class*="k-list"]',
  ].join(', ');

  /** Same as above, plus the Tab Collection container — used for "does this have a text input" checks. */
  const DROPDOWN_OR_TABCOLLECTION_SELECTOR = `${DROPDOWN_CONTAINER_SELECTOR}, [class*="tabcollection"]`;

  /** Selector matching elements that are themselves inside a standard (non-Veeva-special) dropdown/menu. */
  const STANDARD_DROPDOWN_MEMBER_SELECTOR = [
    '[role="listbox"]', '[role="option"]', '[role="menu"]', '[role="menuitem"]',
    '[class*="dropdown-menu"]', '[class*="dropdown-list"]', '[class*="option-list"]',
    '[class*="picklist"]', '[class*="autocomplete"]', '[class*="vv-menu"]', '[class*="vv-list"]',
    '[class*="select2-"]', '[class*="chzn-"]', '[class*="k-list"]',
  ].join(', ');

  /** Selector for elements that mark a "Tab Collection" or "User Profile" trigger by aria-label/title. */
  const TAB_COLLECTION_OR_PROFILE_HINT_SELECTOR =
    '[aria-label*="Tab Collection" i], [title*="Tab Collection" i], [aria-label*="User Profile" i], [title*="User Profile" i]';

  /** Generic clickable-element selector used when walking up to find an interactive ancestor. */
  const CLICKABLE_SELECTOR = [
    'button', 'a', '[role="button"]', '[role="tab"]', '[role="link"]', '[role="menuitem"]', '[role="option"]',
    '[class*="btn"]', '[class*="tab"]', '[onclick]', '[data-action]', '[data-test]',
  ].join(', ');

  /** Maximum number of ancestor levels to walk when searching for labels/interactive parents. */
  const MAX_ANCESTOR_SEARCH_DEPTH = 6;

  /** Human-readable names for each classified element type, used in generated KB sentences. */
  const ELEMENT_TYPE_DISPLAY_NAMES = {
    button: 'button',
    link: 'link',
    tab: 'tab',
    'menu-item': 'menu item',
    'dropdown-option': 'dropdown option',
    checkbox: 'checkbox',
    radio: 'radio button',
    select: 'dropdown',
    input: 'input field',
    textarea: 'text area',
    toggle: 'toggle',
    'file-upload': 'file upload',
    image: 'image',
    label: 'label',
  };

  /** Labels resolved to these strings are treated as "not meaningful" and trigger fallback logic. */
  const GENERIC_LABEL_BLOCKLIST = ['input field', 'icon', 'svg', 'element'];

  /** Max time (ms) between two otherwise-identical steps for the second to be treated as a duplicate. */
  const DUPLICATE_STEP_WINDOW_MS = 400;

  /** How long (ms) the toast stays visible before fading out. */
  const TOAST_DISPLAY_MS = 2800;

  const TOAST_COLORS_BY_ACTION = {
    click: '#22d3ee',
    enter: '#4ade80',
    select: '#f59e0b',
    screenshot: '#c084fc',
    step_marker: '#f472b6',
  };
  const TOAST_DEFAULT_COLOR = '#94a3b8';

  /** DOM element ids owned by this extension's own UI — events on these are always ignored. */
  const OWN_UI_IDS = {
    panel: '__veeva-recorder-ui',
    toast: '__veeva-recorder-toast',
    stepInput: '__veeva-recorder-input',
  };


  /**
   * Pure, stateless string helpers used throughout DOM analysis.
   * Kept separate from DOM-walking logic so they're trivially testable/reusable.
   */
  const TextUtils = {
    /** Collapses whitespace, strips footnote/asterisk marks, and trims. */
    clean(text) {
      if (!text) return '';
      return text
        .replace(/\s+/g, ' ')
        .replace(/[*✱†‡]/g, '')
        .replace(/\u00a0/g, ' ')
        .trim();
    },

    /** True if `text` is empty, too short/long, or matches a known noise pattern. */
    isNoisy(text) {
      if (!text || text.length < MIN_LABEL_LENGTH || text.length > MAX_LABEL_LENGTH) return true;
      return NOISE_PATTERNS.some((pattern) => pattern.test(text));
    },

    /** Converts arbitrary text into a short, lowercase, underscore-joined slug. */
    toSlug(text, maxLength = 40) {
      return (text || '')
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, '')
        .trim()
        .replace(/\s+/g, '_')
        .substring(0, maxLength);
    },
  };


  /**
   * Resolves *what* an element is (classification), *what it should be called*
   * (label resolution), and *where it sits* in Veeva's UI (navbar / tab
   * collection menu / dropdown / plain element). All methods are pure
   * functions of the DOM — no side effects, no internal state.
   */
  const ElementInspector = {
    /**
     * Classifies an element into a recorder-level "type" (button, input,
     * checkbox, link, etc.), or `null` if it isn't something we record.
     */
    classify(el) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || '').toLowerCase();
      const role = (el.getAttribute('role') || '').toLowerCase();
      const className = typeof el.className === 'string' ? el.className : '';
      const title = (el.getAttribute('title') || '').toLowerCase();
      const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();

      if (this._isTabCollectionOrProfileTrigger(el, { title, ariaLabel, className })) return 'button';

      if (tag === 'button' || role === 'button' || (tag === 'input' && ['submit', 'button', 'reset'].includes(type))) {
        return 'button';
      }

      if (tag === 'input') {
        if (type === 'checkbox') return 'checkbox';
        if (type === 'radio') return 'radio';
        if (type === 'file') return 'file-upload';
        return 'input';
      }

      if (['select', 'textarea', 'a', 'label', 'img'].includes(tag)) {
        if (tag === 'a') return 'link';
        if (tag === 'img') return 'image';
        return tag;
      }

      if (['tab', 'menuitem', 'menu-item', 'option', 'checkbox', 'switch'].includes(role)) {
        if (role === 'switch') return 'toggle';
        if (role === 'menuitem') return 'menu-item';
        if (role === 'option') return 'dropdown-option';
        return role;
      }

      if (role === 'listbox' || role === 'combobox') return 'select';

      if (['span', 'div', 'li'].includes(tag)) {
        const looksClickable =
          el.onclick ||
          el.getAttribute('onclick') ||
          el.getAttribute('ng-click') ||
          el.getAttribute('(click)') ||
          el.getAttribute('tabindex') ||
          className.match(/\b(btn|button|action|clickable|tab|option|item|link|trigger|toggle|menu)\b/i);
        if (looksClickable) return 'button';

        const parentClassName = typeof el.parentElement?.className === 'string' ? el.parentElement.className : '';
        if (parentClassName.match(/\b(nav|menu|toolbar|tabs|actions|controls)\b/i)) return 'button';
      }

      return null;
    },

    /** True if this element is (or contains) a Tab Collection / User Profile trigger. */
    _isTabCollectionOrProfileTrigger(el, { title, ariaLabel, className }) {
      if (
        ariaLabel.includes('tab collection') || title.includes('tab collection') ||
        ariaLabel.includes('user profile') || title.includes('user profile')
      ) return true;
      if (className.includes('tabcollection-menu-button') || className.includes('tabcollection-menu')) return true;
      // The "waffle" icon is always the Tab Collections trigger in Veeva's UI.
      if (el.getAttribute('data-icon') === 'waffle' || el.querySelector('[data-icon="waffle"]')) return true;
      if (el.querySelector(TAB_COLLECTION_OR_PROFILE_HINT_SELECTOR)) return true;
      return false;
    },

    /**
     * Attempts to resolve the best human-readable label for an element,
     * trying (in order): special Tab Collection/User Profile detection,
     * aria-label, `<label for>`, aria-labelledby, title, placeholder,
     * wrapping `<label>`, data-* attributes, own text content, nearby
     * Veeva field labels, preceding siblings, and table headers.
     * Returns `null` if nothing usable is found.
     */
    resolveLabel(el) {
      const specialLabel = this._resolveTabCollectionOrProfileLabel(el);
      if (specialLabel) return specialLabel;

      const ariaLabel = el.getAttribute('aria-label') || '';
      if (ariaLabel && !TextUtils.isNoisy(ariaLabel)) return ariaLabel;

      const labelForId = this._resolveLabelForId(el);
      if (labelForId) return labelForId;

      const labelledBy = this._resolveAriaLabelledBy(el);
      if (labelledBy) return labelledBy;

      const title = el.getAttribute('title') || '';
      if (title && !TextUtils.isNoisy(title)) return title;

      const placeholderLabel = this._resolvePlaceholder(el);
      if (placeholderLabel) return placeholderLabel;

      const wrappingLabel = this._resolveWrappingLabel(el);
      if (wrappingLabel) return wrappingLabel;

      const dataAttrLabel = el.getAttribute('data-label') || el.getAttribute('data-name') || el.getAttribute('data-title');
      if (dataAttrLabel && !TextUtils.isNoisy(dataAttrLabel)) return dataAttrLabel;

      const ownTextLabel = this._resolveOwnText(el);
      if (ownTextLabel) return ownTextLabel;

      const ancestorLabel = this._resolveFromAncestors(el);
      if (ancestorLabel) return ancestorLabel;

      const tableHeaderLabel = this._resolveTableHeader(el);
      if (tableHeaderLabel) return tableHeaderLabel;

      return null;
    },

    /** Checks title/aria-label (own and one level of children) for Tab Collection / User Profile hints. */
    _resolveTabCollectionOrProfileLabel(el) {
      const title = (el.getAttribute('title') || '').toLowerCase();
      const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
      if (title.includes('tab collection') || ariaLabel.includes('tab collection')) return 'Tab Collection';
      if (title.includes('user profile') || ariaLabel.includes('user profile')) return 'User Profile';

      const child = el.querySelector(TAB_COLLECTION_OR_PROFILE_HINT_SELECTOR);
      if (!child) return null;

      const childTitle = (child.getAttribute('title') || '').toLowerCase();
      const childAriaLabel = (child.getAttribute('aria-label') || '').toLowerCase();
      if (childTitle.includes('tab collection') || childAriaLabel.includes('tab collection')) return 'Tab Collections';
      if (childTitle.includes('user profile') || childAriaLabel.includes('user profile')) return 'User Profile';
      return null;
    },

    /** Resolves via `<label for="el.id">`. */
    _resolveLabelForId(el) {
      if (!el.id) return null;
      try {
        const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        return label ? TextUtils.clean(label.innerText) : null;
      } catch {
        return null;
      }
    },

    /** Resolves via `aria-labelledby` (may reference multiple space-separated ids). */
    _resolveAriaLabelledBy(el) {
      const ref = el.getAttribute('aria-labelledby');
      if (!ref) return null;
      const parts = ref
        .split(/\s+/)
        .map((id) => {
          const target = document.getElementById(id);
          return target ? TextUtils.clean(target.innerText) : '';
        })
        .filter(Boolean);
      return parts.length ? parts.join(' ') : null;
    },

    /** Resolves via `placeholder` / `data-placeholder` on inputs and textareas. */
    _resolvePlaceholder(el) {
      if (!['input', 'textarea'].includes(el.tagName.toLowerCase())) return null;
      const placeholder = el.getAttribute('placeholder') || el.getAttribute('data-placeholder');
      return placeholder && !TextUtils.isNoisy(placeholder) ? placeholder : null;
    },

    /** Resolves via a wrapping `<label>` ancestor (text minus the element's own value). */
    _resolveWrappingLabel(el) {
      const wrappingLabel = el.closest('label');
      if (!wrappingLabel) return null;
      const text = TextUtils.clean(wrappingLabel.innerText.replace(el.value || '', ''));
      return text && !TextUtils.isNoisy(text) ? text : null;
    },

    /** Resolves via the element's own visible text, for tags where that's meaningful. */
    _resolveOwnText(el) {
      const tag = el.tagName.toLowerCase();

      if (['button', 'a', 'li'].includes(tag)) {
        const text = TextUtils.clean(el.innerText);
        if (text && !TextUtils.isNoisy(text) && text.length <= MAX_LABEL_LENGTH) return text;
      }

      if (['span', 'div'].includes(tag)) {
        const text = TextUtils.clean(el.innerText);
        if (text && text.length <= 30 && !TextUtils.isNoisy(text) && !text.includes('\n')) return text;
      }

      return null;
    },

    /**
     * Walks up to MAX_ANCESTOR_SEARCH_DEPTH ancestors looking for a Veeva
     * field-label element nearby, or a usable preceding sibling's text.
     */
    _resolveFromAncestors(el) {
      let node = el.parentElement;
      for (let depth = 0; depth < MAX_ANCESTOR_SEARCH_DEPTH && node; depth += 1) {
        const nearbyVeevaLabel = this._findVeevaLabelWithin(node, el);
        if (nearbyVeevaLabel) return nearbyVeevaLabel;

        const siblingText = this._findUsablePrecedingSiblingText(el, 50);
        if (siblingText) return siblingText;

        node = node.parentElement;
      }
      return null;
    },

    /** Searches `container` for a Veeva label selector match that isn't `el` itself or its ancestor/descendant. */
    _findVeevaLabelWithin(container, el) {
      for (const selector of VEEVA_LABEL_SELECTORS) {
        try {
          const found = container.querySelector(selector);
          if (found && found !== el && !found.contains(el) && !el.contains(found)) {
            const text = TextUtils.clean(found.innerText);
            if (text && !TextUtils.isNoisy(text)) return text;
          }
        } catch {
          // Invalid selector match for this DOM — skip.
        }
      }
      return null;
    },

    /** Finds the closest preceding sibling (of `el`) with usable, non-form text. */
    _findUsablePrecedingSiblingText(el, maxLength) {
      let sibling = el.previousElementSibling;
      while (sibling) {
        if (!sibling.querySelector('input,select,textarea,button')) {
          const text = TextUtils.clean(sibling.innerText);
          if (text && !TextUtils.isNoisy(text) && text.length <= maxLength) return text;
        }
        sibling = sibling.previousElementSibling;
      }
      return null;
    },

    /** Resolves via the matching `<th>` when the element sits inside a `<td>`. */
    _resolveTableHeader(el) {
      const cell = el.closest('td');
      if (!cell) return null;
      const columnIndex = Array.from(cell.parentElement?.children || []).indexOf(cell);
      const header = cell.closest('table')?.querySelector(`th:nth-child(${columnIndex + 1})`);
      return header ? TextUtils.clean(header.innerText) : null;
    },

    /**
     * Finds a usable label by walking up from `el` and checking preceding
     * siblings at each level (used as a final fallback when resolveLabel
     * fails, e.g. for generic input fields with no other identifying info).
     */
    findNearbyLabel(el) {
      if (!el) return null;
      let node = el;
      for (let depth = 0; depth < MAX_ANCESTOR_SEARCH_DEPTH && node; depth += 1) {
        const siblingText = this._findUsablePrecedingSiblingText(node, Infinity);
        if (siblingText) return siblingText;

        const parent = node.parentElement;
        const parentSibling = parent?.previousElementSibling;
        if (parentSibling && !parentSibling.querySelector('input,select,textarea,button')) {
          const text = TextUtils.clean(parentSibling.innerText || parentSibling.textContent || '');
          if (text && !TextUtils.isNoisy(text)) return text;
        }
        node = node.parentElement;
      }
      return null;
    },

    /** Collects stable identifying attributes (id, name, aria-label, Veeva data-* attrs). */
    getIdentifiers(el) {
      const identifiers = {};
      if (el.id) identifiers.id = el.id;
      if (el.name) identifiers.name = el.name;

      const ariaLabel = el.getAttribute('aria-label');
      if (ariaLabel) identifiers.ariaLabel = ariaLabel;

      const veevaAttributes = {};
      for (const attr of el.attributes) {
        if (/^(data-vv|data-vault|data-action|data-object|data-type|data-id|data-test)/.test(attr.name)) {
          veevaAttributes[attr.name] = attr.value;
        }
      }
      if (Object.keys(veevaAttributes).length) identifiers.veevaData = veevaAttributes;

      return identifiers;
    },

    /**
     * Resolves the label of the dropdown/menu *containing* `el`, e.g. "Tab
     * Collections" or a picklist's own label — used to describe where a
     * selected option came from. Returns `null` if `el` isn't inside a
     * recognized dropdown-like container.
     */
    getDropdownContext(el) {
      const tabCollectionLabel = this._getTabCollectionContextLabel(el);
      if (tabCollectionLabel) return tabCollectionLabel;

      const container = el.closest(DROPDOWN_CONTAINER_SELECTOR);
      if (!container) return null;

      const ariaLabel = (container.getAttribute('aria-label') || '').toLowerCase();
      const title = (container.getAttribute('title') || '').toLowerCase();
      if (ariaLabel.includes('tab collection') || title.includes('tab collection')) return 'Tab Collections';
      if (ariaLabel.includes('user profile') || title.includes('user profile')) return 'User Profile';

      const triggerLabel = this._getControllingTriggerLabel(container);
      if (triggerLabel) return triggerLabel;

      return this.resolveLabel(container);
    },

    /** If `el` sits inside a Tab Collection container, resolves its display label. */
    _getTabCollectionContextLabel(el) {
      const container = el.closest('[class*="tabcollection"]');
      if (!container) return null;

      const triggerButton =
        container.querySelector('[aria-label*="Tab Collection" i]') ||
        container.querySelector('[class*="tabcollection-menu-button"]') ||
        document.querySelector('[class*="tabcollection-menu-button"]');

      const triggerAriaLabel = triggerButton?.getAttribute('aria-label');
      if (triggerAriaLabel && triggerAriaLabel.toLowerCase().includes('tab collection')) return triggerAriaLabel;

      return 'Tab Collections';
    },

    /** Finds the element that controls `container` via aria-controls and returns its label, if usable. */
    _getControllingTriggerLabel(container) {
      if (!container.id) return null;
      const trigger = document.querySelector(`[aria-controls="${CSS.escape(container.id)}"]`);
      if (!trigger) return null;
      const text = TextUtils.clean(trigger.innerText) || trigger.getAttribute('aria-label');
      return text && !TextUtils.isNoisy(text) ? text : null;
    },

    /**
     * True if the dropdown/menu/tab-collection container around `el` has an
     * associated free-text input (i.e. it's a searchable/combo control
     * rather than a plain click-to-select list).
     */
    hasDropdownInput(el) {
      const container = el.closest(DROPDOWN_OR_TABCOLLECTION_SELECTOR);
      if (!container) return false;

      const isUsableInput = (node) =>
        node?.tagName?.toLowerCase() === 'input' && !['hidden', 'radio', 'checkbox'].includes(node.type);
      const containsUsableInput = (node) =>
        !!node?.querySelector?.('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])');

      if (containsUsableInput(container)) return true;

      if (container.id) {
        if (document.querySelector(`input[aria-controls="${CSS.escape(container.id)}"]`)) return true;
        if (document.querySelector(`input[aria-owns="${CSS.escape(container.id)}"]`)) return true;
      }

      const parentWrapper = container.closest('[class*="combo"], [class*="select"], [class*="lookup"], [class*="picker"]');
      if (parentWrapper && containsUsableInput(parentWrapper)) return true;

      let sibling = container.previousElementSibling;
      while (sibling) {
        if (isUsableInput(sibling) || containsUsableInput(sibling)) return true;
        sibling = sibling.previousElementSibling;
      }

      return false;
    },

    /**
     * True if `el` is one of Veeva's top-navbar links
     * (`<a class="vv-navbar-link">` inside `<ul class="vv-navbar-nav">`).
     * Navbar items must never be classified as dropdown selections.
     */
    isNavbarItem(el) {
      return !!(
        el.closest('ul.vv-navbar-nav') ||
        el.closest('[class*="vv-navbar-nav"]') ||
        el.closest('[class*="vv-navbar-item"]') ||
        (el.tagName?.toLowerCase() === 'a' && el.classList.contains('vv-navbar-link'))
      );
    },

    /**
     * True if `el` (or an ancestor) carries `data-corgix-internal="MENU-ITEM"`,
     * marking it as part of the Tab Collection Menu. These must never be
     * classified as dropdown selections.
     */
    isTabCollectionMenuItem(el) {
      return !!(
        el.getAttribute('data-corgix-internal') === 'MENU-ITEM' ||
        el.closest('[data-corgix-internal="MENU-ITEM"]')
      );
    },

    /**
     * True if `el` sits inside a standard dropdown/listbox/menu, or inside a
     * Tab Collection container without itself being the trigger button.
     * Tab Collection Menu items and navbar items are excluded first, since
     * those are handled as their own special-cased interaction types.
     */
    isInsideVeevaDropdown(el) {
      if (this.isTabCollectionMenuItem(el)) return false;
      if (this.isNavbarItem(el)) return false;

      if (el.closest(STANDARD_DROPDOWN_MEMBER_SELECTOR)) return true;

      const tabCollectionContainer = el.closest('[class*="tabcollection"]');
      if (tabCollectionContainer) {
        const isTriggerButton = !!(
          el.closest('[class*="tabcollection-menu-button"]') ||
          (el.tagName.toLowerCase() === 'button' && (el.getAttribute('aria-label') || '').toLowerCase().includes('tab collection'))
        );
        if (!isTriggerButton) return true;
      }

      return false;
    },

    /**
     * Walks up from `node` to find the nearest interactive ancestor: a Tab
     * Collection trigger, a generically clickable element, or (as a last
     * resort) any ancestor whose title/aria-label/text mentions "Tab
     * Collection" or "User Profile". Returns `null` if none is found within
     * MAX_ANCESTOR_SEARCH_DEPTH levels.
     */
    findInteractiveParent(node) {
      if (!node) return null;

      const tabCollectionButton = node.closest('[class*="tabcollection-menu-button"], [aria-label*="Tab Collection" i]');
      if (tabCollectionButton) return tabCollectionButton;

      const clickableAncestor = node.closest(CLICKABLE_SELECTOR);
      if (clickableAncestor) return clickableAncestor;

      let current = node;
      for (let depth = 0; depth < MAX_ANCESTOR_SEARCH_DEPTH && current && current !== document.body; depth += 1) {
        const title = (current.getAttribute('title') || '').toLowerCase();
        const ariaLabel = (current.getAttribute('aria-label') || '').toLowerCase();
        const className = typeof current.className === 'string' ? current.className : '';

        const mentionsTabCollectionOrProfile =
          title.includes('tab collection') || ariaLabel.includes('tab collection') ||
          title.includes('user profile') || ariaLabel.includes('user profile') ||
          className.includes('tabcollection-menu-button');
        if (mentionsTabCollectionOrProfile) return current;

        const ownText = (current.innerText || current.textContent || '').toLowerCase();
        if (ownText.length < 50 && (ownText.includes('tab collection') || ownText.includes('user profile'))) {
          return current;
        }

        current = current.parentElement;
      }
      return null;
    },
  };


  /**
   * Builds structured "step" records and their corresponding natural-language
   * "KB entries" from raw DOM interactions. Pure with respect to extension
   * state — it only reads the DOM and returns data; callers decide whether
   * to persist the result.
   */
  const StepFactory = {
    /** Builds a `{ url, title }` snapshot of the current page. */
    currentPageInfo() {
      return { url: window.location.href, title: document.title };
    },

    /**
     * Builds a step record for a manual "step marker" entry (text typed
     * into the control panel's "Current Step" field).
     */
    buildStepMarker(label) {
      return {
        timestamp: new Date().toISOString(),
        action: 'step_marker',
        element: { type: 'marker', label },
        page: this.currentPageInfo(),
      };
    },

    /** Builds a step record for a manual screenshot action. */
    buildScreenshotStep() {
      return {
        timestamp: new Date().toISOString(),
        action: 'screenshot',
        element: { type: 'screenshot', label: 'Take screenshot of the page' },
        page: this.currentPageInfo(),
      };
    },

    /** Builds a step + KB entry for storing a value in the agent's memory. */
    buildMemoryStoreStep(varName, value) {
      const step = {
        timestamp: new Date().toISOString(),
        action: 'memory_store',
        element: { type: 'memory', label: varName, inputValue: value },
        page: this.currentPageInfo(),
      };
      step._kb = {
        name: `memory_store_${TextUtils.toSlug(varName, Infinity).replace(/[^a-z0-9]/g, '_')}_veeva`,
        input: { action: 'memory_store', label: varName, value },
        output: `Store the <<${value}>> in the memory with the key <<${varName}>>.`,
      };
      return step;
    },

    /** Builds a step + KB entry for fetching a value from the agent's memory. */
    buildMemoryFetchStep(varName) {
      const step = {
        timestamp: new Date().toISOString(),
        action: 'memory_fetch',
        element: { type: 'memory', label: varName },
        page: this.currentPageInfo(),
      };
      step._kb = {
        name: `memory_fetch_${TextUtils.toSlug(varName, Infinity).replace(/[^a-z0-9]/g, '_')}_veeva`,
        input: { action: 'memory_fetch', label: varName },
        output: `Fetch the <<${varName}>> value from the memory.`,
      };
      return step;
    },

    /**
     * Builds a step record for a DOM interaction (click / typed input /
     * select change), or `null` if the element/value doesn't resolve to
     * anything worth recording (unclassifiable element, decorative icon,
     * or a noisy/generic label such as a session-timeout banner).
     *
     * @param {Element} el - The (possibly bubbled-up) interactive element.
     * @param {'input'|'change'|'click'|'select-click'} eventType
     * @param {string|undefined} typedValue - Typed/checked value, if any.
     * @param {Element} originalTarget - The raw event target before bubbling.
     *   Reserved for callers/future label-resolution heuristics; not currently
     *   read here. (The original implementation computed an "is this an
     *   SVG/icon" check from it, but that check was unreachable dead code —
     *   the unconditional `if (!type) return null` immediately below already
     *   covered every case it handled, so it was removed during refactoring.)
     */
    buildInteractionStep(el, eventType, typedValue, originalTarget) { // eslint-disable-line no-unused-vars
      const type = ElementInspector.classify(el);
      if (!type) {
        return null; // Includes decorative icon/SVG clicks — never classify, never record.
      }

      const identifiers = ElementInspector.getIdentifiers(el);
      const label = this._resolveBestLabel(el, identifiers);
      if (!label) return null;

      const tag = el.tagName.toLowerCase();
      const action = this._resolveAction(el, tag, eventType, typedValue);
      const placeholder = el.getAttribute('placeholder') || el.getAttribute('data-placeholder');

      const step = {
        timestamp: new Date().toISOString(),
        action,
        element: this._buildElementPayload({ type, tag, label, placeholder, typedValue, eventType, el }),
        identifiers,
        page: this.currentPageInfo(),
      };

      this._attachDropdownContext(step, el, label, tag);

      step._kb = this.generateKnowledgeBaseEntry(step);
      return step;
    },

    /**
     * Resolves the best available label for an interaction, applying the
     * Tab Collection / User Profile container fallback and rejecting
     * generic/noisy results entirely (returns `null` to drop the step).
     */
    _resolveBestLabel(el, identifiers) {
      const resolvedLabel = ElementInspector.resolveLabel(el);
      const placeholder = el.getAttribute('placeholder') || el.getAttribute('data-placeholder');
      const visibleText = TextUtils.clean((el.value !== undefined ? el.value : (el.innerText || el.textContent)) || '');

      let label = resolvedLabel || placeholder || identifiers.ariaLabel || identifiers.name || identifiers.id
        || ElementInspector.findNearbyLabel(el);
      if (!label && TextUtils.isNoisy(visibleText)) {
        label = identifiers.name || identifiers.id || 'input field';
      }

      const isMeaningful = label && !GENERIC_LABEL_BLOCKLIST.includes(label.toLowerCase().trim());
      if (!isMeaningful) {
        if (el.closest('[class*="tabcollection"]')) {
          label = 'Tab Collections';
        } else if (el.closest('[class*="userprofile"], [class*="user-profile"]')) {
          label = 'User Profile';
        }
      }

      if (!label || GENERIC_LABEL_BLOCKLIST.includes(label.toLowerCase().trim()) || TextUtils.isNoisy(label)) {
        return null;
      }
      return label;
    },

    /** Determines whether this interaction is an 'enter' (typed), 'select', or 'click' action. */
    _resolveAction(el, tag, eventType, typedValue) {
      const isTypedInput =
        eventType === 'input' ||
        (['input', 'textarea'].includes(tag) && typedValue !== undefined) ||
        (el.isContentEditable && typedValue !== undefined);
      if (isTypedInput) return 'enter';

      const isSelectLike = tag === 'select' || eventType === 'change' || eventType === 'select-click';
      if (isSelectLike) return 'select';

      return 'click';
    },

    /** Builds the `element` sub-object for a step, omitting null/empty fields. */
    _buildElementPayload({ type, tag, label, placeholder, typedValue, eventType, el }) {
      const selectedValue = (eventType === 'change' && tag === 'select')
        ? (el.options[el.selectedIndex]?.text || el.value)
        : null;

      return Object.fromEntries(
        Object.entries({
          type,
          tag,
          label,
          placeholder,
          inputValue: typedValue !== undefined ? typedValue : null,
          selectedValue,
        }).filter(([, value]) => value !== null && value !== '')
      );
    },

    /** Attaches `dropdownParent` / `hasInput` metadata to a step when relevant. */
    _attachDropdownContext(step, el, label, tag) {
      const dropdownContext = ElementInspector.getDropdownContext(el);
      if (dropdownContext && dropdownContext !== label) {
        step.dropdownParent = dropdownContext;
        step.element.hasInput = ElementInspector.hasDropdownInput(el);
      } else if (tag === 'select') {
        step.element.hasInput = false;
      }
    },

    /**
     * Generates the natural-language KB entry (`{ name, input, output }`)
     * for a step. Tab Collection Menu and Navbar interactions take a fast
     * deterministic path (skipping any downstream LLM); everything else
     * follows the general click/enter/select phrasing rules.
     */
    generateKnowledgeBaseEntry(step) {
      const el = step.element;

      if (el.context === 'tab_collection_menu') return this._buildFastPathEntry(step, 'tabmenu', 'menu', 'from tab collection menu');
      if (el.context === 'navbar') return this._buildFastPathEntry(step, 'navbar', 'navbar', 'from navbar');

      const label = el.label || el.placeholder || el.type;
      const humanType = ELEMENT_TYPE_DISPLAY_NAMES[el.type] || 'element';
      const action = (step.action === 'click' && step.dropdownParent && el.selectedValue) ? 'select' : step.action;

      const input = this._buildKbInput(step, action, label);
      const output = this._buildKbOutput(step, action, label, humanType, input);
      const name = this._buildKbName(step, action, label, el);

      return { name, input, output };
    },

    /**
     * Builds the deterministic KB entry shared by the Tab Collection Menu
     * and Navbar fast paths — both produce "Click on <label> from <where>."
     */
    _buildFastPathEntry(step, namePrefix, slugFallback, outputSuffix) {
      const el = step.element;
      const label = el.label || el.placeholder || el.type;
      const slug = TextUtils.toSlug(label || slugFallback);

      const input = { action: 'click', label, context: el.context };
      if (step.userStep) input.userStep = step.userStep;
      if (step.identifiers?.id) input.elementId = step.identifiers.id;
      if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;

      return {
        name: `${namePrefix}_${slug}_veeva`,
        input,
        output: `Click on ${label} ${outputSuffix}.`,
      };
    },

    /** Builds the `input` object of a non-fast-path KB entry. */
    _buildKbInput(step, action, label) {
      const el = step.element;
      const input = { action, label };
      if (step.userStep) input.userStep = step.userStep;

      if (action === 'enter') {
        input.value = el.inputValue || '<<value>>';
        if (el.placeholder) input.placeholder = el.placeholder;
      }
      if (action === 'select') {
        input.selectedText = el.selectedValue || el.inputValue || '<<value>>';
        if (step.dropdownParent) input.dropdownLabel = step.dropdownParent;
        if (el.hasInput) input.hasInput = true;
      }
      if (action === 'click' && step.dropdownParent) {
        input.dropdownLabel = step.dropdownParent;
      }
      if (step.identifiers?.id) input.elementId = step.identifiers.id;
      if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;

      return input;
    },

    /** Builds the natural-language `output` sentence of a KB entry (may mutate `input.label` for select-from-named-menu cases). */
    _buildKbOutput(step, action, label, humanType, input) {
      const el = step.element;

      if (action === 'click') {
        return step.dropdownParent
          ? `Click the ${label} button in ${step.dropdownParent}.`
          : `Click the ${label} ${humanType}.`;
      }

      if (action === 'enter') {
        return `Enter <<${el.inputValue || 'value'}>> in the ${el.label || el.placeholder || 'input'} input field.`;
      }

      if (action === 'select') {
        const selectedValue = el.selectedValue || el.inputValue || 'value';
        const parentContext = step.dropdownParent || el.label || 'dropdown';

        if (parentContext === 'Tab Collections' || parentContext === 'User Profile') {
          input.label = parentContext;
          return `Select ${selectedValue} from ${parentContext}.`;
        }
        if (el.hasInput) {
          return `Enter ${selectedValue} and select ${selectedValue} from '${parentContext}' dropdown.`;
        }
        return `Select ${selectedValue} from '${parentContext}' dropdown.`;
      }

      return '';
    },

    /** Builds the KB entry's unique `name` slug. */
    _buildKbName(step, action, label, el) {
      const finalLabel = (action === 'select' && step.dropdownParent) ? step.dropdownParent : label;
      let slug = TextUtils.toSlug(finalLabel || el.type || 'element');

      if (slug === 'tab_collections') slug = 'tab_collection';
      if (slug === 'user_profiles') slug = 'user_profile';

      if (action === 'select') {
        const selectedValue = el.selectedValue || el.inputValue;
        if (selectedValue) {
          slug = `select_${TextUtils.toSlug(selectedValue, 30)}_${slug}`;
        }
      }

      return `${slug}_veeva`;
    },
  };


  /**
   * Owns the recorder's persisted state (recorded steps, recording flag,
   * step counter, current user-supplied step label) and synchronizes it
   * with `chrome.storage.local`. Also notifies the extension's badge.
   */
  class RecordingState {
    constructor() {
      this.steps = [];
      this.isRecording = false;
      this.stepCounter = 0;
      this.userStep = null;
    }

    /** Loads persisted state from chrome.storage.local. */
    async load() {
      return new Promise((resolve) => {
        chrome.storage.local.get(['veevaSteps', 'veevaRecording', 'veevaCounter'], (result) => {
          this.steps = result.veevaSteps || [];
          this.stepCounter = result.veevaCounter || 0;
          this.isRecording = result.veevaRecording || false;
          resolve();
        });
      });
    }

    /** Persists current state to chrome.storage.local. */
    async save() {
      return new Promise((resolve) => {
        chrome.storage.local.set(
          { veevaSteps: this.steps, veevaRecording: this.isRecording, veevaCounter: this.stepCounter },
          resolve
        );
      });
    }

    /** Notifies the extension UI (badge) of the current step count / recording status. */
    async updateBadge() {
      try {
        await chrome.runtime.sendMessage({ type: 'BADGE', count: this.steps.length, recording: this.isRecording });
      } catch {
        // No listener (e.g. popup closed) — safe to ignore.
      }
    }

    /**
     * Adds a step to the recording, after de-duplicating against the
     * immediately preceding step (same action/label/value within
     * DUPLICATE_STEP_WINDOW_MS is treated as a repeat, not a new step —
     * this happens when multiple DOM events fire for one logical action).
     * Persists state and updates the badge on success.
     *
     * @returns {object|null} The added step, or `null` if it was `null`/a duplicate.
     */
    async addStep(step) {
      if (!step) return null;
      if (this._isDuplicateOfLastStep(step)) return null;

      step.step = ++this.stepCounter;
      step.userStep = this.userStep;
      this.steps.push(step);

      await this.save();
      await this.updateBadge();
      return step;
    }

    /** True if `step` is an immediate repeat of the last recorded step. */
    _isDuplicateOfLastStep(step) {
      const last = this.steps[this.steps.length - 1];
      if (!last) return false;

      const elapsedMs = new Date(step.timestamp) - new Date(last.timestamp);
      const sameActionAndLabel = last.action === step.action && last.element.label === step.element.label;
      if (elapsedMs >= DUPLICATE_STEP_WINDOW_MS || !sameActionAndLabel) return false;

      const sameInputValue = (last.element.inputValue ?? '') === (step.element.inputValue ?? '');
      const sameSelectedValue = (last.element.selectedValue ?? '') === (step.element.selectedValue ?? '');
      return sameInputValue && sameSelectedValue;
    }

    /** Clears all recorded steps and resets the counter. */
    async clear() {
      this.steps = [];
      this.stepCounter = 0;
      await this.save();
      await this.updateBadge();
    }
  }


  /** Renders a small fixed-position "last action recorded" notification. */
  class ToastNotifier {
    constructor() {
      this.element = null;
      this.hideTimer = null;
    }

    /** Lazily creates the toast DOM element on first use. */
    _ensureElement() {
      if (this.element) return;
      this.element = document.createElement('div');
      this.element.id = OWN_UI_IDS.toast;
      this.element.style.cssText = `
        position:fixed;bottom:20px;right:20px;z-index:2147483647;
        background:#0f172a;color:#e2e8f0;font:600 11px/1.4 'SF Mono',monospace;
        padding:9px 13px;border-radius:8px;border-left:3px solid #22d3ee;
        box-shadow:0 8px 24px rgba(0,0,0,0.5);pointer-events:none;
        transition:opacity 0.25s ease;max-width:300px;
      `;
      document.body.appendChild(this.element);
    }

    /** Shows a toast summarizing the given step, auto-hiding after TOAST_DISPLAY_MS. */
    show(step) {
      if (!step) return;
      this._ensureElement();

      const color = TOAST_COLORS_BY_ACTION[step.action] || TOAST_DEFAULT_COLOR;
      const label = step.element.label || step.element.type;
      const value = step.element.inputValue || step.element.selectedValue || '';
      const humanType = ELEMENT_TYPE_DISPLAY_NAMES[step.element.type] || step.element.type;
      const userStepPrefix = step.userStep ? `[${step.userStep}] ` : '';
      const valueSuffix = value ? ` → "${value}"` : '';

      this.element.style.borderLeftColor = color;
      this.element.innerHTML =
        `<span style="color:${color};text-transform:uppercase;font-size:9px;letter-spacing:.08em;">` +
        `#${step.step} ${step.action} · ${humanType}</span><br>` +
        `<span style="color:#f1f5f9;">${userStepPrefix}${label}${valueSuffix}</span>`;
      this.element.style.opacity = '1';

      clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => {
        if (this.element) this.element.style.opacity = '0';
      }, TOAST_DISPLAY_MS);
    }
  }


  /**
   * Renders the draggable on-page control panel: a "current step" label
   * input and a screenshot button. Visible only while recording.
   */
  class ControlPanel {
    /**
     * @param {RecordingState} state
     * @param {ToastNotifier} toast
     */
    constructor(state, toast) {
      this.state = state;
      this.toast = toast;
      this.container = null;
      this.stepInput = null;

      this._drag = { isDragging: false, startX: 0, startY: 0, offsetX: 0, offsetY: 0 };
    }

    /** Builds and inserts the panel DOM (idempotent — no-ops if already created). */
    create() {
      if (this.container) return;

      this.container = this._buildContainer();
      this.stepInput = this._buildStepInputSection();
      const screenshotButton = this._buildScreenshotButton();

      this.container.appendChild(this.stepInput.wrapper);
      this.container.appendChild(screenshotButton);
      document.body.appendChild(this.container);
    }

    /** Shows/hides the panel based on current recording state. */
    updateVisibility() {
      if (this.container) this.container.style.display = this.state.isRecording ? 'flex' : 'none';
    }

    _buildContainer() {
      const container = document.createElement('div');
      container.id = OWN_UI_IDS.panel;
      container.style.cssText = `
        position:fixed;top:20px;right:20px;z-index:2147483647;
        background:#080d14;border:1px solid #1e2d3e;border-radius:8px;padding:12px;
        display:none;flex-direction:column;gap:10px;font-family:'Outfit',sans-serif;
        color:#e2e8f0;box-shadow:0 8px 24px rgba(0,0,0,0.5);width:180px;
        cursor:move;user-select:none;
      `;
      container.addEventListener('mousedown', this._onDragStart.bind(this));
      document.addEventListener('mouseup', this._onDragEnd.bind(this));
      document.addEventListener('mousemove', this._onDrag.bind(this), { passive: true });
      return container;
    }

    _buildStepInputSection() {
      const wrapper = document.createElement('div');
      wrapper.style.cssText = 'display:flex;flex-direction:column;gap:4px;';

      const label = document.createElement('label');
      label.innerText = 'Current Step:';
      label.style.cssText = 'font-size:11px;color:#6b8094;font-weight:600;';

      const input = document.createElement('input');
      input.type = 'text';
      input.id = OWN_UI_IDS.stepInput;
      input.placeholder = 'e.g., Step 1';
      input.setAttribute('autocomplete', 'off');
      input.style.cssText = `
        background:#111e2e;border:1px solid #253545;color:#e2e8f0;
        padding:6px 8px;border-radius:4px;font-size:12px;outline:none;
      `;

      // Prevent keystrokes in the panel's own input from being picked up
      // by the page-wide recording listeners.
      ['keydown', 'keyup', 'keypress', 'input'].forEach((evt) =>
        input.addEventListener(evt, (e) => e.stopPropagation())
      );

      input.addEventListener('change', () => this._onStepLabelChange(input));

      wrapper.appendChild(label);
      wrapper.appendChild(input);
      return { wrapper, input };
    }

    async _onStepLabelChange(input) {
      const value = input.value.trim();
      this.state.userStep = value || null;
      if (!value) return;

      const step = await this.state.addStep(StepFactory.buildStepMarker(value));
      if (step) this.toast.show(step);
    }

    _buildScreenshotButton() {
      const button = document.createElement('button');
      button.innerText = '📷 Screenshot';
      button.style.cssText = `
        background:#253545;border:1px solid #1e2d3e;color:#e2e8f0;
        padding:8px 10px;border-radius:4px;font-size:12px;cursor:pointer;
        font-weight:600;transition:background 0.15s;
      `;
      button.onmouseover = () => { button.style.background = '#1e2d3e'; };
      button.onmouseout = () => { button.style.background = '#253545'; };
      button.addEventListener('click', (e) => this._onScreenshotClick(e));
      return button;
    }

    async _onScreenshotClick(e) {
      e.stopPropagation();
      e.preventDefault();
      const step = await this.state.addStep(StepFactory.buildScreenshotStep());
      if (step) this.toast.show(step);
    }

    _onDragStart(e) {
      if (['INPUT', 'BUTTON'].includes(e.target.tagName)) return;
      this._drag.startX = e.clientX - this._drag.offsetX;
      this._drag.startY = e.clientY - this._drag.offsetY;
      this._drag.isDragging = true;
    }

    _onDragEnd() {
      this._drag.isDragging = false;
    }

    _onDrag(e) {
      if (!this._drag.isDragging) return;
      e.preventDefault();
      this._drag.offsetX = e.clientX - this._drag.startX;
      this._drag.offsetY = e.clientY - this._drag.startY;
      requestAnimationFrame(() => {
        if (this.container) {
          this.container.style.transform = `translate3d(${this._drag.offsetX}px, ${this._drag.offsetY}px, 0)`;
        }
      });
    }
  }


  /**
   * Listens for relevant DOM events (clicks, input, change, blur, keydown)
   * and converts them into steps via StepFactory, applying Veeva-specific
   * classification priority: Tab Collection Menu items > Navbar items >
   * standard dropdown selections > plain clicks.
   */
  class EventRecorder {
    /**
     * @param {RecordingState} state
     * @param {ToastNotifier} toast
     */
    constructor(state, toast) {
      this.state = state;
      this.toast = toast;

      /** Tracks live (uncommitted) values for inputs/contenteditables being typed into. */
      this.liveInputValues = new WeakMap();
      /** Marks inputs whose value was already committed via Enter-keydown, so blur doesn't double-record. */
      this.committedInputs = new WeakSet();

      this._bindEvents();
    }

    /** True if `el` belongs to this extension's own UI (and should never be recorded). */
    _isOwnUiElement(el) {
      return (
        !el ||
        el.id === OWN_UI_IDS.panel ||
        el.closest(`#${OWN_UI_IDS.panel}`) ||
        el.id === OWN_UI_IDS.toast ||
        el.id === OWN_UI_IDS.stepInput
      );
    }

    /** Starts tracking live value changes on an input/contenteditable element. */
    _trackInputValue(el) {
      if (!el || this.liveInputValues.has(el)) return;

      const isContentEditable = !!el.isContentEditable;
      const captureValue = () => {
        try {
          this.liveInputValues.set(el, isContentEditable ? (el.innerText || '') : (el.value || ''));
        } catch {
          // Element may have been detached — safe to ignore.
        }
      };

      captureValue();
      ['input', 'compositionend', 'paste'].forEach((evt) =>
        el.addEventListener(evt, captureValue, { passive: true, capture: true })
      );
    }

    /** Records a step (if successfully built) and shows a toast for it. */
    async _recordStep(step) {
      if (!step) return;
      const added = await this.state.addStep(step);
      if (added) this.toast.show(added);
    }

    /**
     * Main delegated handler for mousedown/click/change/blur. Resolves the
     * effective target (bubbling up to an interactive ancestor for
     * non-form elements), then dispatches to the matching handling branch.
     */
    async _onInteract(e) {
      if (!this.state.isRecording || !e.target || this._isOwnUiElement(e.target)) return;

      let el = e.target;
      const tag = el.tagName.toLowerCase();
      const isFormElement = ['input', 'textarea', 'select'].includes(tag) || el.isContentEditable || el.closest('[contenteditable="true"]');

      if (!isFormElement) {
        const interactiveAncestor = ElementInspector.findInteractiveParent(el);
        if (interactiveAncestor) {
          el = interactiveAncestor;
        } else if (SKIP_TAGS.has(el.tagName.toLowerCase())) {
          return;
        }
      }

      if (e.type === 'blur' && isFormElement) {
        return this._handleBlur(el, e.target);
      }
      if (e.type === 'change' && tag === 'select') {
        return this._recordStep(StepFactory.buildInteractionStep(el, 'change', undefined, e.target));
      }
      if (e.type === 'click' || e.type === 'mousedown') {
        return this._handleClickLike(e, el, tag, isFormElement);
      }
    }

    /** Handles a blur event on a tracked form element, committing its final value. */
    async _handleBlur(el, originalTarget) {
      if (this.committedInputs.has(el)) {
        this.committedInputs.delete(el);
        this.liveInputValues.delete(el);
        return;
      }

      const value = this.liveInputValues.get(el) ?? (el.isContentEditable ? el.innerText : el.value);
      if (!value) return;

      await this._recordStep(StepFactory.buildInteractionStep(el, 'input', value, originalTarget));
      this.liveInputValues.delete(el);
    }

    /**
     * Handles click/mousedown events, applying classification priority:
     * checkbox/radio toggle > Tab Collection Menu item > Navbar item >
     * standard dropdown selection > plain click.
     */
    async _handleClickLike(e, el, tag, isFormElement) {
      if (isFormElement && tag !== 'select') {
        this._trackInputValue(el);
        return;
      }

      if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) {
        if (e.type === 'mousedown') return; // Only record on the resulting click.
        return this._recordStep(StepFactory.buildInteractionStep(el, 'click', el.checked ? 'checked' : 'unchecked', e.target));
      }

      if (await this._handleTabCollectionMenuClick(e, el)) return;
      if (await this._handleNavbarClick(e, el)) return;
      if (await this._handleDropdownSelectionClick(e, el)) return;

      return this._recordStep(StepFactory.buildInteractionStep(el, 'click', undefined, e.target));
    }

    /**
     * Priority 1: Tab Collection Menu items (`data-corgix-internal="MENU-ITEM"`).
     * Checked first so it takes precedence over navbar/dropdown classification.
     * @returns {Promise<boolean>} true if this event was handled here.
     */
    async _handleTabCollectionMenuClick(e, el) {
      const menuItemEl =
        e.target.closest('[data-corgix-internal="MENU-ITEM"]') ||
        (el.getAttribute?.('data-corgix-internal') === 'MENU-ITEM' ? el : null);
      if (!menuItemEl) return false;

      const rawText = TextUtils.clean(menuItemEl.innerText || menuItemEl.textContent || '');
      const menuLabel = (rawText && !TextUtils.isNoisy(rawText)) ? rawText : ElementInspector.resolveLabel(el);
      if (!menuLabel) return false;

      const step = StepFactory.buildInteractionStep(el, 'click', undefined, e.target);
      if (step) {
        step.element.label = menuLabel;
        step.element.context = 'tab_collection_menu';
        step._kb = StepFactory.generateKnowledgeBaseEntry(step);
        await this._recordStep(step);
      }
      return true;
    }

    /**
     * Priority 2: Navbar links — always a plain navigation click.
     * @returns {Promise<boolean>} true if this event was handled here.
     */
    async _handleNavbarClick(e, el) {
      if (!ElementInspector.isNavbarItem(el)) return false;

      const step = StepFactory.buildInteractionStep(el, 'click', undefined, e.target);
      if (step) {
        step.element.context = 'navbar';
        step._kb = StepFactory.generateKnowledgeBaseEntry(step);
        await this._recordStep(step);
      }
      return true;
    }

    /**
     * Priority 3: Clicking an option inside a standard Veeva dropdown/picklist.
     * @returns {Promise<boolean>} true if this event was handled here.
     */
    async _handleDropdownSelectionClick(e, el) {
      if (!ElementInspector.isInsideVeevaDropdown(el)) return false;

      const text = TextUtils.clean(el.innerText || el.textContent || '');
      if (!text || TextUtils.isNoisy(text)) return false;

      const step = StepFactory.buildInteractionStep(el, 'select-click', undefined, e.target);
      if (!step) return false;

      step.action = 'select';
      step.element.selectedValue = text;
      step.dropdownParent =
        ElementInspector.getDropdownContext(el) || step.dropdownParent || ElementInspector.findNearbyLabel(el) || 'dropdown';
      step._kb = StepFactory.generateKnowledgeBaseEntry(step);
      await this._recordStep(step);
      return true;
    }

    /** Handles Enter-keydown inside text inputs/textareas as an immediate "enter" commit. */
    async _onKeyDown(e) {
      if (!this.state.isRecording || !e.target || this._isOwnUiElement(e.target)) return;

      const el = e.target;
      const tag = el.tagName.toLowerCase();
      if (!(['input', 'textarea'].includes(tag) && e.key === 'Enter')) return;
      if (tag === 'input' && ['checkbox', 'radio'].includes(el.type)) return;

      const value = this.liveInputValues.get(el) ?? el.value;
      if (!value) return;

      const step = StepFactory.buildInteractionStep(el, 'input', value, e.target);
      if (!step) return;

      await this._recordStep(step);
      this.committedInputs.add(el);
      this.liveInputValues.delete(el);
    }

    /** True if `target` is a trackable form element (input/textarea/contenteditable), excluding checkbox/radio. */
    _isTrackableFormTarget(target) {
      const tag = (target.tagName || '').toLowerCase();
      const isFormTag = ['input', 'textarea'].includes(tag) || target.isContentEditable;
      return isFormTag && target.type !== 'checkbox' && target.type !== 'radio';
    }

    _bindEvents() {
      const interactHandler = this._onInteract.bind(this);
      ['mousedown', 'click', 'change', 'blur'].forEach((evt) =>
        document.addEventListener(evt, interactHandler, true)
      );
      document.addEventListener('keydown', this._onKeyDown.bind(this), true);

      document.addEventListener('focusin', (e) => {
        if (this.state.isRecording && !this._isOwnUiElement(e.target) && this._isTrackableFormTarget(e.target)) {
          this._trackInputValue(e.target);
        }
      }, true);

      const onGlobalInput = (e) => {
        if (!e.target || this._isOwnUiElement(e.target)) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (!(['input', 'textarea'].includes(tag) || e.target.isContentEditable)) return;

        this._trackInputValue(e.target);
        this.liveInputValues.set(e.target, e.target.isContentEditable ? (e.target.innerText || '') : (e.target.value || ''));
      };
      ['input', 'paste'].forEach((evt) => document.addEventListener(evt, onGlobalInput, true));
    }
  }


  /**
   * Handles messages from the extension's background script / popup
   * (start/stop/restart recording, state queries, and memory store/fetch
   * step injection).
   */
  class RuntimeMessageHandler {
    /**
     * @param {RecordingState} state
     * @param {ToastNotifier} toast
     * @param {ControlPanel} panel
     */
    constructor(state, toast, panel) {
      this.state = state;
      this.toast = toast;
      this.panel = panel;
      this._handlers = {
        START: () => this._setRecording(true),
        STOP: () => this._setRecording(false),
        RESTART: () => this._restart(),
        GET_STATE: () => this._getState(),
        GET_KB: () => this._getKnowledgeBase(),
        MEMORY_STORE: (msg) => this._memoryStore(msg),
        MEMORY_FETCH: (msg) => this._memoryFetch(msg),
      };
    }

    /** Registers this handler with `chrome.runtime.onMessage`. */
    listen() {
      chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
        this._dispatch(message, sendResponse);
        return true; // Keep the message channel open for the async response.
      });
    }

    async _dispatch(message, sendResponse) {
      try {
        const handler = this._handlers[message.type];
        const response = handler ? await handler(message) : undefined;
        sendResponse(response);
      } catch (error) {
        sendResponse({ error: error.message });
      }
    }

    async _setRecording(isRecording) {
      this.state.isRecording = isRecording;
      await this.state.save();
      await this.state.updateBadge();
      this.panel.updateVisibility();
      return { ok: true };
    }

    async _restart() {
      this.state.isRecording = false;
      await this.state.clear();
      this.panel.updateVisibility();
      return { ok: true };
    }

    _getState() {
      return { steps: this.state.steps, isRecording: this.state.isRecording, stepCounter: this.state.stepCounter };
    }

    _getKnowledgeBase() {
      return { kb: this.state.steps.map((s) => s._kb).filter(Boolean) };
    }

    async _memoryStore(msg) {
      const step = await this.state.addStep(StepFactory.buildMemoryStoreStep(msg.varName, msg.value));
      if (step) this.toast.show(step);
      return { ok: true, step };
    }

    async _memoryFetch(msg) {
      const step = await this.state.addStep(StepFactory.buildMemoryFetchStep(msg.varName));
      if (step) this.toast.show(step);
      return { ok: true, step };
    }
  }


  async function init() {
    const state = new RecordingState();
    const toast = new ToastNotifier();
    const panel = new ControlPanel(state, toast);

    await state.load();

    new EventRecorder(state, toast); // eslint-disable-line no-new -- self-registers via DOM listeners
    new RuntimeMessageHandler(state, toast, panel).listen();

    const renderPanel = () => {
      panel.create();
      panel.updateVisibility();
    };

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', renderPanel);
    } else {
      renderPanel();
    }
  }

  init();
})();