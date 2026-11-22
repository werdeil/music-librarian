// Validates the Chrome extension: manifest fields, referenced files and
// consistency of the locale files. Usage: node check-extension.mjs extensions/chrome
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const dir = process.argv[2] ?? "extensions/chrome";
const errors = [];
const readJson = (path) => {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (e) {
    errors.push(`${path}: invalid JSON (${e.message})`);
    return null;
  }
};
const mustExist = (rel, what) => {
  if (!existsSync(join(dir, rel))) errors.push(`manifest.json: ${what} "${rel}" not found`);
};

const manifest = readJson(join(dir, "manifest.json"));
if (manifest) {
  if (manifest.manifest_version !== 3) errors.push("manifest.json: manifest_version must be 3");
  for (const field of ["name", "version", "description"]) {
    if (!manifest[field]) errors.push(`manifest.json: missing "${field}"`);
  }
  if (manifest.version && !/^\d+(\.\d+){0,3}$/.test(manifest.version)) {
    errors.push(`manifest.json: invalid version "${manifest.version}"`);
  }
  if (manifest.background?.service_worker) mustExist(manifest.background.service_worker, "service worker");
  if (manifest.action?.default_popup) mustExist(manifest.action.default_popup, "popup");
  for (const cs of manifest.content_scripts ?? []) {
    for (const js of cs.js ?? []) mustExist(js, "content script");
  }
  for (const icon of Object.values(manifest.icons ?? {})) mustExist(icon, "icon");
}

// Locales: every locale must define the same keys, each with a "message".
const localesDir = join(dir, "_locales");
const locales = {};
for (const lang of readdirSync(localesDir)) {
  const messages = readJson(join(localesDir, lang, "messages.json"));
  if (!messages) continue;
  locales[lang] = new Set(Object.keys(messages));
  for (const [key, value] of Object.entries(messages)) {
    if (typeof value?.message !== "string") errors.push(`_locales/${lang}: "${key}" has no "message"`);
  }
}
const defaultLocale = manifest?.default_locale;
if (defaultLocale && !locales[defaultLocale]) {
  errors.push(`default_locale "${defaultLocale}" has no _locales/${defaultLocale}/messages.json`);
}
const allKeys = new Set(Object.values(locales).flatMap((keys) => [...keys]));
for (const [lang, keys] of Object.entries(locales)) {
  for (const key of allKeys) {
    if (!keys.has(key)) errors.push(`_locales/${lang}: missing key "${key}"`);
  }
}

// __MSG_xxx__ placeholders in the manifest and keys used from JS/HTML must exist.
const used = new Set();
const manifestText = readFileSync(join(dir, "manifest.json"), "utf8");
for (const m of manifestText.matchAll(/__MSG_(\w+)__/g)) used.add(m[1]);
for (const file of readdirSync(dir).filter((f) => /\.(js|html)$/.test(f))) {
  const src = readFileSync(join(dir, file), "utf8");
  for (const m of src.matchAll(/data-i18n="(\w+)"/g)) used.add(m[1]);
  for (const m of src.matchAll(/\bmsg\(\s*['"](\w+)['"]/g)) used.add(m[1]);
  for (const m of src.matchAll(/getMessage\(\s*['"](\w+)['"]/g)) used.add(m[1]);
}
for (const key of used) {
  if (!allKeys.has(key)) errors.push(`i18n key "${key}" is used but not defined in _locales`);
}

if (errors.length) {
  console.error(errors.map((e) => `::error::${e}`).join("\n"));
  process.exit(1);
}
console.log(`Extension OK: ${Object.keys(locales).length} locales, ${allKeys.size} keys, ${used.size} used`);
