
function app() {
  return {
    view: 'dashboard', sidebarOpen: false, configOpen: false, openSelect: null,
    stats: {}, rotationMode: 'round_robin', rotCfg: { mode: 'round_robin', cooldown: 60 },
    accounts: [], rotationAccounts: {}, activeId: '', activeAccount: {},
    models: [],
    auth: { token: '' },
    authEnabled: false,
    toast: { show: false, msg: '', t: null },
    cookieModal: { open: false, cookies: '', name: '', email: '', importing: false, autoProbe: true },
    async init() {
      await this.checkAuth();
      this.loadFromCache();
      this.loadStats();
      this.loadAccounts();
      this.loadRotation();
      this.$watch('auth.token', () => this.saveToCache());
      document.addEventListener('click', () => this.openSelect = null);
    },

    async checkAuth() {
      try {
        const res = await fetch('/auth/check');
        const data = await res.json();
        this.authEnabled = data.auth_enabled;

        if (this.authEnabled) {
          const token = localStorage.getItem('asp_api_token');
          if (!token) {
            window.location.href = '/static/login.html';
            return;
          }
          // 验证 token 是否有效（/stats 受鉴权保护）
          const verifyRes = await fetch('/stats', {
            headers: { 'Authorization': `Bearer ${token}` }
          });
          if (!verifyRes.ok) {
            localStorage.removeItem('asp_api_token');
            window.location.href = '/static/login.html';
            return;
          }
          this.auth.token = token;
        }
      } catch (e) {
        console.error('Auth check failed', e);
      }
    },

    logout() {
      localStorage.removeItem('asp_api_token');
      this.auth.token = '';
      window.location.href = '/static/login.html';
    },
    loadFromCache() {
      try {
        const token = localStorage.getItem('asp_api_token');
        if (token) this.auth.token = token;
      } catch (e) { console.error('Cache load error', e); }
    },
    saveToCache() {
      try {
        if (this.auth.token.trim()) localStorage.setItem('asp_api_token', this.auth.token.trim());
        else localStorage.removeItem('asp_api_token');
      } catch (e) { console.error('Cache save error', e); }
    },
    clearCache() {
      if (!confirm('确定要清理本地缓存吗？')) return;
      localStorage.removeItem('asp_api_token');
      location.reload();
    },
    go(v) { this.view = v; this.sidebarOpen = false; if (v === 'dashboard') this.loadStats(); if (v === 'accounts') { this.loadAccounts(); this.loadRotation() } },

    showToast(m) { this.toast.msg = m; this.toast.show = true; if (this.toast.t) clearTimeout(this.toast.t); this.toast.t = setTimeout(() => this.toast.show = false, 3000) },
    toggleSelect(k, e) { e.stopPropagation(); this.openSelect = this.openSelect === k ? null : k },
    selectOpt(k, model, val) { this[model] = val; this.openSelect = null },
    authHeaders(headers = {}) {
      const next = { ...headers };
      const token = this.auth.token.trim();
      if (token && !next.Authorization && !next.authorization) next.Authorization = `Bearer ${token}`;
      return next;
    },
    async apiFetch(url, options = {}) {
      const res = await fetch(url, { ...options, headers: this.authHeaders(options.headers || {}) });
      if (res.status === 401) this.showToast('鉴权失败，请检查 API Token');
      return res;
    },

    async loadModels() { try { const r = await this.apiFetch('/v1beta/models'); const d = await r.json(); this.models = d.models || d.data || []; if (!this.model && this.models.length) this.model = this.models[0].name || this.models[0].id; this.saveToCache(); } catch (e) { } },
    async loadStats() { try { const r = await this.apiFetch('/stats'); const d = await r.json(); this.stats = d.models || {} } catch (e) { } },
    async loadAccounts() { try { const [a, b] = await Promise.all([this.apiFetch('/accounts').then(r => r.json()), this.apiFetch('/accounts/active').then(r => r.json())]); this.accounts = a || []; this.activeId = b?.id || ''; this.activeAccount = b || {} } catch (e) { } },
    async loadRotation() { try { const r = await this.apiFetch('/rotation'); const d = await r.json(); this.rotationMode = d.mode || 'round_robin'; this.rotCfg.mode = d.mode || 'round_robin'; this.rotCfg.cooldown = d.cooldown_seconds || 60; this.rotationAccounts = d.accounts || {} } catch (e) { } },

    get accountRows() { return this.accounts.map(a => ({ ...a, ...(this.rotationAccounts[a.id] || {}) })) },
    get totalReqs() { return Object.values(this.stats).reduce((s, v) => s + (v.requests || 0), 0) },
    get totalRL() { return Object.values(this.stats).reduce((s, v) => s + (v.rate_limited || 0), 0) },

    async saveRotation() { try { await this.apiFetch('/rotation/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: this.rotCfg.mode, cooldown_seconds: this.rotCfg.cooldown }) }); this.showToast('已保存'); this.loadRotation() } catch (e) { this.showToast('保存失败') } },
    async forceNext() { try { await this.apiFetch('/rotation/next', { method: 'POST' }); this.showToast('已切换账号'); this.loadAccounts() } catch (e) { this.showToast('切换失败') } },
    async activateAccount(id) { try { await this.apiFetch(`/accounts/${id}/activate`, { method: 'POST' }); this.showToast('已激活'); this.loadAccounts(); this.loadRotation() } catch (e) { this.showToast('激活失败') } },
    async importCookies() {
      const raw = this.cookieModal.cookies.trim();
      if (!raw) { this.showToast('请输入 Cookie'); return }
      this.cookieModal.importing = true;
      try {
        if (this.cookieModal.autoProbe) {
          const body = { cookies: raw, name_prefix: this.cookieModal.name.trim() || undefined };
          const r = await this.apiFetch('/accounts/probe-import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
          });
          const d = await r.json();
          if (r.ok) {
            this.showToast(`🎉 成功探测并导入 ${d.imported_count} 个多登录账号`);
            this.cookieModal.open = false; this.cookieModal.cookies = ''; this.cookieModal.name = ''; this.cookieModal.email = '';
            this.loadAccounts(); this.loadRotation();
          } else {
            this.showToast(d.detail || '自动探活导入失败');
          }
        } else {
          const body = { cookies: raw };
          if (this.cookieModal.name.trim()) body.name = this.cookieModal.name.trim();
          if (this.cookieModal.email.trim()) body.email = this.cookieModal.email.trim();
          const r = await this.apiFetch('/accounts/import-cookies', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
          });
          const d = await r.json();
          if (r.ok) {
            this.showToast(`导入成功: ${d.cookie_count} 个 cookie (u/${d.auth_user})`);
            this.cookieModal.open = false; this.cookieModal.cookies = ''; this.cookieModal.name = ''; this.cookieModal.email = '';
            this.loadAccounts(); this.loadRotation();
          } else {
            this.showToast(d.detail || '导入失败');
          }
        }
      } catch (e) {
        this.showToast('网络错误');
      } finally {
        this.cookieModal.importing = false;
      }
    },


    fmtDate(s) { if (!s) return '-'; try { return new Date(s).toLocaleString() } catch (e) { return s } }
  }
}
