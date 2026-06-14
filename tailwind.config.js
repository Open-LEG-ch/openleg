module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#f59e0b", light: "#fbbf24", dark: "#d97706" },
        accent: "#f59e0b",
        ink: { DEFAULT: "#0f172a", soft: "#1e293b", muted: "#475569" },
      },
    },
  },
};
