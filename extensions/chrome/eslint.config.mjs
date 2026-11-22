// ESLint flat config for the Chrome extension (no npm dependencies needed).
const browserGlobals = Object.fromEntries(
  [
    "window", "document", "console", "fetch", "setTimeout", "clearTimeout",
    "setInterval", "clearInterval", "URL", "URLSearchParams", "TextDecoder",
    "AbortController", "navigator", "location", "history", "MutationObserver",
    "Event", "CustomEvent", "Promise", "alert", "self", "XMLHttpRequest",
  ].map((name) => [name, "readonly"]),
);

export default [
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: { ...browserGlobals, chrome: "readonly" },
    },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["warn", { args: "none", caughtErrors: "none" }],
      "no-unreachable": "error",
      "no-dupe-keys": "error",
      "no-redeclare": "error",
      "no-const-assign": "error",
      "no-self-assign": "error",
      "no-unsafe-finally": "error",
      "use-isnan": "error",
      "valid-typeof": "error",
      eqeqeq: ["warn", "smart"],
    },
  },
];
