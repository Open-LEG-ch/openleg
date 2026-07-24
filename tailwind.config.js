module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        // Daylight cooperative: pine on paper, warmed by solar.
        brand: { DEFAULT: "#1f3d32", light: "#2c5545", dark: "#16302a" }, // pine
        accent: { DEFAULT: "#e8a13a", light: "#f0b968", dark: "#c9832a" }, // solar
        sage: { DEFAULT: "#6e8f7c", light: "#e4ede6", dark: "#4f6d5c" },
        paper: { DEFAULT: "#f5f2ea", deep: "#ece7da" },
        line: "#ded7c6", // warm hairline
        ink: { DEFAULT: "#22201b", soft: "#3a362c", muted: "#6b6555" },
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
