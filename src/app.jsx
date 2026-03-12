import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";
import * as XLSX from "xlsx";
import "./app.css";
import { C, s } from "./theme";
import {
  IcoBrain,
  IcoTrophy,
  IcoClock,
  IcoCalendar,
  IcoBook,
  IcoSettings,
  IcoRefresh,
  IcoChat,
  IcoLeft,
  IcoRight,
  IcoUpload,
  IcoCloud,
  IcoCheck,
  IcoCross,
  IcoTrending,
  IcoBolt,
  IcoTarget,
  ZenQuizLogo,
} from "./icons";

// ── Config & Helpers ──────────────────────────────────────────────────────────
const STORAGE_KEY = "quiz_abcd_attempts_v6";
const CLOUD_SETTINGS_KEY = "quiz_abcd_cloud_settings_v2";
const SUPABASE_SETTINGS_KEY = "quiz_abcd_supabase_settings_v1";
const optionKeys = ["A", "B", "C", "D"];
const diffW = { easy: 1, medium: 1.5, hard: 2 };
const DEFAULT_MODEL = "claude-sonnet-4-5-20250929";
const DEFAULT_SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL || "https://ylqloszldyzpeaikweyl.supabase.co").trim();
const DEFAULT_SUPABASE_ANON_KEY = (
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ||
  import.meta.env.VITE_SUPABASE_ANON_KEY ||
  ""
).trim();
const CLOUD_BROWSER_NOTICE =
  "Ta aplikacja działa wyłącznie w przeglądarce. Bez backend proxy lub Edge Function nie wykonasz bezpiecznego połączenia z Claude API z tego widoku.";

const isJwtLike = (value) => {
  const parts = String(value || "").trim().split(".");
  return parts.length === 3 && parts.every(Boolean);
};

