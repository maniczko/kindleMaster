import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";
import * as XLSX from "xlsx";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from "recharts";

// ── icons ─────────────────────────────────────────────────────────────────────
const Icon = ({ d, size = 18 }) => <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>;
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
const IcoTrash    = () => <Icon d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />;
const IcoRefresh  = () => <Icon d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />;
const IcoChat     = () => <Icon d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />;
const IcoLeft     = () => <Icon d="M19 12H5M12 19l-7-7 7-7" />;
const IcoRight    = () => <Icon d="M5 12h14M12 5l7 7-7 7" />;

// ── Supabase config ───────────────────────────────────────────────────────────
const SUPABASE_URL      = "https://ylqloszldyzpeaikweyl.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlscWxvc3psZHl6cGVhaWt3ZXlsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMyNDg2NDUsImV4cCI6MjA4ODgyNDY0NX0.JgwZKn5_ifnoZHViOGb7aED9sZ3MnijeeI66cFhSJaQ";
const SB_ENABLED = SUPABASE_URL.startsWith("https://") && SUPABASE_ANON_KEY.startsWith("eyJ");

const sbH = (prefer = "return=representation") => ({
  "Content-Type": "application/json", apikey: SUPABASE_ANON_KEY, Authorization: `Bearer ${SUPABASE_ANON_KEY}`, Prefer: prefer,
});
async function sbSelect(table, params = "") {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}?${params}`, { headers: sbH() });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function sbUpsert(table, rows) {
  for (let i = 0; i < rows.length; i += 100) {
    const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, { method: "POST", headers: sbH("resolution=merge-duplicates,return=minimal"), body: JSON.stringify(rows.slice(i, i + 100)) });
    if (!r.ok) throw new Error(`Batch ${i/100+1}: ${await r.text()}`);
  }
}
async function sbInsert(table, row) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${table}`, { method: "POST", headers: sbH(), body: JSON.stringify(row) });
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

// ── data parsers ──────────────────────────────────────────────────────────────
const rowToQ = (row,i) => ({ id: row.id??i+1, questionNo: row.question_no??i+1, question: row.question_text, options: { A:row.option_a, B:row.option_b, C:row.option_c, D:row.option_d }, correct: row.correct_answer||null, explanation: row.explanation||"Brak wyjaśnienia.", category: row.category||"General", difficulty: normDiff(row.difficulty||"medium"), sourceType: row.source_type||"database", sourceFile: row.source_file||null });
const qToRow = (q,i) => ({ question_no: q.questionNo??i+1, question_text: q.question, option_a: q.options.A, option_b: q.options.B, option_c: q.options.C, option_d: q.options.D, correct_answer: q.correct||null, explanation: q.explanation||null, category: q.category||"General", difficulty: normDiff(q.difficulty||"medium"), source_type: q.sourceType||"import", source_file: q.sourceFile||null, is_active: true });

