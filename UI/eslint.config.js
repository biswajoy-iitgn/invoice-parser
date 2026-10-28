// Lint: TypeScript, React, the rules of hooks, and accessibility (jsx-a11y), the checks a Microsoft frontend PR runs.
import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'public', 'scripts/**/*.cjs'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser, parserOptions: { ecmaFeatures: { jsx: true } } },
    settings: { react: { version: 'detect' } },
    plugins: { react, 'react-hooks': reactHooks, 'jsx-a11y': jsxA11y },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.strict.rules,
      'react/prop-types': 'off', // TypeScript checks props
      // Fluent components carry their own roles and keyboard handling; the rules below can't see through them
      'jsx-a11y/no-autofocus': ['error', { ignoreNonDOM: true }],
      // a labelled scroll region must take focus so keyboard users can scroll it (axe scrollable-region-focusable)
      'jsx-a11y/no-noninteractive-tabindex': ['error', { roles: ['tabpanel', 'region'] }],
    },
  },
  {
    files: ['scripts/**/*.{js,mjs}', '*.config.{js,ts}', 'public/boot.js'],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
);
