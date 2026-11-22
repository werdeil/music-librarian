// Content script injected into Deezer pages
// Captures license token from network requests and page data

// guard against multiple injections (manifest + programmatic)
if (window.__DMD_EXTENSION_LOADED) {
    console.log('[Deezer Music Download] Content script already loaded, skipping');
} else {
    window.__DMD_EXTENSION_LOADED = true;
    console.log('[Deezer Music Download] Content script loaded');

// Listen for messages from background script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'getToken') {
        // Try to get token from various sources
        const token = extractToken();
        
        if (token) {
            sendResponse({ token: token });
        } else {
            sendResponse({ error: 'No token found' });
        }
    }
});

// Extract license token from page data or window object
function extractToken() {
    try {
        // Try to extract from window.__DZR_APP_STATE__
        if (window.__DZR_APP_STATE__) {
            const state = window.__DZR_APP_STATE__;
            
            if (state.initialState && state.initialState.user && state.initialState.user.license) {
                const license = state.initialState.user.license;
                if (license.token) {
                    console.log('[Deezer Music Download] Token found in __DZR_APP_STATE__');
                    return license.token;
                }
            }
        }
    } catch (e) {
        console.log('[Deezer Music Download] Error accessing __DZR_APP_STATE__:', e);
    }
    
    return null;
}

// Intercept fetch requests to capture license token
const originalFetch = window.fetch;
window.fetch = function(...args) {
    const [resource, config] = args;
    
    // Check if this is an API call (including media endpoint where tokens live)
    if (typeof resource === 'string' &&
        (resource.includes('api.deezer.com') || resource.includes('media.deezer.com') || resource.includes('dzcdn.net'))) {
        // attempt to inspect body for license_token before sending
        if (config && config.body) {
            try {
                const bodyStr = typeof config.body === 'string' ? config.body : JSON.stringify(config.body);
                if (bodyStr.includes('license_token')) {
                    const parsed = JSON.parse(bodyStr);
                    if (parsed.license_token && parsed.license_token.length > 30) {
                        chrome.runtime.sendMessage({ action: 'saveToken', token: parsed.license_token })
                            .catch(e => console.log('Message send error:', e));
                    }
                }
            } catch (e) {
                // ignore non-json bodies
            }
        }

        // Try to extract token from the response headers as before
        const promise = originalFetch.apply(this, args);
        
        promise.then(response => {
            try {
                if (response.headers) {
                    const authHeader = response.headers.get('authorization') || response.headers.get('x-license-token');
                    if (authHeader && authHeader.length > 30) {
                        chrome.runtime.sendMessage({
                            action: 'saveToken',
                            token: authHeader
                        }).catch(e => console.log('Message send error:', e));
                    }
                }
            } catch (e) {
                console.log('[Deezer Music Download] Error reading response headers:', e);
            }
        }).catch(e => console.log('[Deezer Music Download] Fetch error:', e));
        
        return promise;
    }
    
    return originalFetch.apply(this, args);
};

// Intercept XHR requests as well and inspect request body
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url, ...args) {
    if (typeof url === 'string' &&
        (url.includes('api.deezer.com') || url.includes('media.deezer.com') || url.includes('dzcdn.net'))) {
        // intercept send to capture body tokens
        const originalSend = this.send;
        this.send = function(body) {
            if (body && typeof body === 'string' && body.includes('license_token')) {
                try {
                    const parsed = JSON.parse(body);
                    if (parsed.license_token && parsed.license_token.length > 30) {
                        chrome.runtime.sendMessage({ action: 'saveToken', token: parsed.license_token })
                            .catch(e => console.log('Message send error:', e));
                    }
                } catch (e) {
                    // ignore non-json
                }
            }
            return originalSend.call(this, body);
        };

        // Store original setRequestHeader
        const originalSetHeader = this.setRequestHeader;
        const headers = {};
        
        this.setRequestHeader = function(header, value) {
            headers[header.toLowerCase()] = value;
            return originalSetHeader.call(this, header, value);
        };
        
        // After request completes, try to extract token
        this.addEventListener('loadend', function() {
            try {
                // Check response headers
                const authHeader = this.getResponseHeader('authorization') || this.getResponseHeader('x-license-token');
                if (authHeader && authHeader.length > 30) {
                    chrome.runtime.sendMessage({
                        action: 'saveToken',
                        token: authHeader
                    }).catch(e => console.log('Message send error:', e));
                }
            } catch (e) {
                console.log('[Deezer Music Download] Error reading XHR response:', e);
            }
        });
    }
    
    return originalOpen.call(this, method, url, ...args);
};

// On page load, try to extract token immediately
window.addEventListener('load', () => {
    console.log('[Deezer Music Download] Page loaded, attempting token extraction');
    const token = extractToken();
    if (token) {
        chrome.runtime.sendMessage({
            action: 'saveToken',
            token: token
        }).catch(e => console.log('Message send error:', e));
    }
});

console.log('[Deezer Music Download] Content script ready to capture tokens');
}

