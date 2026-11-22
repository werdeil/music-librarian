// i18n helper
const msg = (key, ...substitutions) => chrome.i18n.getMessage(key, substitutions);

// Apply i18n to all elements with data-i18n attribute
function applyI18n() {
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        const prefix = el.getAttribute('data-i18n-prefix') || '';
        const translated = msg(key);
        if (translated) {
            el.textContent = prefix + translated;
        }
    });
}

// State
let currentContentType = 'album';
let selectedUrl = '';

// UI Elements
const tokenStatusEl = document.getElementById('tokenStatus');
const tokenStatusText = document.getElementById('tokenStatusText');
const refreshTokenBtn = document.getElementById('refreshTokenBtn');
const connectionStatus = document.getElementById('connectionStatus');

const deezerUrlEl = document.getElementById('deezerUrl');
const extractBtn = document.getElementById('extractBtn');
const contentIdEl = document.getElementById('contentId');
const downloadBtn = document.getElementById('downloadBtn');
const downloadStatus = document.getElementById('downloadStatus');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');

const serverUrlEl = document.getElementById('serverUrl');
const serverKeyEl = document.getElementById('serverKey');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    applyI18n();
    loadSettings();
    updateTokenStatus();
    setupEventListeners();
    checkServerConnection();
    extractCurrentPageInfo();
});

function setupEventListeners() {
    refreshTokenBtn.addEventListener('click', refreshToken);
    extractBtn.addEventListener('click', extractUrlId);
    downloadBtn.addEventListener('click', startDownload);
    saveSettingsBtn.addEventListener('click', saveSettings);
    
    deezerUrlEl.addEventListener('paste', () => {
        setTimeout(extractUrlId, 10);
    });
}

function extractUrlId() {
    const url = deezerUrlEl.value || selectedUrl;
    
    // Pattern: /album/123456 ou /playlist/123456
    const match = url.match(/\/(album|playlist)\/(\d+)/);
    
    if (match) {
        const type = match[1];
        const id = match[2];
        
        currentContentType = type; // Auto-detect type from URL
        contentIdEl.value = id;
        showStatus(msg('idExtracted', type, id), 'success');
    } else {
        showStatus(msg('errorInvalidUrl'), 'error');
    }
}

function updateTokenStatus() {
    chrome.storage.local.get(['licenseToken'], (result) => {
        const token = result.licenseToken;
        
        if (token) {
            tokenStatusEl.classList.add('active');
            const displayToken = token.substring(0, 20) + '...';
            tokenStatusText.textContent = '✅ ' + msg('tokenFound', displayToken);
        } else {
            tokenStatusEl.classList.remove('active');
            tokenStatusText.textContent = '⚠️ ' + msg('tokenNoneFallback');
        }
    });
}

function refreshToken() {
    refreshTokenBtn.textContent = '⏳ ' + msg('btnRefreshing');
    refreshTokenBtn.disabled = true;
    
    chrome.runtime.sendMessage({ action: 'getTokenFromTab' }, (response) => {
        refreshTokenBtn.disabled = false;
        refreshTokenBtn.textContent = '🔄 ' + msg('btnRefresh');
        
        if (response && response.token) {
            chrome.storage.local.set({ licenseToken: response.token }, () => {
                updateTokenStatus();
                showStatus(msg('tokenRefreshed'), 'success');
            });
        } else {
            showStatus(msg('tokenCaptureError'), 'error');
        }
    });
}

const DEFAULT_SERVER = 'http://localhost:8080';
const POLL_MS = 2000;

// The dashboard's address and key, as saved in the settings.
function getServer(callback) {
    chrome.storage.local.get(['serverUrl', 'serverKey'], (result) => {
        callback((result.serverUrl || DEFAULT_SERVER).replace(/\/+$/, ''), result.serverKey || '');
    });
}

function authHeaders(key) {
    return { 'Authorization': `Bearer ${key}`, 'Content-Type': 'application/json' };
}

function checkServerConnection() {
    getServer((serverUrl, key) => {
        fetch(`${serverUrl}/deezer/ext/health`, { method: 'GET', headers: authHeaders(key) })
            .then(res => res.ok ? res.json() : Promise.reject())
            .then((health) => {
                if (!health.enabled || !health.apply) return Promise.reject();
                connectionStatus.textContent = msg('connected');
                connectionStatus.classList.add('success');
            })
            .catch(() => {
                connectionStatus.textContent = msg('disconnected');
                connectionStatus.classList.remove('success');
            });
    });
}

function extractCurrentPageInfo() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        if (tab.url.includes('deezer.com')) {
            deezerUrlEl.value = tab.url;
            extractUrlId();
        }
    });
}

