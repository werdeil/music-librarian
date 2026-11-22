// Background Service Worker
// Captures license token from network requests and manages extension state

// Listen for messages from popup and content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === 'getTokenFromTab') {
        // Forward the request to the current tab
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            const tab = tabs[0];
            chrome.tabs.sendMessage(tab.id, { action: 'getToken' }, (response) => {
                if (response && response.token) {
                    sendResponse({ token: response.token });
                } else {
                    sendResponse({ error: 'No token found' });
                }
            });
        });
        return true; // Keep channel open for async response
    }
    
    if (request.action === 'saveToken') {
        chrome.storage.local.set({ licenseToken: request.token }, () => {
            sendResponse({ success: true });
        });
        return true;
    }
});

// Listen for web request handling
// This captures API calls that contain the license token
chrome.webRequest.onBeforeSendHeaders.addListener(
    (details) => {
        if (details.url.includes('api.deezer.com') ||
            details.url.includes('media.deezer.com') ||
            details.url.includes('cdn-ssl-reloc.dzcdn.net')) {
            // Extract token from request headers
            const headers = details.requestHeaders || [];
            
            headers.forEach(header => {
                if (header.name.toLowerCase() === 'authorization') {
                    const token = header.value;
                    if (token && token.length > 30) {
                        chrome.storage.local.set({ licenseToken: token });
                    }
                }
            });
        }
    },
    { urls: ['https://api.deezer.com/*', 'https://cdn-ssl-reloc.dzcdn.net/*'] },
    ['requestHeaders']
);

// Also inspect request body for cases where the token is sent in JSON payload (e.g. get_url)
chrome.webRequest.onBeforeRequest.addListener(
    (details) => {
        if ((details.url.includes('api.deezer.com') || details.url.includes('media.deezer.com')) &&
        details.requestBody && details.requestBody.raw) {
            try {
                const decoder = new TextDecoder('utf-8');
                for (const part of details.requestBody.raw) {
                    const text = decoder.decode(part.bytes);
                    if (text.includes('license_token')) {
                        const parsed = JSON.parse(text);
                        if (parsed.license_token && parsed.license_token.length > 30) {
                            chrome.storage.local.set({ licenseToken: parsed.license_token });
                            break;
                        }
                    }
                }
            } catch (e) {
                console.log('Error parsing request body for token:', e);
            }
        }
    },
    { urls: ['https://api.deezer.com/*', 'https://media.deezer.com/*'] },
    ['requestBody']
);

