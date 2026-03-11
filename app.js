import { useState, useEffect, useMemo, useRef } from "react";
import * as XLSX from "xlsx";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

// ── inline SVG icons (no lucide-react) ───────────────────────────────────────
const Icon = ({ d, size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);
const IcoRotate   = () => <Icon d="M1 4v6h6M23 20v-6h-6M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 0 1 3.51 15" />;
const IcoTrophy   = () => <Icon d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6m12 5h1.5a2.5 2.5 0 0 0 0-5H18M12 12v4m-4 4h8M3 3h18v6a9 9 0 0 1-18 0V3z" />;
const IcoKeyboard = () => <Icon d="M20 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zM8 10H6m4 0h2m4 0h2M8 14h8" />;
const IcoBrain    = () => <Icon d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18a4 4 0 1 0 7.967-1.517 4 4 0 0 0 .556-6.588 4 4 0 0 0-2.526-5.77A3 3 0 0 0 12 5" />;
const IcoTarget   = () => <Icon d="M12 12m-1 0a1 1 0 1 1 2 0 1 1 0 1 1-2 0M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20M12 6a6 6 0 1 1 0 12 6 6 0 0 1 0-12" />;
const IcoClock    = () => <Icon d="M12 2a10 10 0 1 1 0 20A10 10 0 0 1 12 2zm0 4v6l4 2" />;
const IcoTrend    = () => <Icon d="M22 7l-8.5 8.5-5-5L2 17M22 7h-5m5 0v5" />;
const IcoUpload   = () => <Icon d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />;
const IcoCalendar = () => <Icon d="M8 2v4M16 2v4M3 8h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />;
const IcoBook     = () => <Icon d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z" />;
const IcoSpark    = () => <Icon d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.27 5.82 21 7 14.14 2 9.27l6.91-1.01L12 2z" />;
const IcoChart    = () => <Icon d="M18 20V10M12 20V4M6 20v-6" />;
const IcoCloud    = () => <Icon d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />;
const IcoFile     = () => <Icon d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6" />;
const IcoSettings = () => <Icon d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />;
const IcoTrash    = () => <Icon d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />;
const IcoRefresh  = () => <Icon d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;

// ── Supabase config ───────────────────────────────────────────────────────────
// ANON KEY musi być kluczem JWT (zaczyna się od eyJ...) z Project Settings → API
const SUPABASE_URL      = "https://ylqloszldyzpeaikweyl.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlscWxvc3psZHl6cGVhaWt3ZXlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyNDg2NDUsImV4cCI6MjA4ODgyNDY0NX0.JgwZKn5_ifnoZHViOGb7aED9sZ3MnijeeI66cFhSJaQ";

const SB_ENABLED =
  SUPABASE_URL.startsWith("https://") &&
  !SUPABASE_URL.includes("YOUR_PROJECT") &&
  !!SUPABASE_ANON_KEY &&
  !SUPABASE_ANON_KEY.includes("YOUR_KEY");

// ── Supabase REST helpers (no library) ───────────────────────────────────────
const sbHeaders = () => ({
  "Content-Type": "application/json",
  apikey: SUPABASE_ANON_KEY,
  Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
  Prefer: "return=representation",
});

async function sbSelect(table, params = "") {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, { headers: sbHeaders() });
  if (!res.ok) { const e = await res.text(); throw new Error(e); }
  return res.json();
}

async function sbUpsert(table, rows) {
  const BATCH = 100;
  for (let i = 0; i < rows.length; i += BATCH) {
    const chunk = rows.slice(i, i + BATCH);
    const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, {
      method: "POST",
      mode: "cors",
      headers: {
        "Content-Type": "application/json",
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
        Prefer: "resolution=merge-duplicates,return=minimal",
      },
      body: JSON.stringify(chunk),
    });
    if (!res.ok) { const e = await res.text(); throw new Error(`Batch ${i/BATCH+1}: ${e}`); }
  }
}

async function sbInsert(table, row) {
  const res = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, {
    method: "POST", headers: sbHeaders(), body: JSON.stringify(row),
  });
  if (!res.ok) { const e = await res.text(); throw new Error(e); }
}

// ── domain helpers ────────────────────────────────────────────────────────────
const STORAGE_KEY = "quiz_abcd_attempts_v1";
const optionKeys = ["A", "B", "C", "D"];
const difficultyWeights = { easy: 1, medium: 1.5, hard: 2 };

const normalizeDifficulty = (v) => {
  const r = String(v || "medium").trim().toLowerCase();
  return ["easy", "medium", "hard"].includes(r) ? r : "medium";
};

const loadLocalAttempts = () => { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; } };
const saveLocalAttempt = (a) => { const u = [a, ...loadLocalAttempts()].slice(0, 50); localStorage.setItem(STORAGE_KEY, JSON.stringify(u)); return u; };

// ── question mapping ──────────────────────────────────────────────────────────
function rowToQuestion(row, i) {
  return {
    id: row.id ?? i + 1,
    questionNo: row.question_no ?? i + 1,
    question: row.question_text,
    options: { A: row.option_a, B: row.option_b, C: row.option_c, D: row.option_d },
    correct: row.correct_answer || null,
    explanation: row.explanation || "Brak wyjaśnienia.",
    category: row.category || "General",
    difficulty: normalizeDifficulty(row.difficulty || "medium"),
    sourceType: row.source_type || "database",
    sourceFile: row.source_file || null,
  };
}

