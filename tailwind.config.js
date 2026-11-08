// Only needed when changing styles: see "Rebuilding styles" in README.md.
module.exports = {
  content: ["./index.html", "./static/js/**/*.js"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)", panel: "var(--panel)", raised: "var(--raised)", hover: "var(--hover)",
        line: "var(--line)", ink: "var(--ink)", soft: "var(--soft)", faint: "var(--faint)",
        accent: "var(--accent)", "accent-ink": "var(--accent-ink)", "accent-wash": "var(--accent-wash)",
        good: "var(--good)", "good-wash": "var(--good-wash)",
        bad: "var(--bad)", "bad-wash": "var(--bad-wash)",
        warn: "var(--warn)", "warn-wash": "var(--warn-wash)",
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Inter", "Roboto", "sans-serif"],
        mono: ["SF Mono", "ui-monospace", "Menlo", "Consolas", "monospace"],
      },
      fontSize: { "2xs": ["11px", "15px"], xs: ["12px", "17px"], sm: ["13px", "19px"] },
    },
  },
  plugins: [],
};