function parseRows(rows, sourceFile=null) {
  return (rows||[]).map((row,i) => {
    const q = row.question??row.Question??row.pytanie??row.question_text;
    const a = row.A??row.a??row.optionA??row.option_a; const b = row.B??row.b??row.optionB??row.option_b; const c = row.C??row.c??row.optionC??row.option_c; const d = row.D??row.d??row.optionD??row.option_d;
    const correct = String(row.correct??row.Correct??row.poprawna??row.correct_answer??"").trim().toUpperCase();
    if (!q||!a||!b||!c||!d) return null;
    return { id: row.id??`import-${i+1}`, questionNo: Number(row.questionNo??row.nr??row.question_no??i+1), question: String(q).trim(), options: { A:String(a).trim(), B:String(b).trim(), C:String(c).trim(), D:String(d).trim() }, correct: optionKeys.includes(correct)?correct:null, explanation: String(row.explanation??row.wyjasnienie??"Brak wyjaśnienia.").trim(), category: String(row.category??row.kategoria??"General").trim(), difficulty: normDiff(row.difficulty??row.trudnosc??"medium"), sourceType:"spreadsheet", sourceFile };
  }).filter(Boolean);
}
function parseTxt(text, sourceFile="import.txt") {
  return [...String(text||"").replace(/\r/g,"").matchAll(/Question\s+#(\d+)\s*([\s\S]*?)(?=\nQuestion\s+#\d+|$)/g)].map(m => {
    const no=Number(m[1]), block=m[2].trim();
    const p=block.match(/^([\s\S]*?)\nA\.\s*([\s\S]*?)\nB\.\s*([\s\S]*?)\nC\.\s*([\s\S]*?)\nD\.\s*([\s\S]*?)(?:\nView answer|$)/);
    if (!p) return null;
    return { id:`txt-${no}`, questionNo:no, question: p[1].replace(/\s+/g," ").trim(), options:{ A:p[2].replace(/\s+/g," ").trim(), B:p[3].replace(/\s+/g," ").trim(), C:p[4].replace(/\s+/g," ").trim(), D:p[5].replace(/\s+/g," ").trim() }, correct:null, explanation:"Brak odpowiedzi w pliku źródłowym.", category:"Import", difficulty:"medium", sourceType:"txt_import", sourceFile };
  }).filter(Boolean);
}
function buildCalDays(month) { const start=som(month), fw=(start.getDay()+6)%7; const gs=new Date(start); gs.setDate(start.getDate()-fw); return Array.from({length:42},(_,i)=>{ const d=new Date(gs); d.setDate(gs.getDate()+i); return {date:d,key:dayKey(d.getTime()),inCurrent:d.getMonth()===start.getMonth()}; }); }
function buildPlan(history, weakCat) {
  if (!history.length) return { readiness:"Brak danych", recommendation:"Ukończ kilka prób quizu.", improvements:[], weeklyPlan:[] };
  const l5=history.slice(0,5); const avgAcc=Math.round(l5.reduce((s,a)=>s+a.percent,0)/l5.length); const avgPace=Number((l5.reduce((s,a)=>s+a.avgResponseMs,0)/l5.length/1000).toFixed(1));
  const wm={},sm={}; l5.forEach(a=>{ if(a.weakestCategory) wm[a.weakestCategory]=(wm[a.weakestCategory]||0)+1; if(a.strongestCategory) sm[a.strongestCategory]=(sm[a.strongestCategory]||0)+1; });
  const weak=Object.entries(wm).sort((a,b)=>b[1]-a[1])[0]?.[0]||weakCat?.category||"Mieszane tematy"; const strong=Object.entries(sm).sort((a,b)=>b[1]-a[1])[0]?.[0]||"Wiedza ogólna";
  return { readiness: avgAcc>=85?"Zaawansowany":avgAcc>=65?"Średniozaawansowany":"Buduj podstawy", recommendation: avgAcc>=85?"Gotowy na trudniejsze zestawy.":avgAcc>=65?"Dobra baza — wzmocnij słabe kategorie.":"Skup się najpierw na dokładności.", improvements: [ avgAcc<70?"Zwolnij przy trudnych pytaniach.":"Utrzymaj dokładność.", avgPace>18?"Pracuj nad szybkością na łatwych pytaniach.":"Tempo jest dobre.", `Poświęć czas na: ${weak}.` ], weeklyPlan:[ {day:"Dzień 1", task:`Przejrzyj błędy z ${weak}.`, duration:"25m"}, {day:"Dzień 2", task:`Skupiony quiz z ${weak}.`, duration:"30m"}, {day:"Dzień 3", task:"Mieszane z czasomierzem.", duration:"20m"}, {day:"Dzień 4", task:"Podsumuj zasady.", duration:"20m"}, {day:"Dzień 5", task:`Quiz w ${strong}.`, duration:"25m"}, {day:"Dzień 6", task:"Pełny próbny quiz.", duration:"30m"}, {day:"Dzień 7", task:"Lekki przegląd.", duration:"15m"} ] };
}