function questionToRow(q, i) {
  return {
    question_no: q.questionNo ?? i + 1,
    question_text: q.question,
    option_a: q.options.A, option_b: q.options.B,
    option_c: q.options.C, option_d: q.options.D,
    correct_answer: q.correct || null,
    explanation: q.explanation || null,
    category: q.category || "General",
    difficulty: normalizeDifficulty(q.difficulty || "medium"),
    source_type: q.sourceType || "import",
    source_file: q.sourceFile || null,
    is_active: true,
  };
}

// ── file parsers ──────────────────────────────────────────────────────────────
function parseQuestionsFromRows(rows, sourceFile = null) {
  return rows.map((row, i) => {
    const question = row.question ?? row.Question ?? row.pytanie ?? row.Pytanie;
    const a = row.A ?? row.a ?? row.optionA;
    const b = row.B ?? row.b ?? row.optionB;
    const c = row.C ?? row.c ?? row.optionC;
    const d = row.D ?? row.d ?? row.optionD;
    const correct = String(row.correct ?? row.Correct ?? row.poprawna ?? "").trim().toUpperCase();
    if (!question || !a || !b || !c || !d) return null;
    return {
      id: row.id ?? i + 1, questionNo: Number(row.questionNo ?? row.nr ?? i + 1),
      question: String(question).trim(),
      options: { A: String(a).trim(), B: String(b).trim(), C: String(c).trim(), D: String(d).trim() },
      correct: optionKeys.includes(correct) ? correct : null,
      explanation: String(row.explanation ?? row.wyjasnienie ?? "Brak wyjaśnienia.").trim(),
      category: String(row.category ?? row.kategoria ?? "General").trim(),
      difficulty: normalizeDifficulty(row.difficulty ?? row.trudnosc ?? "medium"),
      sourceType: "spreadsheet", sourceFile,
    };
  }).filter(Boolean);
}

function parseQuestionsFromTxt(text, sourceFile = "import.txt") {
  const norm = text.replace(/\r/g, "");
  return [...norm.matchAll(/Question\s+#(\d+)\s*([\s\S]*?)(?=\nQuestion\s+#\d+|$)/g)].map(match => {
    const no = Number(match[1]);
    const block = match[2].trim();
    const m = block.match(/^([\s\S]*?)\nA\.\s*([\s\S]*?)\nB\.\s*([\s\S]*?)\nC\.\s*([\s\S]*?)\nD\.\s*([\s\S]*?)(?:\nView answer|$)/);
    if (!m) return null;
    return {
      id: no, questionNo: no,
      question: m[1].replace(/\s+/g, " ").trim(),
      options: { A: m[2].replace(/\s+/g, " ").trim(), B: m[3].replace(/\s+/g, " ").trim(), C: m[4].replace(/\s+/g, " ").trim(), D: m[5].replace(/\s+/g, " ").trim() },
      correct: null, explanation: "Brak odpowiedzi w pliku źródłowym.",
      category: "Import", difficulty: "medium", sourceType: "txt_import", sourceFile,
    };
  }).filter(Boolean);
}

// ── calendar helpers ──────────────────────────────────────────────────────────
const formatDayKey = (ts) => { const d = new Date(ts); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const startOfMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const addMonths = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);

function buildCalendarDays(month) {
  const start = startOfMonth(month);
  const firstWeekday = (start.getDay() + 6) % 7;
  const gridStart = new Date(start); gridStart.setDate(start.getDate() - firstWeekday);
  return Array.from({ length: 42 }, (_, i) => {
    const date = new Date(gridStart); date.setDate(gridStart.getDate() + i);
    return { date, key: formatDayKey(date.getTime()), inCurrentMonth: date.getMonth() === start.getMonth() };
  });
}

