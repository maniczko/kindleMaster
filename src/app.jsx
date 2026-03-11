import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client"; // <--- DODAJ TĘ LINIJKĘ
import * as XLSX from "xlsx";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

// ── icons ─────────────────────────────────────────────────────────────────────
const Icon = ({ d, size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
const SUPABASE_URL      = "https://ylqloszldyzpeaikweyl.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlscWxvc3psZHl6cGVhaWt3ZXlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyNDg2NDUsImV4cCI6MjA4ODgyNDY0NX0.JgwZKn5_ifnoZHViOGb7aED9sZ3MnijeeI66cFhSJaQ";
// Supabase REST działa tylko gdy uruchamiasz lokalnie (Vite/Next.js).
// W artefakcie Claude fetch jest blokowany przez sandbox — użyj opcji lokalnej.
const SB_ENABLED = SUPABASE_URL.startsWith("https://") && SUPABASE_ANON_KEY.startsWith("eyJ");

// ── Supabase REST ─────────────────────────────────────────────────────────────
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
async function sbUpsert(table, rows) {
  for (let i = 0; i < rows.length; i += 100) {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, {
      method: "POST",
      headers: sbH("resolution=merge-duplicates,return=minimal"),
      body: JSON.stringify(rows.slice(i, i + 100)),
    });
    if (!r.ok) throw new Error(`Batch ${i/100+1}: ${await r.text()}`);
  }
}
async function sbInsert(table, row) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, {
    method: "POST", headers: sbH(), body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
}

// ── constants / helpers ───────────────────────────────────────────────────────
const STORAGE_KEY = "quiz_abcd_attempts_v2";
const optionKeys = ["A","B","C","D"];
const diffW = { easy:1, medium:1.5, hard:2 };
const normDiff = v => { const r = String(v||"medium").trim().toLowerCase(); return ["easy","medium","hard"].includes(r)?r:"medium"; };
const fmt = ms => `${(ms/1000).toFixed(1)}s`;
const dayKey = ts => { const d=new Date(ts); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const som = d => new Date(d.getFullYear(),d.getMonth(),1);
const addM = (d,n) => new Date(d.getFullYear(),d.getMonth()+n,1);

const loadLocal = () => { try { return JSON.parse(localStorage.getItem(STORAGE_KEY)||"[]"); } catch { return []; } };
const saveLocal = list => { try { localStorage.setItem(STORAGE_KEY,JSON.stringify((list||[]).slice(0,100))); } catch {} };
const dedupe = items => {
  const m = new Map();
  for (const a of (items||[])) {
    if (!a?.id) continue;
    const ex = m.get(a.id);
    if (!ex || (ex.source!=="supabase" && a.source==="supabase")) m.set(a.id,a);
  }
  return [...m.values()].sort((a,b)=>b.finishedAt-a.finishedAt);
};

// ── question mapping ──────────────────────────────────────────────────────────
const rowToQ = (row,i) => ({
  id: row.id??i+1, questionNo: row.question_no??i+1,
  question: row.question_text,
  options: { A:row.option_a, B:row.option_b, C:row.option_c, D:row.option_d },
  correct: row.correct_answer||null,
  explanation: row.explanation||"Brak wyjaśnienia.",
  category: row.category||"General",
  difficulty: normDiff(row.difficulty||"medium"),
  sourceType: row.source_type||"database", sourceFile: row.source_file||null,
});
const qToRow = (q,i) => ({
  question_no: q.questionNo??i+1,
  question_text: q.question,
  option_a: q.options.A, option_b: q.options.B, option_c: q.options.C, option_d: q.options.D,
  correct_answer: q.correct||null, explanation: q.explanation||null,
  category: q.category||"General", difficulty: normDiff(q.difficulty||"medium"),
  source_type: q.sourceType||"import", source_file: q.sourceFile||null, is_active: true,
});

// ── file parsers ──────────────────────────────────────────────────────────────
function parseRows(rows, sourceFile=null) {
  return (rows||[]).map((row,i) => {
    const q = row.question??row.Question??row.pytanie??row.question_text;
    const a = row.A??row.a??row.optionA??row.option_a;
    const b = row.B??row.b??row.optionB??row.option_b;
    const c = row.C??row.c??row.optionC??row.option_c;
    const d = row.D??row.d??row.optionD??row.option_d;
    const correct = String(row.correct??row.Correct??row.poprawna??row.correct_answer??"").trim().toUpperCase();
    if (!q||!a||!b||!c||!d) return null;
    return {
      id: row.id??`import-${i+1}`, questionNo: Number(row.questionNo??row.nr??row.question_no??i+1),
      question: String(q).trim(),
      options: { A:String(a).trim(), B:String(b).trim(), C:String(c).trim(), D:String(d).trim() },
      correct: optionKeys.includes(correct)?correct:null,
      explanation: String(row.explanation??row.wyjasnienie??"Brak wyjaśnienia.").trim(),
      category: String(row.category??row.kategoria??"General").trim(),
      difficulty: normDiff(row.difficulty??row.trudnosc??"medium"),
      sourceType:"spreadsheet", sourceFile,
    };
  }).filter(Boolean);
}
function parseTxt(text, sourceFile="import.txt") {
  return [...String(text||"").replace(/\r/g,"").matchAll(/Question\s+#(\d+)\s*([\s\S]*?)(?=\nQuestion\s+#\d+|$)/g)]
    .map(m => {
      const no=Number(m[1]), block=m[2].trim();
      const p=block.match(/^([\s\S]*?)\nA\.\s*([\s\S]*?)\nB\.\s*([\s\S]*?)\nC\.\s*([\s\S]*?)\nD\.\s*([\s\S]*?)(?:\nView answer|$)/);
      if (!p) return null;
      return {
        id:`txt-${no}`, questionNo:no,
        question: p[1].replace(/\s+/g," ").trim(),
        options:{ A:p[2].replace(/\s+/g," ").trim(), B:p[3].replace(/\s+/g," ").trim(), C:p[4].replace(/\s+/g," ").trim(), D:p[5].replace(/\s+/g," ").trim() },
        correct:null, explanation:"Brak odpowiedzi w pliku źródłowym.",
        category:"Import", difficulty:"medium", sourceType:"txt_import", sourceFile,
      };
    }).filter(Boolean);
}

// ── calendar ──────────────────────────────────────────────────────────────────
function buildCalDays(month) {
  const start=som(month), fw=(start.getDay()+6)%7;
  const gs=new Date(start); gs.setDate(start.getDate()-fw);
  return Array.from({length:42},(_,i)=>{ const d=new Date(gs); d.setDate(gs.getDate()+i); return {date:d,key:dayKey(d.getTime()),inCurrent:d.getMonth()===start.getMonth()}; });
}

// ── study plan ────────────────────────────────────────────────────────────────
function buildPlan(history, weakCat) {
  if (!history.length) return { readiness:"Brak danych", recommendation:"Ukończ kilka prób quizu.", improvements:[], weeklyPlan:[] };
  const l5=history.slice(0,5);
  const avgAcc=Math.round(l5.reduce((s,a)=>s+a.percent,0)/l5.length);
  const avgPace=Number((l5.reduce((s,a)=>s+a.avgResponseMs,0)/l5.length/1000).toFixed(1));
  const wm={},sm={};
  l5.forEach(a=>{ if(a.weakestCategory) wm[a.weakestCategory]=(wm[a.weakestCategory]||0)+1; if(a.strongestCategory) sm[a.strongestCategory]=(sm[a.strongestCategory]||0)+1; });
  const weak=Object.entries(wm).sort((a,b)=>b[1]-a[1])[0]?.[0]||weakCat?.category||"Mieszane tematy";
  const strong=Object.entries(sm).sort((a,b)=>b[1]-a[1])[0]?.[0]||"Wiedza ogólna";
  return {
    readiness: avgAcc>=85?"Zaawansowany":avgAcc>=65?"Średniozaawansowany":"Buduj podstawy",
    recommendation: avgAcc>=85?"Gotowy na trudniejsze zestawy.":avgAcc>=65?"Dobra baza — wzmocnij słabe kategorie.":"Skup się najpierw na dokładności.",
    improvements: [
      avgAcc<70?"Zwolnij przy trudnych pytaniach i skup się na tym, dlaczego odpowiedź jest poprawna.":"Utrzymaj dokładność i mieszaj kategorie.",
      avgPace>18?"Pracuj nad szybkością: szybciej odpowiadaj na łatwe pytania.":"Tempo jest dobre — wykrywaj dystraktory szybciej.",
      `Przeznacz dodatkowy czas na ćwiczenia z kategorii: ${weak}.`,
    ],
    weeklyPlan:[
      {day:"Dzień 1", task:`Przejrzyj błędy z ostatnich 3 prób, szczególnie ${weak}.`, duration:"25 min"},
      {day:"Dzień 2", task:`Skupiony quiz z ${weak} i 3 krótkie wnioski.`, duration:"30 min"},
      {day:"Dzień 3", task:"Quiz mieszany z czasomierzem. Odpowiadaj pewnie.", duration:"20 min"},
      {day:"Dzień 4", task:"Wróć do wyjaśnień i podsumuj zasady własnymi słowami.", duration:"20 min"},
      {day:"Dzień 5", task:`Zestaw pewności w ${strong}, potem 5 trudniejszych pytań.`, duration:"25 min"},
      {day:"Dzień 6", task:"Pełny próbny quiz — porównaj wynik z poprzednimi.", duration:"30 min"},
      {day:"Dzień 7", task:"Lekki przegląd: co się poprawiło, co powtórzyć.", duration:"15 min"},
    ],
  };
}

// ── sample data ───────────────────────────────────────────────────────────────
const SAMPLES = [
  {id:1,questionNo:1,question:"Który dokument formalnie definiuje zakres projektu?",options:{A:"Rejestr ryzyk",B:"Karta projektu",C:"Lessons learned",D:"Dziennik problemów"},correct:"B",explanation:"Karta projektu formalnie autoryzuje projekt i określa jego zakres.",category:"Zarządzanie projektami",difficulty:"medium",sourceType:"sample"},
  {id:2,questionNo:2,question:"Co oznacza skrót VAT?",options:{A:"Value Added Tax",B:"Variable Asset Transfer",C:"Verified Accounting Tool",D:"Value Allocation Table"},correct:"A",explanation:"VAT to podatek od wartości dodanej.",category:"Finanse",difficulty:"easy",sourceType:"sample"},
  {id:3,questionNo:3,question:"Które zdanie brzmi najbardziej naturalnie w angielskim biznesowym?",options:{A:"Let us cut to the chase.",B:"Let us cut to the hunt.",C:"Let us go to the speed.",D:"Let us go to the cut."},correct:"A",explanation:'"Cut to the chase" oznacza „przejdźmy do sedna".',category:"Język angielski",difficulty:"easy",sourceType:"sample"},
];

// ── UI primitives ─────────────────────────────────────────────────────────────
const Card = ({children,className=""}) => <div className={`rounded-2xl border border-slate-200 bg-white shadow-sm ${className}`}>{children}</div>;
const CH = ({children}) => <div className="p-5 pb-0">{children}</div>;
const CT = ({children,className=""}) => <h2 className={`font-semibold text-slate-800 ${className}`}>{children}</h2>;
const CC = ({children,className=""}) => <div className={`p-5 ${className}`}>{children}</div>;
const Badge = ({children,variant="outline"}) => {
  const s={outline:"border border-slate-300 text-slate-600",secondary:"bg-slate-100 text-slate-700",destructive:"border border-red-200 bg-red-100 text-red-700",success:"border border-green-200 bg-green-100 text-green-700"};
  return <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${s[variant]||s.outline}`}>{children}</span>;
};
const Btn = ({children,onClick,disabled,variant="default",className=""}) => {
  const s={default:"bg-slate-900 text-white hover:bg-slate-700",outline:"border border-slate-300 bg-white text-slate-700 hover:bg-slate-50",danger:"border border-red-300 bg-white text-red-600 hover:bg-red-50"};
  return <button onClick={onClick} disabled={disabled} className={`inline-flex items-center gap-2 rounded-2xl px-4 py-2 text-sm font-medium transition-colors focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 ${s[variant]||s.default} ${className}`}>{children}</button>;
};
const Progress = ({value,className=""}) => (
  <div className={`w-full overflow-hidden rounded-full bg-slate-200 ${className}`}>
    <div className="h-full rounded-full bg-slate-900 transition-all" style={{width:`${Math.min(100,Math.max(0,value))}%`}} />
  </div>
);
const Dot = ({status}) => {
  const c={idle:"bg-slate-300",loading:"animate-pulse bg-yellow-400",ok:"bg-green-500",error:"bg-red-500",disabled:"bg-slate-300"};
  return <span className={`inline-block h-2 w-2 rounded-full ${c[status]||"bg-slate-300"}`}/>;
};

// ── app ───────────────────────────────────────────────────────────────────────
export default function QuizAbcdApp() {
  const [questions,   setQuestions]   = useState(SAMPLES);
  const [idx,         setIdx]         = useState(0);
  const [selected,    setSelected]    = useState(null);
  const [answers,     setAnswers]     = useState({});
  const [showResult,  setShowResult]  = useState(false);
  const [startedAt,   setStartedAt]   = useState(()=>Date.now());
  const [qStartedAt,  setQStartedAt]  = useState(()=>Date.now());
  const [finishedAt,  setFinishedAt]  = useState(null);
  const [history,     setHistory]     = useState(()=>loadLocal());
  const [importMsg,   setImportMsg]   = useState("Importuj CSV, Excel lub TXT.");
  const [activeTab,   setActiveTab]   = useState("quiz");
  const [calMonth,    setCalMonth]    = useState(()=>som(new Date()));
  const [qStatus,     setQStatus]     = useState("idle");
  const [qMsg,        setQMsg]        = useState(SB_ENABLED?"Kliknij 'Wczytaj z bazy'.":"Supabase nie jest skonfigurowany.");
  const [attStatus,   setAttStatus]   = useState("idle");
  const [testMsg,     setTestMsg]     = useState("");
  const fileRef = useRef(null);

  const total   = questions.length;
  const current = questions[idx] || SAMPLES[0];
  const answered = Object.keys(answers).length;
  const score   = useMemo(()=>Object.values(answers).filter(a=>a.isCorrect).length,[answers]);

  // ── reset ──────────────────────────────────────────────────────────────────
  const reset = useCallback((qs=null)=>{
    if (qs) setQuestions(qs);
    setIdx(0); setSelected(null); setAnswers({});
    setShowResult(false); setStartedAt(Date.now()); setQStartedAt(Date.now());
    setFinishedAt(null); setActiveTab("quiz");
  },[]);

  // ── Supabase: pytania ──────────────────────────────────────────────────────
  const loadQfromDB = useCallback(async()=>{
    if (!SB_ENABLED){setQStatus("disabled");setQMsg("Supabase nie jest skonfigurowany.");return;}
    setQStatus("loading");setQMsg("Wczytuję pytania z Supabase…");
    try {
      const rows=await sbSelect("quiz_questions","is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length){setQStatus("ok");setQMsg("Baza pytań jest pusta. Zaimportuj plik i wyślij do bazy.");return;}
      reset(rows.map(rowToQ));
      setQStatus("ok");setQMsg(`Wczytano ${rows.length} pytań z Supabase.`);
    } catch(e){setQStatus("error");setQMsg(`Błąd wczytywania: ${e.message}`);}
  },[reset]);

  const pushQtoDB = useCallback(async()=>{
    if (!SB_ENABLED){setQStatus("disabled");return;}
    if (!questions.length){setQMsg("Brak pytań do wysłania.");return;}
    setQStatus("loading");setQMsg(`Wysyłam ${questions.length} pytań…`);
    try {
      await sbUpsert("quiz_questions",questions.map(qToRow));
      setQStatus("ok");setQMsg(`Wysłano ${questions.length} pytań do Supabase.`);
    } catch(e){setQStatus("error");setQMsg(`Błąd wysyłania: ${e.message}`);}
  },[questions]);

  // ── Supabase: statystyki ───────────────────────────────────────────────────
  const loadAttempts = useCallback(async()=>{
    if (!SB_ENABLED){setAttStatus("disabled");return;}
    setAttStatus("loading");
    try {
      const rows=await sbSelect("quiz_attempts","order=finished_at.desc&limit=100");
      const mapped=rows.map(r=>({
        id:r.attempt_id, finishedAt:new Date(r.finished_at).getTime(),
        totalQuestions:r.total_questions, score:r.score, percent:r.percent,
        mastery:r.mastery, avgResponseMs:r.avg_response_ms, totalTimeMs:r.total_time_ms,
        strongestCategory:r.strongest_category, weakestCategory:r.weakest_category, source:"supabase",
      }));
      setHistory(prev=>{ const m=dedupe([...mapped,...prev]); saveLocal(m); return m; });
      setAttStatus("ok");
    } catch(e){setAttStatus("error");console.error(e);}
  },[]);

  // ── test połączenia ────────────────────────────────────────────────────────
  const testConn = useCallback(async()=>{
    setTestMsg("Testuję…");
    if (!SB_ENABLED){setTestMsg("❌ Brak poprawnego klucza Supabase.");return;}
    try {
      const r=await fetch(`${SUPABASE_URL}/rest/v1/quiz_questions?limit=1`,{headers:{apikey:SUPABASE_ANON_KEY,Authorization:`Bearer ${SUPABASE_ANON_KEY}`}});
      if (r.ok){const d=await r.json();setTestMsg(`✅ Połączenie OK. Tabela quiz_questions istnieje (próbka: ${d.length} rekord).`);}
      else setTestMsg(`❌ HTTP ${r.status}: ${await r.text()}`);
    } catch(e){setTestMsg(`❌ Sieć/CORS: ${e.message} — ten błąd oznacza, że artefakt Claude blokuje fetch. Uruchom app lokalnie.`);}
  },[]);

  useEffect(()=>{loadQfromDB();loadAttempts();},[loadQfromDB,loadAttempts]);

  // ── odpowiadanie ───────────────────────────────────────────────────────────
  const handleAnswer = useCallback((key)=>{
    if (selected||showResult) return;
    setSelected(key);
    setAnswers(prev=>({...prev,[current.id]:{questionId:current.id,selected:key,correct:current.correct,isCorrect:current.correct?key===current.correct:false,responseTimeMs:Date.now()-qStartedAt,category:current.category||"General",difficulty:current.difficulty||"medium"}}));
  },[current,qStartedAt,selected,showResult]);

  const next = useCallback(()=>{
    if (idx<total-1){const ni=idx+1;setIdx(ni);setSelected(answers[questions[ni].id]?.selected??null);setQStartedAt(Date.now());}
    else{setFinishedAt(Date.now());setShowResult(true);setActiveTab("results");}
  },[answers,idx,questions,total]);

  // ── import pliku ───────────────────────────────────────────────────────────
  const handleImport = useCallback(async(e)=>{
    const file=e.target.files?.[0]; if (!file) return;
    try {
      let parsed=[];
      if (file.name.toLowerCase().endsWith(".txt")) {
        parsed=parseTxt(await file.text(),file.name);
      } else {
        const wb=XLSX.read(await file.arrayBuffer(),{type:"array"});
        parsed=parseRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{defval:""}),file.name);
      }
      if (!parsed.length){setImportMsg("Import nieudany — sprawdź nagłówki kolumn lub format TXT.");return;}
      reset(parsed);
      setImportMsg(`Zaimportowano ${parsed.length} pytań z ${file.name}.${parsed.some(q=>!q.correct)?" Niektóre pytania nie mają klucza odpowiedzi.":""}`);
      setQMsg(`Zaimportowano ${parsed.length} pytań lokalnie. Kliknij 'Wyślij do bazy', aby zapisać w Supabase.`);
      setQStatus("idle");
    } catch(e){setImportMsg(`Import nieudany: ${e.message}`);}
    finally{e.target.value="";}
  },[reset]);

  // ── statystyki ─────────────────────────────────────────────────────────────
  const stats = useMemo(()=>{
    const list=questions.map(q=>({q,a:answers[q.id]})).filter(x=>x.a);
    const totalTimeMs=(finishedAt??Date.now())-startedAt;
    const avgResponseMs=list.length?list.reduce((s,x)=>s+x.a.responseTimeMs,0)/list.length:0;
    const correctCount=list.filter(x=>x.a.isCorrect).length;
    const incorrectCount=list.length-correctCount;
    const wTotal=questions.reduce((s,q)=>s+(diffW[q.difficulty||"medium"]||1.5),0);
    const wScore=list.reduce((s,x)=>s+(x.a.isCorrect?(diffW[x.q.difficulty||"medium"]||1.5):0),0);
    const mastery=wTotal?Math.round((wScore/wTotal)*100):0;
    const byCat=Object.values(questions.reduce((acc,q)=>{
      const cat=q.category||"General";
      if (!acc[cat]) acc[cat]={category:cat,total:0,correct:0};
      acc[cat].total++; if (answers[q.id]?.isCorrect) acc[cat].correct++;
      return acc;
    },{})).map(c=>({...c,percent:Math.round((c.correct/c.total)*100)}));
    const weakest=byCat.length?[...byCat].sort((a,b)=>a.percent-b.percent)[0]:null;
    const strongest=byCat.length?[...byCat].sort((a,b)=>b.percent-a.percent)[0]:null;
    const perf=mastery>=90?"Doskonale":mastery>=75?"Bardzo dobrze":mastery>=60?"Solidna podstawa":"Dalej ćwicz";
    return {totalTimeMs,avgResponseMs,correctCount,incorrectCount,mastery,byCat,weakest,strongest,perf};
  },[answers,questions,startedAt,finishedAt]);

  // ── zapis próby ────────────────────────────────────────────────────────────
  useEffect(()=>{
    if (!showResult||!finishedAt) return;
    const pct=Math.round((score/Math.max(total,1))*100);
    const attempt={
      id:`${finishedAt}-${Math.random().toString(36).slice(2,7)}`,
      finishedAt, totalQuestions:total, score, percent:pct,
      mastery:stats.mastery, avgResponseMs:Math.round(stats.avgResponseMs),
      totalTimeMs:Math.round(stats.totalTimeMs),
      strongestCategory:stats.strongest?.category||null,
      weakestCategory:stats.weakest?.category||null, source:"local",
    };
    setHistory(prev=>{
      if (prev.some(a=>a.finishedAt===finishedAt&&a.score===score)) return prev;
      const m=dedupe([attempt,...prev]); saveLocal(m); return m;
    });
    if (!SB_ENABLED) return;
    sbInsert("quiz_attempts",{
      attempt_id:attempt.id, finished_at:new Date(attempt.finishedAt).toISOString(),
      total_questions:attempt.totalQuestions, score:attempt.score, percent:attempt.percent,
      mastery:attempt.mastery, avg_response_ms:attempt.avgResponseMs, total_time_ms:attempt.totalTimeMs,
      strongest_category:attempt.strongestCategory, weakest_category:attempt.weakestCategory,
    }).then(()=>{setAttStatus("ok");loadAttempts();}).catch(e=>{setAttStatus("error");console.error(e);});
  },[showResult,finishedAt,score,stats,total,loadAttempts]);

  // ── klawiatura ─────────────────────────────────────────────────────────────
  useEffect(()=>{
    const h=e=>{
      if (e.target?.tagName==="INPUT"||e.target?.tagName==="TEXTAREA") return;
      const k=e.key.toUpperCase();
      if (!showResult&&!selected&&optionKeys.includes(k)){e.preventDefault();handleAnswer(k);return;}
      if (e.key==="Enter"&&!showResult&&selected){e.preventDefault();next();return;}
      if (k==="R"){e.preventDefault();reset();}
    };
    window.addEventListener("keydown",h);
    return ()=>window.removeEventListener("keydown",h);
  },[handleAnswer,next,reset,selected,showResult]);

  // ── pochodne ───────────────────────────────────────────────────────────────
  const uniq   = useMemo(()=>dedupe(history),[history]);
  const dayMap = useMemo(()=>{ const m={}; uniq.forEach(a=>{const k=dayKey(a.finishedAt);m[k]=(m[k]||0)+1;}); return m; },[uniq]);
  const streak = useMemo(()=>{ let s=0; const c=new Date(); while(dayMap[dayKey(c.getTime())]){s++;c.setDate(c.getDate()-1);} return s; },[dayMap]);
  const plan   = useMemo(()=>buildPlan(uniq,stats.weakest),[uniq,stats.weakest]);
  const calDays= useMemo(()=>buildCalDays(calMonth),[calMonth]);
  const chart  = [...uniq].reverse().map((a,i)=>({name:`#${i+1}`,accuracy:a.percent,mastery:a.mastery,avgSeconds:Number((a.avgResponseMs/1000).toFixed(1))}));
  const prevA  = uniq[1]||null;
  const trend  = prevA&&showResult?{score:score-prevA.score,percent:Math.round((score/Math.max(total,1))*100)-prevA.percent,avgResponseMs:Math.round(stats.avgResponseMs)-prevA.avgResponseMs}:null;

  const tabs=[
    {id:"quiz",label:"Quiz",icon:null},
    {id:"calendar",label:"Kalendarz",icon:<IcoCalendar/>},
    {id:"plan",label:"Plan nauki",icon:<IcoBook/>},
    ...(showResult?[{id:"results",label:"Wyniki",icon:<IcoChart/>}]:[]),
    {id:"settings",label:"Ustawienia",icon:<IcoSettings/>},
  ];

  // ── widok: Quiz ────────────────────────────────────────────────────────────
  const QuizView = () => (
    <div className="space-y-4">
      <Card><CC className="flex flex-wrap items-center gap-3 py-3 text-sm text-slate-600">
        <span className="flex items-center gap-1 font-medium text-slate-700"><IcoKeyboard/> Skróty</span>
        <Badge>A/B/C/D = odpowiedź</Badge><Badge>Enter = dalej</Badge><Badge>R = restart</Badge>
      </CC></Card>
      <Progress value={(idx/Math.max(total,1))*100} className="h-3"/>
      <Card>
        <CH><CT className="text-xl">Pytanie {idx+1} z {total}</CT></CH>
        <CC className="space-y-5">
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{current.category||"General"}</Badge>
            <Badge>{current.difficulty||"medium"}</Badge>
            <Badge>Q#{current.questionNo??current.id}</Badge>
            {current.sourceFile&&<Badge><IcoFile/> {current.sourceFile}</Badge>}
          </div>
          <p className="text-lg font-medium leading-relaxed">{current.question}</p>
          <div className="grid gap-3">
            {optionKeys.map(key=>{
              const isSel=selected===key, isCorr=current.correct===key, reveal=!!selected;
              let border="border-slate-200 hover:bg-slate-50";
              if (reveal&&current.correct&&isCorr) border="border-green-500 bg-green-50";
              else if (reveal&&isSel&&current.correct&&!isCorr) border="border-red-400 bg-red-50";
              return (
                <button key={key} onClick={()=>handleAnswer(key)}
                  className={`flex w-full items-center gap-3 rounded-2xl border-2 px-4 py-4 text-left text-base transition-colors ${border}`}>
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-sm font-semibold">{key}</span>
                  <span>{current.options[key]}</span>
                </button>
              );
            })}
          </div>
          {selected&&(
            <div className="rounded-xl bg-slate-100 p-4 text-sm text-slate-700">
              <div className="font-medium">{current.correct?(selected===current.correct?"✓ Poprawnie.":`✗ Poprawna odpowiedź: ${current.correct}.`):"Klucz odpowiedzi niedostępny."}</div>
              <div className="mt-1">{current.explanation}</div>
            </div>
          )}
          <div className="flex flex-wrap gap-3">
            <Btn onClick={next} disabled={!selected}>{idx===total-1?"Pokaż wyniki":"Następne pytanie"}</Btn>
            <Btn variant="outline" onClick={()=>reset()}><IcoRotate/> Reset</Btn>
          </div>
        </CC>
      </Card>
    </div>
  );

  // ── widok: Wyniki ──────────────────────────────────────────────────────────
  const ResultsView = () => {
    const pct=Math.round((score/Math.max(total,1))*100);
    return (
      <div className="space-y-4">
        <Card>
          <CH><CT className="flex items-center gap-2 text-2xl"><IcoTrophy/> Quiz ukończony</CT></CH>
          <CC className="space-y-6">
            <div>
              <div className="mb-2 text-lg font-medium">Wynik: {score}/{total}</div>
              <Progress value={pct} className="h-3"/>
              <div className="mt-2 text-sm text-slate-600">{pct}% poprawnych — {stats.perf}</div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {[
                {icon:<IcoBrain/>,label:"Mastery",value:`${stats.mastery}%`,sub:stats.perf},
                {icon:<IcoClock/>,label:"Śr. tempo",value:fmt(stats.avgResponseMs),sub:"na pytanie"},
                {icon:<IcoTarget/>,label:"Dokładność",value:`${pct}%`,sub:`${stats.correctCount} popr., ${stats.incorrectCount} błędne`},
                {icon:<IcoTrend/>,label:"Całk. czas",value:fmt(stats.totalTimeMs),sub:"cała próba"},
              ].map(c=>(
                <Card key={c.label}><CC className="p-5">
                  <div className="flex items-center gap-1 text-sm text-slate-500">{c.icon} {c.label}</div>
                  <div className="mt-2 text-2xl font-semibold">{c.value}</div>
                  <div className="mt-1 text-sm text-slate-600">{c.sub}</div>
                </CC></Card>
              ))}
            </div>
            {trend&&(
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
              <CH><CT>Wykresy postępów</CT></CH>
              <CC className="space-y-6">
                <div className="h-64"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name"/><YAxis domain={[0,100]}/><Tooltip/><Line type="monotone" dataKey="accuracy" stroke="#334155" strokeWidth={2}/><Line type="monotone" dataKey="mastery" stroke="#94a3b8" strokeWidth={2}/></LineChart></ResponsiveContainer></div>
                <div className="h-48"><ResponsiveContainer width="100%" height="100%"><BarChart data={chart}><CartesianGrid strokeDasharray="3 3"/><XAxis dataKey="name"/><YAxis/><Tooltip/><Bar dataKey="avgSeconds" fill="#334155"/></BarChart></ResponsiveContainer></div>
              </CC>
            </Card>
            <Card>
              <CH><CT>Kategorie</CT></CH>
              <CC className="space-y-2 text-sm text-slate-700">
                {stats.byCat.map(c=>(
                  <div key={c.category} className="flex items-center gap-3">
                    <span className="w-40 truncate font-medium">{c.category}</span>
                    <Progress value={c.percent} className="h-2 flex-1"/>
                    <span className="w-12 text-right">{c.percent}%</span>
                  </div>
                ))}
              </CC>
            </Card>
            <Card>
              <CH><CT>Historia prób</CT></CH>
              <CC className="space-y-3">
                {!uniq.length&&<p className="text-sm text-slate-600">Brak zapisanych prób.</p>}
                {uniq.map((a,i)=>(
                  <div key={a.id} className="rounded-xl border p-4 text-sm text-slate-700">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">Próba {uniq.length-i}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-slate-500">{new Date(a.finishedAt).toLocaleString()}</span>
                        <Badge variant={a.source==="supabase"?"success":"outline"}>{a.source==="supabase"?"☁ chmura":"💾 lokalnie"}</Badge>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-4">
                      <span>Wynik: {a.score}/{a.totalQuestions}</span>
                      <span>Dokładność: {a.percent}%</span>
                      <span>Mastery: {a.mastery}%</span>
                      <span>Tempo: {fmt(a.avgResponseMs)}</span>
                    </div>
                    <p className="mt-1 text-slate-600">Mocna: {a.strongestCategory||"—"} · Słaba: {a.weakestCategory||"—"}</p>
                  </div>
                ))}
                <Btn variant="danger" onClick={()=>{localStorage.removeItem(STORAGE_KEY);setHistory([]);}}><IcoTrash/> Wyczyść historię</Btn>
              </CC>
            </Card>
          </CC>
        </Card>
      </div>
    );
  };

  // ── widok: Kalendarz ───────────────────────────────────────────────────────
  const CalendarView = () => (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        {[{label:"Dni nauki",value:Object.keys(dayMap).length,sub:"unikalne dni"},{label:"Bieżąca seria",value:streak,sub:"dni z rzędu"},{label:"Zapisane próby",value:uniq.length,sub:"łącznie"}]
          .map(c=><Card key={c.label}><CC className="p-5"><div className="text-sm text-slate-500">{c.label}</div><div className="mt-2 text-2xl font-semibold">{c.value}</div><div className="mt-1 text-sm text-slate-600">{c.sub}</div></CC></Card>)}
      </div>
      <Card>
        <CH>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CT className="flex items-center gap-2"><IcoCalendar/> {calMonth.toLocaleString("pl-PL",{month:"long",year:"numeric"})}</CT>
            <div className="flex gap-2">
              <Btn variant="outline" onClick={()=>setCalMonth(m=>addM(m,-1))}>← Poprz.</Btn>
              <Btn variant="outline" onClick={()=>setCalMonth(som(new Date()))}>Dziś</Btn>
              <Btn variant="outline" onClick={()=>setCalMonth(m=>addM(m,1))}>Nast. →</Btn>
            </div>
          </div>
        </CH>
        <CC className="space-y-3">
          <div className="grid grid-cols-7 gap-1 text-center text-xs font-medium text-slate-500">
            {["Pn","Wt","Śr","Cz","Pt","So","Nd"].map(d=><div key={d}>{d}</div>)}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {calDays.map(item=>{
              const cnt=dayMap[item.key]||0, isToday=item.key===dayKey(Date.now());
              return (
                <div key={item.key} className={`min-h-16 rounded-xl border p-1.5 text-xs ${item.inCurrent?"bg-white":"bg-slate-50 text-slate-400"} ${cnt?"border-slate-900":"border-slate-100"}`}>
                  <div className={`font-medium ${isToday?"underline":""}`}>{item.date.getDate()}</div>
                  {cnt>0&&<><div className="mt-1 h-1.5 rounded-full bg-slate-900"/><div className="mt-1 text-slate-600">×{cnt}</div></>}
                </div>
              );
            })}
          </div>
        </CC>
      </Card>
    </div>
  );

  // ── widok: Plan nauki ──────────────────────────────────────────────────────
  const PlanView = () => (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          {label:"Gotowość",value:plan.readiness,sub:"wg ostatnich wyników"},
          {label:"Najsłabszy obszar",value:stats.weakest?.category||"—",sub:"pracuj tutaj najpierw"},
          {label:"Najmocniejszy obszar",value:stats.strongest?.category||"—",sub:"buduj pewność"},
          {label:"Rekomendacja",value:plan.recommendation,sub:"",small:true},
        ].map(c=><Card key={c.label}><CC className="p-5"><div className="text-sm text-slate-500">{c.label}</div><div className={`mt-2 ${c.small?"text-base":"text-2xl"} font-semibold`}>{c.value}</div>{c.sub&&<div className="mt-1 text-sm text-slate-600">{c.sub}</div>}</CC></Card>)}
      </div>
      <Card>
        <CH><CT className="flex items-center gap-2"><IcoSpark/> Co poprawić</CT></CH>
        <CC className="space-y-3 text-sm">{plan.improvements.map((t,i)=><div key={i} className="rounded-xl bg-slate-100 p-4 text-slate-700">{t}</div>)}</CC>
      </Card>
      <Card>
        <CH><CT>7-dniowy plan treningowy</CT></CH>
        <CC className="space-y-3">
          {plan.weeklyPlan.map(item=>(
            <div key={item.day} className="rounded-xl border p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{item.day}</span><Badge>{item.duration}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600">{item.task}</p>
            </div>
          ))}
        </CC>
      </Card>
    </div>
  );

  // ── widok: Ustawienia ──────────────────────────────────────────────────────
  const SettingsView = () => (
    <div className="space-y-4">
      <Card>
        <CH><CT className="flex items-center gap-2"><IcoUpload/> Import pytań z pliku</CT></CH>
        <CC className="space-y-4 text-sm text-slate-600">
          <p>{importMsg}</p>
          <Btn variant="outline" onClick={()=>fileRef.current?.click()}><IcoUpload/> Importuj CSV / Excel / TXT</Btn>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" className="hidden" onChange={handleImport}/>
          <div className="rounded-xl bg-slate-100 p-3">
            Kolumny arkusza: <code className="text-xs">question, A, B, C, D, correct, explanation, category, difficulty</code><br/>
            Format TXT: <code className="text-xs">Question #1</code> + linie A./B./C./D.
          </div>
        </CC>
      </Card>
      <Card>
        <CH><CT className="flex items-center gap-2"><IcoCloud/> Supabase — pytania <Dot status={qStatus}/></CT></CH>
        <CC className="space-y-3 text-sm text-slate-600">
          <p>{qMsg}</p>
          <div className="flex flex-wrap gap-3">
            <Btn variant="outline" onClick={testConn}>🔌 Test połączenia</Btn>
            <Btn variant="outline" onClick={loadQfromDB}><IcoRefresh/> Wczytaj z bazy</Btn>
            <Btn variant="outline" onClick={pushQtoDB}><IcoCloud/> Wyślij do bazy ({questions.length} pyt.)</Btn>
          </div>
          {testMsg&&<div className="rounded-xl bg-slate-100 p-3 font-mono text-xs">{testMsg}</div>}
          <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 text-amber-800 text-xs">
            ⚠️ <strong>Uwaga:</strong> Supabase REST jest blokowany przez sandbox artefaktu Claude. Aby korzystać z synchronizacji, uruchom app lokalnie: <code>npm create vite@latest quiz -- --template react</code>, zainstaluj <code>xlsx recharts</code> i ustaw zmienne środowiskowe <code>VITE_SUPABASE_URL</code> i <code>VITE_SUPABASE_ANON_KEY</code>.
          </div>
          <div className="space-y-1">
            <p>Załadowane pytania: <strong>{questions.length}</strong></p>
            <p>Bez klucza: <strong>{questions.filter(q=>!q.correct).length}</strong></p>
            <p>Źródła: {[...new Set(questions.map(q=>q.sourceType))].join(", ")||"—"}</p>
          </div>
        </CC>
      </Card>
      <Card>
        <CH><CT className="flex items-center gap-2"><IcoChart/> Supabase — statystyki <Dot status={attStatus}/></CT></CH>
        <CC className="space-y-3 text-sm text-slate-600">
          <p>Wyniki prób są zapisywane lokalnie (localStorage) i wysyłane do Supabase po każdym ukończonym quizie.</p>
          <Btn variant="outline" onClick={loadAttempts}><IcoRefresh/> Odśwież z chmury</Btn>
          <p>Zapisane próby: <strong>{uniq.length}</strong></p>
        </CC>
      </Card>
    </div>
  );

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <div className="mx-auto max-w-5xl space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-slate-900">ABCD Quiz App</h1>
            <p className="text-sm text-slate-600">Quiz wielokrotnego wyboru z analityką, kalendarzem i planem nauki.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="secondary">{answered}/{total} odpowiedzi</Badge>
            <Badge>{uniq.length} prób</Badge>
            <Badge>{questions.length} pytań</Badge>
            {SB_ENABLED&&<Badge variant="success">☁ Supabase skonfigurowane</Badge>}
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
        {activeTab==="quiz"&&<QuizView/>}
        {activeTab==="calendar"&&<CalendarView/>}
        {activeTab==="plan"&&<PlanView/>}
        {activeTab==="results"&&showResult&&<ResultsView/>}
        {activeTab==="settings"&&<SettingsView/>}
      </div>
    </div>
  );
}
const rootElement = document.getElementById("root");

if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <QuizAbcdApp />
    </React.StrictMode>
  );
}