module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#4f46e5", light: "#6366f1", dark: "#4338ca" },
        accent: "#4f46e5",
        ink: { DEFAULT: "#0f172a", soft: "#1e293b", muted: "#475569" },
      },
      fontFamily: {
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
    },
  },
};