function buildStudyPlan(history, weakestCategory) {
  if (!history.length) return { readiness:"Brak danych", recommendation:"Ukończ kilka prób quizu, aby wygenerować plan.", focusAreas:[], weeklyPlan:[], improvements:[] };
  const last5 = history.slice(0, 5);
  const avgAcc = Math.round(last5.reduce((s, a) => s + a.percent, 0) / last5.length);
  const avgPace = (last5.reduce((s, a) => s + a.avgResponseMs, 0) / last5.length / 1000).toFixed(1);
  const weakMap = {}, strongMap = {};
  last5.forEach(a => {
    if (a.weakestCategory) weakMap[a.weakestCategory] = (weakMap[a.weakestCategory] || 0) + 1;
    if (a.strongestCategory) strongMap[a.strongestCategory] = (strongMap[a.strongestCategory] || 0) + 1;
  });
  const weak = Object.entries(weakMap).sort((a,b)=>b[1]-a[1])[0]?.[0] || weakestCategory?.category || "Mieszane tematy";
  const strong = Object.entries(strongMap).sort((a,b)=>b[1]-a[1])[0]?.[0] || "Wiedza ogólna";
  const readiness = avgAcc >= 85 ? "Zaawansowany" : avgAcc >= 65 ? "Średniozaawansowany" : "Buduj podstawy";
  const recommendation = avgAcc >= 85 ? "Gotowy na trudniejsze zestawy i testy czasowe." : avgAcc >= 65 ? "Dobra baza — wzmocnij słabe kategorie." : "Skup się najpierw na dokładności.";
  return {
    readiness, recommendation,
    focusAreas: [
      { area: weak, reason: "Najczęściej pojawia się jako Twoja najsłabsza kategoria.", priority: avgAcc < 50 ? "Wysoki" : avgAcc < 75 ? "Średni" : "Niski" },
      { area: avgPace > 18 ? "Szybkość decyzji" : "Konsekwencja pod presją", reason: avgPace > 18 ? "Średnie tempo sugeruje za długie zastanawianie się." : "Tempo jest ok — kolejny zysk to stała dokładność.", priority: avgPace > 18 ? "Wysoki" : "Średni" },
      { area: strong, reason: "Użyj swojej mocnej strony do budowania pewności siebie.", priority: "Niski" },
    ],
    weeklyPlan: [
      { day:"Dzień 1", task:`Przejrzyj błędy z ostatnich 3 prób, szczególnie ${weak}.`, duration:"25 min" },
      { day:"Dzień 2", task:`Skupiony quiz z ${weak} i 3 krótkie wnioski.`, duration:"30 min" },
      { day:"Dzień 3", task:"Quiz mieszany z czasomierzem. Odpowiadaj pewnie.", duration:"20 min" },
      { day:"Dzień 4", task:"Wróć do wyjaśnień i podsumuj zasady własnymi słowami.", duration:"20 min" },
      { day:"Dzień 5", task:`Zestaw pewności w ${strong}, potem 5 trudniejszych pytań ze słabych obszarów.`, duration:"25 min" },
      { day:"Dzień 6", task:"Pełny próbny quiz — porównaj wynik z poprzednimi.", duration:"30 min" },
      { day:"Dzień 7", task:"Lekki przegląd: co się poprawiło, co powtórzyć w przyszłym tygodniu.", duration:"15 min" },
    ],
    improvements: [
      avgAcc < 70 ? "Zwolnij przy trudnych pytaniach i skup się na TYM, dlaczego poprawna odpowiedź jest prawidłowa." : "Utrzymaj dokładność i zwiększaj trudność przez mieszanie kategorii.",
      avgPace > 18 ? "Pracuj nad szybkością: odpowiadaj szybciej na łatwe pytania." : "Tempo jest dobre — popraw się przez szybsze wykrywanie dystraktorów.",
      `Przeznacz dodatkowy czas na ćwiczenia z ${weak}.`,
    ],
  };
}

const sampleQuestions = [
  { id:1, questionNo:1, question:"Który dokument formalnie definiuje zakres projektu?", options:{A:"Rejestr ryzyk",B:"Karta projektu",C:"Lessons learned",D:"Dziennik problemów"}, correct:"B", explanation:"Karta projektu (project charter) formalnie autoryzuje projekt i określa jego zakres.", category:"Zarządzanie projektami", difficulty:"medium", sourceType:"sample" },
  { id:2, questionNo:2, question:"Co oznacza skrót VAT?", options:{A:"Value Added Tax",B:"Variable Asset Transfer",C:"Verified Accounting Tool",D:"Value Allocation Table"}, correct:"A", explanation:"VAT to podatek od wartości dodanej (ang. Value Added Tax).", category:"Finanse", difficulty:"easy", sourceType:"sample" },
  { id:3, questionNo:3, question:"Które zdanie to najbardziej naturalne wyrażenie w angielskim biznesowym?", options:{A:"Let us cut to the chase.",B:"Let us cut to the hunt.",C:"Let us go to the speed.",D:"Let us go to the cut."}, correct:"A", explanation:'"Cut to the chase" oznacza "przejdź od razu do sedna sprawy".', category:"Język angielski", difficulty:"easy", sourceType:"sample" },
];

// ── shared UI primitives ──────────────────────────────────────────────────────
const Card = ({ children, className = "" }) => <div className={`bg-white rounded-2xl shadow-sm border border-slate-200 ${className}`}>{children}</div>;
const CardHeader = ({ children }) => <div className="p-5 pb-0">{children}</div>;
const CardTitle = ({ children, className = "" }) => <h2 className={`font-semibold text-slate-800 ${className}`}>{children}</h2>;
const CardContent = ({ children, className = "" }) => <div className={`p-5 ${className}`}>{children}</div>;
const Badge = ({ children, variant = "outline" }) => {
  const styles = { outline:"border border-slate-300 text-slate-600", secondary:"bg-slate-100 text-slate-700", destructive:"bg-red-100 text-red-700 border border-red-200", success:"bg-green-100 text-green-700 border border-green-200" };
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${styles[variant]||styles.outline}`}>{children}</span>;
};
const Btn = ({ children, onClick, disabled, variant = "default", className = "" }) => {
  const styles = { default:"bg-slate-900 text-white hover:bg-slate-700", outline:"border border-slate-300 bg-white text-slate-700 hover:bg-slate-50", danger:"border border-red-300 bg-white text-red-600 hover:bg-red-50" };
  return <button onClick={onClick} disabled={disabled} className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm font-medium transition-colors focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed ${styles[variant]||styles.default} ${className}`}>{children}</button>;
};
const Progress = ({ value, className = "" }) => (
  <div className={`w-full bg-slate-200 rounded-full overflow-hidden ${className}`}>
    <div className="bg-slate-900 h-full rounded-full transition-all" style={{ width:`${Math.min(100,Math.max(0,value))}%` }} />
  </div>
);
const StatusDot = ({ status }) => {
  const colors = { idle:"bg-slate-300", loading:"bg-yellow-400 animate-pulse", ok:"bg-green-500", error:"bg-red-500", disabled:"bg-slate-300" };
  return <span className={`inline-block h-2 w-2 rounded-full ${colors[status]||"bg-slate-300"}`} />;
};

