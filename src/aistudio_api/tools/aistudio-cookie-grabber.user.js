// ==UserScript==
// @name         Google AI Studio 多账号一键抓取与同步工具
// @namespace    https://github.com/DeconstructedCube/aistudio-api
// @version      2.0.0
// @description  在 Google AI Studio 网页上一键自动抓取浏览器登录的全部账号 Cookie、authUser 索引与权益等级，并支持一键直接同步到本地 aistudio-api 网关服务。
// @author       omp & Lianues
// @match        https://aistudio.google.com/*
// @match        https://aistudio.google.com*
// @match        http://aistudio.google.com/*
// @grant        GM_cookie
// @grant        GM_setClipboard
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// @connect      google.com
// @connect      aistudio.google.com
// @connect      accounts.google.com
// @connect      ogs.google.com
// @connect      alkalimakersuite-pa.clients6.google.com
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const WHITELIST = new Set([
    'SID', 'HSID', 'SSID', 'APISID', 'SAPISID',
    '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID', '__Secure-3PAPISID',
    '__Secure-1PSIDTS', '__Secure-3PSIDTS', 'SIDCC', '__Secure-1PSIDCC', '__Secure-3PSIDCC',
    'LSID', '__Host-1PLSID', '__Host-3PLSID', 'ACCOUNT_CHOOSER', '__Host-GAPS', 'NID',
  ]);

  const DEFAULT_ORIGIN = 'https://aistudio.google.com';
  const DEFAULT_API_KEY = 'AIzaSyDdP816MREB3SkjZO04QXbjsigfcI0GWOs';
  const DEFAULT_USER_AGENT = 'grpc-web-javascript/0.1';
  const DEFAULT_EXT_BIN = 'CAASAUIwATgEQABQBFgDYgJKUHAAeAGQAQCYAQE=';

  function sha1(str) {
    const buffer = new TextEncoder().encode(str);
    return crypto.subtle.digest('SHA-1', buffer).then(b => {
      return Array.from(new Uint8Array(b)).map(x => x.toString(16).padStart(2, '0')).join('');
    });
  }

  async function calculateSapisidHash(cookieMap, origin = DEFAULT_ORIGIN) {
    const sapisid = cookieMap.get('SAPISID') || cookieMap.get('__Secure-3PAPISID') || cookieMap.get('APISID');
    if (!sapisid) return '';
    const ts = Math.floor(Date.now() / 1000);
    const parts = [`SAPISIDHASH ${ts}_${await sha1(`${ts} ${sapisid} ${origin}`)}`];
    const one = cookieMap.get('__Secure-1PAPISID');
    if (one) parts.push(`SAPISID1PHASH ${ts}_${await sha1(`${ts} ${one} ${origin}`)}`);
    const three = cookieMap.get('__Secure-3PAPISID');
    if (three && three !== sapisid) parts.push(`SAPISID3PHASH ${ts}_${await sha1(`${ts} ${three} ${origin}`)}`);
    return parts.join(' ');
  }

  function listCookiesByUrl(url) {
    return new Promise(resolve => {
      if (!GM_cookie || typeof GM_cookie.list !== 'function') {
        return resolve({ cookies: [], error: 'GM_cookie.list API 不可用，请确保使用 Tampermonkey 并开启 Cookie All 权限' });
      }
      GM_cookie.list({ url }, (cookies, error) => {
        resolve({ cookies: Array.isArray(cookies) ? cookies : [], error: error ? String(error) : null });
      });
    });
  }

  async function grabAllCookies() {
    const [aisRes, accRes] = await Promise.all([
      listCookiesByUrl('https://aistudio.google.com/'),
      listCookiesByUrl('https://accounts.google.com/'),
    ]);
    const mergedMap = new Map();
    const httpOnlyNames = [];
    for (const c of [...aisRes.cookies, ...accRes.cookies]) {
      if (c && c.name && c.value && WHITELIST.has(c.name)) {
        mergedMap.set(c.name, c.value);
        if (c.httpOnly && !httpOnlyNames.includes(c.name)) {
          httpOnlyNames.push(c.name);
        }
      }
    }
    const cookieHeader = Array.from(mergedMap.entries()).map(([k, v]) => `${k}=${v}`).join('; ');
    return {
      map: mergedMap,
      header: cookieHeader,
      count: mergedMap.size,
      httpOnlyCount: httpOnlyNames.length,
      hasLsid: mergedMap.has('LSID') || mergedMap.has('__Host-1PLSID') || mergedMap.has('__Host-3PLSID'),
    };
  }

  function parseEmailsFromOgsHtml(html) {
    const mapping = {};
    if (!html) return mapping;
    const mPrimary = html.match(/"oPEP7c"\s*:\s*"([^"@\s]+@[^"\s]+)"/);
    if (mPrimary) mapping['0'] = mPrimary[1].toLowerCase().trim();

    const auEmailRegex = /data-au="(\d+)"\s+data-email="([^"@\s]+@[^"\s]+)"/g;
    let m;
    while ((m = auEmailRegex.exec(html)) !== null) {
      mapping[m[1]] = m[2].toLowerCase().trim();
    }
    const emailAuRegex = /data-email="([^"@\s]+@[^"\s]+)"\s+data-au="(\d+)"/g;
    while ((m = emailAuRegex.exec(html)) !== null) {
      mapping[m[2]] = m[1].toLowerCase().trim();
    }
    return mapping;
  }

  function fetchOgsAccountWidget(cookieHeader) {
    return new Promise(resolve => {
      GM_xmlhttpRequest({
        method: 'GET',
        url: 'https://ogs.google.com/u/0/widget/account',
        headers: {
          'Cookie': cookieHeader,
          'User-Agent': navigator.userAgent,
        },
        onload: resp => {
          if (resp.status === 200) {
            resolve(parseEmailsFromOgsHtml(resp.responseText));
          } else {
            resolve({});
          }
        },
        onerror: () => resolve({}),
      });
    });
  }

  async function probeBenefitTier(cookieMap, cookieHeader, authUser) {
    const authHeader = await calculateSapisidHash(cookieMap);
    return new Promise(resolve => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: 'https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GetAiStudioBenefitTier',
        headers: {
          'Content-Type': 'application/json+protobuf',
          'X-User-Agent': DEFAULT_USER_AGENT,
          'X-Goog-Api-Key': DEFAULT_API_KEY,
          'X-Goog-AuthUser': String(authUser),
          'X-Goog-Ext-519733851-bin': DEFAULT_EXT_BIN,
          'Origin': DEFAULT_ORIGIN,
          'Referer': `${DEFAULT_ORIGIN}/`,
          'Cookie': cookieHeader,
          'Authorization': authHeader,
        },
        data: '[]',
        onload: resp => {
          if (resp.status === 200) {
            try {
              const text = resp.responseText.replace(/^\)\]\}'/, '').trim();
              const arr = JSON.parse(text);
              const idx = Array.isArray(arr) && arr[0] !== null ? Number(arr[0]) : 0;
              const map = { 1: 'Pro', 2: 'Ultra', 3: 'Plus' };
              const tier = map[idx] || 'Free';
              const g1Tier = idx === 1 ? 'TIER1' : (idx === 2 ? 'TIER2' : 'TIER0');
              resolve({ ok: true, tier, g1Tier });
            } catch (e) {
              resolve({ ok: true, tier: 'Free', g1Tier: 'TIER0' });
            }
          } else {
            resolve({ ok: false, status: resp.status, tier: 'Free', g1Tier: 'TIER0' });
          }
        },
        onerror: err => resolve({ ok: false, error: err, tier: 'Free', g1Tier: 'TIER0' }),
      });
    });
  }

  async function discoverAllAccounts() {
    const cookies = await grabAllCookies();
    if (cookies.count === 0) {
      throw new Error('未检测到任何有效 Cookie，请在 Tampermonkey 高级设置中将「脚本 Cookie 访问权限」改为 All');
    }

    const emailMap = await fetchOgsAccountWidget(cookies.header);
    const discoveredIndices = Object.keys(emailMap).map(Number);
    if (!discoveredIndices.includes(0)) discoveredIndices.push(0);
    discoveredIndices.sort((a, b) => a - b);

    const accountItems = [];
    for (const u of discoveredIndices) {
      const email = emailMap[String(u)] || `user_${u}@gmail.com`;
      const tierInfo = await probeBenefitTier(cookies.map, cookies.header, u);
      const name = email.includes('@') ? `Google (${email.split('@')[0]})` : `Google Account (u/${u})`;
      accountItems.push({
        name,
        email,
        auth_user: String(u),
        authUser: u,
        tier: tierInfo.tier,
        g1Tier: tierInfo.g1Tier,
        cookies: cookies.header,
      });
    }

    return {
      bundle: true,
      total: accountItems.length,
      cookieCount: cookies.count,
      httpOnlyCount: cookies.httpOnlyCount,
      hasLsid: cookies.hasLsid,
      accounts: accountItems,
    };
  }

  function postToLocalGateway(payload, port = 8080) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: `http://127.0.0.1:${port}/api/accounts/import-bundle`,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify(payload),
        onload: resp => {
          if (resp.status >= 200 && resp.status < 300) {
            resolve(JSON.parse(resp.responseText));
          } else {
            reject(new Error(`HTTP ${resp.status}: ${resp.responseText.slice(0, 200)}`));
          }
        },
        onerror: () => reject(new Error(`无法连接到本地服务 http://127.0.0.1:${port}，请确保 aistudio-api 已启动`)),
      });
    });
  }

  function createFloatingUI() {
    const container = document.createElement('div');
    container.id = 'aistudio-cookie-grabber-panel';
    Object.assign(container.style, {
      position: 'fixed',
      bottom: '24px',
      right: '24px',
      zIndex: '2147483647',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      fontSize: '13px',
      boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
      padding: '12px',
      borderRadius: '16px',
      background: 'rgba(255, 255, 255, 0.95)',
      backdropFilter: 'blur(10px)',
      border: '1px solid rgba(0, 0, 0, 0.08)',
    });

    const title = document.createElement('div');
    title.innerHTML = '⚡ <b>AI Studio 多账号同步助手</b>';
    title.style.color = '#1f2937';
    title.style.marginBottom = '4px';

    const statusText = document.createElement('div');
    statusText.style.color = '#6b7280';
    statusText.style.fontSize = '11px';
    statusText.textContent = '就绪：支持一键自动抓取并同步全部子账号';

    const btnSyncAll = document.createElement('button');
    btnSyncAll.innerHTML = '🚀 <b>一键自动同步全部账号到本地</b>';
    Object.assign(btnSyncAll.style, {
      background: '#2563eb',
      color: '#fff',
      border: 'none',
      padding: '8px 14px',
      borderRadius: '8px',
      cursor: 'pointer',
      fontWeight: '600',
    });

    const btnCopyBundle = document.createElement('button');
    btnCopyBundle.innerHTML = '📋 复制全部账号 Bundle JSON';
    Object.assign(btnCopyBundle.style, {
      background: '#f3f4f6',
      color: '#374151',
      border: '1px solid #d1d5db',
      padding: '6px 12px',
      borderRadius: '8px',
      cursor: 'pointer',
    });

    btnSyncAll.onclick = async () => {
      btnSyncAll.disabled = true;
      btnSyncAll.innerHTML = '⏳ 正在发现全部子账号并探测权益...';
      try {
        const bundle = await discoverAllAccounts();
        btnSyncAll.innerHTML = `⏳ 发现 ${bundle.total} 个账号，正在推送到本地网关...`;
        const res = await postToLocalGateway(bundle);
        btnSyncAll.style.background = '#16a34a';
        btnSyncAll.innerHTML = `🎉 成功同步 ${res.count || bundle.total} 个账号！`;
        statusText.textContent = `全部子账号已自动入驻，耗时 ${(res.count || bundle.total)} 项！`;
      } catch (err) {
        btnSyncAll.style.background = '#dc2626';
        btnSyncAll.innerHTML = '❌ 同步失败，点击重试';
        statusText.textContent = err.message;
      } finally {
        setTimeout(() => {
          btnSyncAll.disabled = false;
          btnSyncAll.style.background = '#2563eb';
          btnSyncAll.innerHTML = '🚀 <b>一键自动同步全部账号到本地</b>';
        }, 5000);
      }
    };

    btnCopyBundle.onclick = async () => {
      btnCopyBundle.disabled = true;
      btnCopyBundle.textContent = '⏳ 正在生成多账号 Bundle...';
      try {
        const bundle = await discoverAllAccounts();
        GM_setClipboard(JSON.stringify(bundle, null, 2));
        btnCopyBundle.textContent = `✅ 已复制 ${bundle.total} 个账号 Bundle！`;
      } catch (err) {
        btnCopyBundle.textContent = '❌ 复制失败';
        statusText.textContent = err.message;
      } finally {
        setTimeout(() => {
          btnCopyBundle.disabled = false;
          btnCopyBundle.textContent = '📋 复制全部账号 Bundle JSON';
        }, 4000);
      }
    };

    container.appendChild(title);
    container.appendChild(btnSyncAll);
    container.appendChild(btnCopyBundle);
    container.appendChild(statusText);
    return container;
  }

  console.log('%c[AIStudio Sync Tool] 助手脚本已成功激活！', 'color:#2563eb;font-weight:bold;font-size:14px;');

  let containerInstance = null;
  function ensureFloatingUI() {
    if (!document.body) return;
    if (document.getElementById('aistudio-cookie-grabber-panel')) return;
    if (!containerInstance) {
      containerInstance = createFloatingUI();
    }
    document.body.appendChild(containerInstance);
  }

  setInterval(ensureFloatingUI, 1000);
  ensureFloatingUI();

  GM_registerMenuCommand('🚀 一键同步浏览器全部账号到 aistudio-api', async () => {
    try {
      const bundle = await discoverAllAccounts();
      const res = await postToLocalGateway(bundle);
      alert(`🎉 成功同步 ${res.count || bundle.total} 个账号到本地网关！`);
    } catch (e) {
      alert(`❌ 同步失败: ${e.message}`);
    }
  });

  GM_registerMenuCommand('📋 复制全部账号 Bundle JSON 到剪切板', async () => {
    try {
      const bundle = await discoverAllAccounts();
      GM_setClipboard(JSON.stringify(bundle, null, 2));
      alert(`✅ 已复制 ${bundle.total} 个账号的 Bundle JSON 到剪切板！可以直接粘贴到管理面板导入。`);
    } catch (e) {
      alert(`❌ 复制失败: ${e.message}`);
    }
  });
})();
