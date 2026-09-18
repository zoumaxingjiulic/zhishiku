import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist"] },
  { ...js.configs.recommended, files: ["**/*.js"] },
  ...tseslint.configs.recommended,
  ...vue.configs["flat/essential"],
  {
    rules: {
      // Explicit-any removal is tracked per feature in governance phase 3; phase 1 keeps correctness checks runnable.
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: [".vue"],
      },
    },
  },
);