// ── main app ──────────────────────────────────────────────────────────────────
export default function QuizAbcdApp() {
  const [questions, setQuestions]             = useState(sampleQuestions);
  const [currentIndex, setCurrentIndex]       = useState(0);
  const [selected, setSelected]               = useState(null);
  const [answers, setAnswers]                 = useState({});
  const [showResult, setShowResult]           = useState(false);
  const [startedAt]                           = useState(() => Date.now());
  const [questionStartedAt, setQSA]           = useState(() => Date.now());
  const [finishedAt, setFinishedAt]           = useState(null);
  const [attemptHistory, setAttemptHistory]   = useState(() => loadLocalAttempts());
  const [importMsg, setImportMsg]             = useState("Importuj CSV, Excel lub TXT.");
  const [activeTab, setActiveTab]             = useState("quiz");
  const [calendarMonth, setCalendarMonth]     = useState(() => startOfMonth(new Date()));
  const fileInputRef                          = useRef(null);

  // cloud status
  const [qStatus,   setQStatus]   = useState("idle");
  const [qMsg,      setQMsg]      = useState(SB_ENABLED ? "Kliknij 'Wczytaj z bazy' lub zaimportuj plik." : "Supabase nie jest skonfigurowany.");
  const [attStatus, setAttStatus] = useState("idle");
  const [testMsg,   setTestMsg]   = useState("");

  // ── Supabase: wczytaj pytania ─────────────────────────────────────────────
  const loadQuestionsFromDB = async () => {
    if (!SB_ENABLED) { setQStatus("disabled"); setQMsg("Supabase nie jest skonfigurowany."); return; }
    setQStatus("loading"); setQMsg("Wczytuję pytania z Supabase…");
    try {
      const rows = await sbSelect("quiz_questions", "is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length) { setQStatus("ok"); setQMsg("Baza pytań jest pusta. Zaimportuj plik i kliknij 'Wyślij do bazy'."); return; }
      const parsed = rows.map(rowToQuestion);
      setQuestions(parsed);
      setCurrentIndex(0); setSelected(null); setAnswers({});
      setShowResult(false); setFinishedAt(null); setActiveTab("quiz");
      setQStatus("ok"); setQMsg(`Wczytano ${parsed.length} pytań z Supabase.`);
    } catch (e) { setQStatus("error"); setQMsg(`Błąd wczytywania pytań: ${e.message}`); }
  };

  // ── Supabase: wyślij pytania ──────────────────────────────────────────────
  const pushQuestionsToDB = async () => {
    if (!SB_ENABLED) { setQStatus("disabled"); setQMsg("Supabase nie jest skonfigurowany."); return; }
    if (!questions.length) { setQMsg("Brak pytań do wysłania."); return; }
    setQStatus("loading"); setQMsg(`Wysyłam ${questions.length} pytań do Supabase…`);
    try {
      const rows = questions.map(questionToRow);
      await sbUpsert("quiz_questions", rows);
      setQStatus("ok"); setQMsg(`Wysłano ${rows.length} pytań do Supabase.`);
    } catch (e) { setQStatus("error"); setQMsg(`Błąd wysyłania pytań: ${e.message}`); }
  };

  // ── Supabase: wczytaj statystyki ──────────────────────────────────────────
  const loadAttemptsFromDB = async () => {
    if (!SB_ENABLED) { setAttStatus("disabled"); return; }
    setAttStatus("loading");
    try {
      const rows = await sbSelect("quiz_attempts", "order=finished_at.desc&limit=50");
      const mapped = rows.map(r => ({
        id: r.attempt_id, finishedAt: new Date(r.finished_at).getTime(),
        totalQuestions: r.total_questions, score: r.score, percent: r.percent,
        mastery: r.mastery, avgResponseMs: r.avg_response_ms,
        totalTimeMs: r.total_time_ms,
        strongestCategory: r.strongest_category,
        weakestCategory: r.weakest_category, source: "supabase",
      }));
      if (mapped.length) setAttemptHistory(mapped);
      setAttStatus("ok");
    } catch (e) { setAttStatus("error"); console.error(e); }
  };

  const testConnection = async () => {
    setTestMsg("Testuję…");
    if (!SB_ENABLED) { setTestMsg("❌ Klucz nie jest ustawiony — uzupełnij SUPABASE_ANON_KEY w kodzie."); return; }
    try {
      const res = await fetch(`${SUPABASE_URL}/rest/v1/quiz_questions?limit=1`, {
        headers: { apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}` },
      });
      if (res.ok) {
        const d = await res.json();
        setTestMsg(`✅ Połączenie OK. Tabela quiz_questions istnieje (${d.length} wierszy w próbce).`);
      } else {
        const e = await res.text();
        setTestMsg(`❌ HTTP ${res.status}: ${e}`);
      }
    } catch (e) {
      setTestMsg(`❌ Sieć/CORS: ${e.message} — sprawdź czy klucz zaczyna się od eyJ...`);
    }
  };

  // wczytaj dane przy starcie
  useEffect(() => { loadQuestionsFromDB(); loadAttemptsFromDB(); }, []);

  const current = questions[currentIndex] || sampleQuestions[0];
  const total   = questions.length;
  const answeredCount = Object.keys(answers).length;
  const score   = useMemo(() => Object.values(answers).filter(a => a.isCorrect).length, [answers]);

  const handleAnswer = (key) => {
    if (selected || showResult) return;
    const responseTimeMs = Date.now() - questionStartedAt;
    setSelected(key);
    setAnswers(prev => ({ ...prev, [current.id]: { questionId:current.id, selected:key, correct:current.correct, isCorrect:current.correct ? key===current.correct : false, responseTimeMs, category:current.category||"General", difficulty:current.difficulty||"medium" } }));
  };

  const nextQuestion = () => {
    if (currentIndex < total - 1) {
      const ni = currentIndex + 1;
      setCurrentIndex(ni); setSelected(answers[questions[ni].id]?.selected ?? null); setQSA(Date.now());
    } else {
      setFinishedAt(Date.now()); setShowResult(true); setActiveTab("results");
    }
  };

  const restart = () => { setCurrentIndex(0); setSelected(null); setAnswers({}); setShowResult(false); setQSA(Date.now()); setFinishedAt(null); setActiveTab("quiz"); };

  const handleFileImport = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    try {
      let parsed = [];
      if (file.name.toLowerCase().endsWith(".txt")) {
        parsed = parseQuestionsFromTxt(await file.text(), file.name);
      } else {
        const wb = XLSX.read(await file.arrayBuffer(), { type:"array" });
        parsed = parseQuestionsFromRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval:"" }), file.name);
      }
      if (!parsed.length) { setImportMsg("Import nieudany — sprawdź nagłówki kolumn lub format TXT."); return; }
      setQuestions(parsed); setCurrentIndex(0); setSelected(null); setAnswers({});
      setShowResult(false); setQSA(Date.now()); setFinishedAt(null); setActiveTab("quiz");
      setImportMsg(`Zaimportowano ${parsed.length} pytań z ${file.name}.${parsed.some(q=>!q.correct) ? " Niektóre pytania nie mają klucza odpowiedzi." : ""}`);
      setQStatus("idle"); setQMsg(`Zaimportowano ${parsed.length} pytań lokalnie. Kliknij 'Wyślij do bazy', aby zapisać w Supabase.`);
    } catch (err) { setImportMsg(`Import nieudany: ${err.message}`); }
    finally { e.target.value = ""; }
  };

  const stats = useMemo(() => {
    const list = questions.map(q => ({ q, a:answers[q.id] })).filter(x => x.a);
    const totalTimeMs = (finishedAt ?? Date.now()) - startedAt;
    const avgResponseMs = list.length ? list.reduce((s,x)=>s+x.a.responseTimeMs,0)/list.length : 0;
    const correctCount = list.filter(x=>x.a.isCorrect).length;
    const incorrectCount = list.length - correctCount;
    const wTotal = questions.reduce((s,q)=>s+(difficultyWeights[q.difficulty||"medium"]||1.5),0);
    const wScore = list.reduce((s,x)=>s+(x.a.isCorrect?(difficultyWeights[x.q.difficulty||"medium"]||1.5):0),0);
    const mastery = wTotal ? Math.round((wScore/wTotal)*100) : 0;
    const byCategory = Object.values(questions.reduce((acc,q)=>{
      const cat = q.category||"General";
      if(!acc[cat]) acc[cat]={category:cat,total:0,correct:0};
      acc[cat].total++; if(answers[q.id]?.isCorrect) acc[cat].correct++; return acc;
    },{})).map(c=>({...c,percent:Math.round((c.correct/c.total)*100)}));
    const weakest  = byCategory.length ? [...byCategory].sort((a,b)=>a.percent-b.percent)[0] : null;
    const strongest = byCategory.length ? [...byCategory].sort((a,b)=>b.percent-a.percent)[0] : null;
    const fastest  = list.filter(x=>x.a.isCorrect).sort((a,b)=>a.a.responseTimeMs-b.a.responseTimeMs)[0];
    const slowest  = [...list].sort((a,b)=>b.a.responseTimeMs-a.a.responseTimeMs)[0];
    const perf = mastery>=90?"Doskonale":mastery>=75?"Bardzo dobrze":mastery>=60?"Solidna podstawa":"Dalej ćwicz";
    return { totalTimeMs, avgResponseMs, correctCount, incorrectCount, mastery, byCategory, weakest, strongest, fastest, slowest, perf };
  }, [answers, questions, startedAt, finishedAt]);

  // zapisz próbę lokalnie + Supabase
  useEffect(() => {
    if (!showResult || !finishedAt) return;
    const percent = Math.round((score/total)*100);
    const attempt = {
      id:`${finishedAt}-${Math.random().toString(36).slice(2,7)}`,
      finishedAt, totalQuestions:total, score, percent,
      mastery:stats.mastery, avgResponseMs:Math.round(stats.avgResponseMs),
      totalTimeMs:Math.round(stats.totalTimeMs),
      strongestCategory:stats.strongest?.category||null,
      weakestCategory:stats.weakest?.category||null, source:"local",
    };
    setAttemptHistory(prev => { if(prev[0]?.finishedAt===finishedAt) return prev; return saveLocalAttempt(attempt); });
    if (!SB_ENABLED) return;
    sbInsert("quiz_attempts", {
      attempt_id: attempt.id,
      finished_at: new Date(attempt.finishedAt).toISOString(),
      total_questions: attempt.totalQuestions, score: attempt.score, percent: attempt.percent,
      mastery: attempt.mastery, avg_response_ms: attempt.avgResponseMs,
      total_time_ms: attempt.totalTimeMs,
      strongest_category: attempt.strongestCategory, weakest_category: attempt.weakestCategory,
    }).then(() => { setAttStatus("ok"); loadAttemptsFromDB(); }).catch(e => { setAttStatus("error"); console.error(e); });
  }, [showResult, finishedAt]);

  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target?.tagName;
      if (tag==="INPUT"||tag==="TEXTAREA"||e.target?.isContentEditable) return;
      const k = e.key.toUpperCase();
      if (!showResult && !selected && optionKeys.includes(k)) { e.preventDefault(); handleAnswer(k); return; }
      if (e.key==="Enter" && !showResult && selected) { e.preventDefault(); nextQuestion(); return; }
      if (k==="R") { e.preventDefault(); restart(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showResult, selected, questionStartedAt, answers, questions]);

  const fmt = (ms) => `${(ms/1000).toFixed(1)}s`;
  const studyDaysMap = useMemo(() => { const m={}; attemptHistory.forEach(a=>{ const k=formatDayKey(a.finishedAt); m[k]=(m[k]||0)+1; }); return m; }, [attemptHistory]);
  const calendarDays = useMemo(() => buildCalendarDays(calendarMonth), [calendarMonth]);
  const currentStreak = useMemo(() => { let s=0; const c=new Date(); while(studyDaysMap[formatDayKey(c.getTime())]){s++;c.setDate(c.getDate()-1);} return s; }, [studyDaysMap]);
  const studyPlan = useMemo(() => buildStudyPlan(attemptHistory, stats.weakest), [attemptHistory, stats.weakest]);
  const chartData = [...attemptHistory].reverse().map((a,i)=>({ name:`#${i+1}`, accuracy:a.percent, mastery:a.mastery, avgSeconds:Number((a.avgResponseMs/1000).toFixed(1)) }));
  const prev = attemptHistory[1] || null;
  const trend = prev && showResult ? { score:score-prev.score, percent:Math.round((score/total)*100)-prev.percent, avgResponseMs:Math.round(stats.avgResponseMs)-prev.avgResponseMs } : null;

  // ── tabs ──────────────────────────────────────────────────────────────────
  const tabs = [
    { id:"quiz",     label:"Quiz",        icon:null },
    { id:"calendar", label:"Kalendarz",   icon:<IcoCalendar /> },
    { id:"plan",     label:"Plan nauki",  icon:<IcoBook /> },
    ...(showResult ? [{ id:"results", label:"Wyniki", icon:<IcoChart /> }] : []),
    { id:"settings", label:"Ustawienia",  icon:<IcoSettings /> },
  ];

  // ── Quiz View ─────────────────────────────────────────────────────────────
  const QuizView = () => (
    <div className="space-y-4">
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 py-3 text-sm text-slate-600">
          <span className="flex items-center gap-1 font-medium text-slate-700"><IcoKeyboard /> Skróty</span>
          <Badge>A / B / C / D = odpowiedź</Badge>
          <Badge>Enter = dalej</Badge>
          <Badge>R = restart</Badge>
        </CardContent>
      </Card>
      <Progress value={(currentIndex/Math.max(total,1))*100} className="h-3" />
      <Card>
        <CardHeader><CardTitle className="text-xl">Pytanie {currentIndex+1} z {total}</CardTitle></CardHeader>
        <CardContent className="space-y-5">
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{current.category||"General"}</Badge>
            <Badge>{current.difficulty||"medium"}</Badge>
            <Badge>Q#{current.questionNo??current.id}</Badge>
            {current.sourceFile && <Badge><IcoFile /> {current.sourceFile}</Badge>}
          </div>
          <p className="text-lg font-medium leading-relaxed">{current.question}</p>
          <div className="grid gap-3">
            {optionKeys.map(key => {
              const isSel=selected===key, isCorr=current.correct===key, reveal=!!selected;
              let border="border-slate-200 hover:bg-slate-50";
              if(reveal&&current.correct&&isCorr) border="border-green-500 bg-green-50";
              else if(reveal&&isSel&&current.correct&&!isCorr) border="border-red-400 bg-red-50";
              return (
                <button key={key} onClick={()=>handleAnswer(key)}
                  className={`w-full text-left flex items-center gap-3 rounded-2xl border-2 px-4 py-4 text-base transition-colors ${border}`}>
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm font-semibold">{key}</span>
                  <span>{current.options[key]}</span>
                </button>
              );
            })}
          </div>
          {selected && (
            <div className="rounded-xl bg-slate-100 p-4 text-sm text-slate-700">
              <div className="font-medium">{current.correct ? selected===current.correct ? "✓ Poprawnie." : `✗ Poprawna odpowiedź: ${current.correct}.` : "Klucz odpowiedzi niedostępny."}</div>
              <div className="mt-1">{current.explanation}</div>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            <Btn onClick={nextQuestion} disabled={!selected}>{currentIndex===total-1?"Pokaż wyniki":"Następne pytanie"}</Btn>
            <Btn variant="outline" onClick={restart}><IcoRotate /> Reset</Btn>
          </div>
        </CardContent>
      </Card>
    </div>
  );

  // ── Results View ──────────────────────────────────────────────────────────
  const ResultsView = () => {
    const pct = Math.round((score/total)*100);
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2 text-2xl"><IcoTrophy /> Quiz ukończony</CardTitle></CardHeader>
          <CardContent className="space-y-6">
            <div>
              <div className="mb-2 text-lg font-medium">Wynik: {score}/{total}</div>
              <Progress value={pct} className="h-3" />
              <div className="mt-2 text-sm text-slate-600">{pct}% poprawnych odpowiedzi — {stats.perf}</div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {[
                { icon:<IcoBrain />,  label:"Mastery",      value:`${stats.mastery}%`,          sub:stats.perf },
                { icon:<IcoClock />,  label:"Śr. tempo",    value:fmt(stats.avgResponseMs),      sub:"na pytanie" },
                { icon:<IcoTarget />, label:"Dokładność",   value:`${pct}%`,                     sub:`${stats.correctCount} poprawne, ${stats.incorrectCount} błędne` },
                { icon:<IcoTrend />,  label:"Całk. czas",   value:fmt(stats.totalTimeMs),        sub:"cała próba" },
              ].map(c=>(
                <Card key={c.label}><CardContent className="p-5">
                  <div className="flex items-center gap-1 text-sm text-slate-500">{c.icon} {c.label}</div>
                  <div className="mt-2 text-2xl font-semibold">{c.value}</div>
                  <div className="mt-1 text-sm text-slate-600">{c.sub}</div>
                </CardContent></Card>
              ))}
            </div>
            {trend && (
              <div className="rounded-xl bg-slate-100 p-4 text-sm text-slate-700">
                <div className="font-medium">vs poprzednia próba</div>
                <div className="mt-2 flex flex-wrap gap-4">
                  <span>Wynik: {trend.score>=0?"+":""}{trend.score}</span>
                  <span>Dokładność: {trend.percent>=0?"+":""}{trend.percent} pp</span>
                  <span>Tempo: {trend.avgResponseMs<=0?"szybciej":"wolniej"} o {fmt(Math.abs(trend.avgResponseMs))}</span>
                </div>
              </div>
            )}
            <Card>
              <CardHeader><CardTitle>Wykresy postępów</CardTitle></CardHeader>
              <CardContent className="space-y-6">
                <div className="h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={chartData}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name"/><YAxis domain={[0,100]}/><Tooltip/><Line type="monotone" dataKey="accuracy" stroke="#334155" strokeWidth={2}/><Line type="monotone" dataKey="mastery" stroke="#94a3b8" strokeWidth={2}/></LineChart></ResponsiveContainer></div>
                <div className="h-48"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name"/><YAxis/><Tooltip/><Bar dataKey="avgSeconds" fill="#334155"/></BarChart></ResponsiveContainer></div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Historia prób</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {!attemptHistory.length && <p className="text-sm text-slate-600">Brak zapisanych prób.</p>}
                {attemptHistory.map((a,i)=>(
                  <div key={a.id} className="rounded-xl border p-4 text-sm text-slate-700">
                    <div className="flex flex-wrap justify-between gap-2">
                      <span className="font-medium">Próba {attemptHistory.length-i}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-slate-500">{new Date(a.finishedAt).toLocaleString()}</span>
                        <Badge variant={a.source==="supabase"?"success":"outline"}>{a.source==="supabase"?"☁ chmura":"💾 lokalnie"}</Badge>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-4">
                      <span>Wynik: {a.score}/{a.totalQuestions}</span>
                      <span>Dokladność: {a.percent}%</span>
                      <span>Mastery: {a.mastery}%</span>
                      <span>Tempo: {fmt(a.avgResponseMs)}</span>
                    </div>
                    <p className="mt-1 text-slate-600">Mocna: {a.strongestCategory||"—"} · Słaba: {a.weakestCategory||"—"}</p>
                  </div>
                ))}
                <Btn variant="danger" onClick={()=>{ localStorage.removeItem(STORAGE_KEY); setAttemptHistory([]); }}><IcoTrash /> Wyczyść lokalną historię</Btn>
              </CardContent>
            </Card>
          </CardContent>
        </Card>
      </div>
    );
  };

  // ── Calendar View ─────────────────────────────────────────────────────────
  const CalendarView = () => (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        {[
          { label:"Dni nauki", value:Object.keys(studyDaysMap).length, sub:"unikalne dni" },
          { label:"Bieżąca seria", value:currentStreak, sub:"dni z rzędu" },
          { label:"Zapisane próby", value:attemptHistory.length, sub:"łącznie" },
        ].map(c=>(
          <Card key={c.label}><CardContent className="p-5"><div className="text-sm text-slate-500">{c.label}</div><div className="mt-2 text-2xl font-semibold">{c.value}</div><div className="mt-1 text-sm text-slate-600">{c.sub}</div></CardContent></Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2"><IcoCalendar /> {calendarMonth.toLocaleString("pl-PL",{month:"long",year:"numeric"})}</CardTitle>
            <div className="flex gap-2">
              <Btn variant="outline" onClick={()=>setCalendarMonth(m=>addMonths(m,-1))}>← Poprz.</Btn>
              <Btn variant="outline" onClick={()=>setCalendarMonth(startOfMonth(new Date()))}>Dziś</Btn>
              <Btn variant="outline" onClick={()=>setCalendarMonth(m=>addMonths(m,1))}>Nast. →</Btn>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-7 gap-1 text-center text-xs font-medium text-slate-500">
            {["Pn","Wt","Śr","Cz","Pt","So","Nd"].map(d=><div key={d}>{d}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {calendarDays.map(item=>{
              const count=studyDaysMap[item.key]||0, isToday=item.key===formatDayKey(Date.now());
              return (
                <div key={item.key} className={`min-h-16 rounded-xl border p-1.5 text-xs ${item.inCurrentMonth?"bg-white":"bg-slate-50 text-slate-400"} ${count?"border-slate-900":"border-slate-100"}`}>
                  <div className={`font-medium ${isToday?"underline":""}`}>{item.date.getDate()}</div>
                  {count>0 && <><div className="mt-1 h-1.5 rounded-full bg-slate-900"/><div className="mt-1 text-slate-600">×{count}</div></>}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );

  // ── Plan View ─────────────────────────────────────────────────────────────
  const PlanView = () => (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label:"Gotowość", value:studyPlan.readiness, sub:"wg ostatnich wyników" },
          { label:"Najsłabszy obszar", value:stats.weakest?.category||"—", sub:"pracuj tutaj najpierw" },
          { label:"Najmocniejszy obszar", value:stats.strongest?.category||"—", sub:"buduj pewność siebie" },
          { label:"Rekomendacja", value:studyPlan.recommendation, sub:"", small:true },
        ].map(c=>(
          <Card key={c.label}><CardContent className="p-5"><div className="text-sm text-slate-500">{c.label}</div><div className={`mt-2 ${c.small?"text-base":"text-2xl"} font-semibold`}>{c.value}</div>{c.sub&&<div className="mt-1 text-sm text-slate-600">{c.sub}</div>}</CardContent></Card>
        ))}
      </div>
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><IcoSpark /> Co poprawić</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm">{studyPlan.improvements.map((t,i)=><div key={i} className="rounded-xl bg-slate-100 p-4 text-slate-700">{t}</div>)}</CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>7-dniowy plan treningowy</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {studyPlan.weeklyPlan.map(item=>(
            <div key={item.day} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{item.day}</span>
                <Badge>{item.duration}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600">{item.task}</p>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );

  // ── Settings View ─────────────────────────────────────────────────────────
  const SettingsView = () => (
    <div className="space-y-4">
      {/* Import pliku */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><IcoUpload /> Import pytań z pliku</CardTitle></CardHeader>
        <CardContent className="space-y-4 text-sm text-slate-600">
          <p>{importMsg}</p>
          <Btn variant="outline" onClick={()=>fileInputRef.current?.click()}><IcoUpload /> Importuj CSV / Excel / TXT</Btn>
          <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls,.txt" className="hidden" onChange={handleFileImport} />
          <div className="rounded-xl bg-slate-100 p-3">
            Kolumny arkusza: <code className="text-xs">question, A, B, C, D, correct, explanation, category, difficulty</code>.<br/>
            Format TXT: <code className="text-xs">Question #2</code> + linie A./B./C./D.
          </div>
        </CardContent>
      </Card>

      {/* Supabase — pytania */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IcoCloud /> Supabase — pytania
            <StatusDot status={qStatus} />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-slate-600">
          <p>{qMsg}</p>
          <div className="flex flex-wrap gap-3">
            <Btn variant="outline" onClick={testConnection}>🔌 Test połączenia</Btn>
            <Btn variant="outline" onClick={loadQuestionsFromDB}><IcoRefresh /> Wczytaj z bazy</Btn>
            <Btn variant="outline" onClick={pushQuestionsToDB}><IcoCloud /> Wyślij do bazy ({questions.length} pyt.)</Btn>
          </div>
          {testMsg && <div className="rounded-xl bg-slate-100 p-3 text-sm text-slate-700 font-mono">{testMsg}</div>}
          <div className="rounded-xl bg-slate-100 p-3 space-y-1">
            <p>Załadowano: <strong>{questions.length}</strong> pytań</p>
            <p>Bez klucza odpowiedzi: <strong>{questions.filter(q=>!q.correct).length}</strong></p>
            <p>Źródła: {[...new Set(questions.map(q=>q.sourceType))].join(", ")||"—"}</p>
          </div>
        </CardContent>
      </Card>

      {/* Supabase — statystyki */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IcoChart /> Supabase — statystyki prób
            <StatusDot status={attStatus} />
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-slate-600">
          <p>Wyniki prób są automatycznie zapisywane lokalnie i w Supabase po każdym ukończonym quizie.</p>
          <Btn variant="outline" onClick={loadAttemptsFromDB}><IcoRefresh /> Odśwież historię</Btn>
          <p>Zapisane próby: <strong>{attemptHistory.length}</strong></p>
        </CardContent>
      </Card>
    </div>
  );

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-900">ABCD Quiz App</h1>
            <p className="text-sm text-slate-600">Quiz wielokrotnego wyboru z analityką, kalendarzem i planem nauki.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{answeredCount}/{total} odpowiedzi</Badge>
            <Badge>{attemptHistory.length} prób</Badge>
            <Badge>{questions.length} pytań</Badge>
            {SB_ENABLED && <Badge variant="success">☁ Supabase aktywne</Badge>}
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {tabs.map(t=>(
            <button key={t.id} onClick={()=>setActiveTab(t.id)}
              className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm font-medium transition-colors ${activeTab===t.id?"bg-slate-900 text-white":"border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
              {t.icon}{t.label}
            </button>
          ))}
        </div>

        {activeTab==="quiz"     && <QuizView />}
        {activeTab==="calendar" && <CalendarView />}
        {activeTab==="plan"     && <PlanView />}
        {activeTab==="results"  && showResult && <ResultsView />}
        {activeTab==="settings" && <SettingsView />}
      </div>
    </div>
  );
}