const SAMPLES = [
  {id:1,questionNo:1,question:"You are the program manager for your organization. You need to formally define the scope of the new project. Which document is used?",options:{A:"Risk Register",B:"Project Charter",C:"Lessons Learned",D:"Issue Log"},correct:"B",explanation:"Project Charter formalnie autoryzuje projekt i określa jego ramy.",category:"PgMP",difficulty:"medium",sourceType:"sample"},
  {id:2,questionNo:2,question:"Co oznacza skrót VAT w ujęciu księgowym?",options:{A:"Value Added Tax",B:"Variable Asset Transfer",C:"Verified Accounting Tool",D:"Value Allocation Table"},correct:"A",explanation:"VAT to podatek od wartości dodanej.",category:"Finanse",difficulty:"easy",sourceType:"sample"},
  {id:3,questionNo:3,question:"Które zdanie brzmi najbardziej naturalnie w angielskim biznesowym przy przechodzeniu do sedna sprawy?",options:{A:"Let us cut to the chase.",B:"Let us cut to the hunt.",C:"Let us go to the speed.",D:"Let us go to the cut."},correct:"A",explanation:'"Cut to the chase" to powszechny idiom.',category:"Angielski",difficulty:"easy",sourceType:"sample"},
];

// ── app ───────────────────────────────────────────────────────────────────────
export default function QuizAbcdApp() {
  const [questionPool, setQuestionPool] = useState(SAMPLES);
  const [quizLength,   setQuizLength]   = useState(10);
  const [questions,    setQuestions]    = useState(() => SAMPLES.slice(0, 10)); 
  
  const [idx,          setIdx]          = useState(0);
  const [selected,     setSelected]     = useState(null);
  const [answers,      setAnswers]      = useState({});
  const [showResult,   setShowResult]   = useState(false);
  const [startedAt,    setStartedAt]    = useState(()=>Date.now());
  const [qStartedAt,   setQStartedAt]   = useState(()=>Date.now());
  const [finishedAt,   setFinishedAt]   = useState(null);
  
  const [history,      setHistory]      = useState(()=>loadLocal());
  const [importMsg,    setImportMsg]    = useState("Importuj plik CSV lub TXT.");
  const [activeTab,    setActiveTab]    = useState("quiz");
  const [calMonth,     setCalMonth]     = useState(()=>som(new Date()));
  
  const [qStatus,      setQStatus]      = useState("idle");
  const [attStatus,    setAttStatus]    = useState("idle");
  const [chatStatus,   setChatStatus]   = useState("idle");
  const [chatRes,      setChatRes]      = useState("");
  const fileRef = useRef(null);

  const total    = questions.length;
  const current  = questions[idx] || SAMPLES[0];
  const score    = useMemo(()=>Object.values(answers).filter(a=>a.isCorrect).length,[answers]);
  const answeredCount = Object.keys(answers).length;

  // ── start / tasowanie pytań (NAPRAWIONE) ───────────────────────────────────
  const startQuiz = useCallback((customPool, customLength) => {
    // Odczytujemy pule i dlugosc bezpiecznie
    const poolToUse = customPool || questionPool;
    const lengthToUse = customLength || quizLength;
    
    // Tasowanie
    const shuffled = [...poolToUse].sort(() => 0.5 - Math.random());
    const selectedQ = lengthToUse === "all" ? shuffled : shuffled.slice(0, lengthToUse);
    
    // Bezpieczne ustawianie stanu (płaskie wywołania, bez zagnieżdżeń)
    if (customPool) setQuestionPool(customPool);
    setQuestions(selectedQ.length ? selectedQ : poolToUse);
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
  }, [questionPool, quizLength]);

  // ── DB ─────────────────────────────────────────────────────────────────────
  const loadQfromDB = useCallback(async()=>{
    if (!SB_ENABLED){setQStatus("disabled");return;}
    setQStatus("loading");
    try {
      const rows=await sbSelect("quiz_questions","is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length){setQStatus("ok");return;}
      const parsed = rows.map(rowToQ);
      setQuestionPool(parsed); 
      
      // Inicjalizacja bez wywoływania dodatkowych hooków
      const shuffled = [...parsed].sort(() => 0.5 - Math.random());
      setQuestions(shuffled.slice(0, 10));
      setQStatus("ok");
    } catch(e){setQStatus("error");}
  },[]);

  const loadAttempts = useCallback(async()=>{
    if (!SB_ENABLED) return;
    try {
      const rows=await sbSelect("quiz_attempts","order=finished_at.desc&limit=100");
      const mapped=rows.map(r=>({ id:r.attempt_id, finishedAt:new Date(r.finished_at).getTime(), totalQuestions:r.total_questions, score:r.score, percent:r.percent, mastery:r.mastery, avgResponseMs:r.avg_response_ms, totalTimeMs:r.total_time_ms, strongestCategory:r.strongest_category, weakestCategory:r.weakest_category, source:"supabase" }));
      setHistory(prev=>{ const m=dedupe([...mapped,...prev]); saveLocal(m); return m; });
    } catch(e){}
  },[]);

  useEffect(()=>{loadQfromDB();loadAttempts();},[loadQfromDB,loadAttempts]);

  // ── nawigacja pytań ────────────────────────────────────────────────────────
  const handleAnswer = useCallback((key)=>{
    if (selected || showResult) return;
    setSelected(key);
    setAnswers(prev=>({...prev,[current.id]:{questionId:current.id,selected:key,correct:current.correct,isCorrect:current.correct?key===current.correct:false,responseTimeMs:Date.now()-qStartedAt,category:current.category||"General",difficulty:current.difficulty||"medium"}}));
  },[current, qStartedAt, selected, showResult]);

  const next = useCallback(()=>{
    if (idx < total - 1){
      const ni = idx + 1; setIdx(ni); setSelected(answers[questions[ni].id]?.selected ?? null); setQStartedAt(Date.now());
      setChatStatus("idle"); setChatRes("");
    } else {
      setFinishedAt(Date.now()); setShowResult(true); setActiveTab("results");
    }
  },[answers, idx, questions, total]);

  const prev = useCallback(() => {
    if (idx > 0) {
      const ni = idx - 1; setIdx(ni); setSelected(answers[questions[ni].id]?.selected ?? null);
      setChatStatus("idle"); setChatRes("");
    }
  }, [answers, idx, questions]);

  // ── AI ─────────────────────────────────────────────────────────────────────
  const askAI = useCallback(async () => {
    if (chatStatus === 'loading') return;
    setChatStatus('loading');
    setTimeout(() => {
      setChatRes(`Wskazówka AI: Pytanie dotyczy kategorii "${current.category}". Zwróć uwagę na definicje kluczowe w opcjach. Poprawna odpowiedź (${current.correct}) najlepiej odzwierciedla standardowe procedury.`);
      setChatStatus('loaded');
    }, 1000);
  }, [chatStatus, current]);

  // ── Naprawiony import plików ───────────────────────────────────────────────
  const handleImport = useCallback(async(e)=>{
    const file=e.target.files?.[0]; if (!file) return;
    try {
      let parsed=[];
      if (file.name.toLowerCase().endsWith(".txt")) parsed=parseTxt(await file.text(),file.name);
      else { const wb=XLSX.read(await file.arrayBuffer(),{type:"array"}); parsed=parseRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]],{defval:""}),file.name); }
      if (!parsed.length){setImportMsg("Import nieudany.");return;}
      startQuiz(parsed, quizLength);
      setImportMsg(`Zaimportowano ${parsed.length} pytań z ${file.name}.`);
    } catch(err){setImportMsg(`Błąd: ${err.message}`);} finally{e.target.value="";}
  },[quizLength, startQuiz]);

  // ── statystyki ─────────────────────────────────────────────────────────────
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
    return {totalTimeMs,avgResponseMs,correctCount,incorrectCount:list.length-correctCount,mastery,byCat,weakest,strongest,perf:mastery>=90?"Doskonale":mastery>=70?"Dobrze":"Dalej ćwicz"};
  },[answers,questions,startedAt,finishedAt]);

  useEffect(()=>{
    if (!showResult||!finishedAt) return;
    const attempt={ id:`${finishedAt}-${Math.random().toString(36).slice(2,7)}`, finishedAt, totalQuestions:total, score, percent:Math.round((score/Math.max(total,1))*100), mastery:stats.mastery, avgResponseMs:Math.round(stats.avgResponseMs), totalTimeMs:Math.round(stats.totalTimeMs), strongestCategory:stats.strongest?.category||null, weakestCategory:stats.weakest?.category||null, source:"local" };
    setHistory(prev=>{ if (prev.some(a=>a.finishedAt===finishedAt)) return prev; const m=dedupe([attempt,...prev]); saveLocal(m); return m; });
    if (SB_ENABLED) sbInsert("quiz_attempts",{ attempt_id:attempt.id, finished_at:new Date(attempt.finishedAt).toISOString(), total_questions:attempt.totalQuestions, score:attempt.score, percent:attempt.percent, mastery:attempt.mastery, avg_response_ms:attempt.avgResponseMs, total_time_ms:attempt.totalTimeMs, strongest_category:attempt.strongestCategory, weakest_category:attempt.weakestCategory }).then(()=>loadAttempts()).catch(e=>console.error(e));
  },[showResult,finishedAt,score,stats,total,loadAttempts]);

  // ── klawiatura ─────────────────────────────────────────────────────────────
  useEffect(()=>{
    const h=e=>{
      if (e.target?.tagName==="INPUT"||e.target?.tagName==="TEXTAREA") return;
      const k=e.key.toUpperCase();
      if (!showResult&&!selected&&optionKeys.includes(k)){e.preventDefault();handleAnswer(k);return;}
      if (e.key==="Enter"){e.preventDefault();next();return;}
      if (k==="R"){e.preventDefault(); startQuiz(questionPool, quizLength);}
    };
    window.addEventListener("keydown",h); return ()=>window.removeEventListener("keydown",h);
  },[handleAnswer, next, startQuiz, selected, showResult, questionPool, quizLength]);

  const uniq   = useMemo(()=>dedupe(history),[history]);
  const dayMap = useMemo(()=>{ const m={}; uniq.forEach(a=>{const k=dayKey(a.finishedAt);m[k]=(m[k]||0)+1;}); return m; },[uniq]);
  const streak = useMemo(()=>{ let s=0; const c=new Date(); while(dayMap[dayKey(c.getTime())]){s++;c.setDate(c.getDate()-1);} return s; },[dayMap]);
  const plan   = useMemo(()=>buildPlan(uniq,stats.weakest),[uniq,stats.weakest]);
  const calDays= useMemo(()=>buildCalDays(calMonth),[calMonth]);

  const tabs=[
    {id:"quiz", label:"Quiz"}, {id:"calendar", label:"Kalendarz"}, {id:"plan", label:"Plan nauki"},
    ...(showResult?[{id:"results", label:"Wyniki"}]:[]), {id:"settings", label:"Ustawienia"}
  ];

  // ── WIDOKI ─────────────────────────────────────────────────────────────────
  const QuizView = () => (
    <div className="flex flex-col h-full overflow-y-auto pr-2 pb-10">
      <div className="mb-6">
        <div className="flex justify-between text-xs font-semibold text-slate-500 tracking-wider uppercase mb-2">
          <span>Question {idx + 1} of {total}</span>
          <span>{Math.round((idx / Math.max(total, 1)) * 100)}%</span>
        </div>
        <div className="h-2 w-full bg-slate-200 rounded-full overflow-hidden">
          <div className="h-full bg-blue-600 transition-all duration-300" style={{width:`${((idx)/Math.max(total,1))*100}%`}} />
        </div>
      </div>

      <div className="mb-8">
        <div className="text-sm text-slate-500 font-medium mb-3 flex items-center gap-2">
          <span className="bg-slate-200 text-slate-700 px-2 py-0.5 rounded-md text-xs">{current.category || "General"}</span>
          <span>•</span>
          <span className="capitalize">{current.difficulty || "Medium"}</span>
        </div>
        <h2 className="text-[22px] font-semibold text-slate-800 leading-snug">{current.question}</h2>
      </div>

      <div className="space-y-3 flex-1">
        {optionKeys.map(key => {
          const isSel = selected === key;
          const isCorr = current.correct === key;
          const reveal = !!selected;

          let cardClass = "border-slate-200 bg-white hover:border-blue-600 hover:bg-blue-50 text-slate-700";
          let letterClass = "bg-slate-100 text-slate-600";

          if (reveal) {
            if (isCorr) { cardClass = "border-green-600 bg-green-600 text-white shadow-md"; letterClass = "bg-white text-green-700"; } 
            else if (isSel && !isCorr) { cardClass = "border-red-600 bg-red-600 text-white shadow-md"; letterClass = "bg-white text-red-700"; } 
            else { cardClass = "border-slate-200 bg-white opacity-40"; letterClass = "bg-slate-100 text-slate-500"; }
          }

          return (
            <button key={key} onClick={() => handleAnswer(key)} className={`w-full text-left p-4 rounded-xl border-2 transition-all flex items-center gap-4 group ${cardClass}`}>
              <span className={`w-8 h-8 rounded shrink-0 flex items-center justify-center font-bold text-sm transition-colors ${!reveal ? "group-hover:bg-blue-200 group-hover:text-blue-700" : ""} ${letterClass}`}>
                {key}
              </span>
              <span className="text-[15px] font-medium leading-tight">{current.options[key]}</span>
            </button>
          );
        })}
      </div>

      {selected && (
        <div className="mt-6 bg-slate-100 rounded-xl p-4 border border-slate-200">
           <div className="text-sm font-medium text-slate-800 mb-1">{current.correct ? (selected === current.correct ? "✓ Poprawnie." : `✗ Poprawna odpowiedź: ${current.correct}.`) : "Klucz niedostępny."}</div>
           <div className="text-sm text-slate-600 mb-3">{current.explanation}</div>
           
           {chatStatus === 'idle' && (
             <button onClick={askAI} className="inline-flex items-center gap-2 text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors"><IcoChat/> Zapytaj AI o detale</button>
           )}
           {chatStatus === 'loading' && <div className="text-sm text-blue-500 animate-pulse flex items-center gap-2"><IcoChat/> Łączenie z asystentem AI...</div>}
           {chatStatus === 'loaded' && <div className="text-sm bg-blue-50 border border-blue-100 rounded-lg p-3 text-blue-800 mt-2">{chatRes}</div>}
        </div>
      )}

      <div className="mt-8 pt-4 border-t border-slate-200 flex justify-between items-center shrink-0">
        <button onClick={prev} disabled={idx === 0} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-30 transition-colors">
          <IcoLeft/> Previous
        </button>
        <button onClick={next} disabled={!selected} className="flex items-center gap-2 px-6 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors shadow-sm">
          {idx === total - 1 ? "Results" : "Next"} <IcoRight/>
        </button>
      </div>
    </div>
  );

  const ResultsView = () => (
    <div className="h-full overflow-y-auto pr-2 pb-10 space-y-6">
      <h2 className="text-2xl font-bold text-slate-800 flex items-center gap-2"><IcoTrophy/> Ukończono</h2>
      <div className="p-6 bg-white border border-slate-200 rounded-2xl shadow-sm text-center">
        <div className="text-4xl font-bold text-blue-600 mb-2">{Math.round((score/Math.max(total,1))*100)}%</div>
        <div className="text-slate-500 font-medium">{score} poprawnych na {total} pytań</div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        {[ {l:"Tempo", v:fmt(stats.avgResponseMs)}, {l:"Czas", v:fmt(stats.totalTimeMs)}, {l:"Poprawne", v:stats.correctCount}, {l:"Błędne", v:stats.incorrectCount} ].map(c=>(
          <div key={c.l} className="p-4 bg-white border border-slate-200 rounded-xl"><div className="text-sm text-slate-500">{c.l}</div><div className="text-xl font-bold text-slate-800">{c.v}</div></div>
        ))}
      </div>
    </div>
  );

  const CalendarView = () => (
    <div className="h-full overflow-y-auto pr-2 pb-10 space-y-6">
      <div className="grid grid-cols-3 gap-4">
        {[{l:"Dni",v:Object.keys(dayMap).length},{l:"Seria",v:streak},{l:"Próby",v:uniq.length}].map(c=><div key={c.l} className="p-4 bg-white border border-slate-200 rounded-xl text-center"><div className="text-xs text-slate-500">{c.l}</div><div className="text-xl font-bold text-slate-800">{c.v}</div></div>)}
      </div>
      <div className="p-6 bg-white border border-slate-200 rounded-2xl">
        <div className="flex justify-between items-center mb-6"><h3 className="font-bold text-lg">{calMonth.toLocaleString("pl-PL",{month:"long",year:"numeric"})}</h3><div className="flex gap-2"><button onClick={()=>setCalMonth(m=>addM(m,-1))} className="p-2 bg-slate-100 rounded hover:bg-slate-200"><IcoLeft/></button><button onClick={()=>setCalMonth(m=>addM(m,1))} className="p-2 bg-slate-100 rounded hover:bg-slate-200"><IcoRight/></button></div></div>
        <div className="grid grid-cols-7 gap-2 text-center text-xs font-medium text-slate-400 mb-2">{["Pn","Wt","Śr","Cz","Pt","So","Nd"].map(d=><div key={d}>{d}</div>)}</div>
        <div className="grid grid-cols-7 gap-2 text-center text-sm">
          {calDays.map(i=><div key={i.key} className={`aspect-square flex items-center justify-center rounded-lg ${i.inCurrent?"bg-slate-50 text-slate-700":"text-slate-300"} ${dayMap[i.key]?"border-2 border-blue-600 font-bold bg-blue-50 text-blue-700":""}`}>{i.date.getDate()}</div>)}
        </div>
      </div>
    </div>
  );

  const PlanView = () => (
    <div className="h-full overflow-y-auto pr-2 pb-10 space-y-6">
      <div className="p-6 bg-blue-600 text-white rounded-2xl shadow-sm">
        <div className="text-blue-200 text-sm font-medium mb-1">Rekomendacja Systemu</div>
        <div className="text-2xl font-bold">{plan.recommendation}</div>
      </div>
      <div className="space-y-3">
        {plan.weeklyPlan.map(item => (
          <div key={item.day} className="p-4 bg-white border border-slate-200 rounded-xl flex items-center justify-between">
            <div><div className="font-bold text-slate-800">{item.day}</div><div className="text-sm text-slate-600">{item.task}</div></div>
            <div className="text-xs font-semibold bg-slate-100 px-3 py-1 rounded-full text-slate-600">{item.duration}</div>
          </div>
        ))}
      </div>
    </div>
  );

  const SettingsView = () => (
    <div className="h-full overflow-y-auto pr-2 pb-10 space-y-6">
      <div className="p-6 bg-white border border-slate-200 rounded-2xl space-y-4">
        <h3 className="font-bold text-lg text-slate-800 border-b pb-2">Baza Pytań ({questionPool.length})</h3>
        <p className="text-sm text-slate-600">{importMsg}</p>
        <button onClick={()=>fileRef.current?.click()} className="w-full flex justify-center items-center gap-2 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl font-medium transition-colors"><IcoUpload/> Importuj CSV / TXT</button>
        <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt" className="hidden" onChange={handleImport}/>
      </div>

      <div className="p-6 bg-white border border-slate-200 rounded-2xl space-y-4">
        <h3 className="font-bold text-lg text-slate-800 border-b pb-2">Skróty Klawiaturowe</h3>
        <div className="grid grid-cols-2 gap-y-3 text-sm">
          <div className="flex items-center gap-3"><span className="bg-slate-100 border px-2 py-1 rounded text-xs font-bold text-slate-600">A B C D</span> Odpowiedź</div>
          <div className="flex items-center gap-3"><span className="bg-slate-100 border px-2 py-1 rounded text-xs font-bold text-slate-600">Enter</span> Następne</div>
          <div className="flex items-center gap-3"><span className="bg-slate-100 border px-2 py-1 rounded text-xs font-bold text-slate-600">R</span> Restart</div>
        </div>
      </div>

      <div className="p-6 bg-white border border-slate-200 rounded-2xl space-y-4">
        <h3 className="font-bold text-lg text-slate-800 border-b pb-2">Połączenie Supabase</h3>
        <div className="flex items-center gap-2 text-sm font-medium">
          Status: <span className={SB_ENABLED ? "text-green-600 flex items-center gap-1" : "text-slate-400"}>{SB_ENABLED ? "🟢 Connected" : "⚪ Disconnected"}</span>
        </div>
        <p className="text-xs text-slate-500">Aby używać bazy, skonfiguruj zmienne środowiskowe.</p>
      </div>
      
      <button onClick={()=>{localStorage.removeItem(STORAGE_KEY);setHistory([]);}} className="w-full flex justify-center items-center gap-2 py-3 bg-red-50 hover:bg-red-100 text-red-600 rounded-xl font-medium transition-colors"><IcoTrash/> Wyczyść lokalną historię</button>
    </div>
  );

  // ── GŁÓWNY LAYOUT (1 EKRAM) ────────────────────────────────────────────────
  return (
    <div className="h-screen w-full flex flex-col bg-slate-50 font-sans overflow-hidden text-slate-800">
      
      {/* 1. Header (Kompaktowy) */}
      <header className="flex items-center justify-between px-6 py-4 bg-white border-b border-slate-200 shrink-0">
        <div className="font-bold text-xl text-blue-600 tracking-tight">ABCD Quiz App</div>
        
        {/* Navigation Tabs */}
        <nav className="hidden md:flex gap-1">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setActiveTab(t.id)} 
              className={`px-4 py-2 text-sm font-medium transition-colors rounded-lg ${activeTab === t.id ? "bg-slate-100 text-slate-900" : "text-slate-500 hover:text-slate-800 hover:bg-slate-50"}`}>
              {t.label}
            </button>
          ))}
        </nav>

        {/* Minimal Stats in header */}
        <div className="text-xs font-medium text-slate-500 bg-slate-100 px-3 py-1.5 rounded-full hidden sm:block">
          Attempts: <span className="text-slate-800">{uniq.length}</span>
        </div>
      </header>

      {/* Mobile nav fallback */}
      <nav className="flex md:hidden gap-1 p-2 bg-white border-b overflow-x-auto shrink-0">
        {tabs.map(t=><button key={t.id} onClick={()=>setActiveTab(t.id)} className={`px-3 py-1.5 text-xs font-medium whitespace-nowrap rounded-lg ${activeTab===t.id?"bg-slate-100 text-slate-900":"text-slate-500"}`}>{t.label}</button>)}
      </nav>

      {/* 2. Main Content Area */}
      <main className="flex-1 flex overflow-hidden">
        
        {/* Lewa Strona: Aktywny Widok */}
        <div className="flex-1 px-6 pt-6 pb-0 overflow-hidden flex flex-col">
          <div className="max-w-3xl w-full mx-auto flex-1 overflow-hidden">
            {activeTab === "quiz" && <QuizView />}
            {activeTab === "results" && showResult && <ResultsView />}
            {activeTab === "calendar" && <CalendarView />}
            {activeTab === "plan" && <PlanView />}
            {activeTab === "settings" && <SettingsView />}
          </div>
        </div>

        {/* Prawa Strona: Panel Statystyk */}
        {(activeTab === "quiz" || activeTab === "results") && (
          <aside className="hidden lg:flex w-80 bg-white border-l border-slate-200 p-6 flex-col gap-8 shrink-0 overflow-y-auto">
            
            {/* Control Panel */}
            <div>
              <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Session Setup</div>
              <div className="flex flex-wrap gap-2 mb-3">
                {[5, 10, 20, "all"].map(len => (
                  <button key={len} onClick={() => { setQuizLength(len); startQuiz(questionPool, len); }} 
                    className={`px-3 py-1 text-xs font-medium rounded-md border transition-colors ${quizLength === len ? "bg-blue-50 border-blue-600 text-blue-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                    {len === "all" ? "All" : len}
                  </button>
                ))}
              </div>
              <button onClick={() => startQuiz(questionPool, quizLength)} className="w-full py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-medium rounded-lg transition-colors flex justify-center items-center gap-2">
                <IcoRotate/> Restart Quiz
              </button>
            </div>

            {/* Session Stats */}
            <div>
              <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Session Progress</div>
              <div className="text-sm font-medium text-slate-700 flex justify-between mb-1">
                <span>Completed</span>
                <span>{answeredCount} / {total}</span>
              </div>
              <div className="h-2 w-full bg-slate-100 rounded-full mb-6">
                <div className="h-full bg-slate-800 rounded-full transition-all duration-300" style={{width:`${(answeredCount/Math.max(total,1))*100}%`}}></div>
              </div>

              <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Accuracy</div>
              <div className="text-3xl font-bold text-slate-800 mb-6">{answeredCount > 0 ? Math.round((stats.correctCount / answeredCount) * 100) : 0}%</div>

              <div className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Session Stats</div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600 flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-green-500"></span> Correct</span>
                  <span className="font-semibold">{stats.correctCount}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-600 flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-red-500"></span> Wrong</span>
                  <span className="font-semibold">{stats.incorrectCount}</span>
                </div>
              </div>
            </div>

          </aside>
        )}

      </main>
    </div>
  );
}

const rootElement = document.getElementById("root");
if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(<React.StrictMode><QuizAbcdApp /></React.StrictMode>);
}