const isValidSupabaseUrl = (value) => {
  try {
    const parsed = new URL(String(value || "").trim());
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
};

const isValidSupabaseKey = (value) => {
  const key = String(value || "").trim();
  return key.startsWith("sb_publishable_") || isJwtLike(key);
};

const hasSupabaseConfig = (config) => isValidSupabaseUrl(config?.url) && isValidSupabaseKey(config?.apiKey);

const sbH = (apiKey, prefer = "return=representation") => {
  const headers = {
    "Content-Type": "application/json",
    apikey: apiKey,
    Prefer: prefer,
  };

  if (isJwtLike(apiKey)) headers.Authorization = `Bearer ${apiKey}`;
  return headers;
};

async function sbSelect(config, table, params = "") {
  const r = await fetch(`${config.url}/rest/v1/${table}?${params}`, { headers: sbH(config.apiKey) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function sbInsert(config, table, row) {
  const r = await fetch(`${config.url}/rest/v1/${table}`, {
    method: "POST",
    headers: sbH(config.apiKey),
    body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
}

const normDiff = (v) => {
  const r = String(v || "medium").trim().toLowerCase();
  return ["easy", "medium", "hard"].includes(r) ? r : "medium";
};

const fmt = (ms) => `${(ms / 1000).toFixed(1)}s`;
const weekdayLabels = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"];

const dayKey = (ts) => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const humanDate = (key) => {
  const d = new Date(`${key}T12:00:00`);
  return d.toLocaleDateString("pl-PL", {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
};

const shortDay = (key) =>
  new Date(`${key}T12:00:00`).toLocaleDateString("pl-PL", {
    day: "numeric",
    month: "short",
  });

const fmtDuration = (ms) => {
  if (!ms) return "0 min";
  if (ms < 30000) return "< 1 min";
  const totalMinutes = Math.round(ms / 60000);
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
};

const fmtDurationCompact = (ms) => {
  if (!ms) return "0m";
  if (ms < 30000) return "<1m";
  const totalMinutes = Math.round(ms / 60000);
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
};

const fmtClock = (ts) =>
  new Date(ts).toLocaleTimeString("pl-PL", {
    hour: "2-digit",
    minute: "2-digit",
  });

const som = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const addM = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);
const daysInMonth = (d) => new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();

const startOfWeek = (input) => {
  const d = new Date(input);
  const offset = (d.getDay() + 6) % 7;
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - offset);
  return d;
};

const weekKey = (ts) => dayKey(startOfWeek(ts).getTime());

const weekLabel = (key) => {
  const start = new Date(`${key}T12:00:00`);
  const end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `${shortDay(dayKey(start.getTime()))} - ${shortDay(dayKey(end.getTime()))}`;
};

const diffDaysBetweenKeys = (left, right) => {
  const a = new Date(`${left}T12:00:00`);
  const b = new Date(`${right}T12:00:00`);
  return Math.round((b - a) / 86400000);
};

const isSameMonth = (key, monthDate) => {
  const d = new Date(`${key}T12:00:00`);
  return d.getMonth() === monthDate.getMonth() && d.getFullYear() === monthDate.getFullYear();
};

const pickTopLabel = (items, field) => {
  const scores = {};
  for (const item of items || []) {
    if (!item?.[field]) continue;
    scores[item[field]] = (scores[item[field]] || 0) + 1;
  }
  return Object.entries(scores).sort((a, b) => b[1] - a[1])[0]?.[0] || null;
};

const toneForPercent = (value) => {
  if (value >= 85) return { bg: C.successBg, color: C.successText, border: "#CFE3D8" };
  if (value >= 65) return { bg: "#F4E8D9", color: "#8A5A24", border: "#E6C9A8" };
  return { bg: C.errorBg, color: C.errorText, border: "#E8C9B9" };
};

const toneForStatus = (status) => {
  if (status === "success") return { bg: C.successBg, color: C.successText, border: "#CFE3D8" };
  if (status === "error") return { bg: C.errorBg, color: C.errorText, border: "#E8C9B9" };
  if (status === "loading") return { bg: C.accentSoft, color: C.accent, border: "#D6DDF3" };
  return { bg: C.cardAlt, color: C.textSub, border: C.border };
};

const getErrorText = (error) => {
  const message = String(error?.message || error || "Nieznany błąd").trim();
  return message.length > 180 ? `${message.slice(0, 177)}...` : message;
};

const loadLocal = () => {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
};

const saveLocal = (list) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify((list || []).slice(0, 200)));
  } catch {}
};

const loadCloudSettings = () => {
  try {
    return JSON.parse(localStorage.getItem(CLOUD_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
};

const saveCloudSettings = (settings) => {
  try {
    localStorage.setItem(CLOUD_SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
};

const loadSupabaseSettings = () => {
  try {
    return JSON.parse(localStorage.getItem(SUPABASE_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
};

const saveSupabaseSettings = (settings) => {
  try {
    localStorage.setItem(SUPABASE_SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
};

const dedupe = (items) => {
  const m = new Map();
  for (const a of items || []) {
    if (!a?.id) continue;
    const ex = m.get(a.id);
    if (!ex || (ex.source !== "supabase" && a.source === "supabase")) m.set(a.id, a);
  }
  return [...m.values()].sort((a, b) => b.finishedAt - a.finishedAt);
};

const rowToQ = (row, i) => ({
  id: row.id ?? i + 1,
  questionNo: row.question_no ?? i + 1,
  question: row.question_text,
  options: { A: row.option_a, B: row.option_b, C: row.option_c, D: row.option_d },
  correct: row.correct_answer || null,
  explanation: row.explanation || "Brak wyjaśnienia.",
  category: row.category || "General",
  difficulty: normDiff(row.difficulty || "medium"),
  sourceType: row.source_type || "database",
});

function parseRows(rows, sourceFile = null) {
  return (rows || [])
    .map((row, i) => {
      const q = row.question ?? row.Question ?? row.pytanie ?? row.question_text;
      const a = row.A ?? row.a ?? row.option_a;
      const b = row.B ?? row.b ?? row.option_b;
      const c = row.C ?? row.c ?? row.option_c;
      const d = row.D ?? row.d ?? row.option_d;
      const correct = String(row.correct ?? row.Correct ?? row.correct_answer ?? "").trim().toUpperCase();
      if (!q || !a || !b || !c || !d) return null;

      return {
        id: `import-${i + 1}`,
        questionNo: Number(row.questionNo ?? i + 1),
        question: String(q).trim(),
        options: {
          A: String(a).trim(),
          B: String(b).trim(),
          C: String(c).trim(),
          D: String(d).trim(),
        },
        correct: optionKeys.includes(correct) ? correct : null,
        explanation: String(row.explanation ?? "Brak wyjaśnienia.").trim(),
        category: String(row.category ?? "General").trim(),
        difficulty: normDiff(row.difficulty ?? "medium"),
        sourceType: "spreadsheet",
        sourceFile,
      };
    })
    .filter(Boolean);
}

function parseTxt(text, sourceFile = "import.txt") {
  return [...String(text || "").replace(/\r/g, "").matchAll(/Question\s+#(\d+)\s*([\s\S]*?)(?=\nQuestion\s+#\d+|$)/g)]
    .map((m) => {
      const no = Number(m[1]);
      const block = m[2].trim();
      const p = block.match(/^([\s\S]*?)\nA\.\s*([\s\S]*?)\nB\.\s*([\s\S]*?)\nC\.\s*([\s\S]*?)\nD\.\s*([\s\S]*?)(?:\nView answer|$)/);
      if (!p) return null;

      return {
        id: `txt-${no}`,
        questionNo: no,
        question: p[1].replace(/\s+/g, " ").trim(),
        options: {
          A: p[2].replace(/\s+/g, " ").trim(),
          B: p[3].replace(/\s+/g, " ").trim(),
          C: p[4].replace(/\s+/g, " ").trim(),
          D: p[5].replace(/\s+/g, " ").trim(),
        },
        correct: null,
        explanation: "Brak odpowiedzi w pliku.",
        category: "Import",
        difficulty: "medium",
        sourceType: "txt_import",
        sourceFile,
      };
    })
    .filter(Boolean);
}

function buildCalDays(month) {
  const start = som(month);
  const fw = (start.getDay() + 6) % 7;
  const gs = new Date(start);
  gs.setDate(start.getDate() - fw);

  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(gs);
    d.setDate(gs.getDate() + i);
    return { date: d, key: dayKey(d.getTime()), inCurrent: d.getMonth() === start.getMonth() };
  });
}

function buildPlan(history, weakCat) {
  if (!history.length) {
    return {
      recommendation: "Ukończ kilka prób quizu, a system przygotuje lepszy plan.",
      improvements: [],
      weeklyPlan: [],
    };
  }

  const l5 = history.slice(0, 5);
  const avgAcc = Math.round(l5.reduce((s, a) => s + a.percent, 0) / l5.length);

  const wm = {};
  l5.forEach((a) => {
    if (a.weakestCategory) wm[a.weakestCategory] = (wm[a.weakestCategory] || 0) + 1;
  });

  const weak = Object.entries(wm).sort((a, b) => b[1] - a[1])[0]?.[0] || weakCat?.category || "Mieszane tematy";

  return {
    readiness: avgAcc >= 85 ? "Zaawansowany" : avgAcc >= 65 ? "Średniozaawansowany" : "Buduj podstawy",
    recommendation:
      avgAcc >= 85
        ? "Masz dobrą bazę. Przesuwaj ciężar na trudniejsze zestawy i presję czasu."
        : avgAcc >= 65
        ? "Jesteś blisko stabilnej formy. Największy zysk da praca na powtarzających się słabościach."
        : "Najpierw stabilizuj poprawność, dopiero później tempo.",
    improvements: [
      avgAcc < 70 ? "Nie przyspieszaj na siłę przy trudnych pytaniach." : "Utrzymuj dokładność i testuj trudniejsze warianty.",
      `Najczęściej wracający obszar do poprawy: ${weak}.`,
    ],
    weeklyPlan: [
      { day: "Pon", task: `Analiza błędów: ${weak}`, duration: "25m" },
      { day: "Wt", task: `Skupiony quiz: ${weak}`, duration: "30m" },
      { day: "Śr", task: "Zestaw mieszany z limitem czasu", duration: "20m" },
      { day: "Czw", task: "Krótki przegląd wyjaśnień i notatek", duration: "20m" },
      { day: "Pt", task: "Szybki quiz kontrolny", duration: "20m" },
      { day: "Sob", task: "Pełny próbny quiz", duration: "30m" },
      { day: "Nd", task: "Lekka powtórka i reset", duration: "15m" },
    ],
  };
}

function buildLocalTrainingSummary({ attempt, stats, questions, answers }) {
  const weakestAreas = stats.byCat
    .filter((x) => x.percent < 70)
    .sort((a, b) => a.percent - b.percent)
    .slice(0, 3)
    .map((x) => `${x.category} (${x.percent}%)`);

  const strongestAreas = stats.byCat
    .filter((x) => x.percent >= 70)
    .sort((a, b) => b.percent - a.percent)
    .slice(0, 3)
    .map((x) => `${x.category} (${x.percent}%)`);

  const hardMistakes = questions.filter((q) => q.difficulty === "hard" && answers[q.id] && !answers[q.id].isCorrect).length;

  const accuracy = attempt.percent;
  const pace = attempt.avgResponseMs;

  const wentWell = [];
  const watchOut = [];
  const improve = [];

  if (accuracy >= 80) wentWell.push("Dobra skuteczność w całej sesji.");
  if (stats.strongest?.category) wentWell.push(`Najlepiej poszedł obszar: ${stats.strongest.category}.`);
  if (pace > 0 && pace < 12000) wentWell.push("Tempo było płynne i bez dużych przestojów.");
  if (!wentWell.length) wentWell.push("Ta sesja daje już dobry materiał do dalszej poprawy.");

  if (accuracy < 70) watchOut.push("Dokładność wymaga poprawy — zwolnij przy podobnie brzmiących odpowiedziach.");
  if (hardMistakes > 0) watchOut.push(`Pojawiły się błędy w trudniejszych pytaniach: ${hardMistakes}.`);
  if (stats.weakest?.category) watchOut.push(`Najwięcej uwagi wymaga obszar: ${stats.weakest.category}.`);
  if (pace > 18000) watchOut.push("Średni czas odpowiedzi jest dość wysoki — pilnuj pierwszej selekcji odpowiedzi.");

  if (weakestAreas.length) improve.push(`Skup powtórkę na: ${weakestAreas.join(", ")}.`);
  if (strongestAreas.length) improve.push(`Utrwal też mocne obszary: ${strongestAreas.join(", ")}.`);
  improve.push("Po następnym treningu porównaj, czy poprawiły się wynik i tempo jednocześnie.");

  return {
    source: "local",
    title: accuracy >= 80 ? "Bardzo solidna sesja" : accuracy >= 65 ? "Dobra baza do poprawy" : "Sesja diagnostyczna",
    text: [
      `Co poszło dobrze: ${wentWell.join(" ")}`,
      `Co poszło słabiej: ${watchOut.join(" ") || "Brak większych sygnałów ostrzegawczych."}`,
      `Na co zwrócić uwagę: ${improve.join(" ")}`,
    ].join("\n\n"),
  };
}

async function fetchCloudTrainingSummary({ apiKey, model, attempt, stats, questions, answers }) {
  const wrongQuestions = questions
    .filter((q) => answers[q.id] && !answers[q.id].isCorrect)
    .slice(0, 8)
    .map((q) => ({
      question: q.question,
      correct: q.correct,
      selected: answers[q.id]?.selected,
      category: q.category,
      difficulty: q.difficulty,
    }));

  const prompt = [
    "Jesteś trenerem przygotowującym do nauki testowej.",
    "Napisz krótkie, konkretne podsumowanie treningu po polsku.",
    "Struktura: 1) Co poszło dobrze 2) Co poszło źle 3) Na co zwrócić uwagę 4) Obszary do poprawy.",
    "Maksymalnie 180 słów.",
    "Bądź praktyczny i zwięzły.",
    "Dane sesji:",
    JSON.stringify(
      {
        percent: attempt.percent,
        score: attempt.score,
        totalQuestions: attempt.totalQuestions,
        avgResponseMs: attempt.avgResponseMs,
        strongestCategory: stats.strongest?.category || null,
        weakestCategory: stats.weakest?.category || null,
        byCategory: stats.byCat,
        wrongQuestions,
      },
      null,
      2
    ),
  ].join("\n");

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: model || DEFAULT_MODEL,
      max_tokens: 350,
      temperature: 0.3,
      messages: [{ role: "user", content: prompt }],
    }),
  });

  if (!res.ok) throw new Error(`Cloud API error: ${res.status}`);

  const data = await res.json();
  const text = (data.content || []).filter((x) => x.type === "text").map((x) => x.text).join("\n").trim();
  if (!text) throw new Error("Cloud API returned empty summary");

  return { source: "cloud", title: "Podsumowanie AI", text };
}

const SAMPLES = [
  {
    id: 1,
    questionNo: 1,
    question: "You are the program manager. You need to formally define the scope of the new project. Which document is used?",
    options: { A: "Risk Register", B: "Project Charter", C: "Lessons Learned", D: "Issue Log" },
    correct: "B",
    explanation: "Project Charter formalnie autoryzuje projekt i określa jego ramy.",
    category: "PgMP",
    difficulty: "medium",
    sourceType: "sample",
  },
  {
    id: 2,
    questionNo: 2,
    question: "Which one of the following is not an output of the direct and manage program execution process?",
    options: {
      A: "Results of program work",
      B: "Program budget",
      C: "Change requests",
      D: "Request to terminate the program",
    },
    correct: "B",
    explanation: "Program budget jest wejściem, nie wyjściem procesu Direct and Manage.",
    category: "PgMP",
    difficulty: "medium",
    sourceType: "sample",
  },
  {
    id: 3,
    questionNo: 3,
    question: "What does VAT stand for in accounting?",
    options: {
      A: "Value Added Tax",
      B: "Variable Asset Transfer",
      C: "Verified Accounting Tool",
      D: "Value Allocation Table",
    },
    correct: "A",
    explanation: "VAT to podatek od wartości dodanej.",
    category: "Finance",
    difficulty: "easy",
    sourceType: "sample",
  },
];


const GLOBAL_CSS = `
  * { box-sizing: border-box; }
  html, body, #root { height: 100%; margin: 0; }
  :root {
    --paper: #F2F0E9;
    --paper-strong: #ECE7DA;
    --surface: rgba(255,255,255,.78);
    --surface-strong: rgba(255,255,255,.92);
    --ink: #1A1A1B;
    --ink-subtle: #5F645C;
  }
  body {
    background:
      radial-gradient(circle at top left, rgba(75,94,170,.12), transparent 28%),
      radial-gradient(circle at 85% 10%, rgba(176,137,104,.12), transparent 24%),
      linear-gradient(180deg, #F5F1E8 0%, #EFE8DB 100%);
    color: #1A1A1B;
    font-family: "Aptos", "Segoe UI", "Trebuchet MS", sans-serif;
  }

  button, input, select, textarea { font-family: inherit; }

  .app-shell {
    min-height: 100vh;
    background:
      radial-gradient(circle at top left, rgba(75,94,170,.08), transparent 24%),
      radial-gradient(circle at bottom right, rgba(130,148,196,.10), transparent 22%),
      linear-gradient(180deg, rgba(255,255,255,.1), rgba(255,255,255,0)),
      var(--paper);
    padding: 24px;
  }

  .app-frame {
    max-width: 1520px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: 300px minmax(0, 1fr);
    gap: 22px;
    align-items: start;
    min-height: calc(100vh - 48px);
  }

  .sidebar {
    position: sticky;
    top: 24px;
    background:
      linear-gradient(180deg, rgba(255,255,255,.88), rgba(250,248,242,.92)),
      rgba(255,255,255,.72);
    backdrop-filter: blur(18px);
    border: 1px solid rgba(220,215,201,.95);
    border-radius: 30px;
    padding: 20px;
    box-shadow: 0 18px 48px rgba(44,62,80,.08);
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-height: calc(100vh - 48px);
    overflow: hidden;
  }

  .brand-panel {
    padding: 18px;
    border-radius: 24px;
    background:
      linear-gradient(145deg, rgba(75,94,170,.13), rgba(255,255,255,.66) 58%, rgba(176,137,104,.10));
    border: 1px solid rgba(220,215,201,.9);
  }

  .brand-title,
  .workspace-title {
    font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
    letter-spacing: -.03em;
  }

  .nav-rail {
    display: flex;
    gap: 10px;
    overflow-x: auto;
    padding-bottom: 4px;
  }

  .sidebar-footer {
    margin-top: auto;
    display: grid;
    gap: 12px;
  }

  .sidebar-footer > :not(.sidebar-primary) {
    display: none;
  }

  .progress-panel {
    display: grid;
    gap: 12px;
  }

  .mini-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0,1fr));
    gap: 10px;
  }

  .content-area {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }

  .workspace-hero {
    position: sticky;
    top: 24px;
    z-index: 5;
    display: grid;
    gap: 16px;
    padding: 22px;
    border-radius: 30px;
    background:
      linear-gradient(135deg, rgba(75,94,170,.10), rgba(255,255,255,.95) 42%, rgba(246,242,233,.98));
    border: 1px solid rgba(220,215,201,.95);
    box-shadow: 0 18px 46px rgba(44,62,80,.06);
  }

  .workspace-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 18px;
    flex-wrap: wrap;
  }

  .top-nav-shell {
    display: grid;
    gap: 14px;
  }

  .workspace-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .workspace-stats {
    display: none;
    grid-template-columns: repeat(4, minmax(0,1fr));
    gap: 12px;
  }

  .workspace-stat {
    padding: 14px 16px;
    border-radius: 18px;
    border: 1px solid rgba(220,215,201,.95);
    background: rgba(255,255,255,.72);
  }

  .workspace-stack {
    display: grid;
    gap: 16px;
  }

  .tinyLabel {
    font-size: 11px;
    font-weight: 700;
    color: #5F645C;
    letter-spacing: .04em;
    text-transform: uppercase;
  }

  .quiz-inline-stats {
    display: grid;
    grid-template-columns: repeat(4, minmax(0,1fr));
    gap: 12px;
    margin-top: 18px;
  }

  .soft-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid #DCD7C9;
    background: #F7F4EC;
    color: #5F645C;
    font-size: 11px;
    font-weight: 700;
  }

  .tab-btn {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 10px;
    justify-content: flex-start;
    padding: 14px;
    border-radius: 18px;
    border: 1px solid rgba(220,215,201,.7);
    background: rgba(255,255,255,.38);
    color: #5F645C;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all .18s ease;
  }

  .tab-btn:hover {
    background: rgba(255,255,255,.74);
    color: #2C3E50;
    border-color: #D8D2C4;
  }

  .tab-btn.active {
    background: linear-gradient(135deg, #4B5EAA, #8294C4);
    color: white;
    box-shadow: 0 8px 18px rgba(75,94,170,.18);
  }

  .tab-btn-icon {
    width: 34px;
    height: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 12px;
    background: rgba(255,255,255,.48);
  }

  .tab-btn.active .tab-btn-icon {
    background: rgba(255,255,255,.2);
  }

  .tab-btn-text {
    display: grid;
    gap: 2px;
    text-align: left;
  }

  .tab-btn-text strong {
    font-size: 14px;
    font-weight: 700;
  }

  .tab-btn-text span {
    font-size: 11px;
    opacity: .78;
  }

  .tab-btn.top {
    width: auto;
    min-width: 168px;
    background: rgba(255,255,255,.58);
  }

  .field-help {
    font-size: 12px;
    color: #8C8A7E;
    line-height: 1.45;
    margin-top: 6px;
  }

  .settings-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
    gap: 16px;
    align-items: start;
  }

  .settings-stack {
    display: grid;
    gap: 16px;
    align-content: start;
  }

  .settings-main-card {
    display: grid;
    gap: 22px;
  }

  .settings-section-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
  }

  .settings-actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }

  .calendar-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0,1fr));
    gap: 10px;
  }

  .calendar-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
    gap: 16px;
  }

  .calendar-side {
    display: grid;
    gap: 16px;
    min-height: 0;
  }

  .calendar-bottom-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
  }

  .calendar-session-list {
    display: grid;
    gap: 10px;
    max-height: 520px;
    overflow: auto;
    padding-right: 4px;
  }

  @media (max-width: 1180px) {
    .app-frame { grid-template-columns: 1fr; }
    .sidebar {
      position: static;
      min-height: auto;
    }
    .workspace-hero { position: static; }
    .settings-grid { grid-template-columns: 1fr; }
    .calendar-layout { grid-template-columns: 1fr; }
    .calendar-bottom-grid { grid-template-columns: 1fr; }
  }

  @media (max-width: 840px) {
    .quiz-inline-stats { grid-template-columns: repeat(2, minmax(0,1fr)); }
    .workspace-stats { grid-template-columns: repeat(2, minmax(0,1fr)); }
    .calendar-grid { gap: 8px; }
    .calendar-session-list { max-height: none; }
    .mini-grid { grid-template-columns: 1fr; }
    .settings-section-grid { grid-template-columns: 1fr; }
    .tab-btn.top { min-width: 150px; }
  }
`;

// ── App ───────────────────────────────────────────────────────────────────────
function QuizAbcdApp() {
  const initialCloud = loadCloudSettings();
  const initialSupabase = loadSupabaseSettings();

  const [questionPool, setQuestionPool] = useState(SAMPLES);
  const [quizLength, setQuizLength] = useState(10);
  const [questions, setQuestions] = useState(() => SAMPLES.slice(0, 10));
  const [idx, setIdx] = useState(0);
  const [selected, setSelected] = useState(null);
  const [answers, setAnswers] = useState({});
  const [showResult, setShowResult] = useState(false);
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [qStartedAt, setQStartedAt] = useState(() => Date.now());
  const [finishedAt, setFinishedAt] = useState(null);

  const [history, setHistory] = useState(() => loadLocal());
  const [importMsg, setImportMsg] = useState(null);
  const [activeTab, setActiveTab] = useState("quiz");

  const [calMonth, setCalMonth] = useState(() => som(new Date()));
  const [selectedCalDay, setSelectedCalDay] = useState(() => dayKey(Date.now()));

  const [chatStatus, setChatStatus] = useState("idle");
  const [chatRes, setChatRes] = useState("");

  const [cloudApiEnabled, setCloudApiEnabled] = useState(Boolean(initialCloud.cloudApiEnabled));
  const [cloudApiKey, setCloudApiKey] = useState(initialCloud.cloudApiKey || "");
  const [cloudModel, setCloudModel] = useState(initialCloud.cloudModel || DEFAULT_MODEL);
  const [supabaseUrl, setSupabaseUrl] = useState(initialSupabase.supabaseUrl || DEFAULT_SUPABASE_URL);
  const [supabaseAnonKey, setSupabaseAnonKey] = useState(initialSupabase.supabaseAnonKey || DEFAULT_SUPABASE_ANON_KEY);
  const [supabaseCheck, setSupabaseCheck] = useState({ status: "idle", message: "Nie sprawdzono połączenia." });
  const [cloudCheck, setCloudCheck] = useState({ status: "idle", message: "Nie sprawdzono połączenia." });

  const [trainingSummary, setTrainingSummary] = useState(null);
  const [trainingSummaryStatus, setTrainingSummaryStatus] = useState("idle");

  const fileRef = useRef(null);

  useEffect(() => {
    saveCloudSettings({ cloudApiEnabled, cloudApiKey, cloudModel });
  }, [cloudApiEnabled, cloudApiKey, cloudModel]);

  useEffect(() => {
    saveSupabaseSettings({ supabaseUrl, supabaseAnonKey });
  }, [supabaseUrl, supabaseAnonKey]);

  useEffect(() => {
    setSupabaseCheck({ status: "idle", message: "Nie sprawdzono połączenia." });
  }, [supabaseUrl, supabaseAnonKey]);

  useEffect(() => {
    setCloudCheck({ status: "idle", message: "Nie sprawdzono połączenia." });
  }, [cloudApiEnabled, cloudApiKey, cloudModel]);

  const supabaseConfig = useMemo(
    () => ({
      url: supabaseUrl.trim().replace(/\/+$/, ""),
      apiKey: supabaseAnonKey.trim(),
    }),
    [supabaseUrl, supabaseAnonKey]
  );
  const sbEnabled = useMemo(() => hasSupabaseConfig(supabaseConfig), [supabaseConfig]);

  const total = questions.length;
  const current = questions[idx] || SAMPLES[0];
  const answeredCount = Object.keys(answers).length;
  const score = useMemo(() => Object.values(answers).filter((a) => a.isCorrect).length, [answers]);

  const startQuiz = useCallback(
    (customPool, customLength) => {
      const pool = customPool || questionPool;
      const len = customLength !== undefined ? customLength : quizLength;
      const shuffled = [...pool].sort(() => 0.5 - Math.random());
      const selectedQuestions = len === "all" ? shuffled : shuffled.slice(0, len);

      if (customPool) setQuestionPool(customPool);
      setQuestions(selectedQuestions.length ? selectedQuestions : pool);
      setIdx(0);
      setSelected(null);
      setAnswers({});
      setShowResult(false);
      setStartedAt(Date.now());
      setQStartedAt(Date.now());
      setFinishedAt(null);
      setActiveTab("quiz");
      setChatStatus("idle");
      setChatRes("");
      setTrainingSummary(null);
      setTrainingSummaryStatus("idle");
    },
    [questionPool, quizLength]
  );

  const loadQfromDB = useCallback(async () => {
    if (!sbEnabled) return;
    try {
      const rows = await sbSelect(supabaseConfig, "quiz_questions", "is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length) return;
      const parsed = rows.map(rowToQ);
      setQuestionPool(parsed);
      const shuffled = [...parsed].sort(() => 0.5 - Math.random());
      setQuestions(shuffled.slice(0, quizLength === "all" ? shuffled.length : quizLength));
    } catch {}
  }, [quizLength, sbEnabled, supabaseConfig]);

  const loadAttempts = useCallback(async () => {
    if (!sbEnabled) return;
    try {
      const rows = await sbSelect(supabaseConfig, "quiz_attempts", "order=finished_at.desc&limit=100");
      const mapped = rows.map((r) => ({
        id: r.attempt_id,
        finishedAt: new Date(r.finished_at).getTime(),
        totalQuestions: r.total_questions,
        score: r.score,
        percent: r.percent,
        mastery: r.mastery,
        avgResponseMs: r.avg_response_ms,
        totalTimeMs: r.total_time_ms,
        strongestCategory: r.strongest_category,
        weakestCategory: r.weakest_category,
        source: "supabase",
      }));
      setHistory((prev) => {
        const merged = dedupe([...mapped, ...prev]);
        saveLocal(merged);
        return merged;
      });
    } catch {}
  }, [sbEnabled, supabaseConfig]);

  useEffect(() => {
    loadQfromDB();
    loadAttempts();
  }, [loadQfromDB, loadAttempts]);

  const checkSupabaseConnection = useCallback(async () => {
    if (!sbEnabled) {
      setSupabaseCheck({
        status: "error",
        message: "Wpisz poprawny Supabase URL oraz pełny publishable key (`sb_publishable_...`) albo pełny anon JWT z 3 segmentami.",
      });
      return;
    }

    setSupabaseCheck({ status: "loading", message: "Sprawdzam połączenie z Supabase..." });

    try {
      const rows = await sbSelect(supabaseConfig, "quiz_questions", "select=id&limit=1");
      setSupabaseCheck({
        status: "success",
        message: `Połączenie działa. Odczyt zakończony poprawnie${rows.length ? " i zwrócono dane." : ", ale tabela jest pusta."}`,
      });
    } catch (error) {
      setSupabaseCheck({ status: "error", message: getErrorText(error) });
    }
  }, [sbEnabled, supabaseConfig]);

  const checkCloudConnection = useCallback(async () => {
    if (!cloudApiEnabled) {
      setCloudCheck({ status: "error", message: "Cloud AI jest wyłączone." });
      return;
    }

    if (!cloudApiKey.trim()) {
      setCloudCheck({ status: "error", message: "Brakuje klucza API." });
      return;
    }

    setCloudCheck({ status: "info", message: CLOUD_BROWSER_NOTICE });
  }, [cloudApiEnabled, cloudApiKey]);

  const handleAnswer = useCallback(
    (key) => {
      if (selected || showResult) return;
      setSelected(key);
      setAnswers((prev) => ({
        ...prev,
        [current.id]: {
          questionId: current.id,
          selected: key,
          correct: current.correct,
          isCorrect: current.correct ? key === current.correct : false,
          responseTimeMs: Date.now() - qStartedAt,
          category: current.category || "General",
          difficulty: current.difficulty || "medium",
        },
      }));
    },
    [current, qStartedAt, selected, showResult]
  );

  const next = useCallback(() => {
    if (idx < total - 1) {
      const ni = idx + 1;
      setIdx(ni);
      setSelected(answers[questions[ni].id]?.selected ?? null);
      setQStartedAt(Date.now());
      setChatStatus("idle");
      setChatRes("");
    } else {
      setFinishedAt(Date.now());
      setShowResult(true);
      setActiveTab("results");
    }
  }, [answers, idx, questions, total]);

  const prev = useCallback(() => {
    if (idx > 0) {
      const ni = idx - 1;
      setIdx(ni);
      setSelected(answers[questions[ni].id]?.selected ?? null);
      setChatStatus("idle");
      setChatRes("");
    }
  }, [answers, idx, questions]);

  const stats = useMemo(() => {
    const list = questions.map((q) => ({ q, a: answers[q.id] })).filter((x) => x.a);
    const totalTimeMs = (finishedAt ?? Date.now()) - startedAt;
    const avgResponseMs = list.length ? list.reduce((s0, x) => s0 + x.a.responseTimeMs, 0) / list.length : 0;
    const correctCount = list.filter((x) => x.a.isCorrect).length;

    const wTotal = questions.reduce((s0, q) => s0 + (diffW[q.difficulty || "medium"] || 1.5), 0);
    const wScore = list.reduce((s0, x) => s0 + (x.a.isCorrect ? diffW[x.q.difficulty || "medium"] || 1.5 : 0), 0);
    const mastery = wTotal ? Math.round((wScore / wTotal) * 100) : 0;

    const byCat = Object.values(
      questions.reduce((acc, q) => {
        const cat = q.category || "General";
        if (!acc[cat]) acc[cat] = { category: cat, total: 0, correct: 0 };
        acc[cat].total++;
        if (answers[q.id]?.isCorrect) acc[cat].correct++;
        return acc;
      }, {})
    ).map((c) => ({ ...c, percent: Math.round((c.correct / c.total) * 100) }));

    const weakest = byCat.length ? [...byCat].sort((a, b) => a.percent - b.percent)[0] : null;
    const strongest = byCat.length ? [...byCat].sort((a, b) => b.percent - a.percent)[0] : null;

    return {
      totalTimeMs,
      avgResponseMs,
      correctCount,
      incorrectCount: list.length - correctCount,
      mastery,
      byCat,
      weakest,
      strongest,
    };
  }, [answers, questions, startedAt, finishedAt]);

  const attemptDraft = useMemo(() => {
    if (!showResult || !finishedAt) return null;
    return {
      id: `${finishedAt}-${Math.random().toString(36).slice(2, 7)}`,
      finishedAt,
      totalQuestions: total,
      score,
      percent: Math.round((score / Math.max(total, 1)) * 100),
      mastery: stats.mastery,
      avgResponseMs: Math.round(stats.avgResponseMs),
      totalTimeMs: Math.round(stats.totalTimeMs),
      strongestCategory: stats.strongest?.category || null,
      weakestCategory: stats.weakest?.category || null,
      source: "local",
    };
  }, [showResult, finishedAt, total, score, stats]);

  useEffect(() => {
    if (!attemptDraft) return;

    setHistory((prev) => {
      if (prev.some((a) => a.finishedAt === attemptDraft.finishedAt)) return prev;
      const merged = dedupe([attemptDraft, ...prev]);
      saveLocal(merged);
      return merged;
    });

    if (sbEnabled) {
      sbInsert(supabaseConfig, "quiz_attempts", {
        attempt_id: attemptDraft.id,
        finished_at: new Date(attemptDraft.finishedAt).toISOString(),
        total_questions: attemptDraft.totalQuestions,
        score: attemptDraft.score,
        percent: attemptDraft.percent,
        mastery: attemptDraft.mastery,
        avg_response_ms: attemptDraft.avgResponseMs,
        total_time_ms: attemptDraft.totalTimeMs,
        strongest_category: attemptDraft.strongestCategory,
        weakest_category: attemptDraft.weakestCategory,
      })
        .then(() => loadAttempts())
        .catch(() => {});
    }
  }, [attemptDraft, loadAttempts, sbEnabled, supabaseConfig]);

  useEffect(() => {
    if (!attemptDraft) return;
    let cancelled = false;

    const runSummary = async () => {
      setTrainingSummaryStatus("loading");

      const local = buildLocalTrainingSummary({
        attempt: attemptDraft,
        stats,
        questions,
        answers,
      });

      if (!cloudApiEnabled || !cloudApiKey.trim()) {
        if (!cancelled) {
          setTrainingSummary(local);
          setTrainingSummaryStatus("done");
        }
        return;
      }

      if (!cancelled) {
        setTrainingSummary({
          ...local,
          text: `${local.text}\n\nCloud AI pozostaje wyłączone w praktyce dla tej wersji frontendu. Żeby użyć Claude, podłącz backend proxy lub Edge Function i tam trzymaj klucz API.`,
        });
        setTrainingSummaryStatus("done");
      }
    };

    runSummary();
    return () => {
      cancelled = true;
    };
  }, [attemptDraft, cloudApiEnabled, cloudApiKey, cloudModel, stats, questions, answers]);

  const askAI = useCallback(async () => {
    if (chatStatus === "loading") return;
    setChatStatus("loading");

    setTimeout(() => {
      setChatRes(
        `Kategoria: "${current.category}". Najpierw porównaj pojęcia kluczowe w odpowiedziach, potem wyklucz zbyt ogólne lub zbyt wąskie opcje. W tym pytaniu odpowiedź ${current.correct} najlepiej pasuje do standardowej definicji.`
      );
      setChatStatus("loaded");
    }, 700);
  }, [chatStatus, current]);

  const handleImport = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;

      try {
        let parsed = [];
        if (file.name.toLowerCase().endsWith(".txt")) {
          parsed = parseTxt(await file.text(), file.name);
        } else {
          const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
          parsed = parseRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: "" }), file.name);
        }

        if (!parsed.length) {
          setImportMsg("Import nieudany – sprawdź format pliku.");
          return;
        }

        startQuiz(parsed, quizLength);
        setImportMsg(`✓ Zaimportowano ${parsed.length} pytań z "${file.name}"`);
      } catch (err) {
        setImportMsg(`✗ Błąd: ${err.message}`);
      } finally {
        e.target.value = "";
      }
    },
    [quizLength, startQuiz]
  );

  useEffect(() => {
    const h = (e) => {
      if (e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA" || e.target?.tagName === "SELECT") return;

      const k = e.key.toUpperCase();

      if (!showResult && !selected && optionKeys.includes(k)) {
        e.preventDefault();
        handleAnswer(k);
        return;
      }

      if (e.key === "Enter" && selected) {
        e.preventDefault();
        next();
        return;
      }

      if (k === "R") {
        e.preventDefault();
        startQuiz(questionPool, quizLength);
        return;
      }

      if ((e.key === "ArrowRight" || e.key === "ArrowDown") && selected) {
        e.preventDefault();
        next();
        return;
      }

      if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        prev();
      }
    };

    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [handleAnswer, next, prev, startQuiz, selected, showResult, questionPool, quizLength]);

  const uniq = useMemo(() => dedupe(history), [history]);

  const attemptsByDay = useMemo(() => {
    const m = {};
    uniq.forEach((a) => {
      const k = dayKey(a.finishedAt);
      if (!m[k]) m[k] = [];
      m[k].push(a);
    });
    Object.values(m).forEach((list) => list.sort((a, b) => b.finishedAt - a.finishedAt));
    return m;
  }, [uniq]);

  const dayMap = useMemo(() => {
    const m = {};
    Object.entries(attemptsByDay).forEach(([key, list]) => {
      const avgPercent = Math.round(list.reduce((s0, a) => s0 + a.percent, 0) / list.length);
      const avgMastery = Math.round(list.reduce((s0, a) => s0 + a.mastery, 0) / list.length);
      const avgResponseMs = Math.round(list.reduce((s0, a) => s0 + a.avgResponseMs, 0) / list.length);
      const totalTimeMs = list.reduce((s0, a) => s0 + a.totalTimeMs, 0);

      m[key] = {
        count: list.length,
        avgPercent,
        avgMastery,
        avgResponseMs,
        totalTimeMs,
        bestPercent: Math.max(...list.map((a) => a.percent)),
        strongestCategory: pickTopLabel(list, "strongestCategory"),
        weakestCategory: pickTopLabel(list, "weakestCategory"),
      };
    });
    return m;
  }, [attemptsByDay]);

  const activeDayKeys = useMemo(() => Object.keys(dayMap).sort(), [dayMap]);

  const streak = useMemo(() => {
    if (!activeDayKeys.length) return 0;

    let cursor = new Date();
    cursor.setHours(0, 0, 0, 0);

    if (!dayMap[dayKey(cursor.getTime())]) {
      cursor.setDate(cursor.getDate() - 1);
      if (!dayMap[dayKey(cursor.getTime())]) return 0;
    }

    let value = 0;
    while (dayMap[dayKey(cursor.getTime())]) {
      value++;
      cursor.setDate(cursor.getDate() - 1);
    }
    return value;
  }, [activeDayKeys, dayMap]);

  const longestStreak = useMemo(() => {
    if (!activeDayKeys.length) return 0;
    let best = 0;
    let current = 0;
    let prev = null;

    activeDayKeys.forEach((key) => {
      current = prev && diffDaysBetweenKeys(prev, key) === 1 ? current + 1 : 1;
      best = Math.max(best, current);
      prev = key;
    });

    return best;
  }, [activeDayKeys]);

  const lastActiveDayKey = activeDayKeys[activeDayKeys.length - 1] || null;

  useEffect(() => {
    if (selectedCalDay && isSameMonth(selectedCalDay, calMonth)) return;

    const fallback =
      [...activeDayKeys]
        .reverse()
        .find((key) => isSameMonth(key, calMonth)) || dayKey(som(calMonth).getTime());

    setSelectedCalDay(fallback);
  }, [activeDayKeys, calMonth, selectedCalDay]);

  const plan = useMemo(() => buildPlan(uniq, stats.weakest), [uniq, stats.weakest]);
  const calDays = useMemo(() => buildCalDays(calMonth), [calMonth]);

  const selectedDayAttempts = attemptsByDay[selectedCalDay] || [];
  const selectedDaySummary = useMemo(() => {
    if (!selectedDayAttempts.length) return null;
    return {
      count: selectedDayAttempts.length,
      avgPercent: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.percent, 0) / selectedDayAttempts.length),
      avgMastery: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.mastery, 0) / selectedDayAttempts.length),
      bestPercent: Math.max(...selectedDayAttempts.map((a) => a.percent)),
      totalTimeMs: selectedDayAttempts.reduce((s0, a) => s0 + a.totalTimeMs, 0),
      avgTimeMs: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.totalTimeMs, 0) / selectedDayAttempts.length),
      avgResponseMs: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.avgResponseMs, 0) / selectedDayAttempts.length),
      strongestCategory: pickTopLabel(selectedDayAttempts, "strongestCategory"),
      weakestCategory: pickTopLabel(selectedDayAttempts, "weakestCategory"),
    };
  }, [selectedDayAttempts]);

  const monthAttempts = useMemo(() => uniq.filter((a) => isSameMonth(dayKey(a.finishedAt), calMonth)), [uniq, calMonth]);
  const monthDaysActive = useMemo(() => Object.keys(dayMap).filter((key) => isSameMonth(key, calMonth)).length, [dayMap, calMonth]);
  const monthSummary = useMemo(() => {
    if (!monthAttempts.length) {
      return {
        totalSessions: 0,
        avgPercent: 0,
        avgMastery: 0,
        totalTimeMs: 0,
        avgResponseMs: 0,
        bestPercent: 0,
        completionRate: 0,
        strongestCategory: null,
        weakestCategory: null,
      };
    }

    return {
      totalSessions: monthAttempts.length,
      avgPercent: Math.round(monthAttempts.reduce((s0, a) => s0 + a.percent, 0) / monthAttempts.length),
      avgMastery: Math.round(monthAttempts.reduce((s0, a) => s0 + a.mastery, 0) / monthAttempts.length),
      totalTimeMs: monthAttempts.reduce((s0, a) => s0 + a.totalTimeMs, 0),
      avgResponseMs: Math.round(monthAttempts.reduce((s0, a) => s0 + a.avgResponseMs, 0) / monthAttempts.length),
      bestPercent: Math.max(...monthAttempts.map((a) => a.percent)),
      completionRate: Math.round((monthDaysActive / Math.max(daysInMonth(calMonth), 1)) * 100),
      strongestCategory: pickTopLabel(monthAttempts, "strongestCategory"),
      weakestCategory: pickTopLabel(monthAttempts, "weakestCategory"),
    };
  }, [monthAttempts, monthDaysActive, calMonth]);
  const monthAvg = monthSummary.avgPercent;

  const bestStudyDay = useMemo(() => {
    const entries = Object.entries(dayMap).filter(([key]) => isSameMonth(key, calMonth));
    if (!entries.length) return null;
    return entries.sort((a, b) => b[1].count - a[1].count || b[1].avgPercent - a[1].avgPercent)[0];
  }, [dayMap, calMonth]);

  const weekdaySummary = useMemo(
    () =>
      weekdayLabels.map((label, index) => {
        const list = monthAttempts.filter((attempt) => {
          const day = new Date(attempt.finishedAt);
          return (day.getDay() + 6) % 7 === index;
        });

        return {
          label,
          count: list.length,
          avgPercent: list.length ? Math.round(list.reduce((s0, a) => s0 + a.percent, 0) / list.length) : 0,
          totalTimeMs: list.reduce((s0, a) => s0 + a.totalTimeMs, 0),
        };
      }),
    [monthAttempts]
  );

  const bestWeekday = useMemo(() => {
    const active = weekdaySummary.filter((day) => day.count);
    if (!active.length) return null;
    return [...active].sort((a, b) => b.count - a.count || b.avgPercent - a.avgPercent)[0];
  }, [weekdaySummary]);

  const weeklySummary = useMemo(() => {
    const grouped = {};

    monthAttempts.forEach((attempt) => {
      const key = weekKey(attempt.finishedAt);
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(attempt);
    });

    return Object.entries(grouped)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, list]) => ({
        key,
        label: weekLabel(key),
        count: list.length,
        avgPercent: Math.round(list.reduce((s0, a) => s0 + a.percent, 0) / list.length),
        avgMastery: Math.round(list.reduce((s0, a) => s0 + a.mastery, 0) / list.length),
        totalTimeMs: list.reduce((s0, a) => s0 + a.totalTimeMs, 0),
      }));
  }, [monthAttempts]);

  const maxCountInMonth = useMemo(() => {
    const counts = Object.entries(dayMap)
      .filter(([key]) => isSameMonth(key, calMonth))
      .map(([, v]) => v.count);
    return counts.length ? Math.max(...counts) : 0;
  }, [dayMap, calMonth]);

  const getHeat = (info) => {
    if (!info) return { bg: "transparent", border: C.border };
    const ratio = maxCountInMonth ? info.count / maxCountInMonth : info.count > 0 ? 1 : 0;
    if (ratio >= 0.8) return { bg: "#DDE5FF", border: "#BDD0FF", glow: "0 10px 18px rgba(75,94,170,.08)" };
    if (ratio >= 0.5) return { bg: "#EAF0FF", border: "#D5E0FF", glow: "0 8px 14px rgba(75,94,170,.05)" };
    if (ratio > 0) return { bg: "#F4F7FF", border: C.border, glow: "none" };
    return { bg: "transparent", border: C.border };
  };

  const todayCalKey = dayKey(Date.now());

  const TABS = [
    { id: "quiz", label: "Quiz", icon: <IcoBrain size={15} />, eyebrow: "Tryb pracy", description: "Bieżąca sesja, pytania i tempo odpowiedzi." },
    { id: "results", label: "Wyniki", icon: <IcoTrophy size={15} />, eyebrow: "Analiza", description: "Ostatni wynik, mastery i feedback po treningu." },
    { id: "calendar", label: "Kalendarz", icon: <IcoCalendar size={15} />, eyebrow: "Historia", description: "Rytm nauki dzień po dniu, tygodnie i serie." },
    { id: "plan", label: "Plan", icon: <IcoBook size={15} />, eyebrow: "Strategia", description: "Rekomendacje i tygodniowy plan rozwoju." },
    { id: "settings", label: "Ustawienia", icon: <IcoSettings size={15} />, eyebrow: "Zaplecze", description: "Źródła danych, import i integracje." },
  ];
  const activeTabMeta = TABS.find((tab) => tab.id === activeTab) || TABS[0];

  const pct = answeredCount > 0 ? Math.round((stats.correctCount / answeredCount) * 100) : 0;
  const sessionProgress = total ? Math.round(((idx + 1) / total) * 100) : 0;
  const sidebarMetrics = [
    {
      label: "Postęp",
      value: `${idx + 1}/${total}`,
      note: `${sessionProgress}% bieżącej sesji`,
    },
    {
      label: "Skuteczność",
      value: `${pct || 0}%`,
      note: answeredCount ? `${stats.correctCount}/${answeredCount} trafnych` : "Po pierwszej odpowiedzi pojawi się wynik",
    },
    {
      label: "Śr. czas",
      value: answeredCount ? fmt(stats.avgResponseMs) : "—",
      note: answeredCount ? "na jedną odpowiedź" : "Brak danych czasowych",
    },
    {
      label: "Seria",
      value: `${streak} dni`,
      note: longestStreak ? `Rekord: ${longestStreak} dni` : "Zacznij od pierwszej sesji",
    },
  ];

  const QuizView = () => {
    const diffColor = { easy: C.success, medium: C.yellow, hard: C.error }[current.difficulty || "medium"];

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ ...s.card, padding: "24px 24px 22px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
            <span className="tinyLabel">Pytanie {idx + 1} / {total}</span>
            <span style={{ width: 4, height: 4, borderRadius: "50%", background: C.muted }} />
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                padding: "5px 10px",
                borderRadius: 999,
                background: C.tagBg,
                color: C.tagText,
                textTransform: "capitalize",
              }}
            >
              {current.difficulty}
            </span>
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                padding: "5px 10px",
                borderRadius: 999,
                background: C.tagBg,
                color: C.tagText,
              }}
            >
              {current.category}
            </span>
          </div>

          <div
            style={{
              height: 8,
              borderRadius: 999,
              background: "#E9E5DA",
              overflow: "hidden",
              marginBottom: 18,
            }}
          >
            <div
              style={{
                width: `${((idx + 1) / total) * 100}%`,
                height: "100%",
                background: "linear-gradient(90deg, #4B5EAA, #8294C4)",
                borderRadius: 999,
                transition: "width .2s ease",
              }}
            />
          </div>

          <h2
            style={{
              fontSize: 29,
              fontWeight: 500,
              color: C.textStrong,
              lineHeight: 1.38,
              margin: 0,
              letterSpacing: "-0.01em",
            }}
          >
            {current.question}
          </h2>

          <div style={{ marginTop: 12, fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
            Wybierz jedną odpowiedź. Po wyborze od razu zobaczysz informację zwrotną i możesz przejść dalej.
          </div>

        </div>

        <div style={{ display: "grid", gap: 12 }}>
          {optionKeys.map((key) => {
            const isSel = selected === key;
            const isCorr = current.correct === key;
            const reveal = !!selected;

            let bg = "#FFFFFF";
            let border = C.border;
            let color = C.textStrong;
            let labelBg = C.cardAlt;
            let labelColor = C.textSub;
            let iconEl = null;

            if (reveal) {
              if (isCorr) {
                bg = C.successBg;
                border = C.success;
                color = C.successText;
                labelBg = C.success;
                labelColor = "#fff";
                iconEl = <IcoCheck size={15} />;
              } else if (isSel) {
                bg = C.errorBg;
                border = C.error;
                color = C.errorText;
                labelBg = C.error;
                labelColor = "#fff";
                iconEl = <IcoCross size={15} />;
              }
            }

            return (
              <button
                key={key}
                onClick={() => handleAnswer(key)}
                disabled={!!selected}
                className="answer-option"
                style={{
                  "--answer-bg": bg,
                  "--answer-border": border,
                  "--answer-text": color,
                  "--answer-label-bg": labelBg,
                  "--answer-label-color": labelColor,
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "18px 18px",
                  borderRadius: 18,
                  cursor: selected ? "default" : "pointer",
                  textAlign: "left",
                  width: "100%",
                }}
              >
                <span
                  className="answer-option-label"
                  style={{
                    width: 42,
                    height: 42,
                    borderRadius: 14,
                    fontSize: 13,
                    fontWeight: 700,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                    border: `1px solid ${reveal && (isCorr || isSel) ? "transparent" : C.border}`,
                  }}
                >
                  {key}
                </span>

                <span
                  className="answer-option-text"
                  style={{
                    fontSize: 15,
                    fontWeight: 400,
                    flex: 1,
                    lineHeight: 1.58,
                  }}
                >
                  {current.options[key]}
                </span>

                {iconEl && <span style={{ flexShrink: 0, color: isCorr ? C.success : C.error, display: "flex" }}>{iconEl}</span>}
              </button>
            );
          })}
        </div>

        {selected && (
          <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: selected === current.correct ? C.success : C.error,
                marginBottom: 6,
              }}
            >
              {current.correct ? (selected === current.correct ? "✓ Dobra odpowiedź." : `✗ Poprawna odpowiedź: ${current.correct}`) : "Klucz niedostępny."}
            </div>

            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{current.explanation}</div>

            {chatStatus === "idle" && (
              <button onClick={askAI} style={{ ...s.btn("soft"), marginTop: 12, fontSize: 12, padding: "8px 14px" }}>
                <IcoChat size={13} /> Wyjaśnij szerzej
              </button>
            )}

            {chatStatus === "loading" && <div style={{ fontSize: 12, color: C.accent, marginTop: 10 }}>Analiza odpowiedzi...</div>}

            {chatStatus === "loaded" && (
              <div
                style={{
                  fontSize: 13,
                  color: C.text,
                  marginTop: 12,
                  padding: "13px 14px",
                  background: "#fff",
                  borderRadius: 14,
                  border: `1px solid ${C.border}`,
                  lineHeight: 1.65,
                }}
              >
                {chatRes}
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, justifyContent: "space-between", flexWrap: "wrap" }}>
          <button onClick={prev} disabled={idx === 0} style={{ ...s.btn("ghost"), opacity: idx === 0 ? 0.45 : 1 }}>
            <IcoLeft size={14} /> Poprzednie
          </button>

          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={() => startQuiz(questionPool, quizLength)} style={s.btn("ghost")}>
              <IcoRefresh size={14} /> Restart
            </button>

            <button onClick={next} disabled={!selected} style={{ ...s.btn("primary"), opacity: !selected ? 0.55 : 1 }}>
              {idx === total - 1 ? "Zakończ" : "Dalej"} <IcoRight size={14} />
            </button>
          </div>
        </div>
      </div>
    );
  };

  const ResultsView = () => (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ ...s.card, padding: 22 }}>
        <div className="tinyLabel" style={{ marginBottom: 10 }}>
          Podsumowanie sesji
        </div>
        <div style={{ fontSize: 30, fontWeight: 700, color: C.textStrong, marginBottom: 8 }}>
          {attemptDraft ? `${attemptDraft.percent}% poprawnych` : "Brak zakończonej sesji"}
        </div>
        <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
          Tu widzisz wynik, tempo oraz najważniejsze obszary do poprawy po ostatnim treningu.
        </div>

        <div className="quiz-inline-stats" style={{ marginTop: 20 }}>
          {[
            ["Wynik", attemptDraft ? `${attemptDraft.score}/${attemptDraft.totalQuestions}` : "—"],
            ["Mastery", attemptDraft ? `${attemptDraft.mastery}%` : "—"],
            ["Śr. czas", attemptDraft ? fmt(attemptDraft.avgResponseMs) : "—"],
            ["Łączny czas", attemptDraft ? fmt(attemptDraft.totalTimeMs) : "—"],
          ].map(([label, value]) => (
            <div key={label} style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: C.textStrong }}>{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, marginBottom: 12 }}>Kategorie</div>
          <div style={{ display: "grid", gap: 10 }}>
            {stats.byCat.length ? (
              stats.byCat.map((x) => (
                <div key={x.category} style={{ padding: 12, borderRadius: 14, background: C.cardAlt, border: `1px solid ${C.border}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, color: C.textStrong }}>{x.category}</div>
                    <div style={{ fontSize: 13, color: C.textSub }}>{x.percent}%</div>
                  </div>
                  <div style={{ height: 7, borderRadius: 999, background: "#E9E5DA", marginTop: 10, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${x.percent}%`,
                        height: "100%",
                        borderRadius: 999,
                        background: "linear-gradient(90deg, #4B5EAA, #8294C4)",
                      }}
                    />
                  </div>
                </div>
              ))
            ) : (
              <div style={{ fontSize: 14, color: C.textSub }}>Zakończ quiz, aby zobaczyć statystyki kategorii.</div>
            )}
          </div>
        </div>

        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoBolt size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>
              {trainingSummary?.title || "Analiza treningu"}
            </div>
          </div>

          {trainingSummaryStatus === "loading" && <div style={{ fontSize: 14, color: C.textSub }}>Przygotowuję analizę sesji…</div>}

          {trainingSummaryStatus !== "loading" && trainingSummary && (
            <div
              style={{
                whiteSpace: "pre-wrap",
                fontSize: 14,
                color: C.textSub,
                lineHeight: 1.75,
                background: C.cardAlt,
                border: `1px solid ${C.border}`,
                padding: 14,
                borderRadius: 14,
              }}
            >
              {trainingSummary.text}
            </div>
          )}

          {trainingSummaryStatus !== "loading" && !trainingSummary && (
            <div style={{ fontSize: 14, color: C.textSub }}>Zakończ quiz, aby otrzymać podsumowanie treningu.</div>
          )}
        </div>
      </div>
    </div>
  );

  const CalendarViewLegacy = () => (
    <div style={{ display: "grid", gridTemplateColumns: "1.2fr .8fr", gap: 16 }}>
      <div style={{ ...s.card, padding: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <div>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Aktywność nauki
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: C.textStrong }}>
              {calMonth.toLocaleDateString("pl-PL", { month: "long", year: "numeric" })}
            </div>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => setCalMonth(addM(calMonth, -1))} style={s.btn("ghost")}>
              <IcoLeft size={14} />
            </button>
            <button onClick={() => setCalMonth(som(new Date()))} style={s.btn("soft")}>
              Dziś
            </button>
            <button onClick={() => setCalMonth(addM(calMonth, 1))} style={s.btn("ghost")}>
              <IcoRight size={14} />
            </button>
          </div>
        </div>

        <div className="calendar-grid" style={{ marginBottom: 10 }}>
          {["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"].map((d) => (
            <div key={d} style={{ textAlign: "center", fontSize: 12, fontWeight: 700, color: C.textSub, paddingBottom: 4 }}>
              {d}
            </div>
          ))}
        </div>

        <div className="calendar-grid">
          {calDays.map((d) => {
            const info = dayMap[d.key];
            const heat = getHeat(info);
            const isSelected = selectedCalDay === d.key;
            return (
              <button
                key={d.key}
                onClick={() => setSelectedCalDay(d.key)}
                style={{
                  minHeight: 86,
                  borderRadius: 16,
                  border: `1px solid ${isSelected ? C.accent : heat.border}`,
                  background: isSelected ? "#EEF1FA" : heat.bg,
                  padding: 10,
                  textAlign: "left",
                  cursor: "pointer",
                  color: d.inCurrent ? C.textStrong : C.muted,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 700 }}>{d.date.getDate()}</div>
                {info && (
                  <div style={{ marginTop: 10, fontSize: 11, color: C.textSub, lineHeight: 1.4 }}>
                    <div>{info.count} sesji</div>
                    <div>{info.avgPercent}% avg</div>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ display: "grid", gap: 16 }}>
        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, marginBottom: 8 }}>{humanDate(selectedCalDay)}</div>

          {selectedDaySummary ? (
            <div style={{ display: "grid", gap: 10 }}>
              <div className="soft-chip">{selectedDaySummary.count} sesji</div>
              <div style={{ fontSize: 14, color: C.textSub }}>Średni wynik: {selectedDaySummary.avgPercent}%</div>
              <div style={{ fontSize: 14, color: C.textSub }}>Najlepszy wynik: {selectedDaySummary.bestPercent}%</div>
              <div style={{ fontSize: 14, color: C.textSub }}>Śr. czas odpowiedzi: {fmt(selectedDaySummary.avgResponseMs)}</div>
            </div>
          ) : (
            <div style={{ fontSize: 14, color: C.textSub }}>Brak sesji w tym dniu.</div>
          )}
        </div>

        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, marginBottom: 12 }}>Miesiąc w skrócie</div>
          <div style={{ display: "grid", gap: 10 }}>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub }}>Aktywne dni</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{monthDaysActive}</div>
            </div>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub }}>Śr. wynik</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{monthAvg}%</div>
            </div>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub }}>Najaktywniejszy dzień</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.textStrong }}>
                {bestStudyDay ? `${bestStudyDay[0]} · ${bestStudyDay[1].count} sesji` : "—"}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );

  const EnhancedCalendarView = () => {
    const selectedTone = toneForPercent(selectedDaySummary?.avgPercent ?? monthSummary.avgPercent);

    return (
      <div style={{ display: "grid", gap: 16 }}>
        <div
          style={{
            ...s.card,
            padding: 22,
            background: "linear-gradient(135deg, rgba(75,94,170,.08), rgba(255,255,255,.92) 42%, rgba(250,248,242,1))",
          }}
        >
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
            <div>
              <div className="tinyLabel" style={{ marginBottom: 8 }}>
                Kalendarz nauki
                  </div>
              <div style={{ fontSize: 28, fontWeight: 700, color: C.textStrong }}>
                {calMonth.toLocaleDateString("pl-PL", { month: "long", year: "numeric" })}
                  </div>
              <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65, marginTop: 8, maxWidth: 700 }}>
                Widok miesięczny pokazuje rytm pracy, skuteczność i obciążenie nauką. Ostatnia aktywność:{" "}
                {lastActiveDayKey ? humanDate(lastActiveDayKey) : "brak zapisanych sesji"}.
                  </div>
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button onClick={() => setCalMonth(addM(calMonth, -1))} style={s.btn("ghost")}>
                <IcoLeft size={14} />
              </button>
              <button
                onClick={() => {
                  setCalMonth(som(new Date()));
                  setSelectedCalDay(todayCalKey);
                }}
                style={s.btn("soft")}
              >
                Dziś
              </button>
              <button onClick={() => setCalMonth(addM(calMonth, 1))} style={s.btn("ghost")}>
                <IcoRight size={14} />
              </button>
            </div>

          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginTop: 18 }}>
            {[
              ["Bieżąca seria", streak ? `${streak} dni` : "0 dni"],
              ["Najdłuższa seria", longestStreak ? `${longestStreak} dni` : "0 dni"],
              ["Sesje w miesiącu", monthSummary.totalSessions],
              ["Czas nauki", fmtDuration(monthSummary.totalTimeMs)],
            ].map(([label, value]) => (
              <div key={label} style={{ ...s.metric, background: "rgba(255,255,255,.74)" }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{value}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="calendar-layout">
          <div style={{ ...s.card, padding: 20 }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong, marginBottom: 6 }}>Aktywność dzienna</div>
                <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                  Każda komórka pokazuje liczbę sesji, średni wynik i łączny czas w danym dniu.
                </div>
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span className="soft-chip">Więcej koloru = więcej sesji</span>
                <span className="soft-chip">Obramowanie = wybrany dzień</span>
              </div>
            </div>

            <div className="calendar-grid" style={{ marginBottom: 10 }}>
              {weekdayLabels.map((d) => (
                <div key={d} style={{ textAlign: "center", fontSize: 12, fontWeight: 700, color: C.textSub, paddingBottom: 4 }}>
                  {d}
                </div>
              ))}
            </div>

            <div className="calendar-grid">
              {calDays.map((d) => {
                const info = dayMap[d.key];
                const heat = getHeat(info);
                const isSelected = selectedCalDay === d.key;
                const isToday = d.key === todayCalKey;

                return (
                  <button
                    key={d.key}
                    onClick={() => setSelectedCalDay(d.key)}
                    style={{
                      minHeight: 108,
                      borderRadius: 18,
                      border: `1px solid ${isSelected ? C.accent : isToday ? C.accent2 : heat.border}`,
                      background: isSelected ? "#EEF1FA" : heat.bg,
                      boxShadow: isSelected ? "0 0 0 2px rgba(75,94,170,.08)" : heat.glow,
                      padding: 10,
                      textAlign: "left",
                      cursor: "pointer",
                      color: d.inCurrent ? C.textStrong : C.muted,
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      gap: 8,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>{d.date.getDate()}</div>
                      {info ? (
                        <span
                          style={{
                            fontSize: 11,
                            fontWeight: 700,
                            padding: "4px 7px",
                            borderRadius: 999,
                            background: "rgba(255,255,255,.72)",
                            border: `1px solid ${heat.border}`,
                            color: C.textSub,
                          }}
                        >
                          {info.count}x
                        </span>
                      ) : isToday ? (
                        <span style={{ fontSize: 10, fontWeight: 700, color: C.accent }}>dziś</span>
                      ) : null}
                    </div>

                    {info ? (
                      <div style={{ display: "grid", gap: 4 }}>
                        <div style={{ fontSize: 12, fontWeight: 700, color: C.textStrong }}>{info.avgPercent}% śr.</div>
                        <div style={{ fontSize: 11, color: C.textSub }}>{fmtDurationCompact(info.totalTimeMs)}</div>
                        <div style={{ fontSize: 11, color: C.textSub }}>best {info.bestPercent}%</div>
                      </div>
                    ) : (
                      <div style={{ fontSize: 11, color: d.inCurrent ? C.muted : "#B6B1A4" }}>{d.inCurrent ? "Brak sesji" : ""}</div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="calendar-side">
            <div style={{ ...s.card, padding: 20 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10, flexWrap: "wrap" }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{humanDate(selectedCalDay)}</div>
                {selectedDaySummary && (
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "6px 10px",
                      borderRadius: 999,
                      background: selectedTone.bg,
                      color: selectedTone.color,
                      border: `1px solid ${selectedTone.border}`,
                      fontSize: 12,
                      fontWeight: 700,
                    }}
                  >
                    {selectedDaySummary.avgPercent}% średnio
                  </span>
                )}
              </div>

              {selectedDaySummary ? (
                <div style={{ display: "grid", gap: 12 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 10 }}>
                    {[
                      ["Sesje", selectedDaySummary.count],
                      ["Najlepszy wynik", `${selectedDaySummary.bestPercent}%`],
                      ["Mastery", `${selectedDaySummary.avgMastery}%`],
                      ["Śr. reakcja", fmt(selectedDaySummary.avgResponseMs)],
                      ["Śr. długość", fmtDuration(selectedDaySummary.avgTimeMs)],
                      ["Łączny czas", fmtDuration(selectedDaySummary.totalTimeMs)],
                    ].map(([label, value]) => (
                      <div key={label} style={{ ...s.metric, background: C.cardAlt, padding: "12px 14px" }}>
                        <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                        <div style={{ fontSize: 17, fontWeight: 700, color: C.textStrong }}>{value}</div>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: "grid", gap: 8 }}>
                    {selectedDaySummary.strongestCategory && (
                      <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.6 }}>
                        Najczęściej mocny obszar: <strong style={{ color: C.textStrong }}>{selectedDaySummary.strongestCategory}</strong>
                      </div>
                    )}
                    {selectedDaySummary.weakestCategory && (
                      <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.6 }}>
                        Najczęściej wraca do poprawy: <strong style={{ color: C.textStrong }}>{selectedDaySummary.weakestCategory}</strong>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>
                  Brak sesji w tym dniu. Wybierz aktywną datę albo zacznij nowy quiz, aby zapisać postęp w kalendarzu.
                </div>
              )}
            </div>

            <div style={{ ...s.card, padding: 20 }}>
              <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, marginBottom: 12 }}>Sesje z wybranego dnia</div>

              {selectedDayAttempts.length ? (
                <div className="calendar-session-list">
                  {selectedDayAttempts.map((attempt) => {
                    const tone = toneForPercent(attempt.percent);
                    return (
                      <div
                        key={attempt.id}
                        style={{
                          padding: 14,
                          borderRadius: 16,
                          background: C.cardAlt,
                          border: `1px solid ${C.border}`,
                          display: "grid",
                          gap: 8,
                        }}
                      >
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                          <div style={{ fontSize: 14, fontWeight: 700, color: C.textStrong }}>{fmtClock(attempt.finishedAt)}</div>
                          <span
                            style={{
                              fontSize: 12,
                              fontWeight: 700,
                              padding: "6px 10px",
                              borderRadius: 999,
                              background: tone.bg,
                              color: tone.color,
                              border: `1px solid ${tone.border}`,
                            }}
                          >
                            {attempt.percent}%
                          </span>
                        </div>

                        <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.65 }}>
                          Wynik {attempt.score}/{attempt.totalQuestions} • mastery {attempt.mastery}% • czas {fmtDuration(attempt.totalTimeMs)}
                        </div>

                        {(attempt.strongestCategory || attempt.weakestCategory) && (
                          <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                            {attempt.strongestCategory ? `Mocna kategoria: ${attempt.strongestCategory}` : "Mocna kategoria: —"}
                            {" • "}
                            {attempt.weakestCategory ? `Do poprawy: ${attempt.weakestCategory}` : "Do poprawy: —"}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{ fontSize: 14, color: C.textSub }}>Po zapisaniu sesji zobaczysz tu szczegółową listę podejść z tego dnia.</div>
              )}
            </div>
          </div>
        </div>

        <div className="calendar-bottom-grid">
          <div style={{ ...s.card, padding: 20 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>Miesiąc w skrócie</div>
              {bestStudyDay && <span className="soft-chip">Najaktywniejszy dzień: {shortDay(bestStudyDay[0])}</span>}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 10, marginBottom: 14 }}>
              {[
                ["Aktywne dni", monthDaysActive],
                ["Śr. wynik", `${monthSummary.avgPercent}%`],
                ["Śr. mastery", `${monthSummary.avgMastery}%`],
                ["Pokrycie miesiąca", `${monthSummary.completionRate}%`],
              ].map(([label, value]) => (
                <div key={label} style={{ ...s.metric, background: C.cardAlt, padding: "12px 14px" }}>
                  <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{value}</div>
                </div>
              ))}
            </div>

            <div style={{ display: "grid", gap: 8, marginBottom: 16 }}>
              <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
                Najlepszy wynik w miesiącu: <strong style={{ color: C.textStrong }}>{monthSummary.bestPercent || 0}%</strong>
                {" • "}
                Średni czas odpowiedzi: <strong style={{ color: C.textStrong }}>{fmt(monthSummary.avgResponseMs || 0)}</strong>
              </div>
              <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
                {bestWeekday
                  ? `Najczęściej uczysz się w ${bestWeekday.label} (${bestWeekday.count} sesji, śr. ${bestWeekday.avgPercent}%).`
                  : "Brak wystarczających danych do oceny rytmu tygodnia."}
              </div>
              {monthSummary.weakestCategory && (
                <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
                  Najczęściej do poprawy wraca: <strong style={{ color: C.textStrong }}>{monthSummary.weakestCategory}</strong>
                </div>
              )}
            </div>

            <div style={{ display: "grid", gap: 10 }}>
              {weekdaySummary.map((day) => (
                <div key={day.label} style={{ display: "grid", gridTemplateColumns: "36px 1fr auto", alignItems: "center", gap: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.textStrong }}>{day.label}</div>
                  <div style={{ height: 8, borderRadius: 999, background: "#E9E5DA", overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${bestWeekday?.count ? (day.count / bestWeekday.count) * 100 : 0}%`,
                        height: "100%",
                        borderRadius: 999,
                        background: "linear-gradient(90deg, #4B5EAA, #8294C4)",
                      }}
                    />
                  </div>
                  <div style={{ fontSize: 12, color: C.textSub, minWidth: 76, textAlign: "right" }}>
                    {day.count ? `${day.count} • ${day.avgPercent}%` : "0"}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...s.card, padding: 20 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong, marginBottom: 12 }}>Przegląd tygodni</div>

            {weeklySummary.length ? (
              <div style={{ display: "grid", gap: 10 }}>
                {weeklySummary.map((week) => (
                  <div
                    key={week.key}
                    style={{
                      padding: 14,
                      borderRadius: 16,
                      background: C.cardAlt,
                      border: `1px solid ${C.border}`,
                      display: "grid",
                      gap: 8,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                      <div style={{ fontSize: 14, fontWeight: 700, color: C.textStrong }}>{week.label}</div>
                      <span className="soft-chip">{week.count} sesji</span>
                    </div>

                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 10 }}>
                      {[
                        ["Śr. wynik", `${week.avgPercent}%`],
                        ["Mastery", `${week.avgMastery}%`],
                        ["Czas", fmtDuration(week.totalTimeMs)],
                      ].map(([label, value]) => (
                        <div key={label} style={{ ...s.metric, background: "#fff", padding: "10px 12px" }}>
                          <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                          <div style={{ fontSize: 15, fontWeight: 700, color: C.textStrong }}>{value}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>
                Gdy pojawi się kilka sesji w miesiącu, zobaczysz tu tygodniowe porównanie obciążenia i jakości nauki.
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  const PlanView = () => (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ ...s.card, padding: 22 }}>
        <div className="tinyLabel" style={{ marginBottom: 8 }}>
          Plan rozwoju
        </div>
        <div style={{ fontSize: 28, fontWeight: 700, color: C.textStrong, marginBottom: 8 }}>
          {plan.readiness || "Plan nauki"}
        </div>
        <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{plan.recommendation}</div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoTarget size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Co poprawić</div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            {(plan.improvements || []).length ? (
              plan.improvements.map((item, i) => (
                <div key={i} style={{ padding: 12, borderRadius: 14, background: C.cardAlt, border: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.6 }}>{item}</div>
                </div>
              ))
            ) : (
              <div style={{ fontSize: 14, color: C.textSub }}>Brak danych do planu. Zrób kilka sesji quizu.</div>
            )}
          </div>
        </div>

        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoBook size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Tydzień nauki</div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            {(plan.weeklyPlan || []).length ? (
              plan.weeklyPlan.map((x) => (
                <div
                  key={x.day}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "70px 1fr 60px",
                    gap: 12,
                    alignItems: "center",
                    padding: 12,
                    borderRadius: 14,
                    background: C.cardAlt,
                    border: `1px solid ${C.border}`,
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.textStrong }}>{x.day}</div>
                  <div style={{ fontSize: 14, color: C.textSub }}>{x.task}</div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, textAlign: "right" }}>{x.duration}</div>
                </div>
              ))
            ) : (
              <div style={{ fontSize: 14, color: C.textSub }}>Brak tygodniowego planu.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );

  const SettingsView = () => (
    <div className="settings-grid">
      <div className="settings-main-card" style={{ ...s.card, padding: 20 }}>
        <div>
          <div className="tinyLabel" style={{ marginBottom: 10 }}>
            Konfiguracja quizu
          </div>
          <div style={{ fontSize: 28, fontWeight: 600, color: C.textStrong, marginBottom: 8 }}>Ustawienia nauki</div>
          <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65, marginBottom: 20 }}>
            Minimum chaosu, maksimum skupienia. Wszystko w jednym spokojnym panelu.
          </div>

          <div className="settings-section-grid">
            <div>
              <label style={s.label}>Liczba pytań</label>
              <select
                value={quizLength}
                onChange={(e) => setQuizLength(e.target.value === "all" ? "all" : Number(e.target.value))}
                style={s.input}
              >
                <option value={10}>10 pytań</option>
                <option value={20}>20 pytań</option>
                <option value={30}>30 pytań</option>
                <option value="all">Cała baza</option>
              </select>
            </div>

            <div>
              <label style={s.label}>Źródło pytań</label>
              <div style={{ ...s.input, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span>{questionPool?.length || 0} pytań</span>
                <span className="soft-chip">
                  <IcoBook size={12} /> aktywne
                </span>
              </div>
            </div>
          </div>
        </div>

        <div>
          <div className="settings-actions" style={{ marginTop: 18 }}>
            <button onClick={() => startQuiz(questionPool, quizLength)} style={s.btn("primary")}>
              <IcoRefresh size={14} /> Nowa sesja
            </button>

            <button onClick={() => fileRef.current?.click()} style={s.btn("ghost")}>
              <IcoUpload size={14} /> Import pytań
            </button>

            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.txt" onChange={handleImport} style={{ display: "none" }} />
          </div>

          {importMsg && <div style={{ marginTop: 12, fontSize: 13, color: C.textSub }}>{importMsg}</div>}
        </div>
      </div>

      <div className="settings-stack">
        <div style={{ ...s.card, padding: 18, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <IcoCloud size={16} />
              <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Cloud AI</div>
            </div>

            <button
              onClick={() => setCloudApiEnabled((v) => !v)}
              style={{
                ...s.btn(cloudApiEnabled ? "primary" : "ghost"),
                padding: "8px 12px",
                minWidth: 92,
              }}
            >
              {cloudApiEnabled ? "Włączone" : "Wyłączone"}
            </button>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={s.label}>Klucz API</label>
            <input
              type="password"
              value={cloudApiKey}
              onChange={(e) => setCloudApiKey(e.target.value)}
              placeholder="sk-ant-..."
              style={s.input}
            />
            <div className="field-help">Klucz pozostaje lokalnie w przeglądarce. Nie zapisuj go w kodzie ani repozytorium.</div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={s.label}>Model</label>
            <input value={cloudModel} onChange={(e) => setCloudModel(e.target.value)} placeholder={DEFAULT_MODEL} style={s.input} />
          </div>

          <div className="field-help">
            Ten frontend nie wykonuje już bezpośredniego testu do Anthropic. Aktualny układ wymaga backend proxy albo Edge Function.
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
            <button onClick={checkCloudConnection} style={s.btn("soft")}>
              <IcoCloud size={14} /> Status Cloud AI
            </button>
          </div>

          <div
            style={{
              marginTop: 12,
              padding: 12,
              borderRadius: 14,
              background: toneForStatus(cloudCheck.status).bg,
              color: toneForStatus(cloudCheck.status).color,
              border: `1px solid ${toneForStatus(cloudCheck.status).border}`,
              fontSize: 13,
              lineHeight: 1.6,
            }}
          >
            {cloudCheck.message}
          </div>
        </div>

        <div style={{ ...s.card, padding: 18, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoCheck size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Supabase</div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={s.label}>Supabase URL</label>
            <input
              value={supabaseUrl}
              onChange={(e) => setSupabaseUrl(e.target.value)}
              placeholder="https://twoj-projekt.supabase.co"
              style={s.input}
            />
          </div>

          <div>
            <label style={s.label}>Publishable / anon key</label>
            <input
              type="password"
              value={supabaseAnonKey}
              onChange={(e) => setSupabaseAnonKey(e.target.value)}
              placeholder="sb_publishable_... lub pełny anon JWT"
              style={s.input}
            />
            <div className="field-help">
              Wklej pełny publishable key albo pełny anon JWT. Dotychczasowy błąd wynikał z niepełnego klucza w kodzie.
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
            <button onClick={checkSupabaseConnection} style={s.btn("soft")}>
              <IcoCheck size={14} /> Test Supabase
            </button>
          </div>

          <div
            style={{
              marginTop: 12,
              padding: 12,
              borderRadius: 14,
              background: toneForStatus(supabaseCheck.status).bg,
              color: toneForStatus(supabaseCheck.status).color,
              border: `1px solid ${toneForStatus(supabaseCheck.status).border}`,
              fontSize: 13,
              lineHeight: 1.6,
            }}
          >
            {supabaseCheck.message}
          </div>
        </div>

        <div style={{ ...s.card, padding: 18, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoTrending size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Stan aplikacji</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Baza pytań</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{questionPool.length}</div>
            </div>

            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Sesje</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{history.length}</div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
            <span className="soft-chip">
              <IcoCheck size={12} />
              {sbEnabled ? "Supabase gotowe" : "Tryb lokalny"}
            </span>
            <span className="soft-chip">
              <IcoCloud size={12} />
              {cloudApiEnabled ? "Cloud wymaga backendu" : "Cloud AI wyłączone"}
            </span>
          </div>
        </div>

        <div style={{ ...s.card, padding: 18, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoClock size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Tempo i jakość</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              ["Mastery", attemptDraft ? `${attemptDraft.mastery}%` : "—"],
              ["Śr. czas", answeredCount ? fmt(stats.avgResponseMs) : "—"],
              ["Ostatni wynik", attemptDraft ? `${attemptDraft.score}/${attemptDraft.totalQuestions}` : "—"],
              ["Cloud AI", cloudApiEnabled ? "Włączone" : "Wyłączone"],
            ].map(([label, value]) => (
              <div key={label} style={{ ...s.metric, background: C.cardAlt }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 14, fontSize: 13, color: C.textSub, lineHeight: 1.65 }}>
            Widok ustawień został spłaszczony do zwartej siatki, żeby zredukować pionowy scroll i szybciej odsłaniać pola integracji.
          </div>
        </div>
      </div>
    </div>
  );

  const renderTab = () => {
    if (activeTab === "quiz") return <QuizView />;
    if (activeTab === "results") return <ResultsView />;
    if (activeTab === "calendar") return <EnhancedCalendarView />;
    if (activeTab === "plan") return <PlanView />;
    if (activeTab === "settings") return <SettingsView />;
    return <QuizView />;
  };

  return (
    <>
      <div className="app-shell">
        <div className="app-frame">
          <aside className="sidebar">
            <div className="brand-panel">
              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <ZenQuizLogo size={68} />
                <div>
                  <div className="tinyLabel" style={{ marginBottom: 8 }}>
                    Study Suite
                  </div>
                  <div className="brand-title" style={{ fontSize: 28, fontWeight: 700, color: C.textStrong }}>Zen Quiz</div>
                  <div style={{ fontSize: 13, color: C.textSub, marginTop: 6, lineHeight: 1.5 }}>
                Skupienie, rytm, jakość odpowiedzi.
              </div>
            </div>

              </div>
            </div>

            <div className="sidebar-footer">
              <div className="sidebar-primary" style={{ display: "grid", gap: 12 }}>
                <div className="sidebar-summary-card">
                  <div className="sidebar-summary-head">
                    <div>
                      <div className="tinyLabel" style={{ marginBottom: 8 }}>
                        Lewy panel
                      </div>
                      <div style={{ fontSize: 24, fontWeight: 700, color: C.textStrong, lineHeight: 1.1 }}>Postęp nauki</div>
                    </div>

                    <div className="soft-chip">
                      {activeTabMeta.icon}
                      {activeTabMeta.label}
                    </div>
                  </div>

                  <div className="sidebar-summary-body">
                    Najważniejsze liczby są teraz w jednym miejscu: postęp, skuteczność, średni czas i seria.
                  </div>
                </div>

                <div className="sidebar-metric-grid">
                  {sidebarMetrics.map((item) => (
                    <div key={item.label} className="sidebar-metric">
                      <div className="sidebar-metric-label">{item.label}</div>
                      <div className="sidebar-metric-value">{item.value}</div>
                      <div className="sidebar-metric-note">{item.note}</div>
                    </div>
                  ))}
                </div>

                <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, textTransform: "uppercase", letterSpacing: ".05em" }}>
                  Postęp nauki
                </div>

                <div className="mini-grid">
                  {[
                    ["Dzisiejsza forma", `${pct || 0}%`],
                    ["Bieżąca seria", `${streak} dni`],
                    ["Baza pytań", questionPool.length],
                    ["Zapisane sesje", history.length],
                  ].map(([label, value]) => (
                    <div key={label} style={{ ...s.cardSm, padding: 14, background: "rgba(255,255,255,.72)" }}>
                      <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                      <div style={{ fontSize: 21, fontWeight: 700, color: C.textStrong }}>{value}</div>
                    </div>
                  ))}
                </div>

                <div style={{ ...s.cardSm, padding: 14, background: C.cardAlt }}>
                  <div style={{ fontSize: 11, color: C.textSub, marginBottom: 8 }}>Skuteczność i postęp</div>
                  <div style={{ display: "grid", gap: 8 }}>
                    <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                      Ostatni wynik: <strong style={{ color: C.textStrong }}>{attemptDraft ? `${attemptDraft.score}/${attemptDraft.totalQuestions}` : "—"}</strong>
                    </div>
                    <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                      Mastery: <strong style={{ color: C.textStrong }}>{attemptDraft ? `${attemptDraft.mastery}%` : "—"}</strong>
                    </div>
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, textTransform: "uppercase", letterSpacing: ".05em" }}>
                PostÄ™p nauki
              </div>

              <div className="mini-grid">
                {[
                  ["Skuteczność", `${pct || 0}%`],
                  ["Seria", `${streak} dni`],
                  ["Sesje", history.length],
                  ["Pytania", questionPool.length],
                ].map(([label, value]) => (
                  <div key={label} style={{ ...s.cardSm, padding: 14, background: "rgba(255,255,255,.72)" }}>
                    <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                    <div style={{ fontSize: 21, fontWeight: 700, color: C.textStrong }}>{value}</div>
                  </div>
                ))}
              </div>

              <div className="sidebar-progress-card" style={{ ...s.cardSm, padding: 14, background: C.cardAlt }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 8 }}>Skuteczność i postęp</div>
                <div style={{ display: "grid", gap: 8 }}>
                  <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                    Ostatni wynik: <strong style={{ color: C.textStrong }}>{attemptDraft ? `${attemptDraft.score}/${attemptDraft.totalQuestions}` : "—"}</strong>
                  </div>
                  <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                    Aktywna karta: <strong style={{ color: C.textStrong }}>{activeTabMeta.label}</strong>
                  </div>
                </div>
              </div>

              <div style={{ ...s.cardSm, padding: 14, background: C.cardAlt }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 8 }}>Tempo i jakość</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <span className="soft-chip">Mastery: {attemptDraft ? `${attemptDraft.mastery}%` : "—"}</span>
                  <span className="soft-chip">{cloudApiEnabled ? "Cloud AI włączone" : "Cloud AI wyłączone"}</span>
                </div>
              </div>
            </div>
          </aside>

          <main className="content-area">
            <section className="workspace-hero">
              <div className="top-nav-shell">
                <div className="tinyLabel">Nawigacja</div>
                <div className="nav-rail">
                  {TABS.map((tab) => (
                    <button
                      key={tab.id}
                      onClick={() => setActiveTab(tab.id)}
                      className={`tab-btn top ${activeTab === tab.id ? "active" : ""}`}
                    >
                      <span className="tab-btn-icon">{tab.icon}</span>
                      <span className="tab-btn-text">
                        <strong>{tab.label}</strong>
                        <span>{tab.eyebrow}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="workspace-header">
                <div>
                  <div className="tinyLabel" style={{ marginBottom: 8 }}>
                    {activeTabMeta.eyebrow}
                  </div>
                  <div className="workspace-title" style={{ fontSize: 36, fontWeight: 700, color: C.textStrong }}>
                    {activeTabMeta.label}
                  </div>
                  <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7, maxWidth: 760, marginTop: 8 }}>
                    {activeTabMeta.description}
                  </div>
                </div>

                <div className="workspace-meta">
                  <button
                    onClick={() => startQuiz(questionPool, quizLength)}
                    style={{
                      ...s.btn("ghost"),
                      padding: "8px 12px",
                      fontSize: 12,
                      opacity: 0.9,
                    }}
                  >
                    <IcoRefresh size={14} /> Nowa sesja
                  </button>
                </div>
              </div>

              <div className="workspace-stats">
                {[
                  ["Dzisiejsza forma", `${pct || 0}%`],
                  ["Bieżąca seria", `${streak} dni`],
                  ["Baza pytań", questionPool.length],
                  ["Zapisane sesje", history.length],
                ].map(([label, value]) => (
                  <div key={label} className="workspace-stat">
                    <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>{value}</div>
                  </div>
                ))}
              </div>
            </section>

            <div className="workspace-stack">{renderTab()}</div>
          </main>
        </div>
      </div>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<QuizAbcdApp />);
