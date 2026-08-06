/**
 * Sesharo — Home Assistant sidebar panel (/sesharo).
 *
 * A custom panel registered by the `sesharo` integration (see panel.py). It lets the user see push
 * health, manage the entity → Sesharo signal mapping table, toggle the curated presets, accept
 * auto-derived suggestions, and add a mapping inline — either a brand-new signal or one that already
 * exists in their Sesharo account.
 *
 * Design source: "Handoff: Sesharo panel for Home Assistant". This is a *no-build* ES module: rather
 * than bundling Lit, it extends HTMLElement and composes Home Assistant's own registered elements
 * (ha-card, ha-button, ha-switch, ha-icon, ha-entity-picker, …) plus a little custom DOM for the
 * signal-picker popover. All data comes from the integration's WebSocket commands (sesharo/*) and
 * live entity values from `hass.states`. HA theme tokens (--primary-background-color, …) pierce the
 * shadow boundary, so light/dark just work; the Sesharo cobalt is a local brand custom property.
 */

const BRAND = {
  cobalt: "#2D87F0",
  royal: "#0D5FCC",
  cobalt100: "#E1EEFD",
  cobalt050: "#F0F6FE",
  cloud: "#F5F7FA",
  navy: "7, 36, 107",
  success: "#1F9E6E",
  successBg: "#E6F5EF",
  periwinkle: "#59AAFF",
};

const SLUG_RE = /^[a-z0-9][a-z0-9_]{0,48}$/;

// "mdiWaterPercent" → "mdi:water-percent" so <ha-icon> can resolve it from HA's bundled set.
function mdi(name) {
  if (!name) return "mdi:help-circle-outline";
  const kebab = name.replace(/^mdi/, "").replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase();
  return `mdi:${kebab.replace(/^-/, "")}`;
}

function fmtCount(n) {
  return (n || 0).toLocaleString("en-US");
}

function relTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.round((Date.now() - then) / 1000);
  const ago = secs >= 0;
  const s = Math.abs(secs);
  let out;
  if (s < 45) out = "just now";
  else if (s < 90) out = "a minute";
  else if (s < 3600) out = `${Math.round(s / 60)} min`;
  else if (s < 5400) out = "an hour";
  else if (s < 86400) out = `${Math.round(s / 3600)} hr`;
  else out = `${Math.round(s / 86400)} d`;
  if (out === "just now") return out;
  return ago ? `${out} ago` : `in ${out}`;
}

// Small hyperscript helper: el("div", {class, onclick, ".prop": v}, ...children)
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k.startsWith(".")) node[k.slice(1)] = v; // property, not attribute
      else node.setAttribute(k, v);
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

class SesharoPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null; // {interval, presets_enabled, preset_disabled, preset_excluded, mappings, presets}
    this._status = null;
    this._signals = null; // {metrics:[], events:[]}
    this._suggestions = null; // [candidate,…]
    this._editing = null; // inline add/edit row state
    this._selectedSuggestions = new Set();
    this._expandedPresets = new Set(); // preset signals whose per-sensor list is open
    this._error = null;
    this._loaded = false;
    this._statusTimer = null;
    this._lastRender = 0;
  }

  set hass(hass) {
    const first = this._hass == null;
    this._hass = hass;
    if (first) this._load();
    else if (!this._editing) {
      // Live values (preset match, last-sent) come from hass.states — throttle to ~1/2s and never
      // re-render mid-edit (it would blow away the entity/signal pickers' focus).
      const now = Date.now();
      if (now - this._lastRender > 2000) this._render();
    }
  }
  get hass() {
    return this._hass;
  }

  connectedCallback() {
    this._statusTimer = setInterval(() => this._refreshStatus(), 15000);
  }
  disconnectedCallback() {
    if (this._statusTimer) clearInterval(this._statusTimer);
  }

  // ── data ──────────────────────────────────────────────────────────────
  async _ws(type, extra) {
    return this._hass.callWS({ type, ...(extra || {}) });
  }

  async _load() {
    try {
      this._config = await this._ws("sesharo/get_config");
      this._status = await this._ws("sesharo/status").catch(() => null);
      this._loaded = true;
      this._render();
      // Non-blocking secondary loads.
      this._ws("sesharo/suggestions")
        .then((r) => {
          this._suggestions = r.candidates || [];
          this._selectedSuggestions = new Set(this._suggestions.map((c) => c.entity_id));
          if (!this._editing) this._render();
        })
        .catch(() => {});
    } catch (err) {
      this._error = err && err.message ? err.message : String(err);
      this._loaded = true;
      this._render();
    }
  }

  async _refreshStatus() {
    if (!this._hass || !this._loaded || this._editing) return;
    try {
      this._status = await this._ws("sesharo/status");
      this._render();
    } catch (e) {
      /* transient */
    }
  }

  async _ensureSignals() {
    if (this._signals) return this._signals;
    try {
      this._signals = await this._ws("sesharo/list_signals");
    } catch (e) {
      this._signals = { metrics: [], events: [], _error: e.message || String(e) };
    }
    return this._signals;
  }

  async _saveMappings(mappings) {
    await this._ws("sesharo/set_mappings", { mappings });
    this._config.mappings = mappings;
    this._signals = null; // counts may change
    this._editing = null;
    this._render();
    // The entry reloads; pull fresh status shortly after.
    setTimeout(() => this._refreshStatus(), 1500);
  }

  async _savePresets(enabled, disabled) {
    await this._ws("sesharo/set_presets", {
      presets_enabled: enabled,
      preset_disabled: disabled,
    });
    this._config.presets_enabled = enabled;
    this._config.preset_disabled = disabled;
    this._render();
    setTimeout(() => this._refreshStatus(), 1500);
  }

  async _savePresetExcluded(excluded) {
    await this._ws("sesharo/set_preset_excluded", { preset_excluded: excluded });
    this._config.preset_excluded = excluded;
    this._render();
    setTimeout(() => this._refreshStatus(), 1500);
  }

  async _pushNow(btn) {
    if (btn) btn.setAttribute("loading", "");
    try {
      this._status = await this._ws("sesharo/push_now");
    } catch (e) {
      this._toast(`Push failed: ${e.message || e}`);
    } finally {
      if (btn) btn.removeAttribute("loading");
      this._render();
    }
  }

  _toast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true })
    );
  }

  // ── preset helpers (live from hass.states) ─────────────────────────────
  _presetMatches(preset) {
    const states = Object.values(this._hass.states || {});
    let matched = [];
    if (preset.device_class === "occupancy" && preset.signal === "home_presence") {
      matched = states.filter(
        (s) =>
          s.entity_id.startsWith("person.") ||
          ["occupancy", "presence"].includes(s.attributes.device_class)
      );
    } else {
      matched = states.filter((s) => s.attributes.device_class === preset.device_class);
    }
    return matched;
  }

  _presetLiveValue(preset, matched) {
    const usable = matched.filter((s) => !["unknown", "unavailable", ""].includes(s.state));
    if (!usable.length) return "—";
    if (preset.kind === "event") return `${usable.length} active check`;
    const s = usable[0];
    const unit = s.attributes.unit_of_measurement || "";
    return `${s.state}${unit ? " " + unit : ""}`;
  }

  _presetEnabled(preset) {
    if (!this._config.presets_enabled) return false;
    return !(this._config.preset_disabled || []).includes(preset.device_class);
  }

  _isExcluded(entityId) {
    return (this._config.preset_excluded || []).includes(entityId);
  }

  // Toggle whether a single preset-matched entity is sent (checked = sent). The preset stays on for
  // the rest of its class; excluded entities go into preset_excluded.
  _toggleEntityExclusion(entityId, included) {
    const excluded = new Set(this._config.preset_excluded || []);
    if (included) excluded.delete(entityId);
    else excluded.add(entityId);
    this._savePresetExcluded([...excluded]);
  }

  _togglePresetExpand(signal) {
    if (this._expandedPresets.has(signal)) this._expandedPresets.delete(signal);
    else this._expandedPresets.add(signal);
    this._render();
  }

  // ── render ─────────────────────────────────────────────────────────────
  _render() {
    this._lastRender = Date.now();
    const root = this.shadowRoot;
    root.innerHTML = "";
    root.appendChild(this._styles());

    if (!this._loaded) {
      root.appendChild(this._skeleton());
      return;
    }
    if (this._error) {
      root.appendChild(this._wrap(this._errorCard()));
      return;
    }

    const cfg = this._config;
    const nothingFlowing = (cfg.mappings || []).length === 0 && !cfg.presets_enabled;
    if (nothingFlowing) {
      root.appendChild(this._wrap(this._emptyState()));
      return;
    }

    const content = el("div", { class: "content" });
    content.appendChild(this._statusCard());

    const grid = el("div", { class: "grid" });
    grid.appendChild(this._mappingsCard());
    const rightCol = el("div", { class: "col" });
    rightCol.appendChild(this._presetsCard());
    if (this._suggestions && this._suggestions.length) rightCol.appendChild(this._suggestionsCard());
    rightCol.appendChild(this._footnote());
    grid.appendChild(rightCol);
    content.appendChild(grid);

    root.appendChild(content);
  }

  _wrap(...cards) {
    return el("div", { class: "content narrow-wrap" }, ...cards);
  }

  // ── status card ────────────────────────────────────────────────────────
  _statusCard() {
    const st = this._status || {};
    const dark = this._isDark();
    const connected = st.connected; // true / false / null
    const failing = connected === false;
    const host = (st.base_url || "api.sesharo.com").replace(/^https?:\/\//, "");
    const mark = el("img", {
      class: "mark",
      src: `/sesharo_panel/sesharo-mark-${dark ? "dark" : "light"}.svg`,
      alt: "Sesharo",
    });

    const line1 = el(
      "div",
      { class: "st-line1" },
      el("ha-icon", {
        class: failing ? "ic-error" : "ic-ok",
        icon: failing ? "mdi:alert-circle-outline" : "mdi:check-circle",
      }),
      el("span", {}, failing ? "Couldn't reach " : "Sending to "),
      el("span", { class: "mono host" }, host)
    );
    const mappedCount = st.custom_count != null ? st.custom_count : (this._config.mappings || []).length;
    const failLine =
      st.consecutive_failures > 0
        ? `${st.consecutive_failures} push(es) failing`
        : "nothing failed in the last 24 hours";
    const line2 = el(
      "div",
      { class: "st-line2" },
      `${mappedCount} custom mapping${mappedCount === 1 ? "" : "s"} · ${
        connected == null ? "not pushed yet" : "token valid"
      } · ${failLine}`
    );

    const stats = el(
      "div",
      { class: "stats" },
      this._statBlock("LAST PUSH", relTime(st.last_push)),
      this._statBlock("NEXT PUSH", st.next_push ? relTime(st.next_push) : "—")
    );

    const pushBtn = el(
      "button",
      { class: "cobalt-btn", onclick: (e) => this._pushNow(e.currentTarget), title: "Push now" },
      el("ha-icon", { icon: "mdi:refresh" }),
      el("span", {}, "Push now")
    );

    return el(
      "ha-card",
      { class: "status-card" },
      el(
        "div",
        { class: "status-inner" },
        mark,
        el("div", { class: "st-text" }, line1, line2),
        el("div", { class: "st-right" }, stats, pushBtn)
      )
    );
  }

  _statBlock(label, value) {
    return el(
      "div",
      { class: "stat" },
      el("div", { class: "stat-label" }, label),
      el("div", { class: "stat-value" }, value)
    );
  }

  // ── mappings card ────────────────────────────────────────────────────────
  _mappingsCard() {
    const card = el("ha-card", { class: "mappings-card" });
    card.appendChild(
      el(
        "div",
        { class: "card-head" },
        el(
          "div",
          {},
          el("div", { class: "card-title" }, "Mappings"),
          el(
            "div",
            { class: "card-sub" },
            "Each row is one Home Assistant entity becoming one Sesharo signal."
          )
        )
      )
    );

    const mappings = this._config.mappings || [];
    const table = el("div", { class: "table" });
    table.appendChild(
      el(
        "div",
        { class: "trow thead" },
        el("div", {}, "Entity"),
        el("div", {}, "Sesharo signal"),
        el("div", {}, "Kind"),
        el("div", { class: "right" }, "Last sent"),
        el("div", {})
      )
    );

    if (!mappings.length && !this._editing) {
      table.appendChild(
        el("div", { class: "empty-row" }, "No custom mappings yet — presets are handled below.")
      );
    }

    const lastSent = (this._status && this._status.last_sent) || {};
    for (const m of mappings) {
      table.appendChild(this._mappingRow(m, lastSent[m.entity_id]));
    }

    if (this._editing) table.appendChild(this._editRow());

    card.appendChild(table);
    card.appendChild(
      el(
        "div",
        { class: "table-footer" },
        el(
          "button",
          {
            class: "link-btn",
            onclick: () => this._startAdd(),
            disabled: this._editing ? "" : null,
          },
          el("ha-icon", { icon: "mdi:plus" }),
          el("span", {}, "Map another entity")
        )
      )
    );
    return card;
  }

  _mappingRow(m, sent) {
    const stateObj = this._hass.states[m.entity_id];
    const friendly = (stateObj && stateObj.attributes.friendly_name) || m.display_name || m.entity_id;
    const provenance = m.target_unit ? `joins existing · ${m.signal}` : "custom mapping";
    const value = sent ? sent.value : "—";
    const when = sent ? relTime(sent.at) : "not sent yet";

    return el(
      "div",
      { class: "trow" },
      el(
        "div",
        { class: "cell-entity" },
        el("ha-icon", { class: "ent-ic", icon: this._entityIcon(m.entity_id, stateObj) }),
        el(
          "div",
          { class: "ent-text" },
          el("div", { class: "ent-name" }, friendly),
          el("div", { class: "mono ent-id" }, m.entity_id)
        )
      ),
      el(
        "div",
        { class: "cell-signal" },
        el("div", { class: "mono sig" }, m.signal),
        el("div", { class: "prov" }, provenance)
      ),
      el("div", {}, el("span", { class: "kind-pill" }, m.kind)),
      el(
        "div",
        { class: "right cell-last" },
        el("div", { class: "num" }, value),
        el("div", { class: "when" }, when)
      ),
      el(
        "div",
        { class: "cell-actions" },
        el(
          "button",
          { class: "icon-btn", title: "Remove mapping", onclick: () => this._removeMapping(m.entity_id) },
          el("ha-icon", { icon: "mdi:close" })
        )
      )
    );
  }

  _entityIcon(entityId, stateObj) {
    if (stateObj && stateObj.attributes.icon) return stateObj.attributes.icon;
    const domain = entityId.split(".")[0];
    if (domain === "person") return "mdi:account";
    if (domain === "binary_sensor") return "mdi:radiobox-marked";
    return "mdi:gauge";
  }

  async _removeMapping(entityId) {
    const next = (this._config.mappings || []).filter((m) => m.entity_id !== entityId);
    try {
      await this._saveMappings(next);
      this._toast("Mapping removed");
    } catch (e) {
      this._toast(`Couldn't remove: ${e.message || e}`);
    }
  }

  // ── inline add / edit row ────────────────────────────────────────────────
  _startAdd() {
    this._editing = {
      entity_id: "",
      signal: "",
      kind: "metric",
      unit: "",
      target_unit: null,
      existing: null, // chosen existing signal object (2b)
      pickerOpen: false,
      query: "",
      touchedSignal: false,
    };
    this._ensureSignals();
    this._render();
    // Focus the entity picker.
    requestAnimationFrame(() => {
      const p = this.shadowRoot.querySelector("ha-entity-picker");
      if (p && p.focus) p.focus();
    });
  }

  _editRow() {
    const e = this._editing;
    const row = el("div", { class: "edit-row" });

    // Cell 1 — entity picker
    const picker = el("ha-entity-picker", {
      ".hass": this._hass,
      ".value": e.entity_id,
      ".includeDomains": ["sensor", "binary_sensor", "person"],
      "allow-custom-entity": null,
    });
    picker.addEventListener("value-changed", (ev) => {
      e.entity_id = ev.detail.value || "";
      this._prefillFromEntity();
      this._render();
    });

    // Cell 2 — signal field + popover
    const signalCell = el("div", { class: "sig-cell" });
    const signalField = el("input", {
      class: "field mono",
      type: "text",
      value: e.signal,
      placeholder: "signal_slug",
      disabled: e.existing ? "" : null,
      oninput: (ev) => {
        e.signal = ev.target.value.toLowerCase();
        e.touchedSignal = true;
        e.query = ev.target.value;
        e.existing = null;
        e.target_unit = null;
        this._renderPopover();
        this._syncSaveEnabled();
      },
      onfocus: () => {
        e.pickerOpen = true;
        this._renderPopover();
      },
    });
    signalCell.appendChild(signalField);
    if (e.existing) signalCell.appendChild(el("span", { class: "badge existing-badge" }, "existing"));
    const popoverHost = el("div", { class: "popover-host" });
    signalCell.appendChild(popoverHost);

    // Cell 3 — kind
    const kindSel = el(
      "select",
      {
        class: "field",
        disabled: e.existing ? "" : null,
        onchange: (ev) => {
          e.kind = ev.target.value;
        },
      },
      el("option", { value: "metric", selected: e.kind === "metric" ? "" : null }, "metric"),
      el("option", { value: "event", selected: e.kind === "event" ? "" : null }, "event")
    );

    // Cell 4 — unit
    const unitField = el("input", {
      class: "field mono",
      type: "text",
      value: e.existing ? e.existing.unit || "" : e.unit,
      placeholder: "unit",
      disabled: e.existing ? "" : null,
      oninput: (ev) => {
        e.unit = ev.target.value;
      },
    });

    // Cell 5 — save
    const saveBtn = el(
      "button",
      { class: "save-btn", onclick: () => this._commitEdit(), title: "Save mapping" },
      el("ha-icon", { icon: "mdi:check" })
    );
    saveBtn.disabled = !this._canSave();

    const fields = el(
      "div",
      { class: "edit-fields" },
      el("div", { class: "cell1" }, picker),
      signalCell,
      kindSel,
      unitField,
      el("div", { class: "cell5" }, saveBtn)
    );
    row.appendChild(fields);

    // Explanation / mismatch panel (2b)
    const explain = this._editExplain();
    if (explain) row.appendChild(explain);

    // Cancel
    row.appendChild(
      el(
        "div",
        { class: "edit-actions" },
        el("button", { class: "link-btn plain", onclick: () => this._cancelEdit() }, "Cancel")
      )
    );

    this._saveBtnRef = saveBtn;
    // Draw popover contents after mount so we can measure.
    queueMicrotask(() => this._renderPopover());
    return row;
  }

  _prefillFromEntity() {
    const e = this._editing;
    const s = this._hass.states[e.entity_id];
    if (!s) return;
    const objectId = e.entity_id.split(".").slice(1).join(".");
    const domain = e.entity_id.split(".")[0];
    if (!e.touchedSignal) e.signal = this._slugify(objectId);
    e.kind = domain === "binary_sensor" || domain === "person" ? "event" : "metric";
    e.unit = s.attributes.unit_of_measurement || "";
    e.existing = null;
    e.target_unit = null;
  }

  _slugify(text) {
    return (text || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/_+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 49);
  }

  _canSave() {
    const e = this._editing;
    if (!e || !e.entity_id) return false;
    return SLUG_RE.test(e.signal);
  }
  _syncSaveEnabled() {
    if (this._saveBtnRef) this._saveBtnRef.disabled = !this._canSave();
  }

  _entityUnit() {
    const e = this._editing;
    const s = e && this._hass.states[e.entity_id];
    return s ? s.attributes.unit_of_measurement || "" : "";
  }

  // Explanation panel for the join (2b) + unit-mismatch guard.
  _editExplain() {
    const e = this._editing;
    if (!e.existing) return null;
    const entityUnit = this._entityUnit();
    const sigUnit = e.existing.unit || "";
    const mismatch = entityUnit && sigUnit && entityUnit.toLowerCase() !== sigUnit.toLowerCase();
    const convertible = mismatch ? this._convertible(entityUnit, sigUnit) : true;
    if (!mismatch) {
      return el(
        "div",
        { class: "explain ok" },
        el("ha-icon", { class: "ic-ok", icon: "mdi:check-circle" }),
        el(
          "span",
          {},
          `These readings join `,
          el("span", { class: "mono" }, e.existing.slug),
          `. Kind and unit come from the existing signal, so they're locked${
            sigUnit ? " — " + sigUnit + " either way, nothing to convert." : "."
          }`
        )
      );
    }
    if (convertible) {
      return el(
        "div",
        { class: "explain warn" },
        el("ha-icon", { class: "ic-warn", icon: "mdi:alert-circle-outline" }),
        el(
          "span",
          {},
          `This entity reports ${entityUnit} but `,
          el("span", { class: "mono" }, e.existing.slug),
          ` stores ${sigUnit}. Sesharo will convert each reading to ${sigUnit} before sending.`
        )
      );
    }
    return el(
      "div",
      { class: "explain block" },
      el("ha-icon", { class: "ic-error", icon: "mdi:alert-circle-outline" }),
      el(
        "span",
        {},
        `This entity reports ${entityUnit}, but `,
        el("span", { class: "mono" }, e.existing.slug),
        ` stores ${sigUnit} and there's no known conversion — pick a different signal or a new one.`
      )
    );
  }

  _convertible(fromU, toU) {
    const fam = (u) => {
      u = (u || "").toLowerCase();
      if (["°c", "c", "celsius", "°f", "f", "fahrenheit", "k", "kelvin"].includes(u)) return "temp";
      if (["w", "watt", "watts", "kw", "kilowatt", "mw"].includes(u)) return "power";
      if (["kwh", "wh", "watt-hour", "watt hour", "mwh"].includes(u)) return "energy";
      return null;
    };
    const a = fam(fromU);
    return a != null && a === fam(toU);
  }

  _cancelEdit() {
    this._editing = null;
    this._render();
  }

  async _commitEdit() {
    const e = this._editing;
    if (!this._canSave()) return;
    // Block an inconvertible unit mismatch on a joined signal.
    if (e.existing) {
      const entityUnit = this._entityUnit();
      const sigUnit = e.existing.unit || "";
      if (entityUnit && sigUnit && entityUnit.toLowerCase() !== sigUnit.toLowerCase() && !this._convertible(entityUnit, sigUnit)) {
        this._toast("Units don't match and can't be converted — pick another signal.");
        return;
      }
    }
    const mapping = {
      entity_id: e.entity_id,
      signal: e.signal,
      kind: e.existing ? e.existing.kind : e.kind,
      unit: e.existing ? e.existing.unit || "" : e.unit,
      display_name:
        (this._hass.states[e.entity_id] &&
          this._hass.states[e.entity_id].attributes.friendly_name) ||
        "",
    };
    if (e.existing) mapping.target_unit = e.existing.unit || "";
    const next = (this._config.mappings || []).filter((m) => m.entity_id !== e.entity_id);
    next.push(mapping);
    try {
      await this._saveMappings(next);
      this._toast("Mapping saved");
    } catch (err) {
      this._toast(`Couldn't save: ${err.message || err}`);
    }
  }

  // ── signal picker popover ───────────────────────────────────────────────
  _renderPopover() {
    const e = this._editing;
    if (!e) return;
    const host = this.shadowRoot.querySelector(".popover-host");
    if (!host) return;
    host.innerHTML = "";
    if (!e.pickerOpen) return;

    const pop = el("div", { class: "popover" });
    const q = (e.query || "").toLowerCase();
    const entityUnit = this._entityUnit();

    // Group 1 — new signal from this entity (always survives filtering; slug tracks typing)
    const newSlug = e.signal || this._slugify(e.entity_id.split(".").slice(1).join("."));
    pop.appendChild(
      this._popGroup(
        "NEW SIGNAL FROM THIS ENTITY",
        "Sesharo will create the metric type on the first push.",
        [
          this._popRow({
            slug: newSlug,
            meta: `${e.kind} · ${entityUnit || "no unit"} · from ${e.entity_id}`,
            tail: "new",
            onpick: () => this._pickNew(),
          }),
        ]
      )
    );

    // Group 2 — already in your Sesharo
    const sig = this._signals || { metrics: [], events: [] };
    const existing = [...(sig.metrics || []), ...(sig.events || [])].filter(
      (x) => !q || x.slug.includes(q)
    );
    if (sig._error) {
      pop.appendChild(
        this._popGroup("ALREADY IN YOUR SESHARO", "Couldn't load your signals.", [
          el("div", { class: "pop-note" }, sig._error),
        ])
      );
    } else if (existing.length) {
      const rows = existing.slice(0, 12).map((x) => {
        const count =
          x.kind === "event" ? `${fmtCount(x.entry_count)} entries` : `${fmtCount(x.reading_count)} readings`;
        const src = (x.sources && x.sources.length ? x.sources.join(", ") : "logged by you");
        const unitMatch =
          x.kind !== "event" && entityUnit && x.unit && entityUnit.toLowerCase() === x.unit.toLowerCase();
        return this._popRow({
          slug: x.slug,
          meta: `${x.kind} · ${x.unit || "—"} · ${src}`,
          tail: count,
          badge: unitMatch ? "unit matches" : null,
          onpick: () => this._pickExisting(x),
        });
      });
      pop.appendChild(
        this._popGroup(
          "ALREADY IN YOUR SESHARO",
          "Send into a signal you already track and the readings join it.",
          rows
        )
      );
    }

    pop.appendChild(
      el(
        "div",
        { class: "pop-foot" },
        el("ha-icon", { icon: "mdi:magnify" }),
        el("span", {}, "Keep typing to name a signal of your own — lowercase, numbers and underscores.")
      )
    );

    // Dismiss on outside click.
    setTimeout(() => {
      const off = (ev) => {
        if (!pop.contains(ev.composedPath()[0]) && ev.composedPath()[0] !== this.shadowRoot.querySelector(".sig-cell input")) {
          e.pickerOpen = false;
          host.innerHTML = "";
          document.removeEventListener("click", off, true);
        }
      };
      document.addEventListener("click", off, true);
    }, 0);

    host.appendChild(pop);
  }

  _popGroup(label, note, rows) {
    return el(
      "div",
      { class: "pop-group" },
      el(
        "div",
        { class: "pop-head" },
        el("div", { class: "pop-label" }, label),
        el("div", { class: "pop-note" }, note)
      ),
      ...rows
    );
  }

  _popRow({ slug, meta, tail, badge, onpick }) {
    return el(
      "div",
      { class: "pop-row", onclick: onpick },
      el("div", { class: "pop-main" }, el("div", { class: "mono pop-slug" }, slug), el("div", { class: "pop-meta" }, meta)),
      badge ? el("span", { class: "badge match-badge" }, badge) : el("span", {}),
      el("div", { class: "pop-tail" }, tail || "")
    );
  }

  _pickNew() {
    const e = this._editing;
    e.existing = null;
    e.target_unit = null;
    e.pickerOpen = false;
    if (!e.signal) e.signal = this._slugify(e.entity_id.split(".").slice(1).join("."));
    this._render();
  }

  _pickExisting(x) {
    const e = this._editing;
    e.existing = x;
    e.signal = x.slug;
    e.kind = x.kind;
    e.unit = x.unit || "";
    e.pickerOpen = false;
    this._render();
  }

  // ── presets card ────────────────────────────────────────────────────────
  _presetsCard() {
    const cfg = this._config;
    const card = el("ha-card", { class: "presets-card" });
    const master = el("ha-switch", { ".checked": cfg.presets_enabled });
    master.addEventListener("change", (ev) => {
      this._savePresets(ev.target.checked, cfg.preset_disabled || []);
    });
    card.appendChild(
      el(
        "div",
        { class: "card-head row" },
        el(
          "div",
          {},
          el("div", { class: "card-title small" }, "Presets"),
          el("div", { class: "card-sub" }, "Matched by device class. Expand one to pick which sensors send.")
        ),
        master
      )
    );

    const rows = el("div", { class: cfg.presets_enabled ? "preset-rows" : "preset-rows off" });
    for (const p of cfg.presets || []) {
      const matched = this._presetMatches(p);
      const enabled = this._presetEnabled(p);
      const excludedCount = matched.filter((s) => this._isExcluded(s.entity_id)).length;
      const sentCount = matched.length - excludedCount;
      // Only an enabled preset with >1 sensor is worth drilling into — one sensor is just its switch.
      const expandable = enabled && matched.length > 1;
      const open = this._expandedPresets.has(p.signal);

      const countText =
        excludedCount > 0
          ? `${sentCount} of ${matched.length} sensor${matched.length === 1 ? "" : "s"}`
          : `${matched.length} sensor${matched.length === 1 ? "" : "s"}`;

      const sw = el("ha-switch", { ".checked": enabled, disabled: cfg.presets_enabled ? null : "" });
      sw.addEventListener("change", (ev) => this._togglePreset(p, ev.target.checked));

      const chevron = expandable
        ? el(
            "button",
            {
              class: "preset-expand",
              title: open ? "Hide sensors" : "Choose sensors",
              onclick: () => this._togglePresetExpand(p.signal),
            },
            el("ha-icon", { icon: open ? "mdi:chevron-up" : "mdi:chevron-down" })
          )
        : el("div", { class: "preset-expand-spacer" });

      rows.appendChild(
        el(
          "div",
          { class: "preset-row" },
          el("ha-icon", { class: "preset-ic", icon: mdi(p.icon) }),
          el(
            "div",
            { class: "preset-text" },
            el("div", { class: "preset-label" }, p.label),
            el("div", { class: "mono preset-sig" }, `${p.signal} · ${countText}`)
          ),
          el("div", { class: "preset-val" }, this._presetLiveValue(p, matched)),
          chevron,
          sw
        )
      );
      if (expandable && open) rows.appendChild(this._presetSensorList(p, matched));
    }
    card.appendChild(rows);
    return card;
  }

  // The per-sensor include/exclude checklist shown under an expanded preset. Every matched sensor is
  // sent by default (checked); unchecking one drops just that sensor while the preset stays on.
  _presetSensorList(preset, matched) {
    const list = el("div", { class: "preset-sensors" });
    const sorted = [...matched].sort((a, b) => {
      const an = (a.attributes.friendly_name || a.entity_id).toLowerCase();
      const bn = (b.attributes.friendly_name || b.entity_id).toLowerCase();
      return an.localeCompare(bn);
    });
    for (const s of sorted) {
      const included = !this._isExcluded(s.entity_id);
      const friendly = s.attributes.friendly_name || s.entity_id;
      const unit = s.attributes.unit_of_measurement || "";
      const value = ["unknown", "unavailable", ""].includes(s.state)
        ? "—"
        : `${s.state}${unit ? " " + unit : ""}`;
      const cb = el("ha-checkbox", { ".checked": included });
      cb.addEventListener("change", (ev) =>
        this._toggleEntityExclusion(s.entity_id, ev.target.checked)
      );
      list.appendChild(
        el(
          "div",
          { class: included ? "sensor-row" : "sensor-row excluded" },
          cb,
          el(
            "div",
            { class: "sensor-text" },
            el("div", { class: "sensor-name" }, friendly),
            el("div", { class: "mono sensor-id" }, s.entity_id)
          ),
          el("div", { class: "sensor-val" }, value)
        )
      );
    }
    return list;
  }

  _togglePreset(preset, on) {
    const disabled = new Set(this._config.preset_disabled || []);
    if (on) disabled.delete(preset.device_class);
    else disabled.add(preset.device_class);
    this._savePresets(this._config.presets_enabled, [...disabled]);
  }

  // ── suggestions card ─────────────────────────────────────────────────────
  _suggestionsCard() {
    const card = el("ha-card", { class: "suggest-card" });
    const items = this._suggestions;
    card.appendChild(
      el(
        "div",
        { class: "card-head" },
        el("div", { class: "card-title small" }, "Worth tracking"),
        el(
          "div",
          { class: "card-sub" },
          `${items.length} entit${items.length === 1 ? "y" : "ies"} no preset covers. Names and units are filled in for you.`
        )
      )
    );
    const rows = el("div", { class: "suggest-rows" });
    for (const c of items) {
      const cb = el("ha-checkbox", { ".checked": this._selectedSuggestions.has(c.entity_id) });
      cb.addEventListener("change", (ev) => {
        if (ev.target.checked) this._selectedSuggestions.add(c.entity_id);
        else this._selectedSuggestions.delete(c.entity_id);
        this._updateSuggestBtn();
      });
      rows.appendChild(
        el(
          "div",
          { class: "suggest-row" },
          cb,
          el(
            "div",
            { class: "suggest-text" },
            el("div", { class: "mono" }, c.entity_id),
            el(
              "div",
              { class: "suggest-meta" },
              `→ ${c.signal} · ${c.kind}${c.unit ? " · " + c.unit : ""}`
            )
          )
        )
      );
    }
    card.appendChild(rows);
    const n = this._selectedSuggestions.size;
    const trackBtn = el(
      "button",
      { class: "cobalt-btn small", onclick: () => this._acceptSuggestions() },
      el("span", { class: "track-label" }, `Track ${n === items.length ? "all " : ""}${n}`)
    );
    this._trackBtnRef = trackBtn;
    card.appendChild(
      el(
        "div",
        { class: "suggest-foot" },
        trackBtn,
        el("button", { class: "link-btn plain", onclick: () => this._dismissSuggestions() }, "Not now")
      )
    );
    return card;
  }

  _updateSuggestBtn() {
    if (!this._trackBtnRef) return;
    const n = this._selectedSuggestions.size;
    const label = this._trackBtnRef.querySelector(".track-label");
    if (label) label.textContent = `Track ${n === (this._suggestions || []).length ? "all " : ""}${n}`;
    this._trackBtnRef.disabled = n === 0;
  }

  async _acceptSuggestions() {
    const chosen = this._suggestions.filter((c) => this._selectedSuggestions.has(c.entity_id));
    if (!chosen.length) return;
    const existing = this._config.mappings || [];
    const merged = [...existing.filter((m) => !this._selectedSuggestions.has(m.entity_id))];
    for (const c of chosen) {
      merged.push({
        entity_id: c.entity_id,
        signal: c.signal,
        kind: c.kind,
        unit: c.unit || "",
        display_name: c.display_name || "",
      });
    }
    try {
      await this._saveMappings(merged);
      this._suggestions = (this._suggestions || []).filter(
        (c) => !this._selectedSuggestions.has(c.entity_id)
      );
      this._toast(`Tracking ${chosen.length} more`);
    } catch (e) {
      this._toast(`Couldn't add: ${e.message || e}`);
    }
  }

  _dismissSuggestions() {
    this._suggestions = [];
    this._render();
  }

  // ── empty state (1c) ─────────────────────────────────────────────────────
  _emptyState() {
    const dark = this._isDark();
    const hero = el(
      "ha-card",
      { class: "hero" },
      el("img", { class: "mark-lg", src: `/sesharo_panel/sesharo-mark-${dark ? "dark" : "light"}.svg`, alt: "Sesharo" }),
      el("div", { class: "hero-head" }, "Nothing is going to Sesharo yet"),
      el(
        "div",
        { class: "hero-body" },
        "Your token works. Now pick what your house should tell Sesharo about your health — air quality, temperature, energy, presence. Capture first; the correlations come later."
      ),
      el(
        "div",
        { class: "hero-actions" },
        el(
          "button",
          { class: "cobalt-btn", onclick: () => this._savePresets(true, []) },
          el("span", {}, "Turn on the 9 presets"),
          el("ha-icon", { icon: "mdi:arrow-right" })
        ),
        el("button", { class: "outlined-btn", onclick: () => { this._config.presets_enabled = true; this._render(); this._startAdd(); } }, "Map one entity myself")
      )
    );
    return hero;
  }

  _footnote() {
    return el(
      "div",
      { class: "footnote" },
      el("ha-icon", { icon: "mdi:alert-circle-outline" }),
      el(
        "span",
        {},
        `Pushing every ${Math.round((this._config.interval || 300) / 60)} minutes. This integration only exports — it creates no entities here. The picture gets connected in Sesharo.`
      )
    );
  }

  _errorCard() {
    return el(
      "ha-card",
      { class: "hero" },
      el("div", { class: "hero-head" }, "Couldn't load the panel"),
      el("div", { class: "hero-body" }, this._error),
      el(
        "div",
        { class: "hero-actions" },
        el("button", { class: "cobalt-btn", onclick: () => { this._error = null; this._loaded = false; this._render(); this._load(); } }, "Retry")
      )
    );
  }

  _skeleton() {
    return el(
      "div",
      { class: "content" },
      el("ha-card", { class: "skeleton" }),
      el("ha-card", { class: "skeleton tall" })
    );
  }

  _isDark() {
    // HA sets --primary-background-color dark; also expose via hass.themes.darkMode when present.
    if (this._hass && this._hass.themes && typeof this._hass.themes.darkMode === "boolean")
      return this._hass.themes.darkMode;
    return matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // ── styles ───────────────────────────────────────────────────────────────
  _styles() {
    const s = document.createElement("style");
    s.textContent = `
      :host {
        --cobalt: ${BRAND.cobalt}; --royal: ${BRAND.royal}; --cobalt-100: ${BRAND.cobalt100};
        --cobalt-050: ${BRAND.cobalt050}; --cloud: ${BRAND.cloud};
        --s-success: ${BRAND.success}; --s-success-bg: ${BRAND.successBg}; --periwinkle: ${BRAND.periwinkle};
        display: block; background: var(--primary-background-color); min-height: 100vh;
        --mono: var(--ha-font-family-code, ui-monospace, SFMono-Regular, Menlo, monospace);
      }
      .content { padding: 16px; max-width: 1040px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
      .grid { display: grid; grid-template-columns: 1fr 380px; gap: 16px; align-items: start; }
      .col { display: flex; flex-direction: column; gap: 16px; }
      @media (max-width: 1000px) { .grid { grid-template-columns: 1fr; } }
      .mono { font-family: var(--mono); }
      ha-card { display: block; }

      /* status */
      .status-inner { display: flex; align-items: center; gap: 24px; padding: 20px 24px; }
      .mark { width: 44px; height: 44px; flex: none; }
      .st-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
      .st-line1 { display: flex; align-items: center; gap: 6px; font-size: 16px; color: var(--primary-text-color); }
      .st-line1 .host { font-size: 14px; }
      .st-line1 ha-icon { --mdc-icon-size: 16px; }
      .ic-ok { color: var(--success-color); }
      .ic-error { color: var(--error-color); }
      .ic-warn { color: var(--warning-color); }
      .st-line2 { font-size: 13px; color: var(--secondary-text-color); line-height: 1.6; }
      .st-right { margin-left: auto; display: flex; align-items: center; gap: 32px; }
      .stats { display: flex; gap: 32px; }
      .stat { text-align: right; }
      .stat-label { font-family: var(--ha-font-family-body); font-weight: 100; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--secondary-text-color); }
      .stat-value { font-size: 15px; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }

      .cobalt-btn { display: inline-flex; align-items: center; gap: 8px; height: 40px; padding: 0 16px; border: none; border-radius: 9999px; background: var(--cobalt); color: #fff; font-size: 14px; font-weight: 500; cursor: pointer; transition: background 160ms cubic-bezier(.4,0,.2,1); }
      .cobalt-btn:hover { background: var(--royal); }
      .cobalt-btn[loading] { opacity: .7; pointer-events: none; }
      .cobalt-btn.small { height: 34px; font-size: 13px; }
      .cobalt-btn ha-icon { --mdc-icon-size: 18px; }
      .outlined-btn { height: 40px; padding: 0 16px; border-radius: 9999px; background: transparent; border: 1px solid var(--divider-color); color: var(--primary-text-color); font-size: 14px; cursor: pointer; }

      /* cards */
      .card-head { padding: 12px 16px 16px; }
      .card-head.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
      .card-title { font-size: 24px; letter-spacing: -0.012em; color: var(--primary-text-color); }
      .card-title.small { font-size: 20px; }
      .card-sub { font-size: 13px; color: var(--secondary-text-color); margin-top: 2px; }

      /* mappings table */
      .table { }
      .trow { display: grid; grid-template-columns: 1.5fr 1.2fr 88px 1fr 40px; gap: 12px; padding: 10px 16px; min-height: 56px; align-items: center; border-bottom: 1px solid var(--divider-color); }
      .trow:hover { background: var(--ha-color-fill-neutral-quiet-resting, rgba(0,0,0,.03)); }
      .thead { min-height: 0; padding: 8px 16px; font-size: 13px; font-weight: 500; color: var(--secondary-text-color); }
      .thead:hover { background: none; }
      .right { text-align: right; justify-self: end; }
      .empty-row { padding: 18px 16px; color: var(--secondary-text-color); font-size: 13px; border-bottom: 1px solid var(--divider-color); }
      .cell-entity { display: flex; align-items: center; gap: 10px; min-width: 0; }
      .ent-ic { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: none; }
      .ent-text { min-width: 0; }
      .ent-name { font-size: 14px; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .ent-id { font-size: 12px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .cell-signal { min-width: 0; }
      .sig { font-size: 13px; color: var(--royal); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .prov { font-size: 12px; color: var(--secondary-text-color); }
      .kind-pill { display: inline-block; height: 20px; line-height: 20px; padding: 0 8px; border-radius: 9999px; background: var(--cobalt-100); color: var(--royal); font-size: 10px; font-weight: 300; text-transform: uppercase; letter-spacing: 1.2px; }
      .cell-last .num { font-size: 15px; font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
      .cell-last .when { font-size: 12px; color: var(--secondary-text-color); }
      .cell-actions { justify-self: end; }
      .icon-btn { border: none; background: none; cursor: pointer; color: var(--secondary-text-color); border-radius: 50%; width: 32px; height: 32px; }
      .icon-btn:hover { background: var(--ha-color-fill-neutral-quiet-resting, rgba(0,0,0,.06)); }
      .icon-btn ha-icon { --mdc-icon-size: 20px; }
      .table-footer { border-top: 1px solid var(--divider-color); padding: 8px; }
      .link-btn { display: inline-flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px; border: none; background: none; color: var(--cobalt); font-size: 14px; font-weight: 500; cursor: pointer; border-radius: 8px; }
      .link-btn.plain { color: var(--secondary-text-color); font-weight: 400; }
      .link-btn[disabled] { opacity: .4; pointer-events: none; }

      /* edit row */
      .edit-row { background: var(--cobalt-050); border-left: 3px solid var(--cobalt); padding: 12px 13px 14px 16px; display: flex; flex-direction: column; gap: 10px; }
      .edit-fields { display: grid; grid-template-columns: 1.3fr 1.2fr 80px 90px 40px; gap: 12px; align-items: start; }
      .field { height: 44px; border-radius: 12px; background: var(--card-background-color); border: 1px solid var(--divider-color); color: var(--primary-text-color); padding: 0 12px; font-size: 13px; width: 100%; box-sizing: border-box; }
      .field:focus { outline: none; border: 2px solid var(--cobalt); box-shadow: 0 0 0 3px var(--cobalt-100); }
      .field[disabled] { background: var(--cloud); color: #9aa1ad; }
      .sig-cell { position: relative; }
      .sig-cell input { width: 100%; }
      .badge { display: inline-block; height: 18px; line-height: 18px; padding: 0 7px; border-radius: 9999px; font-size: 10px; font-weight: 300; text-transform: uppercase; letter-spacing: .06em; margin-top: 4px; }
      .existing-badge { background: var(--cobalt-100); color: var(--royal); }
      .match-badge { background: var(--s-success-bg); color: var(--s-success); align-self: center; }
      .cell5 { display: flex; align-items: center; height: 44px; }
      .save-btn { width: 36px; height: 36px; border-radius: 50%; border: none; background: var(--cobalt); color: #fff; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
      .save-btn[disabled] { background: var(--cobalt-100); color: #9ec7f7; cursor: default; }
      .save-btn ha-icon { --mdc-icon-size: 18px; }
      .edit-actions { display: flex; justify-content: flex-end; }
      .explain { display: flex; gap: 10px; align-items: flex-start; background: var(--card-background-color); border: 1px solid var(--cobalt-100); border-radius: 12px; padding: 12px 14px; font-size: 13px; line-height: 1.7; color: var(--primary-text-color); }
      .explain ha-icon { --mdc-icon-size: 18px; flex: none; margin-top: 1px; }
      .explain.warn { border-color: var(--warning-color); }
      .explain.block { border-color: var(--error-color); }

      /* popover */
      .popover-host { position: relative; }
      .popover { position: absolute; top: 4px; left: 0; width: max(320px, 140%); z-index: 30; background: var(--card-background-color); border: 1px solid var(--divider-color); border-radius: 12px; box-shadow: 0 8px 24px rgba(${BRAND.navy}, 0.16); overflow: hidden; max-height: 380px; overflow-y: auto; }
      .pop-head { position: sticky; top: 0; background: var(--cloud); padding: 12px 14px 8px; border-bottom: 1px solid var(--divider-color); }
      .pop-label { font-family: var(--ha-font-family-body); font-weight: 100; font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: var(--secondary-text-color); }
      .pop-note { font-size: 12px; color: var(--secondary-text-color); margin-top: 2px; }
      .pop-row { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; align-items: center; min-height: 52px; padding: 9px 14px; cursor: pointer; }
      .pop-row:hover { background: var(--cobalt-100); }
      .pop-slug { font-size: 13px; color: var(--royal); }
      .pop-meta { font-size: 12px; color: var(--secondary-text-color); }
      .pop-tail { font-size: 12px; color: var(--secondary-text-color); text-align: right; }
      .pop-foot { display: flex; gap: 8px; align-items: center; padding: 10px 14px; font-size: 12px; color: var(--secondary-text-color); border-top: 1px solid var(--divider-color); }
      .pop-foot ha-icon { --mdc-icon-size: 16px; }

      /* presets */
      .preset-rows.off { opacity: .45; pointer-events: none; }
      .preset-row { display: grid; grid-template-columns: 20px 1fr auto 32px auto; gap: 12px; align-items: center; min-height: 48px; padding: 8px 16px; border-top: 1px solid var(--divider-color); }
      .preset-ic { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
      .preset-label { font-size: 14px; color: var(--primary-text-color); }
      .preset-sig { font-size: 12px; color: var(--secondary-text-color); }
      .preset-val { font-size: 14px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .preset-expand { border: none; background: none; cursor: pointer; color: var(--secondary-text-color); border-radius: 50%; width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; }
      .preset-expand:hover { background: var(--ha-color-fill-neutral-quiet-resting, rgba(0,0,0,.06)); color: var(--primary-text-color); }
      .preset-expand ha-icon { --mdc-icon-size: 20px; }
      .preset-expand-spacer { width: 32px; }

      /* preset per-sensor checklist */
      .preset-sensors { padding: 4px 16px 10px 48px; background: var(--secondary-background-color); border-top: 1px solid var(--divider-color); }
      .sensor-row { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; min-height: 40px; }
      .sensor-text { min-width: 0; }
      .sensor-name { font-size: 13px; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .sensor-id { font-size: 11px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .sensor-val { font-size: 13px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .sensor-row.excluded .sensor-name, .sensor-row.excluded .sensor-val { color: var(--secondary-text-color); text-decoration: line-through; opacity: .7; }

      /* suggestions */
      .suggest-row { display: flex; gap: 8px; align-items: center; min-height: 52px; padding: 4px 16px; border-top: 1px solid var(--divider-color); }
      .suggest-text { min-width: 0; }
      .suggest-text .mono { font-size: 13px; color: var(--primary-text-color); }
      .suggest-meta { font-size: 12px; color: var(--secondary-text-color); }
      .suggest-foot { display: flex; gap: 12px; align-items: center; padding: 12px 16px; }

      /* footnote */
      .footnote { display: flex; gap: 10px; align-items: flex-start; padding: 14px 16px; border-radius: 12px; background: var(--secondary-background-color); font-size: 13px; line-height: 1.7; color: var(--secondary-text-color); }
      .footnote ha-icon { --mdc-icon-size: 18px; flex: none; margin-top: 1px; }

      /* empty / hero */
      .hero { padding: 40px 40px 32px; text-align: center; display: flex; flex-direction: column; align-items: center; gap: 14px; max-width: 720px; margin: 24px auto 0; }
      .mark-lg { width: 64px; height: 64px; }
      .hero-head { font-family: var(--ha-font-family-body); font-weight: 200; font-size: 28px; line-height: 1.2; color: var(--primary-text-color); }
      .hero-body { font-size: 15px; line-height: 1.7; color: var(--secondary-text-color); max-width: 460px; }
      .hero-actions { display: flex; gap: 12px; flex-wrap: wrap; justify-content: center; margin-top: 6px; }

      .skeleton { height: 96px; background: var(--card-background-color); opacity: .6; }
      .skeleton.tall { height: 320px; }
    `;
    return s;
  }
}

if (!customElements.get("sesharo-panel")) {
  customElements.define("sesharo-panel", SesharoPanel);
}
