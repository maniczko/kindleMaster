export const C = {
  bg: "#F2F0E9",
  surface: "#FFFFFF",
  card: "#FFFFFF",
  cardAlt: "#FAF8F2",
  panel: "#F7F4EC",
  border: "#DCD7C9",
  borderSoft: "#E7E1D2",

  accent: "#4B5EAA",
  accentHover: "#3D4B8A",
  accentSoft: "#EEF1FA",
  accent2: "#8294C4",

  text: "#1A1A1B",
  textStrong: "#2C3E50",
  textSub: "#5F645C",
  muted: "#8C8A7E",

  successBg: "#E6F2ED",
  success: "#2D6A4F",
  successText: "#1B4332",

  errorBg: "#F9EAE1",
  error: "#A54242",
  errorText: "#7B2E2E",

  tagBg: "#E5E5E5",
  tagText: "#666666",

  yellow: "#B08968",

  shadow: "0 4px 20px rgba(0, 0, 0, 0.03)",
  shadowSm: "0 2px 10px rgba(0, 0, 0, 0.025)",
};

export const s = {
  card: {
    background: C.card,
    border: `1px solid ${C.border}`,
    borderRadius: 22,
    boxShadow: C.shadow,
  },
  cardSm: {
    background: C.card,
    border: `1px solid ${C.border}`,
    borderRadius: 16,
    boxShadow: C.shadowSm,
  },
  btn: (variant = "primary") => ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    padding: "10px 16px",
    borderRadius: 14,
    fontSize: 13,
    fontWeight: 600,
    cursor: "pointer",
    border: "none",
    transition: "all .18s ease",
    ...(variant === "primary"
      ? { background: C.accent, color: "#fff" }
      : variant === "ghost"
      ? { background: C.surface, color: C.textSub, border: `1px solid ${C.border}` }
      : variant === "soft"
      ? { background: C.accentSoft, color: C.accent, border: `1px solid ${C.borderSoft}` }
      : variant === "danger"
      ? { background: C.errorBg, color: C.error, border: `1px solid #E7C6B8` }
      : { background: C.cardAlt, color: C.text, border: `1px solid ${C.border}` }),
  }),
  metric: {
    background: C.card,
    border: `1px solid ${C.border}`,
    borderRadius: 16,
    boxShadow: "none",
    padding: "14px 16px",
  },
  input: {
    width: "100%",
    padding: "12px 14px",
    borderRadius: 14,
    border: `1px solid ${C.border}`,
    background: "#fff",
    color: C.text,
    fontSize: 14,
    outline: "none",
  },
  label: {
    display: "block",
    fontSize: 12,
    fontWeight: 700,
    color: C.textSub,
    marginBottom: 8,
    letterSpacing: ".02em",
  },
};