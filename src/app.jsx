import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";
import * as XLSX from "xlsx";

// ── SVG Icons ─────────────────────────────────────────────────────────────────
const Icon = ({ d, size = 16, className = "", strokeWidth = 1.75 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d={d} />
  </svg>
);
const IcoBrain    = ({size=16}) => <Icon size={size} d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18a4 4 0 1 0 7.967-1.517 4 4 0 0 0 .556-6.588 4 4 0 0 0-2.526-5.77A3 3 0 0 0 12 5" />;
const IcoTrophy   = ({size=16}) => <Icon size={size} d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6m12 5h1.5a2.5 2.5 0 0 0 0-5H18M12 12v4m-4 4h8M3 3h18v6a9 9 0 0 1-18 0V3z" />;
const IcoClock    = ({size=16}) => <Icon size={size} d="M12 2a10 10 0 1 1 0 20A10 10 0 0 1 12 2zm0 4v6l4 2" />;
const IcoCalendar = ({size=16}) => <Icon size={size} d="M8 2v4M16 2v4M3 8h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" />;
const IcoBook     = ({size=16}) => <Icon size={size} d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z" />;
const IcoSettings = ({size=16}) => <Icon size={size} d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />;
const IcoRefresh  = ({size=16}) => <Icon size={size} d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
const IcoChat     = ({size=16}) => <Icon size={size} d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />;
const IcoLeft     = ({size=16}) => <Icon size={size} d="M19 12H5M12 19l-7-7 7-7" />;
const IcoRight    = ({size=16}) => <Icon size={size} d="M5 12h14M12 5l7 7-7 7" />;
const IcoUpload   = ({size=16}) => <Icon size={size} d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />;
const IcoCloud    = ({size=16}) => <Icon size={size} d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />;
const IcoTrash    = ({size=16}) => <Icon size={size} d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />;
const IcoKeyboard = ({size=16}) => <Icon size={size} d="M20 5H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zM8 15H6v-2h2v2zm0-4H6V9h2v2zm4 4h-2v-2h2v2zm0-4h-2V9h2v2zm4 4h-2v-2h2v2zm0-4h-2V9h2v2zm4 4h-2V9h2v6z" />;
const IcoCheck    = ({size=16}) => <Icon size={size} d="M20 6L9 17l-5-5" />;
const IcoCross    = ({size=16}) => <Icon size={size} d="M18 6L6 18M6 6l12 12" />;
const IcoStar     = ({size=16}) => <Icon size={size} d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />;

// ── Config & Helpers ──────────────────────────────────────────────────────────
const SUPABASE_URL      = "https://ylqloszldyzpeaikweyl.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlscWxvc3psZHl6cGVhaWt3ZXlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyNDg2NDUsImV4cCI6MjA4ODgyNDY0NX0.JgwZKn5_ifnoZHViOGb7aED9sZ3MnijeeI66cFhSJaQ";
const SB_ENABLED = SUPABASE_URL.startsWith("https://") && SUPABASE_ANON_KEY.startsWith("eyJ");

const sbH = (prefer = "return=representation") => ({ "Content-Type": "application/json", apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}`, Prefer: prefer });
async function sbSelect(table, params = "") { const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, { headers: sbH() }); if (!r.ok) throw new Error(await r.text()); return r.json(); }
async function sbInsert(table, row) { const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, { method: "POST", headers: sbH(), body: JSON.stringify(row) }); if (!r.ok) throw new Error(await r.text()); }

const STORAGE_KEY = "quiz_abcd_attempts_v3";
const optionKeys = ["A","B","C","D"];
const diffW = { easy:1, medium:1.5, hard:2 };
const normDiff = v => { const r = String(v||"medium").trim().toLowerCase(); return ["easy","medium","hard"].includes(r)?r:"medium"; };
const fmt = ms => `${(ms/1000).toFixed(1)}s`;
const dayKey = ts => { const d=new Date(ts); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`; };
const som = d => new Date(d.getFullYear(),d.getMonth(),1);
const addM = (d,n) => new Date(d.getFullYear(),d.getMonth()+n,1);
const loadLocal = () => { try { return JSON.parse(localStorage.getItem(STORAGE_KEY)||"[]"); } catch { return []; } };
const saveLocal = list => { try { localStorage.setItem(STORAGE_KEY,JSON.stringify((list||[]).slice(0,100))); } catch {} };
const dedupe = items => { const m = new Map(); for (const a of (items||[])) { if (!a?.id) continue; const ex = m.get(a.id); if (!ex || (ex.source!=="supabase" && a.source==="supabase")) m.set(a.id,a); } return [...m.values()].sort((a,b)=>b.finishedAt-a.finishedAt); };
const rowToQ = (row,i) => ({ id: row.id??i+1, questionNo: row.question_no??i+1, question: row.question_text, options: { A:row.option_a, B:row.option_b, C:row.option_c, D:row.option_d }, correct: row.correct_answer||null, explanation: row.explanation||"Brak wyjaśnienia.", category: row.category||"General", difficulty: normDiff(row.difficulty||"medium"), sourceType: row.source_type||"database" });

function parseRows(rows, sourceFile=null) {
  return (rows||[]).map((row,i) => {
    const q = row.question??row.Question??row.pytanie??row.question_text;
    const a = row.A??row.a??row.option_a; const b = row.B??row.b??row.option_b;
    const c = row.C??row.c??row.option_c; const d = row.D??row.d??row.option_d;
    const correct = String(row.correct??row.Correct??row.correct_answer??"").trim().toUpperCase();
    if (!q||!a||!b||!c||!d) return null;
    return { id:`import-${i+1}`, questionNo:Number(row.questionNo??i+1), question:String(q).trim(), options:{A:String(a).trim(),B:String(b).trim(),C:String(c).trim(),D:String(d).trim()}, correct:optionKeys.includes(correct)?correct:null, explanation:String(row.explanation??"Brak wyjaśnienia.").trim(), category:String(row.category??"General").trim(), difficulty:normDiff(row.difficulty??"medium"), sourceType:"spreadsheet", sourceFile };
  }).filter(Boolean);
}

function parseTxt(text, sourceFile="import.txt") {
  return [...String(text||"").replace(/\r/g,"").matchAll(/Question\s+#(\d+)\s*([\s\S]*?)(?=\nQuestion\s+#\d+|$)/g)].map(m => {
    const no=Number(m[1]), block=m[2].trim();
    const p=block.match(/^([\s\S]*?)\nA\.\s*([\s\S]*?)\nB\.\s*([\s\S]*?)\nC\.\s*([\s\S]*?)\nD\.\s*([\s\S]*?)(?:\nView answer|$)/);
    if (!p) return null;
    return { id:`txt-${no}`, questionNo:no, question:p[1].replace(/\s+/g," ").trim(), options:{A:p[2].replace(/\s+/g," ").trim(),B:p[3].replace(/\s+/g," ").trim(),C:p[4].replace(/\s+/g," ").trim(),D:p[5].replace(/\s+/g," ").trim()}, correct:null, explanation:"Brak odpowiedzi w pliku.", category:"Import", difficulty:"medium", sourceType:"txt_import", sourceFile };
  }).filter(Boolean);
}

function buildCalDays(month) {
  const start=som(month), fw=(start.getDay()+6)%7;
  const gs=new Date(start); gs.setDate(start.getDate()-fw);
  return Array.from({length:42},(_,i)=>{ const d=new Date(gs); d.setDate(gs.getDate()+i); return {date:d,key:dayKey(d.getTime()),inCurrent:d.getMonth()===start.getMonth()}; });
}

function buildPlan(history, weakCat) {
  if (!history.length) return { recommendation:"Ukończ kilka prób quizu.", improvements:[], weeklyPlan:[] };
  const l5=history.slice(0,5);
  const avgAcc=Math.round(l5.reduce((s,a)=>s+a.percent,0)/l5.length);
  const avgPace=Number((l5.reduce((s,a)=>s+a.avgResponseMs,0)/l5.length/1000).toFixed(1));
  const wm={}; l5.forEach(a=>{ if(a.weakestCategory) wm[a.weakestCategory]=(wm[a.weakestCategory]||0)+1; });
  const weak=Object.entries(wm).sort((a,b)=>b[1]-a[1])[0]?.[0]||weakCat?.category||"Mieszane tematy";
  return {
    readiness: avgAcc>=85?"Zaawansowany":avgAcc>=65?"Średniozaawansowany":"Buduj podstawy",
    recommendation: avgAcc>=85?"Gotowy na trudniejsze zestawy.":avgAcc>=65?"Dobra baza — wzmocnij słabe kategorie.":"Skup się najpierw na dokładności.",
    improvements: [ avgAcc<70?"Zwolnij przy trudnych pytaniach.":"Utrzymaj wysoką dokładność.", `Obszar do poprawy: ${weak}.` ],
    weeklyPlan:[
      {day:"Pon", task:`Przejrzyj błędy: ${weak}`, duration:"25m"},
      {day:"Wt",  task:`Skupiony quiz: ${weak}`,    duration:"30m"},
      {day:"Śr",  task:"Zestaw mieszany + timer",   duration:"20m"},
      {day:"Czw", task:"Podsumuj kluczowe zasady",  duration:"20m"},
      {day:"Pt",  task:"Szybki quiz wiedzy ogólnej",duration:"25m"},
      {day:"Sob", task:"Pełny próbny quiz",         duration:"30m"},
      {day:"Nd",  task:"Lekki przegląd materiału",  duration:"15m"}
    ]
  };
}

const SAMPLES = [
  {id:1,questionNo:1,question:"You are the program manager. You need to formally define the scope of the new project. Which document is used?",options:{A:"Risk Register",B:"Project Charter",C:"Lessons Learned",D:"Issue Log"},correct:"B",explanation:"Project Charter formalnie autoryzuje projekt i określa jego ramy.",category:"PgMP",difficulty:"medium",sourceType:"sample"},
  {id:2,questionNo:2,question:"Which one of the following is not an output of the direct and manage program execution process?",options:{A:"Results of program work",B:"Program budget",C:"Change requests",D:"Request to terminate the program"},correct:"B",explanation:"Program budget jest wejściem (input), nie wyjściem (output) procesu Direct and Manage.",category:"PgMP",difficulty:"medium",sourceType:"sample"},
  {id:3,questionNo:3,question:"What does VAT stand for in accounting?",options:{A:"Value Added Tax",B:"Variable Asset Transfer",C:"Verified Accounting Tool",D:"Value Allocation Table"},correct:"A",explanation:"VAT to podatek od wartości dodanej.",category:"Finance",difficulty:"easy",sourceType:"sample"},
];

// ── COLOUR TOKENS ─────────────────────────────────────────────────────────────
const C = {
  bg:      "#0F1117",
  surface: "#181C27",
  card:    "#1E2235",
  border:  "#2A2F45",
  accent:  "#6C63FF",
  accentL: "#8B84FF",
  muted:   "#4A5168",
  text:    "#E8EAF0",
  textSub: "#8890A4",
  green:   "#00C896",
  red:     "#FF5A6A",
  yellow:  "#FFB847",
};

// ── INLINE STYLES HELPERS ─────────────────────────────────────────────────────
const s = {
  card: { background: C.card, border: `1px solid ${C.border}`, borderRadius: 16 },
  cardSm: { background: C.card, border: `1px solid ${C.border}`, borderRadius: 12 },
  pill: (active) => ({
    padding: "6px 16px", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", border: "none",
    background: active ? C.accent : "transparent",
    color: active ? "#fff" : C.textSub,
    transition: "all .2s",
  }),
  btn: (variant="primary") => ({
    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
    padding: "10px 20px", borderRadius: 10, fontSize: 13, fontWeight: 700, cursor: "pointer",
    border: "none", transition: "all .2s",
    ...(variant === "primary" ? { background: C.accent, color: "#fff" } :
        variant === "ghost"   ? { background: "transparent", color: C.textSub, border: `1px solid ${C.border}` } :
        variant === "danger"  ? { background: "rgba(255,90,106,.1)", color: C.red, border: `1px solid rgba(255,90,106,.2)` } :
        { background: C.surface, color: C.text, border: `1px solid ${C.border}` })
  }),
};

// ── MAIN APP ──────────────────────────────────────────────────────────────────
export default function QuizAbcdApp() {
  const [questionPool, setQuestionPool] = useState(SAMPLES);
  const [quizLength,   setQuizLength]   = useState(10);
  const [questions,    setQuestions]    = useState(() => SAMPLES.slice(0,10));
  const [idx,          setIdx]          = useState(0);
  const [selected,     setSelected]     = useState(null);
  const [answers,      setAnswers]      = useState({});
  const [showResult,   setShowResult]   = useState(false);
  const [startedAt,    setStartedAt]    = useState(()=>Date.now());
  const [qStartedAt,   setQStartedAt]   = useState(()=>Date.now());
  const [finishedAt,   setFinishedAt]   = useState(null);
  const [history,      setHistory]      = useState(()=>loadLocal());
  const [importMsg,    setImportMsg]    = useState(null);
  const [activeTab,    setActiveTab]    = useState("quiz");
  const [calMonth,     setCalMonth]     = useState(()=>som(new Date()));
  const [chatStatus,   setChatStatus]   = useState("idle");
  const [chatRes,      setChatRes]      = useState("");
  const fileRef = useRef(null);

  const total   = questions.length;
  const current = questions[idx] || SAMPLES[0];
  const score   = useMemo(()=>Object.values(answers).filter(a=>a.isCorrect).length,[answers]);
  const answeredCount = Object.keys(answers).length;

  const startQuiz = useCallback((customPool, customLength) => {
    const pool = customPool || questionPool;
    const len  = customLength !== undefined ? customLength : quizLength;
    const shuffled = [...pool].sort(() => 0.5 - Math.random());
    const selected = len === "all" ? shuffled : shuffled.slice(0, len);
    if (customPool) setQuestionPool(customPool);
    setQuestions(selected.length ? selected : pool);
    setIdx(0); setSelected(null); setAnswers({}); setShowResult(false);
    setStartedAt(Date.now()); setQStartedAt(Date.now()); setFinishedAt(null);
    setActiveTab("quiz"); setChatStatus("idle"); setChatRes("");
  }, [questionPool, quizLength]);

  const loadQfromDB = useCallback(async()=>{
    if (!SB_ENABLED) return;
    try {
      const rows=await sbSelect("quiz_questions","is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length) return;
      const parsed = rows.map(rowToQ);
      setQuestionPool(parsed);
      const shuffled = [...parsed].sort(() => 0.5 - Math.random());
      setQuestions(shuffled.slice(0, quizLength === "all" ? shuffled.length : quizLength));
    } catch(e){}
  },[quizLength]);

  const loadAttempts = useCallback(async()=>{
    if (!SB_ENABLED) return;
    try {
      const rows=await sbSelect("quiz_attempts","order=finished_at.desc&limit=100");
      const mapped=rows.map(r=>({ id:r.attempt_id, finishedAt:new Date(r.finished_at).getTime(), totalQuestions:r.total_questions, score:r.score, percent:r.percent, mastery:r.mastery, avgResponseMs:r.avg_response_ms, totalTimeMs:r.total_time_ms, strongestCategory:r.strongest_category, weakestCategory:r.weakest_category, source:"supabase" }));
      setHistory(prev=>{ const m=dedupe([...mapped,...prev]); saveLocal(m); return m; });
    } catch(e){}
  },[]);

  useEffect(()=>{loadQfromDB();loadAttempts();},[]);

  const handleAnswer = useCallback((key)=>{
    if (selected || showResult) return;
    setSelected(key);
    setAnswers(prev=>({...prev,[current.id]:{questionId:current.id,selected:key,correct:current.correct,isCorrect:current.correct?key===current.correct:false,responseTimeMs:Date.now()-qStartedAt,category:current.category||"General",difficulty:current.difficulty||"medium"}}));
  },[current,qStartedAt,selected,showResult]);

  const next = useCallback(()=>{
    if (idx < total - 1){
      const ni=idx+1; setIdx(ni); setSelected(answers[questions[ni].id]?.selected??null); setQStartedAt(Date.now());
      setChatStatus("idle"); setChatRes("");
    } else { setFinishedAt(Date.now()); setShowResult(true); setActiveTab("results"); }
  },[answers,idx,questions,total]);

  const prev = useCallback(()=>{
    if (idx>0){ const ni=idx-1; setIdx(ni); setSelected(answers[questions[ni].id]?.selected??null); setChatStatus("idle"); setChatRes(""); }
  },[answers,idx,questions]);

  const askAI = useCallback(async()=>{
    if (chatStatus==="loading") return;
    setChatStatus("loading");
    setTimeout(()=>{
      setChatRes(`Pytanie dotyczy kategorii "${current.category}". Zwróć uwagę na słowa kluczowe w opcjach. Odpowiedź ${current.correct} najlepiej odzwierciedla standardowe definicje i praktykę egzaminacyjną.`);
      setChatStatus("loaded");
    }, 1000);
  },[chatStatus,current]);

  const handleImport = useCallback(async(e)=>{
    const file=e.target.files?.[0]; if (!file) return;
    try {
      let parsed=[];
      if (file.name.toLowerCase().endsWith(".txt")) parsed=parseTxt(await file.text(),file.name);
      else { const wb=XLSX.read(await file.arrayBuffer(),{type:"array"}); parsed=parseRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{defval:""}),file.name); }
      if (!parsed.length){setImportMsg("Import nieudany – sprawdź format pliku.");return;}
      startQuiz(parsed, quizLength);
      setImportMsg(`✓ Zaimportowano ${parsed.length} pytań z "${file.name}"`);
    } catch(err){setImportMsg(`✗ Błąd: ${err.message}`);}
    finally{e.target.value="";}
  },[quizLength,startQuiz]);

  const stats = useMemo(()=>{
    const list=questions.map(q=>({q,a:answers[q.id]})).filter(x=>x.a);
    const totalTimeMs=(finishedAt??Date.now())-startedAt;
    const avgResponseMs=list.length?list.reduce((s,x)=>s+x.a.responseTimeMs,0)/list.length:0;
    const correctCount=list.filter(x=>x.a.isCorrect).length;
    const wTotal=questions.reduce((s,q)=>s+(diffW[q.difficulty||"medium"]||1.5),0);
    const wScore=list.reduce((s,x)=>s+(x.a.isCorrect?(diffW[x.q.difficulty||"medium"]||1.5):0),0);
    const mastery=wTotal?Math.round((wScore/wTotal)*100):0;
    const byCat=Object.values(questions.reduce((acc,q)=>{
      const cat=q.category||"General"; if (!acc[cat]) acc[cat]={category:cat,total:0,correct:0};
      acc[cat].total++; if (answers[q.id]?.isCorrect) acc[cat].correct++; return acc;
    },{})).map(c=>({...c,percent:Math.round((c.correct/c.total)*100)}));
    const weakest=byCat.length?[...byCat].sort((a,b)=>a.percent-b.percent)[0]:null;
    const strongest=byCat.length?[...byCat].sort((a,b)=>b.percent-a.percent)[0]:null;
    return {totalTimeMs,avgResponseMs,correctCount,incorrectCount:list.length-correctCount,mastery,byCat,weakest,strongest};
  },[answers,questions,startedAt,finishedAt]);

  useEffect(()=>{
    if (!showResult||!finishedAt) return;
    const attempt={ id:`${finishedAt}-${Math.random().toString(36).slice(2,7)}`, finishedAt, totalQuestions:total, score, percent:Math.round((score/Math.max(total,1))*100), mastery:stats.mastery, avgResponseMs:Math.round(stats.avgResponseMs), totalTimeMs:Math.round(stats.totalTimeMs), strongestCategory:stats.strongest?.category||null, weakestCategory:stats.weakest?.category||null, source:"local" };
    setHistory(prev=>{ if (prev.some(a=>a.finishedAt===finishedAt)) return prev; const m=dedupe([attempt,...prev]); saveLocal(m); return m; });
    if (SB_ENABLED) sbInsert("quiz_attempts",{ attempt_id:attempt.id, finished_at:new Date(attempt.finishedAt).toISOString(), total_questions:attempt.totalQuestions, score:attempt.score, percent:attempt.percent, mastery:attempt.mastery, avg_response_ms:attempt.avgResponseMs, total_time_ms:attempt.totalTimeMs, strongest_category:attempt.strongestCategory, weakest_category:attempt.weakestCategory }).then(()=>loadAttempts()).catch(()=>{});
  },[showResult,finishedAt,score,stats,total]);

  useEffect(()=>{
    const h=e=>{
      if (e.target?.tagName==="INPUT"||e.target?.tagName==="TEXTAREA") return;
      const k=e.key.toUpperCase();
      if (!showResult&&!selected&&optionKeys.includes(k)){e.preventDefault();handleAnswer(k);return;}
      if (e.key==="Enter"){e.preventDefault();next();return;}
      if (k==="R"){e.preventDefault();startQuiz(questionPool,quizLength);}
    };
    window.addEventListener("keydown",h); return ()=>window.removeEventListener("keydown",h);
  },[handleAnswer,next,startQuiz,selected,showResult,questionPool,quizLength]);

  const uniq   = useMemo(()=>dedupe(history),[history]);
  const dayMap = useMemo(()=>{ const m={}; uniq.forEach(a=>{const k=dayKey(a.finishedAt);m[k]=(m[k]||0)+1;}); return m; },[uniq]);
  const streak = useMemo(()=>{ let s=0; const c=new Date(); while(dayMap[dayKey(c.getTime())]){s++;c.setDate(c.getDate()-1);} return s; },[dayMap]);
  const plan   = useMemo(()=>buildPlan(uniq,stats.weakest),[uniq,stats.weakest]);
  const calDays= useMemo(()=>buildCalDays(calMonth),[calMonth]);

  const TABS = [
    {id:"quiz",     label:"Quiz",      icon:<IcoBrain size={15}/>},
    {id:"results",  label:"Wyniki",    icon:<IcoTrophy size={15}/>},
    {id:"calendar", label:"Kalendarz", icon:<IcoCalendar size={15}/>},
    {id:"plan",     label:"Plan",      icon:<IcoBook size={15}/>},
    {id:"settings", label:"Ustawienia",icon:<IcoSettings size={15}/>},
  ];

  const pct = answeredCount > 0 ? Math.round((stats.correctCount/answeredCount)*100) : 0;
  const progressPct = Math.round((idx/Math.max(total,1))*100);

  // ── QUIZ VIEW ───────────────────────────────────────────────────────────────
  const QuizView = () => {
    const diffColor = {easy:C.green, medium:C.yellow, hard:C.red}[current.difficulty||"medium"];
    return (
      <div style={{display:"flex",flexDirection:"column",height:"100%",gap:20}}>
        {/* Question header */}
        <div>
          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
            <span style={{fontSize:11,fontWeight:700,letterSpacing:".08em",color:C.textSub,textTransform:"uppercase"}}>
              Pytanie {idx+1} / {total}
            </span>
            <span style={{width:4,height:4,borderRadius:"50%",background:C.muted}}/>
            <span style={{fontSize:11,fontWeight:700,padding:"2px 8px",borderRadius:5,background:`${diffColor}18`,color:diffColor,textTransform:"capitalize"}}>
              {current.difficulty}
            </span>
            <span style={{marginLeft:"auto",fontSize:11,fontWeight:700,padding:"2px 10px",borderRadius:5,background:C.surface,color:C.textSub,border:`1px solid ${C.border}`}}>
              {current.category}
            </span>
          </div>
          <h2 style={{fontSize:18,fontWeight:700,color:C.text,lineHeight:1.55,margin:0}}>
            {current.question}
          </h2>
        </div>

        {/* Options */}
        <div style={{display:"flex",flexDirection:"column",gap:10,flex:1}}>
          {optionKeys.map(key => {
            const isSel=selected===key, isCorr=current.correct===key, reveal=!!selected;
            let bg=C.surface, border=C.border, color=C.text, labelBg=C.card, labelColor=C.textSub;
            let iconEl=null;
            if (reveal) {
              if (isCorr) { bg=`${C.green}12`; border=C.green; color=C.text; labelBg=C.green; labelColor="#fff"; iconEl=<IcoCheck size={13}/>; }
              else if (isSel) { bg=`${C.red}12`; border=C.red; color=C.text; labelBg=C.red; labelColor="#fff"; iconEl=<IcoCross size={13}/>; }
              else { bg="transparent"; border=C.border; color=C.muted; }
            }
            return (
              <button key={key} onClick={()=>handleAnswer(key)} disabled={!!selected} style={{display:"flex",alignItems:"center",gap:14,padding:"14px 16px",borderRadius:12,border:`1.5px solid ${border}`,background:bg,cursor:selected?"default":"pointer",transition:"all .18s",textAlign:"left",width:"100%"}}>
                <span style={{width:30,height:30,borderRadius:8,background:labelBg,color:labelColor,fontSize:12,fontWeight:800,display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0,border:`1px solid ${reveal&&(isCorr||isSel)?'transparent':C.border}`,transition:"all .18s"}}>
                  {key}
                </span>
                <span style={{fontSize:14,fontWeight:500,color,flex:1,lineHeight:1.45}}>{current.options[key]}</span>
                {iconEl && <span style={{flexShrink:0,color:isCorr?C.green:C.red,display:"flex"}}>{iconEl}</span>}
              </button>
            );
          })}
        </div>

        {/* Explanation */}
        {selected && (
          <div style={{...s.cardSm,padding:"16px 18px",background:C.surface,borderColor:selected===current.correct?`${C.green}40`:`${C.red}40`}}>
            <div style={{fontSize:12,fontWeight:700,color:selected===current.correct?C.green:C.red,marginBottom:6}}>
              {current.correct?(selected===current.correct?"✓ Świetnie! Poprawna odpowiedź.":`✗ Poprawna odpowiedź: ${current.correct}`):"Klucz niedostępny."}
            </div>
            <div style={{fontSize:13,color:C.textSub,lineHeight:1.6,marginBottom:chatStatus!=="idle"?12:0}}>
              {current.explanation}
            </div>
            {chatStatus==="idle" && (
              <button onClick={askAI} style={{...s.btn("ghost"),marginTop:10,fontSize:12,padding:"7px 14px"}}>
                <IcoChat size={13}/> Zapytaj AI
              </button>
            )}
            {chatStatus==="loading" && <div style={{fontSize:12,color:C.accent,marginTop:8,display:"flex",alignItems:"center",gap:6}}><IcoChat size={13}/> Łączenie z AI...</div>}
            {chatStatus==="loaded" && <div style={{fontSize:13,color:C.text,marginTop:10,padding:"12px 14px",background:C.card,borderRadius:10,border:`1px solid ${C.accent}30`,lineHeight:1.6}}>{chatRes}</div>}
          </div>
        )}

        {/* Nav */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",paddingTop:4}}>
          <button onClick={prev} disabled={idx===0} style={{...s.btn("ghost"),opacity:idx===0?.35:1,fontSize:12}}>
            <IcoLeft size={13}/> Poprzednie
          </button>
          <button onClick={next} disabled={!selected} style={{...s.btn("primary"),opacity:!selected?.45:1,fontSize:13,padding:"10px 24px"}}>
            {idx===total-1?"Zakończ Quiz":"Następne"} <IcoRight size={13}/>
          </button>
        </div>
      </div>
    );
  };

  // ── RESULTS VIEW ─────────────────────────────────────────────────────────────
  const ResultsView = () => {
    if (!showResult) return (
      <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",height:"100%",gap:16,color:C.textSub}}>
        <IcoTrophy size={40}/>
        <p style={{fontSize:14}}>Ukończ quiz, aby zobaczyć wyniki.</p>
        <button onClick={()=>setActiveTab("quiz")} style={s.btn("primary")}>Wróć do quizu</button>
      </div>
    );
    const pctFinal = Math.round((score/Math.max(total,1))*100);
    const ring = 2*Math.PI*52;
    return (
      <div style={{display:"flex",flexDirection:"column",gap:18,height:"100%"}}>
        {/* Score ring */}
        <div style={{...s.card,padding:"28px 24px",display:"flex",alignItems:"center",gap:24}}>
          <div style={{position:"relative",width:120,height:120,flexShrink:0}}>
            <svg width={120} height={120} style={{transform:"rotate(-90deg)"}}>
              <circle cx={60} cy={60} r={52} fill="none" stroke={C.border} strokeWidth={8}/>
              <circle cx={60} cy={60} r={52} fill="none" stroke={C.accent} strokeWidth={8}
                strokeDasharray={ring} strokeDashoffset={ring*(1-pctFinal/100)} strokeLinecap="round"
                style={{transition:"stroke-dashoffset 1s ease"}}/>
            </svg>
            <div style={{position:"absolute",inset:0,display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center"}}>
              <span style={{fontSize:26,fontWeight:800,color:C.text}}>{pctFinal}%</span>
            </div>
          </div>
          <div>
            <div style={{fontSize:22,fontWeight:800,color:C.text,marginBottom:4}}>Quiz ukończony!</div>
            <div style={{fontSize:14,color:C.textSub,marginBottom:12}}>{score} poprawnych na {total} pytań</div>
            <button onClick={()=>startQuiz(questionPool,quizLength)} style={{...s.btn("primary"),fontSize:12,padding:"8px 16px"}}>
              <IcoRefresh size={13}/> Nowa sesja
            </button>
          </div>
        </div>
        {/* Stats grid */}
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
          {[
            {l:"Czas na pytanie", v:fmt(stats.avgResponseMs), c:C.yellow},
            {l:"Całkowity czas",  v:fmt(stats.totalTimeMs),   c:C.textSub},
            {l:"Poprawne",        v:stats.correctCount,        c:C.green},
            {l:"Błędne",          v:stats.incorrectCount,      c:C.red},
          ].map(item=>(
            <div key={item.l} style={{...s.cardSm,padding:"16px 18px"}}>
              <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>{item.l}</div>
              <div style={{fontSize:24,fontWeight:800,color:item.c}}>{item.v}</div>
            </div>
          ))}
        </div>
        {/* Category breakdown */}
        {stats.byCat.length > 0 && (
          <div style={{...s.card,padding:"18px 20px",flex:1}}>
            <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".07em",marginBottom:14}}>Wyniki wg kategorii</div>
            <div style={{display:"flex",flexDirection:"column",gap:10}}>
              {stats.byCat.map(c=>(
                <div key={c.category}>
                  <div style={{display:"flex",justifyContent:"space-between",marginBottom:5}}>
                    <span style={{fontSize:13,color:C.text,fontWeight:500}}>{c.category}</span>
                    <span style={{fontSize:13,fontWeight:700,color:c.percent>=70?C.green:C.red}}>{c.percent}%</span>
                  </div>
                  <div style={{height:5,background:C.border,borderRadius:4,overflow:"hidden"}}>
                    <div style={{height:"100%",width:`${c.percent}%`,background:c.percent>=70?C.green:C.red,borderRadius:4,transition:"width .6s ease"}}/>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  // ── CALENDAR VIEW ────────────────────────────────────────────────────────────
  const CalendarView = () => (
    <div style={{display:"flex",flexDirection:"column",height:"100%",gap:16}}>
      <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:12}}>
        {[{l:"Dni nauki",v:Object.keys(dayMap).length},{l:"Seria",v:streak},{l:"Próby",v:uniq.length}].map(c=>(
          <div key={c.l} style={{...s.cardSm,padding:"16px 18px"}}>
            <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>{c.l}</div>
            <div style={{fontSize:26,fontWeight:800,color:C.accent}}>{c.v}</div>
          </div>
        ))}
      </div>
      <div style={{...s.card,padding:"20px",flex:1}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}>
          <span style={{fontSize:15,fontWeight:700,color:C.text,textTransform:"capitalize"}}>
            {calMonth.toLocaleString("pl-PL",{month:"long",year:"numeric"})}
          </span>
          <div style={{display:"flex",gap:8}}>
            {[[-1,<IcoLeft key="l" size={14}/>],[1,<IcoRight key="r" size={14}/>]].map(([n,ico])=>(
              <button key={n} onClick={()=>setCalMonth(m=>addM(m,n))} style={{...s.btn("ghost"),padding:"6px 10px",borderRadius:8}}>{ico}</button>
            ))}
          </div>
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:6,marginBottom:8}}>
          {["Pn","Wt","Śr","Cz","Pt","So","Nd"].map(d=>(
            <div key={d} style={{textAlign:"center",fontSize:10,fontWeight:700,color:C.muted,textTransform:"uppercase",letterSpacing:".05em"}}>{d}</div>
          ))}
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:6}}>
          {calDays.map(i=>(
            <div key={i.key} style={{aspectRatio:"1",display:"flex",alignItems:"center",justifyContent:"center",borderRadius:8,fontSize:12,fontWeight:600,
              background:dayMap[i.key]?C.accent:i.inCurrent?C.surface:"transparent",
              color:dayMap[i.key]?"#fff":i.inCurrent?C.text:C.muted,
              border:dayMap[i.key]?`1px solid ${C.accentL}30`:`1px solid ${i.inCurrent?C.border:"transparent"}`,
              boxShadow:dayMap[i.key]?`0 0 12px ${C.accent}40`:"none",
            }}>
              {i.date.getDate()}
            </div>
          ))}
        </div>
      </div>
    </div>
  );

  // ── PLAN VIEW ────────────────────────────────────────────────────────────────
  const PlanView = () => (
    <div style={{display:"flex",flexDirection:"column",height:"100%",gap:14}}>
      <div style={{...s.card,padding:"22px 24px",background:`linear-gradient(135deg,${C.accent},#9C5FE0)`,border:"none"}}>
        <div style={{fontSize:11,fontWeight:700,letterSpacing:".1em",color:"rgba(255,255,255,.6)",textTransform:"uppercase",marginBottom:8}}>
          Rekomendacja systemu
        </div>
        <div style={{fontSize:17,fontWeight:700,color:"#fff",lineHeight:1.5}}>{plan.recommendation}</div>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:8,flex:1}}>
        {plan.weeklyPlan.map((item,i)=>(
          <div key={item.day} style={{...s.cardSm,padding:"14px 18px",display:"flex",alignItems:"center",gap:14,transition:"border-color .2s"}}>
            <div style={{width:40,height:40,borderRadius:10,background:`${C.accent}18`,display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0}}>
              <span style={{fontSize:11,fontWeight:800,color:C.accent}}>{item.day}</span>
            </div>
            <span style={{fontSize:13,color:C.text,flex:1,fontWeight:500}}>{item.task}</span>
            <span style={{fontSize:11,fontWeight:700,color:C.textSub,background:C.surface,border:`1px solid ${C.border}`,padding:"4px 10px",borderRadius:6}}>{item.duration}</span>
          </div>
        ))}
      </div>
    </div>
  );

  // ── SETTINGS VIEW ────────────────────────────────────────────────────────────
  const SettingsView = () => (
    <div style={{display:"flex",flexDirection:"column",height:"100%",gap:16}}>
      {/* Import */}
      <div style={{...s.card,padding:"20px 22px"}}>
        <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".08em",marginBottom:14}}>
          Baza pytań ({questionPool.length} pytań)
        </div>
        <button onClick={()=>fileRef.current?.click()} style={{...s.btn("ghost"),width:"100%",padding:"13px",borderStyle:"dashed",borderColor:C.accent,color:C.accent,fontSize:13}}>
          <IcoUpload size={15}/> Wgraj własny plik (CSV / XLSX / TXT)
        </button>
        <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" style={{display:"none"}} onChange={handleImport}/>
        {importMsg && (
          <div style={{marginTop:10,padding:"10px 14px",borderRadius:8,background:importMsg.startsWith("✓")?`${C.green}12`:`${C.red}12`,border:`1px solid ${importMsg.startsWith("✓")?`${C.green}30`:`${C.red}30`}`,fontSize:12,color:importMsg.startsWith("✓")?C.green:C.red,fontWeight:600}}>
            {importMsg}
          </div>
        )}
      </div>

      {/* Keyboard shortcuts */}
      <div style={{...s.card,padding:"20px 22px"}}>
        <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".08em",marginBottom:14,display:"flex",alignItems:"center",gap:8}}>
          <IcoKeyboard size={14}/> Skróty klawiaturowe
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
          {[["A B C D","Wybór odpowiedzi"],["Enter","Następne pytanie"],["R","Restart quizu"],["↑ / ↓","Poprzednie / Następne"]].map(([key,desc])=>(
            <div key={key} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 12px",background:C.surface,borderRadius:8,border:`1px solid ${C.border}`}}>
              <span style={{padding:"3px 8px",borderRadius:6,background:C.card,border:`1px solid ${C.border}`,fontSize:11,fontWeight:800,color:C.text,letterSpacing:".04em",flexShrink:0}}>{key}</span>
              <span style={{fontSize:12,color:C.textSub}}>{desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Cloud */}
      <div style={{...s.card,padding:"20px 22px"}}>
        <div style={{fontSize:11,fontWeight:700,color:C.textSub,textTransform:"uppercase",letterSpacing:".08em",marginBottom:14,display:"flex",alignItems:"center",gap:8}}>
          <IcoCloud size={14}/> Synchronizacja chmury
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          <div style={{width:8,height:8,borderRadius:"50%",background:SB_ENABLED?C.green:C.muted}}/>
          <span style={{fontSize:13,color:SB_ENABLED?C.green:C.textSub,fontWeight:600}}>
            {SB_ENABLED?"Supabase połączono":"Brak połączenia (tryb lokalny)"}
          </span>
        </div>
        <p style={{fontSize:12,color:C.muted,marginTop:8,lineHeight:1.6}}>
          Synchronizacja wymaga ustawienia zmiennych środowiskowych SUPABASE_URL i SUPABASE_ANON_KEY.
        </p>
      </div>

      {/* Reset */}
      <button onClick={()=>{localStorage.removeItem(STORAGE_KEY);setHistory([]);}} style={{...s.btn("danger"),width:"100%",padding:"13px",fontSize:13,marginTop:"auto"}}>
        <IcoTrash size={14}/> Zresetuj lokalne statystyki
      </button>
    </div>
  );

  // ── SIDEBAR ──────────────────────────────────────────────────────────────────
  const Sidebar = () => (
    <aside style={{width:240,background:C.surface,borderRight:`1px solid ${C.border}`,display:"flex",flexDirection:"column",flexShrink:0,padding:"24px 16px",gap:8}}>
      {/* Logo */}
      <div style={{display:"flex",alignItems:"center",gap:10,padding:"0 8px",marginBottom:24}}>
        <div style={{width:34,height:34,borderRadius:10,background:C.accent,display:"flex",alignItems:"center",justifyContent:"center"}}>
          <IcoBrain size={18}/>
        </div>
        <span style={{fontSize:17,fontWeight:800,color:C.text,letterSpacing:"-.02em"}}>QuizApp</span>
      </div>

      {/* Nav */}
      {TABS.map(t=>(
        <button key={t.id} onClick={()=>setActiveTab(t.id)} style={{display:"flex",alignItems:"center",gap:12,padding:"10px 14px",borderRadius:10,border:"none",cursor:"pointer",textAlign:"left",width:"100%",transition:"all .15s",
          background:activeTab===t.id?`${C.accent}18`:"transparent",
          color:activeTab===t.id?C.accentL:C.textSub,
          fontWeight:activeTab===t.id?700:500, fontSize:13,
        }}>
          <span style={{color:activeTab===t.id?C.accentL:C.muted}}>{t.icon}</span>
          {t.label}
          {t.id==="results"&&showResult&&<span style={{marginLeft:"auto",width:7,height:7,borderRadius:"50%",background:C.accent,flexShrink:0}}/>}
        </button>
      ))}

      {/* Divider */}
      <div style={{height:1,background:C.border,margin:"12px 0"}}/>

      {/* Session controls */}
      <div style={{padding:"0 8px"}}>
        <div style={{fontSize:10,fontWeight:700,color:C.muted,letterSpacing:".1em",textTransform:"uppercase",marginBottom:10}}>Długość sesji</div>
        <div style={{display:"flex",gap:4,background:C.card,padding:4,borderRadius:10,border:`1px solid ${C.border}`}}>
          {[5,10,20,"∞"].map((len,i)=>{
            const val=i===3?"all":len;
            const active=quizLength===val;
            return (
              <button key={len} onClick={()=>{setQuizLength(val);startQuiz(questionPool,val);}}
                style={{flex:1,padding:"6px 0",borderRadius:7,border:"none",cursor:"pointer",fontSize:12,fontWeight:700,transition:"all .15s",
                  background:active?C.accent:"transparent",
                  color:active?"#fff":C.textSub,
                }}>
                {len}
              </button>
            );
          })}
        </div>
      </div>

      <button onClick={()=>startQuiz(questionPool,quizLength)} style={{...s.btn("ghost"),width:"100%",marginTop:8,fontSize:12}}>
        <IcoRefresh size={13}/> Nowa sesja
      </button>

      {/* Bottom stats */}
      <div style={{marginTop:"auto",display:"flex",flexDirection:"column",gap:8}}>
        <div style={{height:1,background:C.border,margin:"4px 0"}}/>
        <div style={{display:"flex",justifyContent:"space-between",padding:"0 4px"}}>
          <span style={{fontSize:11,color:C.muted}}>Ukończone próby</span>
          <span style={{fontSize:11,fontWeight:700,color:C.text}}>{uniq.length}</span>
        </div>
        <div style={{display:"flex",justifyContent:"space-between",padding:"0 4px"}}>
          <span style={{fontSize:11,color:C.muted}}>Seria dni</span>
          <span style={{fontSize:11,fontWeight:700,color:C.accent}}>{streak} 🔥</span>
        </div>
      </div>
    </aside>
  );

  // ── STATUS BAR (single, top of content) ─────────────────────────────────────
  const StatusBar = () => (
    <div style={{height:48,background:C.surface,borderBottom:`1px solid ${C.border}`,display:"flex",alignItems:"center",padding:"0 28px",gap:20,flexShrink:0}}>
      {/* Progress bar */}
      <div style={{flex:1,display:"flex",alignItems:"center",gap:12}}>
        <span style={{fontSize:11,fontWeight:700,color:C.muted,whiteSpace:"nowrap"}}>
          {idx+1} / {total}
        </span>
        <div style={{flex:1,height:4,background:C.border,borderRadius:4,overflow:"hidden"}}>
          <div style={{height:"100%",width:`${progressPct}%`,background:`linear-gradient(90deg,${C.accent},${C.accentL})`,borderRadius:4,transition:"width .4s ease"}}/>
        </div>
        <span style={{fontSize:11,fontWeight:700,color:C.muted}}>{progressPct}%</span>
      </div>

      <div style={{width:1,height:20,background:C.border}}/>

      {/* Live accuracy */}
      <div style={{display:"flex",alignItems:"center",gap:14}}>
        <div style={{display:"flex",alignItems:"center",gap:6}}>
          <span style={{fontSize:11,color:C.muted}}>Skuteczność</span>
          <span style={{fontSize:13,fontWeight:800,color:pct>=70?C.green:pct>=50?C.yellow:C.red}}>{pct}%</span>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:5}}>
          <span style={{fontSize:11,fontWeight:600,color:C.green}}>✓ {stats.correctCount}</span>
          <span style={{fontSize:11,color:C.muted}}>/</span>
          <span style={{fontSize:11,fontWeight:600,color:C.red}}>✗ {stats.incorrectCount}</span>
        </div>
      </div>
    </div>
  );

  // ── LAYOUT ───────────────────────────────────────────────────────────────────
  return (
    <>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0;}
        body{background:${C.bg};font-family:'DM Sans','Segoe UI',system-ui,sans-serif;overflow:hidden;}
        button:focus-visible{outline:2px solid ${C.accent};outline-offset:2px;}
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
      `}</style>
      <div style={{width:"100vw",height:"100vh",display:"flex",background:C.bg,overflow:"hidden"}}>
        <Sidebar/>
        <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden"}}>
          <StatusBar/>
          <main style={{flex:1,overflow:"hidden",padding:"24px 28px"}}>
            <div style={{height:"100%",maxWidth:720,margin:"0 auto",overflow:"hidden"}}>
              {activeTab==="quiz"     && <QuizView/>}
              {activeTab==="results"  && <ResultsView/>}
              {activeTab==="calendar" && <CalendarView/>}
              {activeTab==="plan"     && <PlanView/>}
              {activeTab==="settings" && <SettingsView/>}
            </div>
          </main>
        </div>
      </div>
    </>
  );
}

const rootElement = document.getElementById("root");
if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(<React.StrictMode><QuizAbcdApp/></React.StrictMode>);
}