const msg = (key, ...substitutions) => chrome.i18n.getMessage(key, substitutions);

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

const connectionStatus = document.getElementById('connectionStatus');

const deezerUrlEl = document.getElementById('deezerUrl');
const downloadBtn = document.getElementById('downloadBtn');
const downloadStatus = document.getElementById('downloadStatus');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');

const serverUrlEl = document.getElementById('serverUrl');
const serverKeyEl = document.getElementById('serverKey');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');

document.addEventListener('DOMContentLoaded', () => {
    applyI18n();
    loadSettings();
    setupEventListeners();
    checkServerConnection();
    extractCurrentPageInfo();
});

function setupEventListeners() {
    downloadBtn.addEventListener('click', startDownload);
    saveSettingsBtn.addEventListener('click', saveSettings);
    deezerUrlEl.addEventListener('input', showWhat);
}

// An id alone names an album; a playlist is given by its address only.
function described(value) {
    const text = value.trim();
    if (/^\d+$/.test(text)) {
        return { kind: 'album', id: text };
    }
    const match = text.match(/\/(album|playlist)\/(\d+)/);
    return match ? { kind: match[1], id: match[2] } : null;
}

function showWhat() {
    if (!deezerUrlEl.value.trim()) {
        downloadStatus.classList.remove('show');
        return;
    }
    const what = described(deezerUrlEl.value);
    if (what) {
        showStatus(msg('idExtracted', msg(what.kind === 'album' ? 'kindAlbum' : 'kindPlaylist'), what.id), 'info');
    } else {
        showStatus(msg('errorInvalidUrl'), 'error');
    }
}

const DEFAULT_SERVER = 'http://localhost:8080';
const POLL_MS = 2000;

function getServer(callback) {
    chrome.storage.local.get(['serverUrl', 'serverKey'], (result) => {
        callback((result.serverUrl || DEFAULT_SERVER).replace(/\/+$/, ''), result.serverKey || '');
    });
}

function authHeaders(key) {
    return { 'Authorization': `Bearer ${key}`, 'Content-Type': 'application/json' };
}

function setBadge(key, ok) {
    connectionStatus.textContent = msg(key);
    connectionStatus.classList.toggle('success', ok);
}

function checkServerConnection() {
    getServer((serverUrl, key) => {
        const pattern = hostPattern(serverUrl);
        if (!key) {
            setBadge('disconnected', false);
            return;
        }
        chrome.permissions.contains({ origins: pattern ? [pattern] : [] }, (allowed) => {
            if (!allowed) {
                setBadge('disconnected', false);
                showStatus(msg('permissionNeeded'), 'info');
                return;
            }
            fetch(`${serverUrl}/deezer/ext/health`, { method: 'GET', headers: authHeaders(key) })
                .then((res) => {
                    if (res.status === 401) {
                        setBadge('badgeWrongKey', false);
                        return null;
                    }
                    return res.ok ? res.json() : Promise.reject();
                })
                .then((health) => {
                    if (health) {
                        const ready = health.enabled && health.apply;
                        setBadge(ready ? 'connected' : 'connectedOff', ready);
                    }
                })
                .catch(() => setBadge('disconnected', false));
        });
    });
}

function isDeezerPage(url) {
    try {
        const host = new URL(url).hostname;
        return host === 'deezer.com' || host.endsWith('.deezer.com');
    } catch (e) {
        return false;
    }
}

function extractCurrentPageInfo() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        if (isDeezerPage(tab.url)) {
            deezerUrlEl.value = tab.url;
            showWhat();
        }
    });
}

function startDownload() {
    const value = deezerUrlEl.value.trim();

    if (!described(value)) {
        showStatus(msg(value ? 'errorInvalidUrl' : 'errorMissingId'), 'error');
        return;
    }

    getServer(async (serverUrl, key) => {
        if (!key) {
            showStatus(msg('errorMissingKey'), 'error');
            return;
        }
        const body = { id: value };
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
            } else if (response.status === 403) {
                finish(answer.error ? msg('errorDownload', answer.error) : msg('errorDashboardOff'), 'error');
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
}

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

function hostPattern(serverUrl) {
    try {
        return new URL(serverUrl).origin + '/*';
    } catch (e) {
        return null;
    }
}

// Settings are kept before access is asked: Chrome's prompt can close the popup before its answer.
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
    const pattern = hostPattern(serverUrl);
    if (!pattern) {
        showStatus(msg('errorMissingServerUrl'), 'error');
        return;
    }
    // asked first, in the click itself: Chrome refuses a request made after another asynchronous call
    let asked;
    try {
        asked = chrome.permissions.request({ origins: [pattern] });
    } catch (error) {
        asked = Promise.reject(error);
    }
    chrome.storage.local.set({ serverUrl, serverKey }, () => {
        showStatus(msg('settingsSaved'), 'success');
        asked.then((granted) => {
            if (granted) {
                checkServerConnection();
            } else {
                showStatus(msg('errorPermission'), 'error');
            }
        }).catch((error) => showStatus(msg('errorPermission') + ' (' + error.message + ')', 'error'));
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
