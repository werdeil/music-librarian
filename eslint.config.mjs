// The dashboard's page scripts. Rules are declared inline, without extending
// @eslint/js recommended, so a bare `npx eslint` runs it with no dependency.
export default [
    {
        files: ["src/music_librarian/web/static/*.js"],
        languageOptions: {
            ecmaVersion: 2021,
            sourceType: "script",
            globals: {
                window: "readonly",
                document: "readonly",
                fetch: "readonly",
                setTimeout: "readonly",
                encodeURIComponent: "readonly",
            },
        },
        rules: {
            "no-undef": "error",
            "no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
            "no-redeclare": "error",
            "no-dupe-keys": "error",
            "no-duplicate-case": "error",
            "no-unreachable": "error",
            "no-constant-condition": "error",
            "no-cond-assign": "error",
            "no-self-assign": "error",
            "use-isnan": "error",
            "valid-typeof": "error",
            "eqeqeq": ["warn", "smart"],
        },
    },
];
