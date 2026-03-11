import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";
import * as XLSX from "xlsx";

// ── SVG Icons ─────────────────────────────────────────────────────────────────
const Icon = ({ d, size = 16, className = "", strokeWidth = 1.75 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={strokeWidth}
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d={d} />
  </svg>
);
const IcoBrain = ({ size = 16 }) => <Icon size={size} d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18a4 4 0 1 0 7.967-1.517 4 4 0 0 0 .556-6.588 4 4 0 0 0-2.526-5.77A3 3 0 0 0 12 5" />;
const IcoTrophy = ({ size = 16 }) => <Icon size={size} d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6m12 5h1.5a2.5 2.5 0 0 0 0-5H18M12 12v4m-4 4h8M3 3h18v6a9 9 0 0 1-18 0V3z" />;
const IcoClock = ({ size = 16 }) => <Icon size={size} d="M12 2a10 10 0 1 1 0 20A10 10 0 0 1 12 2zm0 4v6l4 2" />;
const IcoCalendar = ({ size = 16 }) => <Icon size={size} d="M8 2v4M16 2v4M3 8h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />;
const IcoBook = ({ size = 16 }) => <Icon size={size} d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z" />;
const IcoSettings = ({ size = 16 }) => <Icon size={size} d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />;
const IcoRefresh = ({ size = 16 }) => <Icon size={size} d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
const IcoChat = ({ size = 16 }) => <Icon size={size} d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />;
const IcoLeft = ({ size = 16 }) => <Icon size={size} d="M19 12H5M12 19l-7-7 7-7" />;
const IcoRight = ({ size = 16 }) => <Icon size={size} d="M5 12h14M12 5l7 7-7 7" />;
const IcoUpload = ({ size = 16 }) => <Icon size={size} d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />;
const IcoCloud = ({ size = 16 }) => <Icon size={size} d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />;
const IcoTrash = ({ size = 16 }) => <Icon size={size} d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />;
const IcoKeyboard = ({ size = 16 }) => <Icon size={size} d="M20 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zM8 15H6v-2h2v2zm0-4H6V9h2v2zm4 4h-2v-2h2v2zm0-4h-2V9h2v2zm4 4h-2v-2h2v2zm0-4h-2V9h2v2zm4 4h-2V9h2v6z" />;
const IcoCheck = ({ size = 16 }) => <Icon size={size} d="M20 6L9 17l-5-5" />;
const IcoCross = ({ size = 16 }) => <Icon size={size} d="M18 6L6 18M6 6l12 12" />;
const IcoStar = ({ size = 16 }) => <Icon size={size} d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />;
const IcoTarget = ({ size = 16 }) => <Icon size={size} d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83M12 8a4 4 0 1 1 0 8 4 4 0 0 1 0-8z" />;
const IcoTrending = ({ size = 16 }) => <Icon size={size} d="M3 17l6-6 4 4 7-8M14 7h6v6" />;
const IcoBolt = ({ size = 16 }) => <Icon size={size} d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" />;

// ── Config & Helpers ──────────────────────────────────────────────────────────
const SUPABASE_URL = "https://ylqloszldyzpeaikweyl.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlscWxvc3psZHl6cGVhaWt3ZXlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyNDg2NDUsImV4cCI6MjA4ODgyNDY0NX0.JgwZKn5_ifnoZHViOGb7aED9sZ3MnijeeI66cFhSJaQ";
const SB_ENABLED = SUPABASE_URL.startsWith("https://") && SUPABASE_ANON_KEY.startsWith("eyJ");

const sbH = (prefer = "return=representation") => ({
  "Content-Type": "application/json",
  apikey: SUPABASE_ANON_KEY,
  Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
  Prefer: prefer,
});
async function sbSelect(table, params = "") {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, { headers: sbH() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function sbInsert(table, row) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, {
    method: "POST",
    headers: sbH(),
    body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
}

const STORAGE_KEY = "quiz_abcd_attempts_v4";
const optionKeys = ["A", "B", "C", "D"];
const diffW = { easy: 1, medium: 1.5, hard: 2 };
const normDiff = (v) => {
  const r = String(v || "medium").trim().toLowerCase();
  return ["easy", "medium", "hard"].includes(r) ? r : "medium";
};
const fmt = (ms) => `${(ms / 1000).toFixed(1)}s`;
const dayKey = (ts) => {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const humanDate = (key) => {
  const d = new Date(`${key}T12:00:00`);
  return d.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
};
const som = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const addM = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);
const isSameMonth = (key, monthDate) => {
  const d = new Date(`${key}T12:00:00`);
  return d.getMonth() === monthDate.getMonth() && d.getFullYear() === monthDate.getFullYear();
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
    return { recommendation: "Ukończ kilka prób quizu, a system przygotuje lepszy plan.", improvements: [], weeklyPlan: [] };
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

// ── COLOUR TOKENS ─────────────────────────────────────────────────────────────
const C = {
  bg: "#F5F7FB",
  surface: "#FFFFFF",
  card: "#FFFFFF",
  cardAlt: "#F8FAFD",
  border: "#E6EBF4",
  accent: "#4F6BFF",
  accentSoft: "#EEF2FF",
  accentL: "#7E92FF",
  accent2: "#14B8A6",
  text: "#172033",
  textSub: "#667085",
  muted: "#98A2B3",
  green: "#12B76A",
  red: "#F04438",
  yellow: "#F79009",
  shadow: "0 12px 32px rgba(16, 24, 40, 0.06)",
};

// ── INLINE STYLES HELPERS ─────────────────────────────────────────────────────
const s = {
  card: {
    background: C.card,
    border: `1px solid ${C.border}`,
    borderRadius: 20,
    boxShadow: C.shadow,
  },
  cardSm: {
    background: C.card,
    border: `1px solid ${C.border}`,
    borderRadius: 16,
    boxShadow: "0 6px 18px rgba(16, 24, 40, 0.04)",
  },
  btn: (variant = "primary") => ({
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    padding: "10px 18px",
    borderRadius: 12,
    fontSize: 13,
    fontWeight: 700,
    cursor: "pointer",
    border: "none",
    transition: "all .18s ease",
    ...(variant === "primary"
      ? { background: C.accent, color: "#fff" }
      : variant === "ghost"
      ? { background: C.surface, color: C.textSub, border: `1px solid ${C.border}` }
      : variant === "soft"
      ? { background: C.accentSoft, color: C.accent, border: `1px solid #DBE4FF` }
      : variant === "danger"
      ? { background: "#FFF0F0", color: C.red, border: `1px solid #FFD7D5` }
      : { background: C.cardAlt, color: C.text, border: `1px solid ${C.border}` }),
  }),
  metric: {
    ...{
      background: C.card,
      border: `1px solid ${C.border}`,
      borderRadius: 16,
      boxShadow: "0 6px 18px rgba(16, 24, 40, 0.04)",
    },
    padding: "16px 18px",
  },
};

function QuizAbcdApp() {
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
  const fileRef = useRef(null);

  const total = questions.length;
  const current = questions[idx] || SAMPLES[0];
  const score = useMemo(() => Object.values(answers).filter((a) => a.isCorrect).length, [answers]);
  const answeredCount = Object.keys(answers).length;

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
    },
    [questionPool, quizLength]
  );

  const loadQfromDB = useCallback(async () => {
    if (!SB_ENABLED) return;
    try {
      const rows = await sbSelect("quiz_questions", "is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length) return;
      const parsed = rows.map(rowToQ);
      setQuestionPool(parsed);
      const shuffled = [...parsed].sort(() => 0.5 - Math.random());
      setQuestions(shuffled.slice(0, quizLength === "all" ? shuffled.length : quizLength));
    } catch (e) {}
  }, [quizLength]);

  const loadAttempts = useCallback(async () => {
    if (!SB_ENABLED) return;
    try {
      const rows = await sbSelect("quiz_attempts", "order=finished_at.desc&limit=100");
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
        const m = dedupe([...mapped, ...prev]);
        saveLocal(m);
        return m;
      });
    } catch (e) {}
  }, []);

  useEffect(() => {
    loadQfromDB();
    loadAttempts();
  }, [loadQfromDB, loadAttempts]);

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

  const askAI = useCallback(async () => {
    if (chatStatus === "loading") return;
    setChatStatus("loading");
    setTimeout(() => {
      setChatRes(
        `Kategoria: "${current.category}". Najpierw porównaj pojęcia kluczowe w odpowiedziach, a potem wyklucz zbyt ogólne lub zbyt wąskie opcje. W tym pytaniu odpowiedź ${current.correct} najlepiej pasuje do standardowej definicji.`
      );
      setChatStatus("loaded");
    }, 900);
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
        setImportMsg(`✓ Zaimportowano ${parsed.length} pytań z \"${file.name}\"`);
      } catch (err) {
        setImportMsg(`✗ Błąd: ${err.message}`);
      } finally {
        e.target.value = "";
      }
    },
    [quizLength, startQuiz]
  );

  const stats = useMemo(() => {
    const list = questions.map((q) => ({ q, a: answers[q.id] })).filter((x) => x.a);
    const totalTimeMs = (finishedAt ?? Date.now()) - startedAt;
    const avgResponseMs = list.length ? list.reduce((s, x) => s + x.a.responseTimeMs, 0) / list.length : 0;
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

  useEffect(() => {
    if (!showResult || !finishedAt) return;
    const attempt = {
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
    setHistory((prev) => {
      if (prev.some((a) => a.finishedAt === finishedAt)) return prev;
      const m = dedupe([attempt, ...prev]);
      saveLocal(m);
      return m;
    });
    if (SB_ENABLED) {
      sbInsert("quiz_attempts", {
        attempt_id: attempt.id,
        finished_at: new Date(attempt.finishedAt).toISOString(),
        total_questions: attempt.totalQuestions,
        score: attempt.score,
        percent: attempt.percent,
        mastery: attempt.mastery,
        avg_response_ms: attempt.avgResponseMs,
        total_time_ms: attempt.totalTimeMs,
        strongest_category: attempt.strongestCategory,
        weakest_category: attempt.weakestCategory,
      })
        .then(() => loadAttempts())
        .catch(() => {});
    }
  }, [showResult, finishedAt, score, stats, total, loadAttempts]);

  useEffect(() => {
    const h = (e) => {
      if (e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA") return;
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
      const avg = Math.round(list.reduce((s0, a) => s0 + a.percent, 0) / list.length);
      m[key] = { count: list.length, avgPercent: avg, best: Math.max(...list.map((a) => a.percent)) };
    });
    return m;
  }, [attemptsByDay]);

  const streak = useMemo(() => {
    let s0 = 0;
    const c = new Date();
    while (dayMap[dayKey(c.getTime())]) {
      s0++;
      c.setDate(c.getDate() - 1);
    }
    return s0;
  }, [dayMap]);

  const plan = useMemo(() => buildPlan(uniq, stats.weakest), [uniq, stats.weakest]);
  const calDays = useMemo(() => buildCalDays(calMonth), [calMonth]);
  const selectedDayAttempts = attemptsByDay[selectedCalDay] || [];
  const selectedDaySummary = useMemo(() => {
    if (!selectedDayAttempts.length) return null;
    return {
      count: selectedDayAttempts.length,
      avgPercent: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.percent, 0) / selectedDayAttempts.length),
      bestPercent: Math.max(...selectedDayAttempts.map((a) => a.percent)),
      avgTime: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.totalTimeMs, 0) / selectedDayAttempts.length),
      avgResponseMs: Math.round(selectedDayAttempts.reduce((s0, a) => s0 + a.avgResponseMs, 0) / selectedDayAttempts.length),
    };
  }, [selectedDayAttempts]);

  const monthAttempts = useMemo(() => uniq.filter((a) => isSameMonth(dayKey(a.finishedAt), calMonth)), [uniq, calMonth]);
  const monthDaysActive = useMemo(
    () => Object.keys(dayMap).filter((key) => isSameMonth(key, calMonth)).length,
    [dayMap, calMonth]
  );
  const monthAvg = useMemo(
    () => (monthAttempts.length ? Math.round(monthAttempts.reduce((s0, a) => s0 + a.percent, 0) / monthAttempts.length) : 0),
    [monthAttempts]
  );
  const bestStudyDay = useMemo(() => {
    const entries = Object.entries(dayMap).filter(([key]) => isSameMonth(key, calMonth));
    if (!entries.length) return null;
    return entries.sort((a, b) => b[1].count - a[1].count || b[1].avgPercent - a[1].avgPercent)[0];
  }, [dayMap, calMonth]);

  const maxCountInMonth = useMemo(() => {
    const counts = Object.entries(dayMap)
      .filter(([key]) => isSameMonth(key, calMonth))
      .map(([, v]) => v.count);
    return counts.length ? Math.max(...counts) : 0;
  }, [dayMap, calMonth]);

  const getHeat = (info) => {
    if (!info) return { bg: "transparent", color: C.textSub, border: C.border, dot: "transparent" };
    const ratio = maxCountInMonth ? info.count / maxCountInMonth : info.count > 0 ? 1 : 0;
    if (ratio >= 0.8) return { bg: "#DDE5FF", color: C.accent, border: "#BDD0FF", dot: C.accent };
    if (ratio >= 0.5) return { bg: "#EAF0FF", color: C.accent, border: "#D5E0FF", dot: C.accentL };
    if (ratio > 0) return { bg: "#F4F7FF", color: C.text, border: C.border, dot: C.accentL };
    return { bg: "transparent", color: C.textSub, border: C.border, dot: "transparent" };
  };

  const TABS = [
    { id: "quiz", label: "Quiz", icon: <IcoBrain size={15} /> },
    { id: "results", label: "Wyniki", icon: <IcoTrophy size={15} /> },
    { id: "calendar", label: "Kalendarz", icon: <IcoCalendar size={15} /> },
    { id: "plan", label: "Plan", icon: <IcoBook size={15} /> },
    { id: "settings", label: "Ustawienia", icon: <IcoSettings size={15} /> },
  ];

  const pct = answeredCount > 0 ? Math.round((stats.correctCount / answeredCount) * 100) : 0;
  const progressPct = Math.round((idx / Math.max(total - 1, 1)) * 100);
  const nextUnlocked = !!selected;

  const QuizView = () => {
    const diffColor = { easy: C.green, medium: C.yellow, hard: C.red }[current.difficulty || "medium"];
    return (
      <div className="quiz-grid">
        <div style={{ display: "flex", flexDirection: "column", gap: 18, minWidth: 0 }}>
          <div style={{ ...s.card, padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              <span className="tinyLabel">Pytanie {idx + 1} / {total}</span>
              <span style={{ width: 5, height: 5, borderRadius: "50%", background: C.muted }} />
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "4px 9px",
                  borderRadius: 999,
                  background: `${diffColor}18`,
                  color: diffColor,
                  textTransform: "capitalize",
                }}
              >
                {current.difficulty}
              </span>
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "4px 10px",
                  borderRadius: 999,
                  background: C.cardAlt,
                  color: C.textSub,
                  border: `1px solid ${C.border}`,
                }}
              >
                {current.category}
              </span>
            </div>

            <h2 style={{ fontSize: 27, fontWeight: 800, color: C.text, lineHeight: 1.35, margin: 0, letterSpacing: "-.03em" }}>
              {current.question}
            </h2>
            <div style={{ marginTop: 12, fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
              Wybierz jedną odpowiedź. Po wyborze od razu zobaczysz informację zwrotną i możesz przejść dalej.
            </div>
          </div>

          <div style={{ display: "grid", gap: 12 }}>
            {optionKeys.map((key) => {
              const isSel = selected === key;
              const isCorr = current.correct === key;
              const reveal = !!selected;
              let bg = C.surface;
              let border = C.border;
              let color = C.text;
              let labelBg = C.cardAlt;
              let labelColor = C.textSub;
              let iconEl = null;
              if (reveal) {
                if (isCorr) {
                  bg = "#EEFBF3";
                  border = "#B7E7C8";
                  labelBg = C.green;
                  labelColor = "#fff";
                  iconEl = <IcoCheck size={15} />;
                } else if (isSel) {
                  bg = "#FFF3F2";
                  border = "#F4C7C4";
                  labelBg = C.red;
                  labelColor = "#fff";
                  iconEl = <IcoCross size={15} />;
                }
              }
              return (
                <button
                  key={key}
                  onClick={() => handleAnswer(key)}
                  disabled={!!selected}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 16,
                    padding: "18px 18px",
                    borderRadius: 16,
                    border: `1.5px solid ${border}`,
                    background: bg,
                    cursor: selected ? "default" : "pointer",
                    transition: "all .18s",
                    textAlign: "left",
                    width: "100%",
                    boxShadow: reveal && isSel ? "0 10px 24px rgba(240, 68, 56, 0.08)" : "none",
                  }}
                >
                  <span
                    style={{
                      width: 40,
                      height: 40,
                      borderRadius: 12,
                      background: labelBg,
                      color: labelColor,
                      fontSize: 13,
                      fontWeight: 800,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                      border: `1px solid ${reveal && (isCorr || isSel) ? "transparent" : C.border}`,
                    }}
                  >
                    {key}
                  </span>
                  <span style={{ fontSize: 15, fontWeight: 600, color, flex: 1, lineHeight: 1.55 }}>{current.options[key]}</span>
                  {iconEl && <span style={{ flexShrink: 0, color: isCorr ? C.green : C.red, display: "flex" }}>{iconEl}</span>}
                </button>
              );
            })}
          </div>

          {selected && (
            <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }}>
              <div style={{ fontSize: 12, fontWeight: 800, color: selected === current.correct ? C.green : C.red, marginBottom: 6 }}>
                {current.correct ? (selected === current.correct ? "✓ Dobra odpowiedź." : `✗ Poprawna odpowiedź: ${current.correct}`) : "Klucz niedostępny."}
              </div>
              <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>{current.explanation}</div>
              {chatStatus === "idle" && (
                <button onClick={askAI} style={{ ...s.btn("soft"), marginTop: 12, fontSize: 12, padding: "8px 14px" }}>
                  <IcoChat size={13} /> Wyjaśnij szerzej
                </button>
              )}
              {chatStatus === "loading" && (
                <div style={{ fontSize: 12, color: C.accent, marginTop: 10, display: "flex", alignItems: "center", gap: 6 }}>
                  <IcoChat size={13} /> Analiza odpowiedzi...
                </div>
              )}
              {chatStatus === "loaded" && (
                <div style={{ fontSize: 13, color: C.text, marginTop: 12, padding: "13px 14px", background: "#fff", borderRadius: 12, border: `1px solid #DCE5FF`, lineHeight: 1.65 }}>
                  {chatRes}
                </div>
              )}
            </div>
          )}

          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, paddingTop: 2 }}>
            <button onClick={prev} disabled={idx === 0} style={{ ...s.btn("ghost"), opacity: idx === 0 ? 0.45 : 1, fontSize: 13 }}>
              <IcoLeft size={13} /> Poprzednie
            </button>
            <button onClick={next} disabled={!nextUnlocked} style={{ ...s.btn("primary"), opacity: !nextUnlocked ? 0.45 : 1, fontSize: 13, padding: "12px 22px" }}>
              {idx === total - 1 ? "Zakończ quiz" : "Następne"} <IcoRight size={13} />
            </button>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ ...s.card, padding: 18 }}>
            <div className="tinyLabel" style={{ marginBottom: 12 }}>Postęp sesji</div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
              <div>
                <div style={{ fontSize: 28, fontWeight: 800, color: C.text }}>{progressPct}%</div>
                <div style={{ fontSize: 12, color: C.textSub }}>przejścia przez quiz</div>
              </div>
              <div style={{ width: 62, height: 62, borderRadius: 18, background: C.accentSoft, display: "flex", alignItems: "center", justifyContent: "center", color: C.accent }}>
                <IcoBolt size={24} />
              </div>
            </div>
            <div style={{ height: 8, background: "#EDF2F8", borderRadius: 999, overflow: "hidden", marginBottom: 14 }}>
              <div style={{ height: "100%", width: `${progressPct}%`, background: `linear-gradient(90deg, ${C.accent}, ${C.accentL})`, borderRadius: 999, transition: "width .35s ease" }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div style={{ ...s.metric, padding: 14 }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Skuteczność</div>
                <div style={{ fontSize: 20, fontWeight: 800, color: pct >= 70 ? C.green : pct >= 50 ? C.yellow : C.red }}>{pct}%</div>
              </div>
              <div style={{ ...s.metric, padding: 14 }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Śr. czas</div>
                <div style={{ fontSize: 20, fontWeight: 800, color: C.text }}>{answeredCount ? fmt(stats.avgResponseMs) : "—"}</div>
              </div>
            </div>
          </div>

          <div style={{ ...s.card, padding: 18 }}>
            <div className="tinyLabel" style={{ marginBottom: 12 }}>Szybkie wskazówki</div>
            <div style={{ display: "grid", gap: 10 }}>
              {[
                [<IcoTarget key="1" size={15} />, "Najpierw eliminuj skrajnie błędne opcje."],
                [<IcoClock key="2" size={15} />, "Nie przyspieszaj kosztem poprawności przy trudnych pytaniach."],
                [<IcoKeyboard key="3" size={15} />, "A/B/C/D wybiera odpowiedź, Enter przechodzi dalej."],
              ].map(([icon, text], i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 12, borderRadius: 14, background: C.cardAlt, border: `1px solid ${C.border}` }}>
                  <span style={{ width: 30, height: 30, borderRadius: 10, background: "#fff", border: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "center", color: C.accent, flexShrink: 0 }}>{icon}</span>
                  <span style={{ fontSize: 13, lineHeight: 1.55, color: C.textSub }}>{text}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...s.card, padding: 18 }}>
            <div className="tinyLabel" style={{ marginBottom: 10 }}>Sesje ogółem</div>
            <div style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                <span style={{ color: C.textSub }}>Ukończone próby</span>
                <span style={{ fontWeight: 800, color: C.text }}>{uniq.length}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                <span style={{ color: C.textSub }}>Seria dni</span>
                <span style={{ fontWeight: 800, color: C.accent }}>{streak} 🔥</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                <span style={{ color: C.textSub }}>Ostatni słaby obszar</span>
                <span style={{ fontWeight: 700, color: C.text, textAlign: "right" }}>{stats.weakest?.category || "—"}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const ResultsView = () => {
    if (!showResult)
      return (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 16, color: C.textSub }}>
          <div style={{ width: 72, height: 72, borderRadius: 22, background: C.accentSoft, display: "flex", alignItems: "center", justifyContent: "center", color: C.accent }}>
            <IcoTrophy size={34} />
          </div>
          <p style={{ fontSize: 14 }}>Ukończ quiz, aby zobaczyć wyniki.</p>
          <button onClick={() => setActiveTab("quiz")} style={s.btn("primary")}>Wróć do quizu</button>
        </div>
      );
    const pctFinal = Math.round((score / Math.max(total, 1)) * 100);
    const ring = 2 * Math.PI * 52;
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 18, height: "100%" }}>
        <div style={{ ...s.card, padding: "28px 26px", display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
          <div style={{ position: "relative", width: 120, height: 120, flexShrink: 0 }}>
            <svg width={120} height={120} style={{ transform: "rotate(-90deg)" }}>
              <circle cx={60} cy={60} r={52} fill="none" stroke="#EAF0F7" strokeWidth={10} />
              <circle
                cx={60}
                cy={60}
                r={52}
                fill="none"
                stroke={C.accent}
                strokeWidth={10}
                strokeDasharray={ring}
                strokeDashoffset={ring * (1 - pctFinal / 100)}
                strokeLinecap="round"
                style={{ transition: "stroke-dashoffset 1s ease" }}
              />
            </svg>
            <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
              <span style={{ fontSize: 26, fontWeight: 800, color: C.text }}>{pctFinal}%</span>
            </div>
          </div>
          <div style={{ flex: 1, minWidth: 240 }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: C.text, marginBottom: 4, letterSpacing: "-.03em" }}>Quiz ukończony</div>
            <div style={{ fontSize: 14, color: C.textSub, marginBottom: 12 }}>{score} poprawnych na {total} pytań</div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <button onClick={() => startQuiz(questionPool, quizLength)} style={{ ...s.btn("primary"), fontSize: 12, padding: "8px 16px" }}>
                <IcoRefresh size={13} /> Nowa sesja
              </button>
              <button onClick={() => setActiveTab("plan")} style={{ ...s.btn("soft"), fontSize: 12, padding: "8px 16px" }}>
                <IcoBook size={13} /> Plan nauki
              </button>
            </div>
          </div>
        </div>

        <div className="results-grid">
          {[
            { l: "Czas na pytanie", v: fmt(stats.avgResponseMs), c: C.yellow },
            { l: "Całkowity czas", v: fmt(stats.totalTimeMs), c: C.text },
            { l: "Poprawne", v: stats.correctCount, c: C.green },
            { l: "Błędne", v: stats.incorrectCount, c: C.red },
          ].map((item) => (
            <div key={item.l} style={{ ...s.cardSm, padding: "16px 18px" }}>
              <div className="tinyLabel" style={{ marginBottom: 8 }}>{item.l}</div>
              <div style={{ fontSize: 26, fontWeight: 800, color: item.c }}>{item.v}</div>
            </div>
          ))}
        </div>

        {stats.byCat.length > 0 && (
          <div style={{ ...s.card, padding: "20px 22px", flex: 1 }}>
            <div className="tinyLabel" style={{ marginBottom: 14 }}>Wyniki wg kategorii</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {stats.byCat.map((c) => (
                <div key={c.category}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                    <span style={{ fontSize: 14, color: C.text, fontWeight: 600 }}>{c.category}</span>
                    <span style={{ fontSize: 13, fontWeight: 800, color: c.percent >= 70 ? C.green : C.red }}>{c.percent}%</span>
                  </div>
                  <div style={{ height: 8, background: "#EEF2F7", borderRadius: 999, overflow: "hidden" }}>
                    <div style={{ height: "100%", width: `${c.percent}%`, background: c.percent >= 70 ? C.green : C.red, borderRadius: 999, transition: "width .6s ease" }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  const CalendarView = () => {
    const recentActiveDays = Object.entries(dayMap)
      .sort((a, b) => (a[0] < b[0] ? 1 : -1))
      .slice(0, 6);

    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16, height: "100%" }}>
        <div className="calendar-top-grid">
          {[
            { l: "Aktywne dni", v: monthDaysActive, hint: "w tym miesiącu", icon: <IcoCalendar size={16} />, tone: C.accent },
            { l: "Próby", v: monthAttempts.length, hint: "wszystkie sesje", icon: <IcoBrain size={16} />, tone: C.accent2 },
            { l: "Śr. wynik", v: monthAttempts.length ? `${monthAvg}%` : "—", hint: "miesięczna średnia", icon: <IcoTrending size={16} />, tone: C.green },
            { l: "Seria", v: `${streak} dni`, hint: "ciągłość nauki", icon: <IcoBolt size={16} />, tone: C.yellow },
          ].map((c) => (
            <div key={c.l} style={{ ...s.cardSm, padding: "16px 18px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <div className="tinyLabel">{c.l}</div>
                <span style={{ width: 32, height: 32, borderRadius: 10, background: `${c.tone}18`, color: c.tone, display: "flex", alignItems: "center", justifyContent: "center" }}>{c.icon}</span>
              </div>
              <div style={{ fontSize: 26, fontWeight: 800, color: C.text }}>{c.v}</div>
              <div style={{ fontSize: 12, color: C.textSub, marginTop: 4 }}>{c.hint}</div>
            </div>
          ))}
        </div>

        <div className="calendar-main-grid" style={{ minHeight: 0, flex: 1 }}>
          <div style={{ ...s.card, padding: 20, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, gap: 10, flexWrap: "wrap" }}>
              <div>
                <div style={{ fontSize: 22, fontWeight: 800, color: C.text, textTransform: "capitalize", letterSpacing: "-.03em" }}>
                  {calMonth.toLocaleString("pl-PL", { month: "long", year: "numeric" })}
                </div>
                <div style={{ fontSize: 12, color: C.textSub, marginTop: 4 }}>Kliknij dzień, aby zobaczyć szczegóły aktywności.</div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setCalMonth((m) => addM(m, -1))} style={{ ...s.btn("ghost"), padding: "8px 12px" }}><IcoLeft size={14} /></button>
                <button onClick={() => setCalMonth(som(new Date()))} style={{ ...s.btn("soft"), padding: "8px 14px", fontSize: 12 }}>Dziś</button>
                <button onClick={() => setCalMonth((m) => addM(m, 1))} style={{ ...s.btn("ghost"), padding: "8px 12px" }}><IcoRight size={14} /></button>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8, marginBottom: 10 }}>
              {["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"].map((d) => (
                <div key={d} style={{ textAlign: "center", fontSize: 10, fontWeight: 800, color: C.muted, textTransform: "uppercase", letterSpacing: ".08em" }}>{d}</div>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 8, minHeight: 0 }}>
              {calDays.map((i) => {
                const info = dayMap[i.key];
                const heat = getHeat(info);
                const isSelected = selectedCalDay === i.key;
                const isToday = dayKey(Date.now()) === i.key;
                return (
                  <button
                    key={i.key}
                    onClick={() => setSelectedCalDay(i.key)}
                    style={{
                      minHeight: 86,
                      borderRadius: 16,
                      border: `1px solid ${isSelected ? C.accent : heat.border}`,
                      background: isSelected ? "#EEF2FF" : i.inCurrent ? heat.bg : "#FAFBFD",
                      cursor: "pointer",
                      textAlign: "left",
                      padding: 10,
                      position: "relative",
                      transition: "all .18s ease",
                      opacity: i.inCurrent ? 1 : 0.62,
                      boxShadow: isSelected ? "0 10px 24px rgba(79, 107, 255, 0.12)" : "none",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                      <span style={{ fontSize: 13, fontWeight: 800, color: isSelected ? C.accent : i.inCurrent ? C.text : C.muted }}>{i.date.getDate()}</span>
                      {isToday && <span style={{ width: 8, height: 8, borderRadius: "50%", background: C.accent }} />}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <span style={{ fontSize: 11, color: info ? C.textSub : C.muted, fontWeight: 700 }}>
                        {info ? `${info.count} ${info.count === 1 ? "sesja" : "sesje"}` : "brak"}
                      </span>
                      {info && (
                        <>
                          <div style={{ height: 5, borderRadius: 999, background: "rgba(79,107,255,.12)", overflow: "hidden" }}>
                            <div style={{ width: `${info.avgPercent}%`, height: "100%", background: info.avgPercent >= 70 ? C.green : C.accent, borderRadius: 999 }} />
                          </div>
                          <div style={{ fontSize: 10, color: C.textSub }}>avg {info.avgPercent}%</div>
                        </>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
              <span style={{ fontSize: 11, color: C.textSub, fontWeight: 700 }}>Intensywność</span>
              {[
                ["0", "transparent"],
                ["1", "#F4F7FF"],
                ["2", "#EAF0FF"],
                ["3+", "#DDE5FF"],
              ].map(([label, bg]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 16, height: 16, borderRadius: 5, background: bg, border: `1px solid ${C.border}` }} />
                  <span style={{ fontSize: 11, color: C.textSub }}>{label}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
            <div style={{ ...s.card, padding: 18 }}>
              <div className="tinyLabel" style={{ marginBottom: 10 }}>Wybrany dzień</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: C.text, lineHeight: 1.4, textTransform: "capitalize", marginBottom: 14 }}>{humanDate(selectedCalDay)}</div>

              {selectedDaySummary ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <div className="day-summary-grid">
                    {[
                      ["Sesje", selectedDaySummary.count],
                      ["Śr. wynik", `${selectedDaySummary.avgPercent}%`],
                      ["Najlepszy", `${selectedDaySummary.bestPercent}%`],
                      ["Śr. czas", fmt(selectedDaySummary.avgTime)],
                    ].map(([l, v]) => (
                      <div key={l} style={{ ...s.metric, padding: 14 }}>
                        <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{l}</div>
                        <div style={{ fontSize: 20, fontWeight: 800, color: C.text }}>{v}</div>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: "grid", gap: 8, marginTop: 2 }}>
                    {selectedDayAttempts.slice(0, 4).map((a) => (
                      <div key={a.id} style={{ padding: "12px 14px", borderRadius: 14, background: C.cardAlt, border: `1px solid ${C.border}` }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
                          <span style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{new Date(a.finishedAt).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}</span>
                          <span style={{ fontSize: 12, fontWeight: 800, color: a.percent >= 70 ? C.green : a.percent >= 50 ? C.yellow : C.red }}>{a.percent}%</span>
                        </div>
                        <div style={{ fontSize: 12, color: C.textSub, display: "flex", justifyContent: "space-between", gap: 12 }}>
                          <span>{a.score}/{a.totalQuestions} poprawnych</span>
                          <span>{fmt(a.totalTimeMs)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div style={{ padding: "14px 16px", borderRadius: 14, background: C.cardAlt, border: `1px solid ${C.border}`, fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>
                  Tego dnia nie ma zapisanych prób. Kliknij dzień z aktywnością, aby zobaczyć statystyki sesji.
                </div>
              )}
            </div>

            <div style={{ ...s.card, padding: 18 }}>
              <div className="tinyLabel" style={{ marginBottom: 12 }}>Szybki przegląd miesiąca</div>
              <div style={{ display: "grid", gap: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <span style={{ fontSize: 13, color: C.textSub }}>Najaktywniejszy dzień</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{bestStudyDay ? `${bestStudyDay[0]} · ${bestStudyDay[1].count} sesje` : "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <span style={{ fontSize: 13, color: C.textSub }}>Średnia miesiąca</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: monthAvg >= 70 ? C.green : C.text }}>{monthAttempts.length ? `${monthAvg}%` : "—"}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <span style={{ fontSize: 13, color: C.textSub }}>Dni aktywne / miesiąc</span>
                  <span style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{monthDaysActive}</span>
                </div>
              </div>
            </div>

            <div style={{ ...s.card, padding: 18 }}>
              <div className="tinyLabel" style={{ marginBottom: 12 }}>Ostatnia aktywność</div>
              <div style={{ display: "grid", gap: 8 }}>
                {recentActiveDays.length ? recentActiveDays.map(([key, info]) => (
                  <div key={key} style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "10px 12px", borderRadius: 12, background: C.cardAlt, border: `1px solid ${C.border}` }}>
                    <span style={{ fontSize: 12, color: C.text }}>{key}</span>
                    <span style={{ fontSize: 12, color: C.textSub }}>{info.count} sesje · {info.avgPercent}% avg</span>
                  </div>
                )) : <div style={{ fontSize: 13, color: C.textSub }}>Brak historii.</div>}
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  };

  const PlanView = () => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 14 }}>
      <div style={{ ...s.card, padding: "24px 24px", background: "linear-gradient(135deg, #5D75FF, #8C99FF)", border: "none", color: "#fff" }}>
        <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".1em", color: "rgba(255,255,255,.72)", textTransform: "uppercase", marginBottom: 8 }}>Rekomendacja systemu</div>
        <div style={{ fontSize: 20, fontWeight: 800, lineHeight: 1.5 }}>{plan.recommendation}</div>
        <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {plan.improvements.map((item) => (
            <span key={item} style={{ padding: "8px 12px", borderRadius: 999, background: "rgba(255,255,255,.16)", fontSize: 12, fontWeight: 700 }}>{item}</span>
          ))}
        </div>
      </div>
      <div style={{ display: "grid", gap: 10, flex: 1 }}>
        {plan.weeklyPlan.map((item) => (
          <div key={item.day} style={{ ...s.cardSm, padding: "15px 18px", display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ width: 44, height: 44, borderRadius: 14, background: C.accentSoft, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: C.accent }}>{item.day}</span>
            </div>
            <span style={{ fontSize: 14, color: C.text, flex: 1, fontWeight: 600 }}>{item.task}</span>
            <span style={{ fontSize: 11, fontWeight: 800, color: C.textSub, background: C.cardAlt, border: `1px solid ${C.border}`, padding: "5px 10px", borderRadius: 999 }}>{item.duration}</span>
          </div>
        ))}
      </div>
    </div>
  );

  const SettingsView = () => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 16 }}>
      <div style={{ ...s.card, padding: "20px 22px" }}>
        <div className="tinyLabel" style={{ marginBottom: 14 }}>Baza pytań ({questionPool.length} pytań)</div>
        <button onClick={() => fileRef.current?.click()} style={{ ...s.btn("soft"), width: "100%", padding: "14px", borderStyle: "dashed", fontSize: 13 }}>
          <IcoUpload size={15} /> Wgraj własny plik (CSV / XLSX / TXT)
        </button>
        <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" style={{ display: "none" }} onChange={handleImport} />
        {importMsg && (
          <div
            style={{
              marginTop: 10,
              padding: "10px 14px",
              borderRadius: 10,
              background: importMsg.startsWith("✓") ? "#EEFBF3" : "#FFF3F2",
              border: `1px solid ${importMsg.startsWith("✓") ? "#B7E7C8" : "#F4C7C4"}`,
              fontSize: 12,
              color: importMsg.startsWith("✓") ? C.green : C.red,
              fontWeight: 700,
            }}
          >
            {importMsg}
          </div>
        )}
      </div>

      <div style={{ ...s.card, padding: "20px 22px" }}>
        <div className="tinyLabel" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <IcoKeyboard size={14} /> Skróty klawiaturowe
        </div>
        <div className="settings-grid">
          {[
            ["A B C D", "Wybór odpowiedzi"],
            ["Enter", "Następne pytanie"],
            ["← / ↑", "Poprzednie pytanie"],
            ["→ / ↓", "Następne pytanie"],
            ["R", "Restart quizu"],
          ].map(([key, desc]) => (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", background: C.cardAlt, borderRadius: 12, border: `1px solid ${C.border}` }}>
              <span style={{ padding: "4px 8px", borderRadius: 8, background: "#fff", border: `1px solid ${C.border}`, fontSize: 11, fontWeight: 800, color: C.text, letterSpacing: ".04em", flexShrink: 0 }}>{key}</span>
              <span style={{ fontSize: 12, color: C.textSub }}>{desc}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ ...s.card, padding: "20px 22px" }}>
        <div className="tinyLabel" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
          <IcoCloud size={14} /> Synchronizacja chmury
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: SB_ENABLED ? C.green : C.muted }} />
          <span style={{ fontSize: 13, color: SB_ENABLED ? C.green : C.textSub, fontWeight: 700 }}>
            {SB_ENABLED ? "Supabase połączono" : "Brak połączenia (tryb lokalny)"}
          </span>
        </div>
        <p style={{ fontSize: 12, color: C.textSub, marginTop: 8, lineHeight: 1.6 }}>
          Historia wyników zapisuje się lokalnie, a przy poprawnej konfiguracji może być też synchronizowana z Supabase.
        </p>
      </div>

      <button onClick={() => { localStorage.removeItem(STORAGE_KEY); setHistory([]); }} style={{ ...s.btn("danger"), width: "100%", padding: "13px", fontSize: 13, marginTop: "auto" }}>
        <IcoTrash size={14} /> Zresetuj lokalne statystyki
      </button>
    </div>
  );

  const Sidebar = () => (
    <aside className="sidebar-shell">
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "4px 8px", marginBottom: 22 }}>
        <div style={{ width: 42, height: 42, borderRadius: 14, background: "linear-gradient(135deg, #4F6BFF, #7E92FF)", display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", boxShadow: "0 12px 24px rgba(79,107,255,.24)" }}>
          <IcoBrain size={20} />
        </div>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: C.text, letterSpacing: "-.03em" }}>QuizApp</div>
          <div style={{ fontSize: 12, color: C.textSub }}>lżejszy layout · lepsza czytelność</div>
        </div>
      </div>

      <div style={{ display: "grid", gap: 6 }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTab(t.id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "11px 14px",
              borderRadius: 14,
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              width: "100%",
              transition: "all .15s",
              background: activeTab === t.id ? C.accentSoft : "transparent",
              color: activeTab === t.id ? C.accent : C.textSub,
              fontWeight: activeTab === t.id ? 800 : 600,
              fontSize: 13,
            }}
          >
            <span style={{ color: activeTab === t.id ? C.accent : C.muted }}>{t.icon}</span>
            {t.label}
            {t.id === "results" && showResult && <span style={{ marginLeft: "auto", width: 8, height: 8, borderRadius: "50%", background: C.accent, flexShrink: 0 }} />}
          </button>
        ))}
      </div>

      <div style={{ height: 1, background: C.border, margin: "14px 0" }} />

      <div>
        <div className="tinyLabel" style={{ marginBottom: 10, paddingLeft: 8 }}>Długość sesji</div>
        <div style={{ display: "flex", gap: 4, background: C.cardAlt, padding: 4, borderRadius: 14, border: `1px solid ${C.border}` }}>
          {[5, 10, 20, "∞"].map((len, i) => {
            const val = i === 3 ? "all" : len;
            const active = quizLength === val;
            return (
              <button
                key={len}
                onClick={() => {
                  setQuizLength(val);
                  startQuiz(questionPool, val);
                }}
                style={{
                  flex: 1,
                  padding: "8px 0",
                  borderRadius: 10,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 800,
                  transition: "all .15s",
                  background: active ? C.accent : "transparent",
                  color: active ? "#fff" : C.textSub,
                }}
              >
                {len}
              </button>
            );
          })}
        </div>
      </div>

      <button onClick={() => startQuiz(questionPool, quizLength)} style={{ ...s.btn("ghost"), width: "100%", marginTop: 10, fontSize: 12 }}>
        <IcoRefresh size={13} /> Nowa sesja
      </button>

      <div style={{ marginTop: "auto", display: "grid", gap: 10 }}>
        <div style={{ ...s.cardSm, padding: 14 }}>
          <div className="tinyLabel" style={{ marginBottom: 8 }}>Szybki podgląd</div>
          <div style={{ display: "grid", gap: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <span style={{ color: C.textSub }}>Próby</span>
              <span style={{ color: C.text, fontWeight: 800 }}>{uniq.length}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
              <span style={{ color: C.textSub }}>Seria</span>
              <span style={{ color: C.accent, fontWeight: 800 }}>{streak} 🔥</span>
            </div>
          </div>
        </div>
      </div>
    </aside>
  );

  const StatusBar = () => (
    <div className="status-shell">
      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
        <span style={{ fontSize: 12, fontWeight: 800, color: C.textSub, whiteSpace: "nowrap" }}>{idx + 1} / {total}</span>
        <div style={{ flex: 1, height: 8, background: "#E9EEF6", borderRadius: 999, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${progressPct}%`, background: `linear-gradient(90deg, ${C.accent}, ${C.accentL})`, borderRadius: 999, transition: "width .35s ease" }} />
        </div>
        <span style={{ fontSize: 12, fontWeight: 800, color: C.textSub }}>{progressPct}%</span>
      </div>

      <div className="status-right" style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 12, color: C.textSub }}>Skuteczność</span>
          <span style={{ fontSize: 14, fontWeight: 800, color: pct >= 70 ? C.green : pct >= 50 ? C.yellow : C.red }}>{pct}%</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: C.green }}>✓ {stats.correctCount}</span>
          <span style={{ fontSize: 11, color: C.muted }}>/</span>
          <span style={{ fontSize: 12, fontWeight: 700, color: C.red }}>✗ {stats.incorrectCount}</span>
        </div>
      </div>
    </div>
  );

  return (
    <>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0;}
        html,body,#root{height:100%;}
        body{
          background:${C.bg};
          color:${C.text};
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          overflow:hidden;
        }
        button:focus-visible{outline:2px solid ${C.accent};outline-offset:2px;}
        .tinyLabel{
          font-size:11px;
          font-weight:800;
          color:${C.textSub};
          letter-spacing:.08em;
          text-transform:uppercase;
        }
        .app-shell{
          width:100vw;
          height:100vh;
          display:flex;
          background:${C.bg};
          overflow:hidden;
        }
        .sidebar-shell{
          width:264px;
          background:rgba(255,255,255,.8);
          backdrop-filter:blur(8px);
          border-right:1px solid ${C.border};
          display:flex;
          flex-direction:column;
          flex-shrink:0;
          padding:24px 16px;
          gap:8px;
        }
        .content-shell{
          flex:1;
          display:flex;
          flex-direction:column;
          overflow:hidden;
        }
        .status-shell{
          height:60px;
          background:rgba(255,255,255,.78);
          backdrop-filter:blur(8px);
          border-bottom:1px solid ${C.border};
          display:flex;
          align-items:center;
          justify-content:space-between;
          padding:0 28px;
          gap:20px;
          flex-shrink:0;
        }
        .main-shell{
          flex:1;
          overflow:auto;
          padding:28px;
        }
        .page-inner{
          max-width:1320px;
          margin:0 auto;
          min-height:100%;
        }
        .quiz-grid{
          display:grid;
          grid-template-columns:minmax(0,1.7fr) minmax(300px,.8fr);
          gap:18px;
          align-items:start;
        }
        .results-grid{
          display:grid;
          grid-template-columns:repeat(4,minmax(0,1fr));
          gap:12px;
        }
        .calendar-top-grid{
          display:grid;
          grid-template-columns:repeat(4,minmax(0,1fr));
          gap:12px;
        }
        .calendar-main-grid{
          display:grid;
          grid-template-columns:minmax(0,1.55fr) minmax(320px,.75fr);
          gap:16px;
        }
        .day-summary-grid{
          display:grid;
          grid-template-columns:repeat(2,minmax(0,1fr));
          gap:10px;
        }
        .settings-grid{
          display:grid;
          grid-template-columns:1fr 1fr;
          gap:10px;
        }
        @media (max-width: 1180px){
          .quiz-grid,
          .calendar-main-grid{
            grid-template-columns:1fr;
          }
          .calendar-top-grid{
            grid-template-columns:repeat(2,minmax(0,1fr));
          }
          .results-grid{
            grid-template-columns:repeat(2,minmax(0,1fr));
          }
        }
        @media (max-width: 920px){
          .sidebar-shell{display:none;}
          .main-shell{padding:18px;}
          .status-shell{padding:0 18px;}
          .status-right{display:none !important;}
        }
        @media (max-width: 720px){
          .calendar-top-grid,
          .results-grid,
          .day-summary-grid,
          .settings-grid{
            grid-template-columns:1fr;
          }
        }
      `}</style>

      <div className="app-shell">
        <Sidebar />
        <div className="content-shell">
          <StatusBar />
          <main className="main-shell">
            <div className="page-inner">
              {activeTab === "quiz" && <QuizView />}
              {activeTab === "results" && <ResultsView />}
              {activeTab === "calendar" && <CalendarView />}
              {activeTab === "plan" && <PlanView />}
              {activeTab === "settings" && <SettingsView />}
            </div>
          </main>
        </div>
      </div>
    </>
  );
}

export default QuizAbcdApp;

const rootElement = document.getElementById("root");
if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <QuizAbcdApp />
    </React.StrictMode>
  );
}