function startDownload() {
    const contentId = contentIdEl.value.trim();

    if (!contentId) {
        showStatus(msg('errorMissingId'), 'error');
        return;
    }

    chrome.storage.local.get(['licenseToken'], (result) => {
        getServer(async (serverUrl, key) => {
            if (!key) {
                showStatus(msg('errorMissingKey'), 'error');
                return;
            }
            const body = { kind: currentContentType, id: contentId };
            if (result.licenseToken) {
                body.license_token = result.licenseToken;
            }
            downloadBtn.disabled = true;
            downloadBtn.textContent = '⏳ ' + msg('btnDownloading');
            setProgress(0);
            progressContainer.style.display = 'block';
            try {
                const response = await fetch(`${serverUrl}/deezer/ext/request`, {
                    method: 'POST', headers: authHeaders(key), body: JSON.stringify(body)
                });
                const answer = await response.json().catch(() => ({}));
                if (response.status === 401) {
                    finish(msg('errorWrongKey'), 'error');
                } else if (!response.ok) {
                    finish(msg('errorDownload', answer.error || msg('errorServer', response.status.toString())),
                           'error');
                } else {
                    showStatus(msg('statusQueued'), 'info');
                    follow(serverUrl, key, answer.id);
                }
            } catch (error) {
                finish(msg('errorConnection', error.message), 'error');
            }
        });
    });
}

// The request's state, asked every POLL_MS until watch has run it.
function follow(serverUrl, key, requestId) {
    const timer = setInterval(async () => {
        try {
            const response = await fetch(`${serverUrl}/deezer/ext/request/${requestId}`,
                                         { headers: authHeaders(key) });
            if (!response.ok) {
                clearInterval(timer);
                finish(msg('errorServer', response.status.toString()), 'error');
                return;
            }
            const state = await response.json();
            if (state.total > 0) {
                setProgress(Math.round((state.done / state.total) * 100));
            }
            if (state.state === 'done' || state.state === 'cancelled') {
                clearInterval(timer);
                const details = (state.summary || []).join(' · ');
                if (state.code === 0) {
                    setProgress(100);
                    finish('✅ ' + msg('downloadComplete', details), 'success');
                } else if (state.code === 2) {
                    finish(msg('downloadPartial', details), 'error');
                } else {
                    finish(msg('errorDownload', details || state.step || msg('errorUnknown')), 'error');
                }
            }
        } catch (error) {
            clearInterval(timer);
            finish(msg('errorConnection', error.message), 'error');
        }
    }, POLL_MS);
}

function setProgress(pct) {
    progressBar.style.width = pct + '%';
    progressBar.textContent = pct + '%';
}

function finish(message, type) {
    showStatus(message, type);
    downloadBtn.disabled = false;
    downloadBtn.textContent = '⬇️ ' + msg('btnDownload');
    setTimeout(() => {
        progressContainer.style.display = 'none';
    }, 2000);
}

function showStatus(message, type) {
    downloadStatus.textContent = message;
    downloadStatus.className = `show ${type}`;
    
    if (type === 'success') {
        setTimeout(() => {
            downloadStatus.classList.remove('show');
        }, 3000);
    }
}

function loadSettings() {
    chrome.storage.local.get(['serverUrl', 'serverKey'], (result) => {
        if (result.serverUrl) {
            serverUrlEl.value = result.serverUrl;
        }
        if (result.serverKey) {
            serverKeyEl.value = result.serverKey;
        }
    });
}

// Access to the dashboard's host is asked when it is saved: it is a user gesture, as Chrome requires.
function saveSettings() {
    const serverUrl = serverUrlEl.value.trim();
    const serverKey = serverKeyEl.value.trim();

    if (!serverUrl) {
        showStatus(msg('errorMissingServerUrl'), 'error');
        return;
    }
    if (!serverKey) {
        showStatus(msg('errorMissingKey'), 'error');
        return;
    }
    let origin;
    try {
        origin = new URL(serverUrl).origin;
    } catch (e) {
        showStatus(msg('errorMissingServerUrl'), 'error');
        return;
    }
    chrome.permissions.request({ origins: [origin + '/*'] }, (granted) => {
        if (!granted) {
            showStatus(msg('errorPermission'), 'error');
            return;
        }
        chrome.storage.local.set({ serverUrl, serverKey }, () => {
            showStatus(msg('settingsSaved'), 'success');
            checkServerConnection();
        });
    });
}

const settingsToggle = document.getElementById('settingsToggle');
const mainView = document.getElementById('mainView');
const settingsView = document.getElementById('settingsView');

let isSettingsOpen = false;

settingsToggle.addEventListener('click', () => {
    isSettingsOpen = !isSettingsOpen;

    if (isSettingsOpen) {
        mainView.style.display = 'none';
        settingsView.style.display = 'block';
        settingsToggle.textContent = '⬅️';
    } else {
        mainView.style.display = 'block';
        settingsView.style.display = 'none';
        settingsToggle.textContent = '⚙️';
    }
});
