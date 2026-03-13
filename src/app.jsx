import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import ReactDOM from "react-dom/client";
import * as XLSX from "xlsx";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
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
  IcoUser,
  IcoKey,
  IcoTag,
  IcoLogout,
  IcoEdit,
  IcoImage,
  IcoVolume,
  IcoLink,
  IcoFileText,
  IcoVideo,
  IcoPlay,
  IcoLayers,
  IcoTrash,
  ZenQuizLogo,
} from "./icons";

GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

// ── Config & Helpers ──────────────────────────────────────────────────────────
const STORAGE_KEY = "quiz_abcd_attempts_v6";
const CLOUD_SETTINGS_KEY = "quiz_abcd_cloud_settings_v2";
const SUPABASE_SETTINGS_KEY = "quiz_abcd_supabase_settings_v1";
const AUTH_SESSION_KEY = "quiz_abcd_auth_session_v1";
const UI_SETTINGS_KEY = "quiz_abcd_ui_settings_v1";
const QUESTION_LIBRARY_KEY = "quiz_abcd_questions_v1";
const optionKeys = ["A", "B", "C", "D"];
const diffW = { easy: 1, medium: 1.5, hard: 2 };
const DEFAULT_MODEL = "claude-sonnet-4-5-20250929";
const DEFAULT_DECK_NAME = "General knowledge";
const ALL_DECKS_LABEL = "Wszystkie decki";
const DEFAULT_DECKS = ["English", "PgMP", "Russian", DEFAULT_DECK_NAME];
const QUESTION_TYPES = [
  { id: "single_choice", label: "Jednokrotny wybor" },
  { id: "multi_select", label: "Wielokrotny wybor" },
  { id: "flashcard", label: "Fiszka" },
  { id: "cloze_deletion", label: "Cloze deletion" },
  { id: "type_answer", label: "Type answer" },
];
const QUESTION_TYPE_ALIASES = {
  multiple_choice: "single_choice",
  multiplechoice: "single_choice",
  mcq: "single_choice",
  singlechoice: "single_choice",
  single: "single_choice",
  multi: "multi_select",
  multiple_select: "multi_select",
  multiselect: "multi_select",
  flash_card: "flashcard",
  card: "flashcard",
  cloze: "cloze_deletion",
  typeanswer: "type_answer",
  typed_answer: "type_answer",
  free_response: "type_answer",
  free_text: "type_answer",
};
const CLOZE_PATTERN = /\{\{c\d+::(.*?)(?:::(.*?))?\}\}/gi;
const DEFAULT_SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL || "https://ylqloszldyzpeaikweyl.supabase.co").trim();
const DEFAULT_SUPABASE_ANON_KEY = (
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ||
  import.meta.env.VITE_SUPABASE_ANON_KEY ||
  ""
).trim();
const CLOUD_FUNCTION_NAME = "claude-summary";
const CLOUD_BROWSER_NOTICE =
  "Ta aplikacja działa wyłącznie w przeglądarce, więc Claude jest wołany przez Supabase Edge Function, a nie bezpośrednio z frontendu.";

const isJwtLike = (value) => {
  const parts = String(value || "").trim().split(".");
  return parts.length === 3 && parts.every(Boolean);
};

const normalizeSupabaseUrl = (value) => {
  const raw = String(value || "").trim().replace(/\/+$/, "");
  if (!raw) return "";

  try {
    const parsed = new URL(raw);
    const dashboardMatch = parsed.hostname === "supabase.com" ? parsed.pathname.match(/^\/dashboard\/project\/([^/]+)/i) : null;
    if (dashboardMatch?.[1]) {
      return `https://${dashboardMatch[1]}.supabase.co`;
    }
    return raw;
  } catch {
    return raw;
  }
};

const isValidSupabaseUrl = (value) => {
  try {
    const parsed = new URL(normalizeSupabaseUrl(value));
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
};

const isValidSupabaseKey = (value) => {
  const key = String(value || "").trim();
  return key.startsWith("sb_publishable_") || isJwtLike(key);
};

const looksLikeAnthropicKey = (value) => String(value || "").trim().startsWith("sk-ant-");

const hasSupabaseConfig = (config) => isValidSupabaseUrl(config?.url) && isValidSupabaseKey(config?.apiKey);

const normalizeTagValue = (value) => String(value || "").trim().replace(/^#/, "").replace(/\s*::\s*/g, "::");

const normalizeTags = (value) => {
  const source = Array.isArray(value)
    ? value
    : typeof value === "string"
    ? value.startsWith("{") && value.endsWith("}")
      ? value
          .slice(1, -1)
          .split(",")
          .map((item) => item.replace(/^"|"$/g, ""))
      : value.split(/[,\n;]+/).length > 1
      ? value.split(/[,\n;]+/)
      : value.split(/\s+/)
    : [];

  const unique = new Map();
  source.forEach((item) => {
    const tag = normalizeTagValue(item);
    if (!tag) return;
    const key = tag.toLowerCase();
    if (!unique.has(key)) unique.set(key, tag);
  });

  return [...unique.values()];
};

const mergeTags = (...sets) => normalizeTags(sets.flatMap((set) => normalizeTags(set)));

const getQuestionTags = (question, userTagMap = {}) => mergeTags(question?.tags || [], userTagMap?.[String(question?.id)] || []);

const normalizeDeck = (value, fallback = DEFAULT_DECK_NAME) => {
  const deck = String(value || "").trim();
  return deck || fallback;
};

const inferDeckFromFile = (sourceFile) => {
  const base = String(sourceFile || "")
    .replace(/\.[^.]+$/, "")
    .trim();

  if (!base) return "";
  if (/english/i.test(base)) return "English";
  if (/pgmp/i.test(base)) return "PgMP";
  if (/russian/i.test(base)) return "Russian";
  if (/general/i.test(base)) return DEFAULT_DECK_NAME;
  return "";
};

const resolveDeck = (candidate, category, sourceFile = "") => {
  const explicitDeck = normalizeDeck(candidate, "");
  if (explicitDeck) return explicitDeck;

  const categoryDeck = normalizeDeck(category, "");
  if (DEFAULT_DECKS.some((deck) => deck.toLowerCase() === categoryDeck.toLowerCase())) return categoryDeck;

  const fileDeck = inferDeckFromFile(sourceFile);
  return normalizeDeck(fileDeck, DEFAULT_DECK_NAME);
};

const normalizeQuestionType = (value) => {
  const raw = String(value || "").trim().toLowerCase();
  const type = QUESTION_TYPE_ALIASES[raw] || raw;
  return QUESTION_TYPES.some((item) => item.id === type) ? type : "single_choice";
};

const normalizeRequestedQuestionTypes = (value) =>
  [...new Set((Array.isArray(value) ? value : []).map((item) => normalizeQuestionType(item)).filter(Boolean))];

const isPersistedQuestionId = (value) => !/^(local|import|txt|generated-local)-/i.test(String(value || ""));

const parseTextAnswers = (value) => {
  const source = Array.isArray(value)
    ? value
    : typeof value === "string"
    ? value.split(/\r?\n|;|\|/g)
    : [];

  const unique = new Map();
  source.forEach((item) => {
    const answer = String(item || "").trim();
    if (!answer) return;
    const key = answer.toLowerCase();
    if (!unique.has(key)) unique.set(key, answer);
  });

  return [...unique.values()];
};

const parseAnswerKeys = (value) => {
  if (Array.isArray(value)) {
    return [...new Map(value.map((item) => [String(item || "").trim().toUpperCase(), true])).keys()].filter((item) => optionKeys.includes(item));
  }

  const text = String(value || "").toUpperCase();
  return [...new Set(text.match(/[A-D]/g) || [])];
};

const normalizeOptionMap = (source = {}) =>
  optionKeys.reduce((acc, key) => {
    acc[key] = String(source?.[key] ?? source?.[key.toLowerCase()] ?? "").trim();
    return acc;
  }, {});

const getVisibleOptionKeys = (question) => optionKeys.filter((key) => String(question?.options?.[key] || "").trim());

const extractClozeEntries = (questionText = "") => {
  const matches = [];
  String(questionText || "").replace(CLOZE_PATTERN, (_, answer, hint = "") => {
    matches.push({
      answer: String(answer || "").trim(),
      hint: String(hint || "").trim(),
    });
    return _;
  });
  return matches;
};

const hasClozeTokens = (questionText = "") => extractClozeEntries(questionText).length > 0;

const renderClozePrompt = (questionText = "") => {
  let index = 0;
  return String(questionText || "").replace(CLOZE_PATTERN, (_, __, hint = "") => {
    index += 1;
    return `[${String(hint || `luka ${index}`).trim()}]`;
  });
};

const revealClozeText = (questionText = "") => String(questionText || "").replace(CLOZE_PATTERN, (_, answer) => String(answer || "").trim());

const normalizeComparableText = (value) =>
  String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();

const matchesTypedAnswer = (submitted, accepted = []) => {
  const candidate = normalizeComparableText(submitted);
  if (!candidate) return false;
  return parseTextAnswers(accepted).some((answer) => normalizeComparableText(answer) === candidate);
};

const matchesClozeAnswers = (submitted = [], accepted = []) => {
  const left = (submitted || []).map((item) => normalizeComparableText(item)).filter(Boolean);
  const right = parseTextAnswers(accepted).map((item) => normalizeComparableText(item)).filter(Boolean);
  return left.length === right.length && left.every((item, index) => item === right[index]);
};

const getQuestionDisplayText = (question) =>
  normalizeQuestionType(question?.questionType) === "cloze_deletion" ? renderClozePrompt(question?.question || "") : String(question?.question || "").trim();

const inferQuestionType = ({ questionType, question, options, correctAnswers, answerBack }) => {
  const explicit = String(questionType || "").trim().toLowerCase();
  if (QUESTION_TYPES.some((item) => item.id === explicit)) return explicit;

  const visibleOptions = optionKeys.filter((key) => String(options?.[key] || "").trim());
  const keys = parseAnswerKeys(correctAnswers);
  const textAnswers = parseTextAnswers(correctAnswers);

  if (hasClozeTokens(question)) return "cloze_deletion";
  if (!visibleOptions.length && textAnswers.length) return "type_answer";
  if (visibleOptions.length < 2 && String(answerBack || "").trim()) return "flashcard";
  if (keys.length > 1) return "multi_select";
  return "single_choice";
};

const normalizeCorrectAnswers = ({ questionType, question, correctAnswers, correct, answerBack }) => {
  const type = normalizeQuestionType(questionType);
  if (type === "flashcard") return [];
  if (type === "type_answer") {
    const parsed = parseTextAnswers(correctAnswers);
    return parsed.length ? parsed : parseTextAnswers(answerBack);
  }
  if (type === "cloze_deletion") {
    const parsed = parseTextAnswers(correctAnswers);
    const derived = extractClozeEntries(question).map((item) => item.answer);
    return parsed.length ? parsed : derived;
  }

  const parsed = parseAnswerKeys(correctAnswers);
  const fallback = parseAnswerKeys(correct);
  const result = parsed.length ? parsed : fallback;
  return type === "single_choice" ? result.slice(0, 1) : result;
};

const setsEqual = (left, right) => {
  const a = [...new Set(left || [])].sort();
  const b = [...new Set(right || [])].sort();
  return a.length === b.length && a.every((value, index) => value === b[index]);
};

const questionTypeLabel = (type) => QUESTION_TYPES.find((item) => item.id === normalizeQuestionType(type))?.label || "Jednokrotny wybor";

const formatAnswerKeys = (keys = [], question) => {
  const list = parseAnswerKeys(keys);
  if (!list.length) return "Brak klucza";
  return list
    .map((key) => {
      const text = String(question?.options?.[key] || "").trim();
      return text ? `${key}. ${text}` : key;
    })
    .join(" | ");
};

const formatQuestionAnswer = (question, answer) => {
  const type = normalizeQuestionType(question?.questionType);
  if (type === "flashcard") {
    if (answer === "correct") return "Umiem";
    if (answer === "incorrect") return "Do poprawy";
    return String(question?.answerBack || question?.explanation || "Brak odpowiedzi.").trim();
  }
  if (type === "type_answer") {
    return parseTextAnswers(Array.isArray(answer) ? answer : [answer]).join(" | ") || parseTextAnswers(question?.correctAnswers || []).join(" | ");
  }
  if (type === "cloze_deletion") {
    if (Array.isArray(answer) && answer.length) return answer.map((item) => String(item || "").trim()).filter(Boolean).join(" | ");
    return revealClozeText(question?.question || "") || parseTextAnswers(question?.correctAnswers || []).join(" | ");
  }

  return formatAnswerKeys(Array.isArray(answer) ? answer : [answer], question);
};

const createQuestionRecord = (input = {}, index = 0) => {
  const options = normalizeOptionMap(input.options || {
    A: input.optionA ?? input.option_a,
    B: input.optionB ?? input.option_b,
    C: input.optionC ?? input.option_c,
    D: input.optionD ?? input.option_d,
  });
  const questionText = String(input.question || input.question_text || "").trim();
  const answerBack = String(input.answerBack ?? input.answer_back ?? input.answer ?? input.explanation ?? "").trim();
  const questionType = inferQuestionType({
    questionType: input.questionType ?? input.question_type ?? input.type,
    question: questionText,
    options,
    correctAnswers: input.correctAnswers ?? input.correct_answers ?? input.correct_answer ?? input.correct,
    answerBack,
  });
  const correctAnswers = normalizeCorrectAnswers({
    questionType,
    question: questionText,
    correctAnswers: input.correctAnswers ?? input.correct_answers,
    correct: input.correct ?? input.correct_answer,
    answerBack,
  });
  const normalizedAnswerBack =
    answerBack ||
    (questionType === "cloze_deletion" ? revealClozeText(questionText) : questionType === "type_answer" ? correctAnswers[0] || "" : "");
  const explanation = String(input.explanation ?? input.answerBack ?? input.answer_back ?? "Brak wyjasnienia.").trim() || "Brak wyjasnienia.";
  const imageUrl = String(input.imageUrl ?? input.image_url ?? input.image ?? "").trim();
  const audioUrl = String(input.audioUrl ?? input.audio_url ?? input.audio ?? "").trim();

  return {
    id: input.id ?? `local-${Date.now()}-${index}`,
    questionNo: Number(input.questionNo ?? input.question_no ?? index + 1) || index + 1,
    questionType,
    question: questionText,
    options,
    correct: questionType === "single_choice" ? correctAnswers[0] || null : null,
    correctAnswers,
    answerBack: normalizedAnswerBack,
    explanation,
    imageUrl,
    audioUrl,
    deck: resolveDeck(input.deck || input.deck_name || input.deck_title || input.collection_name, input.category, input.sourceFile || input.source_file),
    category: String(input.category || "General").trim() || "General",
    tags: normalizeTags(input.tags || input.tag_list || input.tag || []),
    difficulty: normDiff(input.difficulty || "medium"),
    sourceType: input.sourceType || input.source_type || "database",
    sourceFile: input.sourceFile || input.source_file || null,
    isActive: input.isActive ?? input.is_active ?? true,
  };
};

const mergeQuestionLibraries = (primary = [], override = []) => {
  const merged = new Map();
  primary.forEach((question, index) => {
    const normalized = createQuestionRecord(question, index);
    merged.set(String(normalized.id), normalized);
  });
  override.forEach((question, index) => {
    const normalized = createQuestionRecord(question, index);
    merged.set(String(normalized.id), normalized);
  });
  return [...merged.values()].sort((a, b) => (a.questionNo || 0) - (b.questionNo || 0));
};

const questionToSupabaseRow = (question) => ({
  question_no: Number(question.questionNo || 1),
  question_text: String(question.question || "").trim(),
  question_type: normalizeQuestionType(question.questionType),
  option_a: String(question.options?.A || "").trim() || null,
  option_b: String(question.options?.B || "").trim() || null,
  option_c: String(question.options?.C || "").trim() || null,
  option_d: String(question.options?.D || "").trim() || null,
  correct_answer: normalizeQuestionType(question.questionType) === "single_choice" ? question.correct || null : null,
  correct_answers: normalizeCorrectAnswers({
    questionType: question.questionType,
    question: question.question,
    correctAnswers: question.correctAnswers,
    correct: question.correct,
    answerBack: question.answerBack,
  }),
  answer_back: String(question.answerBack || "").trim() || null,
  explanation: String(question.explanation || "").trim() || null,
  image_url: String(question.imageUrl || "").trim() || null,
  audio_url: String(question.audioUrl || "").trim() || null,
  deck: normalizeDeck(question.deck),
  category: String(question.category || "General").trim() || "General",
  tags: normalizeTags(question.tags || []),
  difficulty: normDiff(question.difficulty),
  source_type: question.sourceType || "editor",
  is_active: question.isActive !== false,
});

const QuestionMediaBlock = ({ imageUrl, audioUrl, compact = false }) => {
  const safeImageUrl = String(imageUrl || "").trim();
  const safeAudioUrl = String(audioUrl || "").trim();

  if (!safeImageUrl && !safeAudioUrl) return null;

  return (
    <div className={`question-media-stack ${compact ? "compact" : ""}`}>
      {safeImageUrl && (
        <div className="question-media-card">
          <div className="question-media-label">
            <IcoImage size={14} /> Obraz
          </div>
          <img src={safeImageUrl} alt="Ilustracja do pytania" className="question-media-image" loading="lazy" />
        </div>
      )}

      {safeAudioUrl && (
        <div className="question-media-card">
          <div className="question-media-label">
            <IcoVolume size={14} /> Audio
          </div>
          <audio controls preload="none" className="question-media-audio">
            <source src={safeAudioUrl} />
            Twoja przegladarka nie obsluguje audio.
          </audio>
        </div>
      )}
    </div>
  );
};

const filterQuestionsByTags = (questions, activeTags, userTagMap = {}) => {
  const filters = normalizeTags(activeTags).map((tag) => tag.toLowerCase());
  if (!filters.length) return questions || [];

  return (questions || []).filter((question) => {
    const tags = new Set(getQuestionTags(question, userTagMap).map((tag) => tag.toLowerCase()));
    return filters.every((tag) => tags.has(tag));
  });
};

const buildAuthSession = (data) => {
  if (!data?.access_token) return null;

  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token || "",
    token_type: data.token_type || "bearer",
    expires_in: Number(data.expires_in || 0),
    expires_at: Number(data.expires_at || 0),
    user: data.user || null,
  };
};

const sbH = (apiKey, prefer = "return=representation", accessToken = "") => {
  const headers = {
    "Content-Type": "application/json",
    apikey: apiKey,
    Prefer: prefer,
  };

  const bearer = String(accessToken || "").trim() || (isJwtLike(apiKey) ? apiKey : "");
  if (bearer) headers.Authorization = `Bearer ${bearer}`;
  return headers;
};

async function sbSelect(config, table, params = "", accessToken = "") {
  const suffix = params ? `?${params}` : "";
  const r = await fetch(`${config.url}/rest/v1/${table}${suffix}`, { headers: sbH(config.apiKey, "return=representation", accessToken) });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function sbInsert(config, table, row, accessToken = "") {
  const r = await fetch(`${config.url}/rest/v1/${table}`, {
    method: "POST",
    headers: sbH(config.apiKey, "return=representation", accessToken),
    body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

async function sbUpsert(config, table, row, accessToken = "", onConflict = "") {
  const query = onConflict ? `?on_conflict=${encodeURIComponent(onConflict)}` : "";
  const r = await fetch(`${config.url}/rest/v1/${table}${query}`, {
    method: "POST",
    headers: sbH(config.apiKey, "resolution=merge-duplicates,return=representation", accessToken),
    body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

async function sbPatch(config, table, params, row, accessToken = "") {
  const r = await fetch(`${config.url}/rest/v1/${table}?${params}`, {
    method: "PATCH",
    headers: sbH(config.apiKey, "return=representation", accessToken),
    body: JSON.stringify(row),
  });
  if (!r.ok) throw new Error(await r.text());
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

async function authRequest(config, path, { method = "GET", body, accessToken = "" } = {}) {
  const endpoint = `${config.url}/auth/v1/${path}`;
  let r;

  try {
    r = await fetch(endpoint, {
      method,
      headers: sbH(config.apiKey, "return=representation", accessToken),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    const hint = !isValidSupabaseUrl(config?.url)
      ? "Sprawdz Supabase URL."
      : !isValidSupabaseKey(config?.apiKey)
        ? "Sprawdz publishable / anon key."
        : "Sprawdz Supabase URL, publishable / anon key oraz polaczenie sieciowe z projektem.";
    throw new Error(`Nie udalo sie polaczyc z Supabase Auth. ${hint}`);
  }

  const text = await r.text();
  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {}

  if (!r.ok) {
    throw new Error(data?.msg || data?.error_description || data?.error || text || `HTTP ${r.status}`);
  }

  return data || {};
}

async function signUpWithPassword(config, email, password, displayName) {
  return authRequest(config, "signup", {
    method: "POST",
    body: {
      email,
      password,
      data: { display_name: displayName || undefined },
    },
  });
}

async function signInWithPassword(config, email, password) {
  return authRequest(config, "token?grant_type=password", {
    method: "POST",
    body: { email, password },
  });
}

async function refreshAuthSession(config, refreshToken) {
  return authRequest(config, "token?grant_type=refresh_token", {
    method: "POST",
    body: { refresh_token: refreshToken },
  });
}

async function fetchAuthUser(config, accessToken) {
  return authRequest(config, "user", {
    method: "GET",
    accessToken,
  });
}

async function signOutAuth(config, accessToken) {
  return authRequest(config, "logout", {
    method: "POST",
    accessToken,
  });
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
  if (/failed to fetch/i.test(message)) {
    return "Nie udalo sie polaczyc z Supabase. Sprawdz URL projektu, publishable / anon key i polaczenie sieciowe.";
  }
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

const loadQuestionLibrary = () => {
  try {
    return (JSON.parse(localStorage.getItem(QUESTION_LIBRARY_KEY) || "[]") || []).map((item, index) => createQuestionRecord(item, index));
  } catch {
    return [];
  }
};

const saveQuestionLibrary = (list) => {
  try {
    localStorage.setItem(QUESTION_LIBRARY_KEY, JSON.stringify((list || []).map((item, index) => createQuestionRecord(item, index))));
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

const loadUiSettings = () => {
  try {
    return JSON.parse(localStorage.getItem(UI_SETTINGS_KEY) || "{}");
  } catch {
    return {};
  }
};

const saveUiSettings = (settings) => {
  try {
    localStorage.setItem(UI_SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
};

const loadAuthSession = () => {
  try {
    return JSON.parse(localStorage.getItem(AUTH_SESSION_KEY) || "null");
  } catch {
    return null;
  }
};

const saveAuthSession = (session) => {
  try {
    if (!session) localStorage.removeItem(AUTH_SESSION_KEY);
    else localStorage.setItem(AUTH_SESSION_KEY, JSON.stringify(session));
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

const rowToQ = (row, i) =>
  createQuestionRecord(
    {
      id: row.id ?? i + 1,
      questionNo: row.question_no ?? i + 1,
      questionType: row.question_type || row.type,
      question: row.question_text,
      options: { A: row.option_a, B: row.option_b, C: row.option_c, D: row.option_d },
      correct: row.correct_answer || null,
      correctAnswers: row.correct_answers || [],
      answerBack: row.answer_back || "",
      imageUrl: row.image_url || row.image || "",
      audioUrl: row.audio_url || row.audio || "",
      explanation: row.explanation || "Brak wyjasnienia.",
      deck: resolveDeck(row.deck || row.deck_name || row.deck_title || row.collection_name, row.category, row.source_file),
      category: row.category || "General",
      tags: normalizeTags(row.tags || row.tag_list || row.tag || []),
      difficulty: normDiff(row.difficulty || "medium"),
      sourceType: row.source_type || "database",
      sourceFile: row.source_file || null,
      isActive: row.is_active ?? true,
    },
    i
  );

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
        imageUrl: row.image_url ?? row.imageUrl ?? row.image ?? "",
        audioUrl: row.audio_url ?? row.audioUrl ?? row.audio ?? "",
        explanation: String(row.explanation ?? "Brak wyjaśnienia.").trim(),
        deck: resolveDeck(row.deck ?? row.Deck ?? row.deck_name ?? row.deckName ?? row.collection ?? row.Collection, row.category, sourceFile),
        category: String(row.category ?? "General").trim(),
        tags: normalizeTags(row.tags ?? row.Tags ?? row.tag ?? row.Tag ?? row.labels ?? row.Labels ?? []),
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
        deck: resolveDeck("", "", sourceFile),
        category: "Import",
        tags: [],
        difficulty: "medium",
        sourceType: "txt_import",
        sourceFile,
      };
    })
    .filter(Boolean);
}

const parseImportedRows = (rows, sourceFile = null) => {
  return (rows || [])
    .map((row, index) => {
      const questionText = row.question ?? row.Question ?? row.pytanie ?? row.question_text;
      if (!questionText) return null;

      const options = normalizeOptionMap({
        A: row.A ?? row.a ?? row.option_a,
        B: row.B ?? row.b ?? row.option_b,
        C: row.C ?? row.c ?? row.option_c,
        D: row.D ?? row.d ?? row.option_d,
      });
      const answerBack = row.answer_back ?? row.answerBack ?? row.answer ?? row.back ?? "";
      const questionType = inferQuestionType({
        questionType: row.question_type ?? row.QuestionType ?? row.type ?? row.Type,
        question: questionText,
        options,
        correctAnswers: row.correct_answers ?? row.correct ?? row.Correct ?? row.correct_answer,
        answerBack,
      });

      if (!["flashcard", "type_answer", "cloze_deletion"].includes(questionType) && optionKeys.filter((key) => options[key]).length < 2) return null;

      return createQuestionRecord(
        {
          id: `import-${index + 1}`,
          questionNo: Number(row.questionNo ?? row.question_no ?? index + 1),
          questionType,
          question: String(questionText).trim(),
          options,
          correct: row.correct ?? row.Correct ?? row.correct_answer ?? "",
          correctAnswers: row.correct_answers ?? row.correct ?? row.Correct ?? row.correct_answer ?? "",
          answerBack,
          imageUrl: row.image_url ?? row.imageUrl ?? row.image ?? "",
          audioUrl: row.audio_url ?? row.audioUrl ?? row.audio ?? "",
          explanation: String(row.explanation ?? row.Explanation ?? answerBack ?? "Brak wyjasnienia.").trim(),
          deck: resolveDeck(row.deck ?? row.Deck ?? row.deck_name ?? row.deckName ?? row.collection ?? row.Collection, row.category, sourceFile),
          category: String(row.category ?? "General").trim(),
          tags: normalizeTags(row.tags ?? row.Tags ?? row.tag ?? row.Tag ?? row.labels ?? row.Labels ?? []),
          difficulty: normDiff(row.difficulty ?? "medium"),
          sourceType: "spreadsheet",
          sourceFile,
        },
        index
      );
    })
    .filter(Boolean);
};

const parseImportedTxt = (text, sourceFile = "import.txt") =>
  parseTxt(text, sourceFile).map((item, index) =>
    createQuestionRecord(
      {
        ...item,
        questionType: item.questionType || "single_choice",
      },
      index
    )
  );

const MATERIAL_STOPWORDS = new Set([
  "this",
  "that",
  "these",
  "those",
  "with",
  "from",
  "into",
  "about",
  "they",
  "them",
  "their",
  "there",
  "have",
  "will",
  "would",
  "could",
  "should",
  "your",
  "ours",
  "because",
  "przez",
  "ktore",
  "ktory",
  "oraz",
  "oraz",
  "jest",
  "byla",
  "byly",
  "tego",
  "tegoroczny",
  "dla",
  "oraz",
  "that",
  "what",
]);

const splitMaterialIntoSentences = (materialText = "") => {
  const normalized = String(materialText || "")
    .replace(/\r/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!normalized) return [];

  const chunks = normalized
    .split(/(?<=[.!?])\s+|\n+/)
    .map((item) => item.trim())
    .filter((item) => item.length >= 40);

  const unique = new Map();
  chunks.forEach((item) => {
    const key = item.toLowerCase();
    if (!unique.has(key)) unique.set(key, item);
  });

  return [...unique.values()];
};

const pickClozeToken = (sentence = "", used = new Set()) => {
  const tokens = String(sentence || "")
    .match(/[\p{L}\p{N}\-]{4,}/gu)
    ?.map((item) => item.trim())
    .filter((item) => item && !MATERIAL_STOPWORDS.has(item.toLowerCase()))
    .sort((left, right) => right.length - left.length) || [];

  return tokens.find((token) => !used.has(token.toLowerCase())) || tokens[0] || "";
};

const replaceTokenOnce = (sentence = "", token = "", replacement = "_____") => {
  if (!token) return sentence;
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return String(sentence || "").replace(new RegExp(`\\b${escaped}\\b`, "i"), replacement);
};

const estimateQuestionCount = (density) => {
  if (density === "low") return 6;
  if (density === "high") return 16;
  return 10;
};

const buildLocalGeneratedQuestions = ({ materialText, questionTypes, questionCount, deck, sourceName, startQuestionNo = 1 }) => {
  const sentences = splitMaterialIntoSentences(materialText).slice(0, Math.max(questionCount * 2, 12));
  if (!sentences.length) return [];

  const selectedTypes = normalizeRequestedQuestionTypes(questionTypes).length ? normalizeRequestedQuestionTypes(questionTypes) : ["cloze_deletion", "type_answer"];
  const localTypes = selectedTypes.filter((type) => ["cloze_deletion", "type_answer", "flashcard"].includes(type));
  if (localTypes.length !== selectedTypes.length) return [];
  const generationTypes = localTypes.length ? localTypes : ["cloze_deletion", "type_answer"];
  const usedTokens = new Set();
  const generated = [];

  for (let index = 0; index < questionCount; index += 1) {
    const sentence = sentences[index % sentences.length];
    const type = generationTypes[index % generationTypes.length];
    const token = pickClozeToken(sentence, usedTokens);
    if (token) usedTokens.add(token.toLowerCase());

    const base = {
      id: `generated-local-${Date.now()}-${index}`,
      questionNo: startQuestionNo + index,
      deck,
      category: sourceName || "Generator",
      difficulty: index % 4 === 0 ? "hard" : index % 3 === 0 ? "easy" : "medium",
      tags: [`generator::${type}`, "source::material"],
      sourceType: "generator-local",
      sourceFile: sourceName || null,
    };

    if (type === "flashcard") {
      generated.push(
        createQuestionRecord(
          {
            ...base,
            questionType: "flashcard",
            question: sentence,
            answerBack: sentence,
            explanation: "Lokalny fallback wygenerowal fiszke na podstawie materialu.",
          },
          index
        )
      );
      continue;
    }

    if (!token) continue;

    if (type === "type_answer") {
      generated.push(
        createQuestionRecord(
          {
            ...base,
            questionType: "type_answer",
            question: `Uzupelnij brakujace pojecie na podstawie materialu: ${replaceTokenOnce(sentence, token, "_____")}`,
            correctAnswers: [token],
            answerBack: sentence,
            explanation: "Wpisz brakujace pojecie albo zwrot wynikajacy z materialu.",
          },
          index
        )
      );
      continue;
    }

    generated.push(
      createQuestionRecord(
        {
          ...base,
          questionType: "cloze_deletion",
          question: replaceTokenOnce(sentence, token, `{{c1::${token}}}`),
          answerBack: sentence,
          explanation: "Uzupelnij luke zgodnie z kontekstem materialu.",
        },
        index
      )
    );
  }

  return generated.filter(Boolean);
};

const normalizeGeneratedQuestionBatch = ({ questions, defaultDeck, sourceName, startQuestionNo = 1, allowedQuestionTypes = [] }) => {
  const source = Array.isArray(questions) ? questions : [];
  const allowedTypes = normalizeRequestedQuestionTypes(allowedQuestionTypes);

  return source
    .map((question, index) =>
      createQuestionRecord(
        {
          ...question,
          id: question.id || `generated-cloud-${Date.now()}-${index}`,
          questionNo: question.questionNo ?? question.question_no ?? startQuestionNo + index,
          deck: question.deck || defaultDeck,
          category: question.category || sourceName || "Generator",
          tags: mergeTags(question.tags || [], [`generator::${normalizeQuestionType(question.questionType || question.type)}`, "source::material", "cloud::verified"]),
          sourceType: question.sourceType || "generator-cloud",
          sourceFile: question.sourceFile || sourceName || null,
        },
        index
      )
    )
    .filter((question) => {
      const type = normalizeQuestionType(question.questionType);
      const visibleOptions = getVisibleOptionKeys(question);
      const correctKeys = parseAnswerKeys(question.correctAnswers || question.correct);

      if (allowedTypes.length && !allowedTypes.includes(type)) return false;
      if (!question.question) return false;
      if (type === "flashcard") return !visibleOptions.length && Boolean(question.answerBack || question.explanation);
      if (type === "type_answer") return !visibleOptions.length && parseTextAnswers(question.correctAnswers || []).length > 0;
      if (type === "cloze_deletion") return extractClozeEntries(question.question).length > 0;
      if (type === "single_choice") return visibleOptions.length === 4 && correctKeys.length === 1 && correctKeys.every((key) => visibleOptions.includes(key));
      if (type === "multi_select") {
        return visibleOptions.length === 4 && correctKeys.length >= 2 && correctKeys.length <= 3 && correctKeys.every((key) => visibleOptions.includes(key));
      }
      return false;
    });
};

async function extractMaterialTextFromFile(file) {
  const name = String(file?.name || "").toLowerCase();
  if (!file) return { text: "", pageTexts: [], pageCount: 0, kind: "empty" };

  if (name.endsWith(".pdf")) {
    const buffer = await file.arrayBuffer();
    const pdf = await getDocument({ data: buffer }).promise;
    const pageTexts = [];

    for (let pageNo = 1; pageNo <= pdf.numPages; pageNo += 1) {
      const page = await pdf.getPage(pageNo);
      const content = await page.getTextContent();
      const text = (content.items || [])
        .map((item) => String(item?.str || "").trim())
        .filter(Boolean)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim();
      pageTexts.push(text);
    }

    return {
      text: pageTexts.join("\n\n").trim(),
      pageTexts,
      pageCount: pdf.numPages,
      kind: "pdf",
    };
  }

  if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
    const workbook = XLSX.read(await file.arrayBuffer(), { type: "array" });
    return {
      text: workbook.SheetNames.map((sheetName) => XLSX.utils.sheet_to_csv(workbook.Sheets[sheetName])).join("\n\n"),
      pageTexts: [],
      pageCount: 0,
      kind: "spreadsheet",
    };
  }

  if (name.endsWith(".csv") || name.endsWith(".txt") || name.endsWith(".md") || name.endsWith(".json")) {
    return { text: await file.text(), pageTexts: [], pageCount: 0, kind: "text" };
  }

  if (String(file.type || "").startsWith("text/")) {
    return { text: await file.text(), pageTexts: [], pageCount: 0, kind: "text" };
  }

  return { text: "", pageTexts: [], pageCount: 0, kind: "unsupported" };
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

function DeckProgressRing({ progress, size = 28, stroke = 3.5 }) {
  const safeProgress = Math.max(0, Math.min(100, Number(progress || 0)));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (safeProgress / 100) * circumference;

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(220,215,201,.78)" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="#E56767"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={dashOffset}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

function normalizeStudyPlanResponse(data) {
  const recommendation = String(data?.recommendation || "").trim();
  const improvements = Array.isArray(data?.improvements)
    ? data.improvements.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 6)
    : [];
  const weeklyPlan = Array.isArray(data?.weeklyPlan)
    ? data.weeklyPlan
        .map((item) => ({
          day: String(item?.day || "").trim(),
          task: String(item?.task || "").trim(),
          duration: String(item?.duration || "").trim(),
        }))
        .filter((item) => item.day && item.task)
        .slice(0, 7)
    : [];

  if (!recommendation) throw new Error("Cloud function returned invalid study plan");

  return {
    source: "cloud",
    readiness: String(data?.readiness || "Plan AI").trim() || "Plan AI",
    recommendation,
    improvements,
    weeklyPlan,
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

async function invokeCloudFunction({ supabaseConfig, body }) {
  const headers = {
    "Content-Type": "application/json",
    apikey: supabaseConfig.apiKey,
  };

  // Edge Functions typically expect Bearer auth; anon JWT works here.
  headers.Authorization = `Bearer ${supabaseConfig.apiKey}`;

  let res;

  try {
    res = await fetch(`${supabaseConfig.url}/functions/v1/${CLOUD_FUNCTION_NAME}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Nie udało się połączyć z Supabase Edge Function. Sprawdź Supabase URL, deploy funkcji `claude-summary` i połączenie sieciowe.");
  }

  const text = await res.text();
  let data = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {}

  if (!res.ok) {
    const message = data?.error || text || `HTTP ${res.status}`;
    throw new Error(message);
  }

  return data || {};
}

async function fetchCloudTrainingSummary({ supabaseConfig, model, cloudApiKey, attempt, stats, questions, answers }) {
  const wrongQuestions = questions
    .filter((q) => answers[q.id] && !answers[q.id].isCorrect)
    .slice(0, 8)
    .map((q) => ({
      question: q.question,
      questionType: normalizeQuestionType(q.questionType),
      correct: formatQuestionAnswer(q, q.correctAnswers || q.correct),
      selected: formatQuestionAnswer(q, answers[q.id]?.selected),
      category: q.category,
      difficulty: q.difficulty,
    }));

  const data = await invokeCloudFunction({
    supabaseConfig,
    body: {
      action: "training_summary",
      model: model || DEFAULT_MODEL,
      apiKey: cloudApiKey?.trim() || undefined,
      payload: {
        percent: attempt.percent,
        score: attempt.score,
        totalQuestions: attempt.totalQuestions,
        avgResponseMs: attempt.avgResponseMs,
        strongestCategory: stats.strongest?.category || null,
        weakestCategory: stats.weakest?.category || null,
        byCategory: stats.byCat,
        wrongQuestions,
      },
    },
  });

  if (!data?.text) throw new Error("Cloud function returned empty summary");

  return { source: "cloud", title: data.title || "Podsumowanie AI", text: data.text };
}

async function fetchCloudStudyPlan({
  supabaseConfig,
  model,
  cloudApiKey,
  history,
  weakCat,
  strongCat,
  avgResponseMs,
  latestAttempt,
  weekdaySummary,
  weeklySummary,
}) {
  const recentAttempts = [...history]
    .sort((a, b) => b.finishedAt - a.finishedAt)
    .slice(0, 8)
    .map((attempt) => ({
      finishedAt: new Date(attempt.finishedAt).toISOString(),
      percent: attempt.percent,
      mastery: attempt.mastery,
      avgResponseMs: attempt.avgResponseMs,
      totalTimeMs: attempt.totalTimeMs,
      strongestCategory: attempt.strongestCategory || null,
      weakestCategory: attempt.weakestCategory || null,
    }));

  const data = await invokeCloudFunction({
    supabaseConfig,
    body: {
      action: "study_plan",
      model: model || DEFAULT_MODEL,
      apiKey: cloudApiKey?.trim() || undefined,
      payload: {
        recentAttempts,
        weakestCategory: weakCat?.category || null,
        strongestCategory: strongCat?.category || null,
        avgResponseMs: Math.round(avgResponseMs || 0),
        latestAttempt: latestAttempt
          ? {
              percent: latestAttempt.percent,
              mastery: latestAttempt.mastery,
              totalQuestions: latestAttempt.totalQuestions,
              score: latestAttempt.score,
            }
          : null,
        weekdaySummary: (weekdaySummary || []).map((day) => ({
          day: day.day,
          count: day.count,
          avgPercent: day.avgPercent,
        })),
        weeklySummary: (weeklySummary || []).slice(-4).map((week) => ({
          label: week.label,
          count: week.count,
          avgPercent: week.avgPercent,
          avgMastery: week.avgMastery,
          totalTimeMs: week.totalTimeMs,
        })),
      },
    },
  });

  return normalizeStudyPlanResponse(data);
}

async function fetchCloudGeneratedQuestions({
  supabaseConfig,
  model,
  cloudApiKey,
  sourceName,
  materialText,
  questionTypes,
  questionCount,
  language,
  deck,
  startQuestionNo,
}) {
  const data = await invokeCloudFunction({
    supabaseConfig,
    body: {
      action: "generate_questions",
      model: model || DEFAULT_MODEL,
      apiKey: cloudApiKey?.trim() || undefined,
      payload: {
        sourceName,
        materialText,
        questionTypes,
        questionCount,
        language,
        deck,
      },
    },
  });

  return normalizeGeneratedQuestionBatch({
    questions: data?.questions || [],
    defaultDeck: deck,
    sourceName,
    startQuestionNo,
    allowedQuestionTypes: questionTypes,
  });
}

const SAMPLES = [
  {
    id: 1,
    questionNo: 1,
    question: "You are the program manager. You need to formally define the scope of the new project. Which document is used?",
    options: { A: "Risk Register", B: "Project Charter", C: "Lessons Learned", D: "Issue Log" },
    correct: "B",
    explanation: "Project Charter formalnie autoryzuje projekt i określa jego ramy.",
    deck: "PgMP",
    category: "PgMP",
    tags: ["pgmp::scope", "foundations", "charter"],
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
    deck: "PgMP",
    category: "PgMP",
    tags: ["pgmp::execution", "outputs", "tricky"],
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
    deck: "General knowledge",
    category: "Finance",
    tags: ["finance::tax", "definitions", "easy-win"],
    difficulty: "easy",
    sourceType: "sample",
  },
  {
    id: 4,
    questionNo: 4,
    question: "Choose the correct English sentence.",
    options: {
      A: "She go to school every day.",
      B: "She goes to school every day.",
      C: "She going to school every day.",
      D: "She gone to school every day.",
    },
    correct: "B",
    explanation: "W Present Simple dla `she` używamy formy `goes`.",
    deck: "English",
    category: "Grammar",
    tags: ["english::grammar", "present-simple", "basics"],
    difficulty: "easy",
    sourceType: "sample",
  },
  {
    id: 5,
    questionNo: 5,
    question: "What is the Russian word for `book`?",
    options: {
      A: "книга",
      B: "машина",
      C: "окно",
      D: "яблоко",
    },
    correct: "A",
    explanation: "`книга` oznacza `book`.",
    deck: "Russian",
    category: "Vocabulary",
    tags: ["russian::vocabulary", "basics", "reading"],
    difficulty: "easy",
    sourceType: "sample",
  },
  {
    id: 6,
    questionNo: 6,
    questionType: "multi_select",
    question: "Which practices usually improve recall in a spaced-repetition workflow?",
    options: {
      A: "Active recall",
      B: "Spacing reviews over time",
      C: "Reading notes once without testing",
      D: "Short feedback after mistakes",
    },
    correctAnswers: ["A", "B", "D"],
    explanation: "Spaced repetition works best with active recall, spacing and fast feedback loops.",
    deck: "General knowledge",
    category: "Learning",
    tags: ["learning::memory", "multi-select", "study-system"],
    difficulty: "medium",
    sourceType: "sample",
  },
  {
    id: 7,
    questionNo: 7,
    questionType: "flashcard",
    question: "PgMP: What is the main purpose of a Program Charter?",
    answerBack: "To formally authorize the program and define high-level outcomes, scope and governance.",
    explanation: "Use this like an Anki basic card: recall the answer first, then self-grade.",
    deck: "PgMP",
    category: "Foundations",
    tags: ["pgmp::charter", "flashcard", "anki-style"],
    difficulty: "medium",
    sourceType: "sample",
  },
].map((question, index) => createQuestionRecord(question, index));


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
  const initialAuthSession = loadAuthSession();
  const initialUi = loadUiSettings();
  const initialQuestionLibrary = loadQuestionLibrary();

  const [questionPool, setQuestionPool] = useState(() => (initialQuestionLibrary.length ? mergeQuestionLibraries(SAMPLES, initialQuestionLibrary) : SAMPLES));
  const [quizLength, setQuizLength] = useState(10);
  const [questions, setQuestions] = useState(() =>
    (initialQuestionLibrary.length ? mergeQuestionLibraries(SAMPLES, initialQuestionLibrary) : SAMPLES).slice(0, 10)
  );
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
  const [selectedDeck, setSelectedDeck] = useState(() =>
    normalizeDeck(initialUi.selectedDeck || import.meta.env.VITE_SUPABASE_DEFAULT_DECK || ALL_DECKS_LABEL, ALL_DECKS_LABEL)
  );
  const [expandedDecks, setExpandedDecks] = useState(() => ({ PgMP: true }));

  const [calMonth, setCalMonth] = useState(() => som(new Date()));
  const [selectedCalDay, setSelectedCalDay] = useState(() => dayKey(Date.now()));

  const [chatStatus, setChatStatus] = useState("idle");
  const [chatRes, setChatRes] = useState("");

  const [cloudApiEnabled, setCloudApiEnabled] = useState(Boolean(initialCloud.cloudApiEnabled));
  const [cloudModel, setCloudModel] = useState(initialCloud.cloudModel || DEFAULT_MODEL);
  const [cloudApiKeyDraft, setCloudApiKeyDraft] = useState("");
  const [supabaseUrl, setSupabaseUrl] = useState(normalizeSupabaseUrl(initialSupabase.supabaseUrl || DEFAULT_SUPABASE_URL));
  const [supabaseAnonKey, setSupabaseAnonKey] = useState(initialSupabase.supabaseAnonKey || DEFAULT_SUPABASE_ANON_KEY);
  const [authSession, setAuthSession] = useState(() => initialAuthSession);
  const [authUser, setAuthUser] = useState(() => initialAuthSession?.user || null);
  const [profileNameDraft, setProfileNameDraft] = useState(() => initialAuthSession?.user?.user_metadata?.display_name || "");
  const [authMode, setAuthMode] = useState("login");
  const [authEmail, setAuthEmail] = useState(() => initialAuthSession?.user?.email || "");
  const [authPassword, setAuthPassword] = useState("");
  const [authStatus, setAuthStatus] = useState({
    status: "idle",
    message: "Zaloguj się, aby zapisywać konto, wyniki i prywatne tagi w Supabase.",
  });
  const [supabaseCheck, setSupabaseCheck] = useState({ status: "idle", message: "Nie sprawdzono połączenia." });
  const [cloudCheck, setCloudCheck] = useState({ status: "idle", message: "Nie sprawdzono połączenia." });

  const [trainingSummary, setTrainingSummary] = useState(null);
  const [trainingSummaryStatus, setTrainingSummaryStatus] = useState("idle");
  const [studyPlan, setStudyPlan] = useState(null);
  const [studyPlanStatus, setStudyPlanStatus] = useState("idle");
  const [userProfile, setUserProfile] = useState(null);
  const [userTagMap, setUserTagMap] = useState({});
  const [selectedTagFilters, setSelectedTagFilters] = useState([]);
  const [questionTagDraft, setQuestionTagDraft] = useState("");
  const [editorSearch, setEditorSearch] = useState("");
  const [editorDeckFilter, setEditorDeckFilter] = useState("all");
  const [editorSelectedId, setEditorSelectedId] = useState(null);
  const [editorStatus, setEditorStatus] = useState({
    status: "idle",
    message: "Dodawaj i edytuj pytania jak w Anki. Zapis lokalny jest natychmiastowy, a przy poprawnej konfiguracji moze tez trafic do Supabase.",
  });
  const [generatorSourceName, setGeneratorSourceName] = useState("");
  const [generatorSourceText, setGeneratorSourceText] = useState("");
  const [generatorLink, setGeneratorLink] = useState("");
  const [generatorPageTexts, setGeneratorPageTexts] = useState([]);
  const [generatorPageStart, setGeneratorPageStart] = useState(1);
  const [generatorPageEnd, setGeneratorPageEnd] = useState(1);
  const [generatorDensity, setGeneratorDensity] = useState("med");
  const [generatorDeckName, setGeneratorDeckName] = useState(() => (selectedDeck !== ALL_DECKS_LABEL ? selectedDeck : DEFAULT_DECK_NAME));
  const [generatorLanguage, setGeneratorLanguage] = useState("Polish");
  const [generatorQuestionTypes, setGeneratorQuestionTypes] = useState(() => ["single_choice", "type_answer", "cloze_deletion"]);
  const [generatorQuestions, setGeneratorQuestions] = useState([]);
  const [generatorStatus, setGeneratorStatus] = useState({
    status: "idle",
    message: "Dolacz material, wybierz typy pytan i wygeneruj nowa paczke do biblioteki.",
  });
  const [deckLibraryStatus, setDeckLibraryStatus] = useState({
    status: "idle",
    message: "Wybierz deck, aby nim zarzadzac: start, dezaktywacja albo usuniecie z biblioteki.",
  });
  const [tagSaveState, setTagSaveState] = useState({
    status: "idle",
    message: "Tagi działają jak w Anki: możesz przypisać wiele etykiet i budować sesje po tagach.",
  });

  const fileRef = useRef(null);
  const generatorFileRef = useRef(null);

  useEffect(() => {
    saveCloudSettings({ cloudApiEnabled, cloudModel });
  }, [cloudApiEnabled, cloudModel]);

  useEffect(() => {
    saveSupabaseSettings({ supabaseUrl: normalizeSupabaseUrl(supabaseUrl), supabaseAnonKey });
  }, [supabaseUrl, supabaseAnonKey]);

  useEffect(() => {
    saveAuthSession(authSession);
  }, [authSession]);

  useEffect(() => {
    saveUiSettings({ selectedDeck });
  }, [selectedDeck]);

  useEffect(() => {
    saveQuestionLibrary(questionPool);
  }, [questionPool]);

  useEffect(() => {
    setSupabaseCheck({ status: "idle", message: "Nie sprawdzono połączenia." });
  }, [supabaseUrl, supabaseAnonKey]);

  useEffect(() => {
    setCloudCheck({ status: "idle", message: "Nie sprawdzono połączenia." });
  }, [cloudApiEnabled, cloudModel, cloudApiKeyDraft, supabaseUrl, supabaseAnonKey]);

  const supabaseConfig = useMemo(
    () => ({
      url: normalizeSupabaseUrl(supabaseUrl),
      apiKey: supabaseAnonKey.trim(),
    }),
    [supabaseUrl, supabaseAnonKey]
  );
  const sbEnabled = useMemo(() => hasSupabaseConfig(supabaseConfig), [supabaseConfig]);
  const manualCloudApiKey = looksLikeAnthropicKey(cloudApiKeyDraft) ? cloudApiKeyDraft.trim() : "";
  const generatorQuestionCount = useMemo(() => estimateQuestionCount(generatorDensity), [generatorDensity]);
  const generatorMaterialText = useMemo(() => {
    if (!generatorPageTexts.length) return String(generatorSourceText || "").trim();
    const safeStart = Math.max(1, Math.min(generatorPageStart, generatorPageTexts.length));
    const safeEnd = Math.max(safeStart, Math.min(generatorPageEnd, generatorPageTexts.length));
    return generatorPageTexts.slice(safeStart - 1, safeEnd).join("\n\n").trim();
  }, [generatorPageEnd, generatorPageStart, generatorPageTexts, generatorSourceText]);
  const authAccessToken = String(authSession?.access_token || "").trim();
  const activeQuestionPool = useMemo(() => questionPool.filter((question) => question.isActive !== false), [questionPool]);
  const availableDecks = useMemo(() => {
    const list = [...DEFAULT_DECKS, ...activeQuestionPool.map((question) => normalizeDeck(question.deck))];
    return [ALL_DECKS_LABEL, ...new Map(list.map((deck) => [deck.toLowerCase(), deck])).values()];
  }, [activeQuestionPool]);
  const deckQuestionPool = useMemo(
    () =>
      selectedDeck === ALL_DECKS_LABEL
        ? activeQuestionPool
        : activeQuestionPool.filter((question) => normalizeDeck(question.deck) === selectedDeck),
    [activeQuestionPool, selectedDeck]
  );
  const availableTags = useMemo(
    () =>
      mergeTags(
        deckQuestionPool.flatMap((question) => question.tags || []),
        Object.values(userTagMap).flat()
      ).sort((a, b) => a.localeCompare(b, "pl")),
    [deckQuestionPool, userTagMap]
  );
  const filteredQuestionPool = useMemo(
    () => filterQuestionsByTags(deckQuestionPool, selectedTagFilters, userTagMap),
    [deckQuestionPool, selectedTagFilters, userTagMap]
  );

  useEffect(() => {
    if (!availableDecks.includes(selectedDeck)) setSelectedDeck(ALL_DECKS_LABEL);
  }, [availableDecks, selectedDeck]);

  const total = questions.length;
  const current = questions[idx] || SAMPLES[0];
  const currentDeck = normalizeDeck(current?.deck, selectedDeck === ALL_DECKS_LABEL ? DEFAULT_DECK_NAME : selectedDeck);
  const currentQuestionType = normalizeQuestionType(current?.questionType);
  const currentVisibleOptions = useMemo(() => getVisibleOptionKeys(current), [current]);
  const currentClozeEntries = useMemo(() => extractClozeEntries(current?.question || ""), [current]);
  const currentAnswer = answers[current?.id] || null;
  const currentQuestionTags = useMemo(() => getQuestionTags(current, userTagMap), [current, userTagMap]);
  const currentUserTags = useMemo(() => normalizeTags(userTagMap[String(current?.id)] || []), [current, userTagMap]);
  const currentPromptText = useMemo(() => getQuestionDisplayText(current), [current]);
  const answeredCount = Object.keys(answers).length;
  const score = useMemo(() => Object.values(answers).filter((a) => a.isCorrect).length, [answers]);

  const buildSelectionState = useCallback((question, answer = null) => {
    const type = normalizeQuestionType(question?.questionType);
    if (answer) {
      if (type === "multi_select") return Array.isArray(answer.selected) ? answer.selected : [];
      if (type === "cloze_deletion") {
        if (Array.isArray(answer.selected)) return answer.selected;
        return extractClozeEntries(question?.question || "").map(() => "");
      }
      if (type === "type_answer") return String(answer.selected || "");
      return answer.selected ?? null;
    }
    if (type === "multi_select") return [];
    if (type === "cloze_deletion") return extractClozeEntries(question?.question || "").map(() => "");
    if (type === "type_answer") return "";
    return null;
  }, []);

  const createEditorDraft = useCallback(
    (question = null) => {
      if (question) {
        const normalized = createQuestionRecord(question, 0);
        return {
          ...normalized,
          tagsText: normalizeTags(normalized.tags).join(" "),
          acceptedAnswersText: parseTextAnswers(normalized.correctAnswers || []).join("\n"),
        };
      }

      const nextQuestionNo = Math.max(...questionPool.map((item) => Number(item.questionNo || 0)), 0) + 1;
      return {
        ...createQuestionRecord(
          {
            id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            questionNo: nextQuestionNo,
            questionType: "single_choice",
            question: "",
            options: { A: "", B: "", C: "", D: "" },
            correct: "A",
            correctAnswers: ["A"],
            answerBack: "",
            explanation: "",
            deck: selectedDeck !== ALL_DECKS_LABEL ? selectedDeck : DEFAULT_DECK_NAME,
            category: "General",
            tags: [],
            difficulty: "medium",
            sourceType: "editor-local",
          },
          nextQuestionNo
        ),
        tagsText: "",
        acceptedAnswersText: "",
      };
    },
    [questionPool, selectedDeck]
  );

  const editorDecks = useMemo(() => availableDecks.filter((deck) => deck !== ALL_DECKS_LABEL), [availableDecks]);
  const editorFilteredQuestions = useMemo(() => {
    const q = String(editorSearch || "").trim().toLowerCase();
    return questionPool.filter((question) => {
      const deckMatch = editorDeckFilter === "all" || normalizeDeck(question.deck) === editorDeckFilter;
      if (!deckMatch) return false;
      if (!q) return true;
      const haystack = [
        question.question,
        question.category,
        question.deck,
        ...(question.tags || []),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [questionPool, editorSearch, editorDeckFilter]);

  const selectedEditorQuestion = useMemo(
    () => questionPool.find((question) => String(question.id) === String(editorSelectedId)) || null,
    [questionPool, editorSelectedId]
  );
  const [editorDraft, setEditorDraft] = useState(null);
  const editorPreview = useMemo(
    () =>
      editorDraft
        ? createQuestionRecord(
            {
              ...editorDraft,
              tags: editorDraft.tagsText,
            },
            0
          )
        : null,
    [editorDraft]
  );

  useEffect(() => {
    setQuestionTagDraft(currentUserTags.join(" "));
  }, [currentUserTags]);

  useEffect(() => {
    if (!questions.length) return;
    if (idx >= questions.length) {
      setIdx(Math.max(questions.length - 1, 0));
      setSelected(buildSelectionState(questions[Math.max(questions.length - 1, 0)], answers[questions[Math.max(questions.length - 1, 0)]?.id] || null));
    }
  }, [answers, buildSelectionState, idx, questions]);

  useEffect(() => {
    if (!questionPool.length) return;
    if ((!editorSelectedId || !questionPool.some((question) => String(question.id) === String(editorSelectedId))) && !editorDraft) {
      setEditorSelectedId(String(questionPool[0].id));
    }
  }, [questionPool, editorSelectedId, editorDraft]);

  useEffect(() => {
    if (selectedEditorQuestion) {
      setEditorDraft(createEditorDraft(selectedEditorQuestion));
      return;
    }

    setEditorDraft((prev) => prev || createEditorDraft());
  }, [selectedEditorQuestion, createEditorDraft]);

  const upsertUserProfile = useCallback(
    async (user, displayName) => {
      if (!sbEnabled || !authAccessToken || !user?.id) return null;

      const row = {
        id: user.id,
        email: user.email || "",
        display_name: String(displayName || user?.user_metadata?.display_name || user?.email?.split("@")[0] || "").trim() || null,
        updated_at: new Date().toISOString(),
      };

      const result = await sbUpsert(supabaseConfig, "profiles", row, authAccessToken, "id");
      return result?.[0] || row;
    },
    [sbEnabled, supabaseConfig, authAccessToken]
  );

  useEffect(() => {
    if (!authSession) {
      setAuthUser(null);
      setUserProfile(null);
      setUserTagMap({});
      return;
    }

    if (!sbEnabled) return;

    let cancelled = false;

    const restoreUser = async () => {
      try {
        let nextSession = authSession;
        const now = Math.floor(Date.now() / 1000);

        if (authSession?.refresh_token && authSession?.expires_at && authSession.expires_at <= now + 60) {
          const refreshed = buildAuthSession(await refreshAuthSession(supabaseConfig, authSession.refresh_token));
          if (refreshed) {
            nextSession = refreshed;
            if (!cancelled) {
              setAuthSession(refreshed);
              setAuthUser(refreshed.user || null);
            }
          }
        }

        const user = await fetchAuthUser(supabaseConfig, nextSession.access_token);
        if (cancelled) return;

        setAuthUser(user);
        setAuthEmail((prev) => prev || user?.email || "");
        setProfileNameDraft((prev) => prev || user?.user_metadata?.display_name || "");
      } catch (error) {
        if (cancelled) return;

        setAuthSession(null);
        setAuthUser(null);
        setUserProfile(null);
        setUserTagMap({});
        setAuthStatus({
          status: "error",
          message: `Sesja wygasła albo nie udało się odczytać użytkownika: ${getErrorText(error)}`,
        });
      }
    };

    restoreUser();
    return () => {
      cancelled = true;
    };
  }, [authSession, sbEnabled, supabaseConfig]);

  useEffect(() => {
    if (!sbEnabled || !authUser?.id || !authAccessToken) return;

    let cancelled = false;

    const loadAccountData = async () => {
      try {
        const [profileRows, tagRows] = await Promise.all([
          sbSelect(supabaseConfig, "profiles", `select=id,email,display_name,created_at,updated_at&id=eq.${authUser.id}&limit=1`, authAccessToken),
          sbSelect(supabaseConfig, "user_question_tags", `select=question_id,tags&user_id=eq.${authUser.id}&limit=5000`, authAccessToken),
        ]);

        let profile = profileRows?.[0] || null;
        if (!profile) profile = await upsertUserProfile(authUser, authUser?.user_metadata?.display_name || "");
        if (cancelled) return;

        setUserProfile(profile);
        setProfileNameDraft(profile?.display_name || authUser?.user_metadata?.display_name || "");

        const nextTagMap = {};
        (tagRows || []).forEach((row) => {
          nextTagMap[String(row.question_id)] = normalizeTags(row.tags || []);
        });
        setUserTagMap(nextTagMap);
      } catch (error) {
        if (cancelled) return;

        setAuthStatus({
          status: "error",
          message: `Nie udało się załadować profilu albo tagów z bazy. Uruchom migrację SQL i sprawdź RLS. ${getErrorText(error)}`,
        });
      }
    };

    loadAccountData();
    return () => {
      cancelled = true;
    };
  }, [sbEnabled, authUser?.id, authAccessToken, supabaseConfig, upsertUserProfile]);

  const startQuiz = useCallback(
    (customPool, customLength) => {
      const pool = customPool || filteredQuestionPool;
      if (!pool.length) {
        setTagSaveState({
          status: "error",
          message: "Brak pytań dla aktywnych tagów. Usuń filtr albo dodaj tagi do pytań.",
        });
        return;
      }

      const len = customLength !== undefined ? customLength : quizLength;
      const shuffled = [...pool].sort(() => 0.5 - Math.random());
      const selectedQuestions = len === "all" ? shuffled : shuffled.slice(0, len);
      const nextQuestions = selectedQuestions.length ? selectedQuestions : pool;

      setQuestions(nextQuestions);
      setIdx(0);
      setSelected(buildSelectionState(nextQuestions[0], null));
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
      setTagSaveState((prev) =>
        prev.status === "error"
          ? { status: "idle", message: "Tagi działają jak w Anki: możesz przypisać wiele etykiet i budować sesje po tagach." }
          : prev
      );
    },
    [filteredQuestionPool, quizLength, buildSelectionState]
  );

  const getDeckPool = useCallback(
    (deckName, categoryName = "") => {
      const scopedPool = activeQuestionPool.filter((question) => {
        const matchesDeck = normalizeDeck(question.deck) === deckName;
        const matchesCategory = !categoryName || String(question.category || "Bez kategorii") === categoryName;
        return matchesDeck && matchesCategory;
      });

      return filterQuestionsByTags(scopedPool, selectedTagFilters, userTagMap);
    },
    [activeQuestionPool, selectedTagFilters, userTagMap]
  );

  const loadQfromDB = useCallback(async () => {
    if (!sbEnabled) return;
    try {
      const rows = await sbSelect(supabaseConfig, "quiz_questions", "is_active=eq.true&order=question_no.asc&limit=5000");
      if (!rows.length) return;
      const parsed = rows.map(rowToQ);
      const merged = mergeQuestionLibraries(parsed, loadQuestionLibrary());
      setQuestionPool(merged);
      const shuffled = [...merged].sort(() => 0.5 - Math.random());
      setQuestions(shuffled.slice(0, quizLength === "all" ? shuffled.length : quizLength));
    } catch {}
  }, [quizLength, sbEnabled, supabaseConfig]);

  const loadAttempts = useCallback(async () => {
    if (!sbEnabled || !authAccessToken || !authUser?.id) return;
    try {
      const rows = await sbSelect(
        supabaseConfig,
        "quiz_attempts",
        `user_id=eq.${authUser.id}&order=finished_at.desc&limit=100`,
        authAccessToken
      );
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
  }, [sbEnabled, supabaseConfig, authAccessToken, authUser?.id]);

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

    if (cloudApiKeyDraft.trim() && isJwtLike(cloudApiKeyDraft.trim())) {
      setCloudCheck({
        status: "error",
        message: "To pole oczekuje klucza Anthropic `sk-ant-...`. Wklejony ciąg `eyJ...` wygląda jak Supabase anon JWT i powinien trafić do pola `Publishable / anon key` poniżej.",
      });
      return;
    }

    if (cloudApiKeyDraft.trim() && !looksLikeAnthropicKey(cloudApiKeyDraft.trim())) {
      setCloudCheck({
        status: "error",
        message: "Cloud API key musi mieć format Anthropic `sk-ant-...`. Jeśli chcesz użyć klucza Supabase, wklej go w pole `Publishable / anon key`.",
      });
      return;
    }

    if (!sbEnabled) {
      setCloudCheck({ status: "error", message: "Najpierw skonfiguruj poprawnie Supabase URL i klucz anon." });
      return;
    }

    setCloudCheck({ status: "loading", message: "Sprawdzam Cloud AI przez Supabase Edge Function..." });

    try {
      const result = await invokeCloudFunction({
        supabaseConfig,
        body: {
          action: "health",
          model: cloudModel.trim() || DEFAULT_MODEL,
          apiKey: manualCloudApiKey || undefined,
        },
      });

      setCloudCheck({
        status: "success",
        message: result?.message || "Edge Function odpowiedziała poprawnie i ma dostęp do sekretu Anthropic.",
      });
    } catch (error) {
      const hint =
        !isJwtLike(supabaseConfig.apiKey) && String(error?.message || "").includes("JWT")
          ? " Do wywołania funkcji użyj pełnego anon JWT albo skonfiguruj funkcję inaczej."
          : "";
      setCloudCheck({ status: "error", message: `${getErrorText(error)}${hint}` });
    }
  }, [cloudApiEnabled, sbEnabled, supabaseConfig, cloudModel, cloudApiKeyDraft, manualCloudApiKey]);

  const handleAuthSubmit = useCallback(async () => {
    if (!sbEnabled) {
      setAuthStatus({
        status: "error",
        message: "Najpierw ustaw poprawny Supabase URL i publishable / anon key.",
      });
      return;
    }

    const email = String(authEmail || "").trim().toLowerCase();
    const password = String(authPassword || "");
    const displayName = String(profileNameDraft || "").trim();

    if (!email.includes("@")) {
      setAuthStatus({ status: "error", message: "Podaj poprawny adres e-mail." });
      return;
    }

    if (password.length < 6) {
      setAuthStatus({ status: "error", message: "Hasło musi mieć co najmniej 6 znaków." });
      return;
    }

    setAuthStatus({
      status: "loading",
      message: authMode === "register" ? "Tworzę konto w Supabase..." : "Loguję użytkownika...",
    });

    try {
      const data =
        authMode === "register"
          ? await signUpWithPassword(supabaseConfig, email, password, displayName)
          : await signInWithPassword(supabaseConfig, email, password);

      const session = buildAuthSession(data);
      const nextUser = data?.user || session?.user || null;

      if (session) {
        setAuthSession(session);
        setAuthUser(nextUser);
      } else {
        setAuthSession(null);
        setAuthUser(null);
      }

      setAuthEmail(email);
      setAuthPassword("");

      if (session && nextUser) {
        const profile = await upsertUserProfile(nextUser, displayName);
        setUserProfile(profile);
        setProfileNameDraft(profile?.display_name || displayName);
      }

      setAuthStatus({
        status: "success",
        message:
          session && nextUser
            ? authMode === "register"
              ? "Konto gotowe. Profil i sesja zostały zapisane."
              : "Zalogowano. Profil i tagi zostały zsynchronizowane."
            : "Konto utworzone. Jeśli w projekcie jest włączone potwierdzenie e-mail, potwierdź adres i zaloguj się ponownie.",
      });
    } catch (error) {
      setAuthStatus({ status: "error", message: getErrorText(error) });
    }
  }, [sbEnabled, authEmail, authPassword, profileNameDraft, authMode, supabaseConfig, upsertUserProfile]);

  const handleProfileSave = useCallback(async () => {
    if (!sbEnabled || !authUser?.id || !authAccessToken) {
      setAuthStatus({ status: "error", message: "Zaloguj się, aby zapisać profil." });
      return;
    }

    setAuthStatus({ status: "loading", message: "Zapisuję profil użytkownika..." });

    try {
      const profile = await upsertUserProfile(authUser, profileNameDraft);
      setUserProfile(profile);
      setProfileNameDraft(profile?.display_name || "");
      setAuthStatus({ status: "success", message: "Profil zapisany w tabeli `profiles`." });
    } catch (error) {
      setAuthStatus({ status: "error", message: getErrorText(error) });
    }
  }, [sbEnabled, authUser, authAccessToken, upsertUserProfile, profileNameDraft]);

  const handleSignOut = useCallback(async () => {
    if (sbEnabled && authAccessToken) {
      try {
        await signOutAuth(supabaseConfig, authAccessToken);
      } catch {}
    }

    setAuthSession(null);
    setAuthUser(null);
    setUserProfile(null);
    setUserTagMap({});
    setAuthPassword("");
    setAuthStatus({ status: "success", message: "Wylogowano z aplikacji." });
  }, [sbEnabled, authAccessToken, supabaseConfig]);

  const toggleTagFilter = useCallback((tag) => {
    const normalized = normalizeTagValue(tag);
    if (!normalized) return;

    setSelectedTagFilters((prev) => {
      const exists = prev.some((item) => item.toLowerCase() === normalized.toLowerCase());
      return exists ? prev.filter((item) => item.toLowerCase() !== normalized.toLowerCase()) : [...prev, normalized];
    });
  }, []);

  const saveQuestionTags = useCallback(async () => {
    if (!sbEnabled || !authUser?.id || !authAccessToken) {
      setTagSaveState({ status: "error", message: "Zaloguj się, aby zapisywać prywatne tagi do pytań." });
      return;
    }

    const tags = normalizeTags(questionTagDraft);

    setTagSaveState({ status: "loading", message: "Zapisuję tagi pytania w Supabase..." });

    try {
      await sbUpsert(
        supabaseConfig,
        "user_question_tags",
        {
          user_id: authUser.id,
          question_id: String(current.id),
          tags,
          updated_at: new Date().toISOString(),
        },
        authAccessToken,
        "user_id,question_id"
      );

      setUserTagMap((prev) => ({
        ...prev,
        [String(current.id)]: tags,
      }));

      setTagSaveState({
        status: "success",
        message: tags.length ? "Tagi zapisane. Nowe sesje uwzględnią ten zestaw." : "Tagi wyczyszczone dla tego pytania.",
      });
    } catch (error) {
      setTagSaveState({ status: "error", message: getErrorText(error) });
    }
  }, [sbEnabled, authUser?.id, authAccessToken, questionTagDraft, supabaseConfig, current.id]);

  const commitAnswer = useCallback(
    (selectedValue, isCorrect) => {
      setSelected(selectedValue);
      setAnswers((prev) => ({
        ...prev,
        [current.id]: {
          questionId: current.id,
          selected: selectedValue,
          correct: current.correct,
          correctAnswers: current.correctAnswers || [],
          isCorrect,
          responseTimeMs: Date.now() - qStartedAt,
          category: current.category || "General",
          difficulty: current.difficulty || "medium",
          questionType: currentQuestionType,
        },
      }));
    },
    [current, currentQuestionType, qStartedAt]
  );

  const handleAnswer = useCallback(
    (key) => {
      if (currentQuestionType !== "single_choice" || currentAnswer || showResult) return;
      const answerKey = String(key || "").toUpperCase();
      if (!currentVisibleOptions.includes(answerKey)) return;
      commitAnswer(answerKey, current.correct ? answerKey === current.correct : false);
    },
    [commitAnswer, current.correct, currentAnswer, currentQuestionType, currentVisibleOptions, showResult]
  );

  const toggleMultiSelectChoice = useCallback(
    (key) => {
      if (currentQuestionType !== "multi_select" || currentAnswer || showResult) return;
      const answerKey = String(key || "").toUpperCase();
      if (!currentVisibleOptions.includes(answerKey)) return;
      setSelected((prev) => {
        const currentSelection = Array.isArray(prev) ? prev : [];
        return currentSelection.includes(answerKey)
          ? currentSelection.filter((item) => item !== answerKey)
          : [...currentSelection, answerKey];
      });
    },
    [currentAnswer, currentQuestionType, currentVisibleOptions, showResult]
  );

  const submitMultiSelectAnswer = useCallback(() => {
    if (currentQuestionType !== "multi_select" || currentAnswer || showResult) return;
    const chosen = parseAnswerKeys(Array.isArray(selected) ? selected : []);
    if (!chosen.length) return;
    commitAnswer(chosen, setsEqual(chosen, current.correctAnswers || []));
  }, [commitAnswer, current.correctAnswers, currentAnswer, currentQuestionType, selected, showResult]);

  const revealFlashcard = useCallback(() => {
    if (currentQuestionType !== "flashcard" || currentAnswer || showResult) return;
    setSelected("__revealed__");
  }, [currentAnswer, currentQuestionType, showResult]);

  const gradeFlashcard = useCallback(
    (isCorrect) => {
      if (currentQuestionType !== "flashcard" || currentAnswer || showResult) return;
      commitAnswer(isCorrect ? "correct" : "incorrect", Boolean(isCorrect));
    },
    [commitAnswer, currentAnswer, currentQuestionType, showResult]
  );

  const updateTypeAnswerDraft = useCallback(
    (value) => {
      if (currentQuestionType !== "type_answer" || currentAnswer || showResult) return;
      setSelected(String(value || ""));
    },
    [currentAnswer, currentQuestionType, showResult]
  );

  const submitTypeAnswer = useCallback(() => {
    if (currentQuestionType !== "type_answer" || currentAnswer || showResult) return;
    const draft = String(selected || "").trim();
    if (!draft) return;
    commitAnswer(draft, matchesTypedAnswer(draft, current.correctAnswers || []));
  }, [commitAnswer, current.correctAnswers, currentAnswer, currentQuestionType, selected, showResult]);

  const updateClozeDraft = useCallback(
    (blankIndex, value) => {
      if (currentQuestionType !== "cloze_deletion" || currentAnswer || showResult) return;
      setSelected((prev) => {
        const base =
          Array.isArray(prev) && prev.length === currentClozeEntries.length ? [...prev] : currentClozeEntries.map(() => "");
        base[blankIndex] = String(value || "");
        return base;
      });
    },
    [currentAnswer, currentClozeEntries, currentQuestionType, showResult]
  );

  const submitClozeAnswer = useCallback(() => {
    if (currentQuestionType !== "cloze_deletion" || currentAnswer || showResult) return;
    const draft =
      Array.isArray(selected) && selected.length === currentClozeEntries.length ? selected.map((item) => String(item || "").trim()) : currentClozeEntries.map(() => "");
    if (!draft.length || draft.some((item) => !item)) return;
    commitAnswer(draft, matchesClozeAnswers(draft, current.correctAnswers || []));
  }, [commitAnswer, current.correctAnswers, currentAnswer, currentClozeEntries, currentQuestionType, selected, showResult]);

  const next = useCallback(() => {
    if (idx < total - 1) {
      const ni = idx + 1;
      setIdx(ni);
      setSelected(buildSelectionState(questions[ni], answers[questions[ni].id] || null));
      setQStartedAt(Date.now());
      setChatStatus("idle");
      setChatRes("");
    } else {
      setFinishedAt(Date.now());
      setShowResult(true);
      setActiveTab("results");
    }
  }, [answers, idx, questions, total, buildSelectionState]);

  const prev = useCallback(() => {
    if (idx > 0) {
      const ni = idx - 1;
      setIdx(ni);
      setSelected(buildSelectionState(questions[ni], answers[questions[ni].id] || null));
      setChatStatus("idle");
      setChatRes("");
    }
  }, [answers, idx, questions, buildSelectionState]);

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

    if (sbEnabled && authUser?.id && authAccessToken) {
      sbInsert(
        supabaseConfig,
        "quiz_attempts",
        {
          attempt_id: attemptDraft.id,
          user_id: authUser.id,
          finished_at: new Date(attemptDraft.finishedAt).toISOString(),
          total_questions: attemptDraft.totalQuestions,
          score: attemptDraft.score,
          percent: attemptDraft.percent,
          mastery: attemptDraft.mastery,
          avg_response_ms: attemptDraft.avgResponseMs,
          total_time_ms: attemptDraft.totalTimeMs,
          strongest_category: attemptDraft.strongestCategory,
          weakest_category: attemptDraft.weakestCategory,
        },
        authAccessToken
      )
        .then(() => loadAttempts())
        .catch(() => {});
    }
  }, [attemptDraft, loadAttempts, sbEnabled, supabaseConfig, authUser?.id, authAccessToken]);

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

      if (!cloudApiEnabled || !sbEnabled) {
        if (!cancelled) {
          setTrainingSummary(local);
          setTrainingSummaryStatus("done");
        }
        return;
      }

      try {
        const cloud = await fetchCloudTrainingSummary({
          supabaseConfig,
          model: cloudModel.trim() || DEFAULT_MODEL,
          cloudApiKey: manualCloudApiKey,
          attempt: attemptDraft,
          stats,
          questions,
          answers,
        });

        if (!cancelled) {
          setTrainingSummary(cloud);
          setTrainingSummaryStatus("done");
        }
      } catch {
        if (!cancelled) {
          setTrainingSummary({
            ...local,
            text: `${local.text}\n\nCloud AI jest włączone, ale Edge Function nie zwróciła odpowiedzi. Sprawdź sekret ANTHROPIC_API_KEY oraz deploy funkcji w Supabase.`,
          });
          setTrainingSummaryStatus("done");
        }
      }
    };

    runSummary();
    return () => {
      cancelled = true;
    };
  }, [attemptDraft, cloudApiEnabled, cloudModel, cloudApiKeyDraft, manualCloudApiKey, stats, questions, answers, sbEnabled, supabaseConfig]);

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

  const askAIEnhanced = useCallback(async () => {
    if (chatStatus === "loading") return;
    setChatStatus("loading");

    setTimeout(() => {
      setChatRes(
        currentQuestionType === "flashcard"
          ? `Kategoria: "${current.category}". Najpierw odpowiedz z pamieci, a potem porownaj swoja odpowiedz z wzorcem: ${current.answerBack || current.explanation}.`
          : `Kategoria: "${current.category}". Najpierw porownaj pojecia kluczowe w odpowiedziach, potem odrzuc zbyt ogolne lub zbyt waskie opcje. Poprawny wzorzec to ${formatQuestionAnswer(current, current.correctAnswers || current.correct)}.`
      );
      setChatStatus("loaded");
    }, 700);
  }, [chatStatus, current, currentQuestionType]);

  const handleImport = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;

      try {
        let parsed = [];
        if (file.name.toLowerCase().endsWith(".txt")) {
          parsed = parseImportedTxt(await file.text(), file.name);
        } else {
          const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
          parsed = parseImportedRows(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { defval: "" }), file.name);
        }

        if (!parsed.length) {
          setImportMsg("Import nieudany – sprawdź format pliku.");
          return;
        }

        setQuestionPool(parsed);
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

  const toggleGeneratorQuestionType = useCallback((type) => {
    setGeneratorQuestionTypes((prev) => {
      const exists = prev.includes(type);
      if (exists) return prev.filter((item) => item !== type);
      return [...prev, type];
    });
  }, []);

  const handleGeneratorFile = useCallback(async (file) => {
    if (!file) return;

    setGeneratorStatus({
      status: "loading",
      message: `Czytam material z pliku "${file.name}"...`,
    });

    try {
      const payload = await extractMaterialTextFromFile(file);
      const text = String(payload.text || "").trim();
      setGeneratorSourceName(file.name);
      setGeneratorSourceText(text);
      setGeneratorPageTexts(payload.pageTexts || []);
      setGeneratorPageStart(1);
      setGeneratorPageEnd(payload.pageCount || 1);
      if (!text.trim()) {
        setGeneratorStatus({
          status: "error",
          message: "Ten typ pliku nadal wymaga backendowego parsera. W tej wersji frontend czyta juz PDF, txt, csv, json i xlsx.",
        });
        return;
      }

      setGeneratorStatus({
        status: "success",
        message:
          payload.kind === "pdf"
            ? `PDF zaladowany. Odczytano ${payload.pageCount} stron i ${text.trim().length} znakow z "${file.name}".`
            : `Material zaladowany. Odczytano ${text.trim().length} znakow z "${file.name}".`,
      });
      setActiveTab("generator");
    } catch (error) {
      setGeneratorStatus({ status: "error", message: getErrorText(error) });
    }
  }, []);

  const handleGeneratorFileChange = useCallback(
    async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      await handleGeneratorFile(file);
      e.target.value = "";
    },
    [handleGeneratorFile]
  );

  const generateQuestionsFromMaterial = useCallback(async () => {
    const materialText = String(generatorMaterialText || "").trim();
    const sourceName = String(generatorSourceName || generatorLink || "Material").trim();
    const deck = normalizeDeck(generatorDeckName, DEFAULT_DECK_NAME);
    const startQuestionNo = Math.max(...questionPool.map((item) => Number(item.questionNo || 0)), 0) + 1;
    const requestedQuestionTypes = normalizeRequestedQuestionTypes(generatorQuestionTypes);
    const localSupportedTypes = ["cloze_deletion", "type_answer", "flashcard"];
    const canUseLocalFallback = requestedQuestionTypes.length > 0 && requestedQuestionTypes.every((type) => localSupportedTypes.includes(type));

    if (!materialText) {
      setGeneratorStatus({
        status: "error",
        message: "Najpierw wgraj plik tekstowy albo wklej material do generatora.",
      });
      return;
    }

    if (!requestedQuestionTypes.length) {
      setGeneratorStatus({
        status: "error",
        message: "Wybierz przynajmniej jeden typ pytania do wygenerowania.",
      });
      return;
    }

    setGeneratorStatus({
      status: "loading",
      message:
        cloudApiEnabled && sbEnabled
          ? "Cloud analizuje material, wybiera najwazniejsze tresci i przygotowuje pytania..."
          : "Generuje pytania z materialu...",
    });

    let generated = [];
    let usedFallback = false;
    let cloudError = "";
    const attemptedCloud = cloudApiEnabled && sbEnabled;

    if (attemptedCloud) {
      try {
        generated = await fetchCloudGeneratedQuestions({
          supabaseConfig,
          model: cloudModel.trim() || DEFAULT_MODEL,
          cloudApiKey: manualCloudApiKey,
          sourceName,
          materialText,
          questionTypes: requestedQuestionTypes,
          questionCount: generatorQuestionCount,
          language: generatorLanguage,
          deck,
          startQuestionNo,
        });
      } catch (error) {
        usedFallback = true;
        cloudError = getErrorText(error);
      }
    } else {
      usedFallback = true;
    }

    if (!generated.length && canUseLocalFallback) {
      generated = buildLocalGeneratedQuestions({
        materialText,
        questionTypes: requestedQuestionTypes,
        questionCount: generatorQuestionCount,
        deck,
        sourceName,
        startQuestionNo,
      });
      usedFallback = true;
    }

    if (!generated.length) {
      setGeneratorStatus({
        status: "error",
        message: canUseLocalFallback
          ? "Nie udalo sie wygenerowac pytan z tego materialu. Sprobuj krotszy, bardziej tekstowy dokument."
          : "Wybrane typy pytan wymagaja generowania przez Cloud. Sprawdz Cloud AI albo wybierz fiszki, cloze albo type answer.",
      });
      return;
    }

    setGeneratorQuestions(generated);
    setQuestionPool((prev) => mergeQuestionLibraries(prev, generated));
    setGeneratorStatus({
      status: "success",
      message: !usedFallback
        ? `Cloud przeanalizowal material, wybral najwazniejsze tresci i przygotowal ${generated.length} pytan.`
        : attemptedCloud
          ? `Cloud nie zwrocil kompletnego zestawu (${cloudError || "brak odpowiedzi"}). Wygenerowano lokalnie ${generated.length} pytan bez pelnej weryfikacji merytorycznej.`
          : `Wygenerowano lokalnie ${generated.length} pytan. Bez Cloud walidacja merytoryczna jest ograniczona.`,
    });
  }, [
    cloudApiEnabled,
    cloudModel,
    generatorDeckName,
    generatorLanguage,
    generatorLink,
    generatorMaterialText,
    generatorPageEnd,
    generatorPageStart,
    generatorQuestionCount,
    generatorQuestionTypes,
    generatorPageTexts,
    generatorSourceName,
    manualCloudApiKey,
    questionPool,
    sbEnabled,
    supabaseConfig,
  ]);

  useEffect(() => {
    const h = (e) => {
      if (e.target?.tagName === "INPUT" || e.target?.tagName === "TEXTAREA" || e.target?.tagName === "SELECT") return;

      const k = e.key.toUpperCase();

      if (!showResult && !currentAnswer && optionKeys.includes(k)) {
        if (currentQuestionType === "single_choice") {
          e.preventDefault();
          handleAnswer(k);
          return;
        }

        if (currentQuestionType === "multi_select") {
          e.preventDefault();
          toggleMultiSelectChoice(k);
          return;
        }
      }

      if (e.key === "Enter" && currentQuestionType === "flashcard" && !currentAnswer && selected !== "__revealed__") {
        e.preventDefault();
        revealFlashcard();
        return;
      }

      if (e.key === "Enter" && currentQuestionType === "multi_select" && !currentAnswer && Array.isArray(selected) && selected.length) {
        e.preventDefault();
        submitMultiSelectAnswer();
        return;
      }

      if (e.key === "Enter" && currentQuestionType === "type_answer" && !currentAnswer && String(selected || "").trim()) {
        e.preventDefault();
        submitTypeAnswer();
        return;
      }

      if (
        e.key === "Enter" &&
        currentQuestionType === "cloze_deletion" &&
        !currentAnswer &&
        Array.isArray(selected) &&
        selected.length &&
        selected.every((item) => String(item || "").trim())
      ) {
        e.preventDefault();
        submitClozeAnswer();
        return;
      }

      if (e.key === "Enter" && currentAnswer) {
        e.preventDefault();
        next();
        return;
      }

      if (k === "R") {
        e.preventDefault();
        startQuiz(undefined, quizLength);
        return;
      }

      if ((e.key === "ArrowRight" || e.key === "ArrowDown") && currentAnswer) {
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
  }, [
    currentAnswer,
    currentQuestionType,
    handleAnswer,
    next,
    prev,
    revealFlashcard,
    selected,
    showResult,
    startQuiz,
    submitClozeAnswer,
    submitMultiSelectAnswer,
    submitTypeAnswer,
    toggleMultiSelectChoice,
    quizLength,
  ]);

  const upsertQuestionInState = useCallback((nextQuestion, previousId = null) => {
    const previousKey = String(previousId ?? nextQuestion.id);
    setQuestionPool((prev) => {
      const filtered = prev.filter((question) => String(question.id) !== previousKey);
      return mergeQuestionLibraries(filtered, [nextQuestion]);
    });
    setQuestions((prev) => prev.map((question) => (String(question.id) === previousKey ? nextQuestion : question)));
  }, []);

  const removeQuestionFromState = useCallback(
    (questionId) => {
      const questionKey = String(questionId);
      setQuestionPool((prev) => prev.filter((question) => String(question.id) !== questionKey));
      setQuestions((prev) => prev.filter((question) => String(question.id) !== questionKey));
      setAnswers((prev) => {
        const next = { ...prev };
        delete next[questionKey];
        return next;
      });
      if (String(current?.id) === questionKey) {
        setIdx(0);
        setSelected(null);
      }
    },
    [current?.id]
  );

  const updateEditorField = useCallback((field, value) => {
    setEditorDraft((prev) => (prev ? { ...prev, [field]: value } : prev));
  }, []);

  const updateEditorOption = useCallback((key, value) => {
    setEditorDraft((prev) => (prev ? { ...prev, options: { ...prev.options, [key]: value } } : prev));
  }, []);

  const updateEditorCorrectAnswer = useCallback((key) => {
    setEditorDraft((prev) => {
      if (!prev) return prev;
      const type = normalizeQuestionType(prev.questionType);
      const currentKeys = parseAnswerKeys(prev.correctAnswers || prev.correct);

      if (type === "single_choice") {
        return { ...prev, correct: key, correctAnswers: [key] };
      }

      if (type === "multi_select") {
        const nextKeys = currentKeys.includes(key) ? currentKeys.filter((item) => item !== key) : [...currentKeys, key];
        return { ...prev, correct: nextKeys[0] || null, correctAnswers: nextKeys };
      }

      return prev;
    });
  }, []);

  const applyEditorQuestionType = useCallback((nextType) => {
    setEditorDraft((prev) => {
      if (!prev) return prev;
      const type = normalizeQuestionType(nextType);
      const visibleOptions = optionKeys.filter((key) => String(prev.options?.[key] || "").trim());
      let nextCorrectAnswers = parseAnswerKeys(prev.correctAnswers || prev.correct).filter((key) => visibleOptions.includes(key));

      if (type === "single_choice") {
        nextCorrectAnswers = [nextCorrectAnswers[0] || visibleOptions[0] || "A"];
      } else if (type === "multi_select") {
        if (nextCorrectAnswers.length < 2) nextCorrectAnswers = visibleOptions.slice(0, Math.min(2, visibleOptions.length));
      } else {
        nextCorrectAnswers = [];
      }

      return {
        ...prev,
        questionType: type,
        correct: nextCorrectAnswers[0] || null,
        correctAnswers: nextCorrectAnswers,
      };
    });
  }, []);

  const createNewQuestion = useCallback(() => {
    setEditorSelectedId(null);
    setEditorDraft(createEditorDraft());
    setEditorStatus({
      status: "idle",
      message: "Nowe pytanie gotowe. Ustaw typ, deck i tresc, a potem zapisz.",
    });
    setActiveTab("editor");
  }, [createEditorDraft]);

  const saveEditorQuestion = useCallback(async () => {
    if (!editorDraft) return;

    const normalized = createQuestionRecord(
      {
        ...editorDraft,
        tags: editorDraft.tagsText,
        correctAnswers:
          editorDraft.questionType === "type_answer" || editorDraft.questionType === "cloze_deletion"
            ? editorDraft.acceptedAnswersText
            : editorDraft.correctAnswers,
        sourceType: editorDraft.sourceType || "editor-local",
      },
      0
    );
    const visibleOptions = getVisibleOptionKeys(normalized);

    if (!normalized.question) {
      setEditorStatus({ status: "error", message: "Pytanie nie moze byc puste." });
      return;
    }

    if (normalized.questionType === "flashcard") {
      if (!String(normalized.answerBack || normalized.explanation || "").trim()) {
        setEditorStatus({ status: "error", message: "Fiszka wymaga pola odpowiedzi po drugiej stronie." });
        return;
      }
    } else if (normalized.questionType === "type_answer") {
      if (!normalized.correctAnswers.length) {
        setEditorStatus({ status: "error", message: "Type answer potrzebuje co najmniej jednej akceptowanej odpowiedzi." });
        return;
      }
    } else if (normalized.questionType === "cloze_deletion") {
      const clozeEntries = extractClozeEntries(normalized.question);
      if (!clozeEntries.length) {
        setEditorStatus({ status: "error", message: "Cloze deletion wymaga skladni {{c1::odpowiedz}} w tresci pytania." });
        return;
      }
      if (!normalized.correctAnswers.length) {
        setEditorStatus({ status: "error", message: "Nie udalo sie odczytac odpowiedzi cloze. Dodaj markery {{c1::...}} albo uzupelnij liste odpowiedzi." });
        return;
      }
      if (parseTextAnswers(editorDraft.acceptedAnswersText).length && normalized.correctAnswers.length !== clozeEntries.length) {
        setEditorStatus({ status: "error", message: "Liczba odpowiedzi cloze musi odpowiadac liczbie luk w pytaniu." });
        return;
      }
    } else {
      if (visibleOptions.length < 2) {
        setEditorStatus({ status: "error", message: "Pytanie wyboru potrzebuje przynajmniej 2 opcji." });
        return;
      }

      if (normalized.questionType === "single_choice" && normalized.correctAnswers.length !== 1) {
        setEditorStatus({ status: "error", message: "Jednokrotny wybor musi miec dokladnie 1 poprawna odpowiedz." });
        return;
      }

      if (normalized.questionType === "multi_select" && normalized.correctAnswers.length < 2) {
        setEditorStatus({ status: "error", message: "Wielokrotny wybor powinien miec co najmniej 2 poprawne odpowiedzi." });
        return;
      }

      if (normalized.correctAnswers.some((key) => !visibleOptions.includes(key))) {
        setEditorStatus({ status: "error", message: "Poprawne odpowiedzi musza wskazywac tylko niepuste opcje." });
        return;
      }
    }

    const previousId = editorSelectedId || normalized.id;
    let savedQuestion = normalized;
    let statusMessage = "Pytanie zapisane lokalnie.";

    upsertQuestionInState(savedQuestion, previousId);
    setEditorSelectedId(String(savedQuestion.id));
    setEditorDraft(createEditorDraft(savedQuestion));

    if (sbEnabled) {
      setEditorStatus({ status: "loading", message: "Zapisuje pytanie do biblioteki i probuje zsynchronizowac z Supabase..." });

      try {
        const isLocalId = /^(local|import|txt)-/i.test(String(savedQuestion.id));
        const payload = questionToSupabaseRow({
          ...savedQuestion,
          sourceType: isLocalId ? "editor" : savedQuestion.sourceType || "editor",
        });
        const result = isLocalId
          ? await sbInsert(supabaseConfig, "quiz_questions", payload, authAccessToken)
          : await sbPatch(supabaseConfig, "quiz_questions", `id=eq.${encodeURIComponent(savedQuestion.id)}`, payload, authAccessToken);
        const rawPersisted = Array.isArray(result) ? result[0] : result;

        if (rawPersisted) {
          const persisted = rowToQ(rawPersisted, 0);
          savedQuestion = persisted;
          upsertQuestionInState(savedQuestion, previousId);
          setEditorSelectedId(String(savedQuestion.id));
          setEditorDraft(createEditorDraft(savedQuestion));
          statusMessage = "Pytanie zapisane lokalnie i w Supabase.";
        }
      } catch (error) {
        statusMessage = `Pytanie zapisane lokalnie, ale Supabase odrzucilo zapis: ${getErrorText(error)}`;
      }
    }

    setEditorStatus({ status: "success", message: statusMessage });
  }, [authAccessToken, createEditorDraft, editorDraft, editorSelectedId, sbEnabled, supabaseConfig, upsertQuestionInState]);

  const deleteEditorQuestion = useCallback(async () => {
    const question = selectedEditorQuestion;
    if (!question) return;

    const questionId = String(question.id);
    removeQuestionFromState(questionId);
    setEditorStatus({ status: "success", message: "Pytanie usuniete z lokalnej biblioteki." });

    if (sbEnabled && !/^(local|import|txt)-/i.test(questionId)) {
      try {
        await sbPatch(
          supabaseConfig,
          "quiz_questions",
          `id=eq.${encodeURIComponent(questionId)}`,
          { is_active: false },
          authAccessToken
        );
        setEditorStatus({ status: "success", message: "Pytanie ukryte lokalnie i w Supabase." });
      } catch (error) {
        setEditorStatus({
          status: "error",
          message: `Pytanie usuniete lokalnie, ale nie udalo sie oznaczyc go jako nieaktywne w Supabase: ${getErrorText(error)}`,
        });
      }
    }

    const nextQuestion = questionPool.find((item) => String(item.id) !== questionId);
    if (nextQuestion) {
      setEditorSelectedId(String(nextQuestion.id));
    } else {
      setEditorSelectedId(null);
      setEditorDraft(createEditorDraft());
    }
  }, [authAccessToken, createEditorDraft, questionPool, removeQuestionFromState, sbEnabled, selectedEditorQuestion, supabaseConfig]);

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

  const localPlan = useMemo(() => ({ source: "local", ...buildPlan(uniq, stats.weakest) }), [uniq, stats.weakest]);
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

  const activeDeckName = useMemo(() => {
    if (selectedDeck !== ALL_DECKS_LABEL) return selectedDeck;
    return activeQuestionPool.find((question) => normalizeDeck(question.deck))?.deck || DEFAULT_DECKS[0];
  }, [selectedDeck, activeQuestionPool]);

  const deckGroups = useMemo(() => {
    const order = new Map(DEFAULT_DECKS.map((deck, index) => [deck, index]));
    const grouped = new Map(
      DEFAULT_DECKS.map((deck) => [
        deck,
        { name: deck, count: 0, totalCount: 0, inactiveCount: 0, categories: [], activeCategories: 0, isActive: false },
      ])
    );

    questionPool.forEach((question) => {
      const deck = normalizeDeck(question.deck);
      const category = String(question.category || "Bez kategorii").trim() || "Bez kategorii";
      const questionIsActive = question.isActive !== false;
      if (!grouped.has(deck)) grouped.set(deck, { name: deck, count: 0, totalCount: 0, inactiveCount: 0, categories: [], activeCategories: 0, isActive: false });

      const entry = grouped.get(deck);
      entry.totalCount += 1;
      if (questionIsActive) {
        entry.count += 1;
        entry.isActive = true;
      } else {
        entry.inactiveCount += 1;
      }

      const categoryEntry = entry.categories.find((item) => item.name === category);
      if (categoryEntry) {
        categoryEntry.totalCount += 1;
        if (questionIsActive) categoryEntry.count += 1;
        else categoryEntry.inactiveCount += 1;
      } else {
        entry.categories.push({ name: category, count: questionIsActive ? 1 : 0, totalCount: 1, inactiveCount: questionIsActive ? 0 : 1 });
      }
    });

    return [...grouped.values()]
      .map((entry) => ({
        ...entry,
        activeCategories: entry.categories.filter((item) => item.count > 0).length,
        categories: entry.categories.sort((a, b) => b.totalCount - a.totalCount || a.name.localeCompare(b.name, "pl")),
      }))
      .sort((a, b) => {
        if (a.name === activeDeckName) return -1;
        if (b.name === activeDeckName) return 1;
        if (order.has(a.name) && order.has(b.name)) return order.get(a.name) - order.get(b.name);
        if (order.has(a.name)) return -1;
        if (order.has(b.name)) return 1;
        return b.count - a.count || a.name.localeCompare(b.name, "pl");
      });
  }, [questionPool, activeDeckName]);

  const maxDeckCount = useMemo(() => Math.max(...deckGroups.map((deck) => deck.count), 1), [deckGroups]);
  const getDeckProgress = useCallback(
    (count, maxValue = maxDeckCount) => {
      const safeMax = Math.max(Number(maxValue || 0), 1);
      return Math.round((Number(count || 0) / safeMax) * 100);
    },
    [maxDeckCount]
  );

  useEffect(() => {
    if (!activeDeckName) return;
    setExpandedDecks((prev) => (prev[activeDeckName] ? prev : { ...prev, [activeDeckName]: true }));
  }, [activeDeckName]);

  useEffect(() => {
    if (!uniq.length) {
      setStudyPlan(localPlan);
      setStudyPlanStatus("idle");
      return;
    }

    if (activeTab !== "plan") return;

    let cancelled = false;

    const runPlan = async () => {
      setStudyPlanStatus("loading");

      if (!cloudApiEnabled || !sbEnabled) {
        if (!cancelled) {
          setStudyPlan(localPlan);
          setStudyPlanStatus("done");
        }
        return;
      }

      try {
        const cloudPlan = await fetchCloudStudyPlan({
          supabaseConfig,
          model: cloudModel.trim() || DEFAULT_MODEL,
          cloudApiKey: manualCloudApiKey,
          history: uniq,
          weakCat: stats.weakest,
          strongCat: stats.strongest,
          avgResponseMs: stats.avgResponseMs,
          latestAttempt: attemptDraft,
          weekdaySummary,
          weeklySummary,
        });

        if (!cancelled) {
          setStudyPlan(cloudPlan);
          setStudyPlanStatus("done");
        }
      } catch {
        if (!cancelled) {
          setStudyPlan({
            ...localPlan,
            recommendation: `${localPlan.recommendation} Cloud AI nie odpowiedziało, więc pokazuję plan lokalny.`,
          });
          setStudyPlanStatus("done");
        }
      }
    };

    runPlan();
    return () => {
      cancelled = true;
    };
  }, [
    activeTab,
    uniq,
    localPlan,
    cloudApiEnabled,
    sbEnabled,
    supabaseConfig,
    cloudModel,
    manualCloudApiKey,
    stats.weakest,
    stats.strongest,
    stats.avgResponseMs,
    attemptDraft,
    weekdaySummary,
    weeklySummary,
  ]);

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
    { id: "quiz", label: "Quiz", icon: <IcoBrain size={15} /> },
    { id: "decks", label: "Decki", icon: <IcoBook size={15} /> },
    { id: "generator", label: "Generator", icon: <IcoUpload size={15} /> },
    { id: "editor", label: "Edytor", icon: <IcoEdit size={15} /> },
    { id: "results", label: "Wyniki", icon: <IcoTrophy size={15} /> },
    { id: "calendar", label: "Kalendarz", icon: <IcoCalendar size={15} /> },
    { id: "plan", label: "Plan", icon: <IcoTarget size={15} /> },
    { id: "settings", label: "Ustawienia", icon: <IcoSettings size={15} /> },
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

  const toggleDeckExpansion = useCallback((deckName) => {
    setExpandedDecks((prev) => ({ ...prev, [deckName]: !prev[deckName] }));
  }, []);

  const selectDeckFromLibrary = useCallback((deckName) => {
    setSelectedDeck(deckName);
    setExpandedDecks((prev) => ({ ...prev, [deckName]: true }));
  }, []);

  const startDeckSession = useCallback(
    (deckName) => {
      const pool = getDeckPool(deckName);
      setSelectedDeck(deckName);
      startQuiz(pool, quizLength);
    },
    [getDeckPool, startQuiz, quizLength]
  );

  const startDeckCategorySession = useCallback(
    (deckName, categoryName) => {
      const pool = getDeckPool(deckName, categoryName);
      setSelectedDeck(deckName);
      setExpandedDecks((prev) => ({ ...prev, [deckName]: true }));
      startQuiz(pool, quizLength);
    },
    [getDeckPool, startQuiz, quizLength]
  );

  const syncDeckActiveState = useCallback(
    async (deckName, nextActive) => {
      if (!sbEnabled) return;
      await sbPatch(
        supabaseConfig,
        "quiz_questions",
        `deck=eq.${encodeURIComponent(deckName)}`,
        { is_active: nextActive },
        authAccessToken
      );
    },
    [sbEnabled, supabaseConfig, authAccessToken]
  );

  const setDeckActiveState = useCallback(
    async (deckName, nextActive) => {
      const normalizedDeck = normalizeDeck(deckName);
      const affected = questionPool.filter((question) => normalizeDeck(question.deck) === normalizedDeck);
      if (!affected.length) return;

      setQuestionPool((prev) =>
        prev.map((question) =>
          normalizeDeck(question.deck) === normalizedDeck
            ? {
                ...question,
                isActive: nextActive,
              }
            : question
        )
      );

      if (!nextActive && selectedDeck === normalizedDeck) {
        setSelectedDeck(ALL_DECKS_LABEL);
      } else if (nextActive) {
        setSelectedDeck(normalizedDeck);
        setExpandedDecks((prev) => ({ ...prev, [normalizedDeck]: true }));
      }

      setDeckLibraryStatus({
        status: "loading",
        message: `${nextActive ? "Aktywuje" : "Dezaktywuje"} deck "${normalizedDeck}"...`,
      });

      try {
        await syncDeckActiveState(normalizedDeck, nextActive);
        setDeckLibraryStatus({
          status: "success",
          message: sbEnabled
            ? `Deck "${normalizedDeck}" ${nextActive ? "jest znowu aktywny" : "zostal zdezaktywowany"} lokalnie i w Supabase.`
            : `Deck "${normalizedDeck}" ${nextActive ? "jest znowu aktywny" : "zostal zdezaktywowany"} lokalnie.`,
        });
      } catch (error) {
        setDeckLibraryStatus({
          status: "error",
          message: `Deck "${normalizedDeck}" zmienil stan lokalnie, ale synchronizacja z Supabase nie udala sie: ${getErrorText(error)}`,
        });
      }
    },
    [questionPool, selectedDeck, sbEnabled, syncDeckActiveState]
  );

  const deleteDeckFromLibrary = useCallback(
    async (deckName) => {
      const normalizedDeck = normalizeDeck(deckName);
      const affected = questionPool.filter((question) => normalizeDeck(question.deck) === normalizedDeck);
      if (!affected.length) return;

      const persistedRows = affected.filter((question) => isPersistedQuestionId(question.id));
      setQuestionPool((prev) => prev.filter((question) => normalizeDeck(question.deck) !== normalizedDeck));

      if (selectedDeck === normalizedDeck) {
        setSelectedDeck(ALL_DECKS_LABEL);
      }

      setDeckLibraryStatus({
        status: "loading",
        message: `Usuwam deck "${normalizedDeck}" z biblioteki...`,
      });

      if (!persistedRows.length || !sbEnabled) {
        setDeckLibraryStatus({
          status: "success",
          message: `Deck "${normalizedDeck}" usuniety z lokalnej biblioteki.`,
        });
        return;
      }

      try {
        await syncDeckActiveState(normalizedDeck, false);
        setDeckLibraryStatus({
          status: "success",
          message: `Deck "${normalizedDeck}" usuniety lokalnie i oznaczony jako nieaktywny w Supabase.`,
        });
      } catch (error) {
        setDeckLibraryStatus({
          status: "error",
          message: `Deck "${normalizedDeck}" usuniety lokalnie, ale nie udalo sie ukryc go w Supabase: ${getErrorText(error)}`,
        });
      }
    },
    [questionPool, selectedDeck, sbEnabled, syncDeckActiveState]
  );

  const DeckProgressRing = ({ progress = 0, active = false }) => {
    const safeProgress = Math.max(0, Math.min(100, Number(progress || 0)));
    const radius = 10;
    const circumference = 2 * Math.PI * radius;
    const offset = circumference - (safeProgress / 100) * circumference;

    return (
      <span className={`deck-ring ${active ? "active" : ""}`} aria-hidden="true">
        <svg width="28" height="28" viewBox="0 0 28 28">
          <circle cx="14" cy="14" r={radius} className="deck-ring-track" />
          <circle
            cx="14"
            cy="14"
            r={radius}
            className="deck-ring-fill"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
          />
        </svg>
      </span>
    );
  };

  const QuizView = () => {
    const diffColor = { easy: C.success, medium: C.yellow, hard: C.error }[current.difficulty || "medium"];
    const multiSelectDraft = Array.isArray(currentAnswer?.selected) ? currentAnswer.selected : Array.isArray(selected) ? selected : [];
    const typedAnswerDraft = currentAnswer?.selected ? String(currentAnswer.selected || "") : String(selected || "");
    const clozeDraft =
      Array.isArray(currentAnswer?.selected) && currentAnswer.selected.length
        ? currentAnswer.selected
        : Array.isArray(selected) && selected.length === currentClozeEntries.length
        ? selected
        : currentClozeEntries.map(() => "");
    const flashcardRevealed = currentQuestionType === "flashcard" && (selected === "__revealed__" || Boolean(currentAnswer));
    const canGoNext = Boolean(currentAnswer);
    const answerHint =
      currentQuestionType === "multi_select"
        ? "Zaznacz wszystkie poprawne odpowiedzi, a potem kliknij Sprawdz."
        : currentQuestionType === "flashcard"
        ? "Sprobuj odpowiedziec z pamieci, odkryj odpowiedz i ocen siebie."
        : currentQuestionType === "type_answer"
        ? "Wpisz odpowiedz wlasnymi slowami. System porowna ja z akceptowanym wzorcem."
        : currentQuestionType === "cloze_deletion"
        ? "Uzupelnij wszystkie luki i sprawdz pelne zdanie po wyslaniu odpowiedzi."
        : "Wybierz jedna odpowiedz. Po wyborze od razu zobaczysz informacje zwrotna i mozesz przejsc dalej.";

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
              {currentDeck}
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
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                padding: "5px 10px",
                borderRadius: 999,
                background: C.accentSoft,
                color: C.accent,
              }}
            >
              {questionTypeLabel(currentQuestionType)}
            </span>
            {currentQuestionTags.map((tag) => {
              const isActive = selectedTagFilters.some((item) => item.toLowerCase() === tag.toLowerCase());

              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => toggleTagFilter(tag)}
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "5px 10px",
                    borderRadius: 999,
                    border: `1px solid ${isActive ? C.accent : C.border}`,
                    background: isActive ? C.accentSoft : C.cardAlt,
                    color: isActive ? C.accent : C.textSub,
                    cursor: "pointer",
                  }}
                >
                  #{tag}
                </button>
              );
            })}
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
              fontSize: 28,
              fontWeight: 500,
              color: C.textStrong,
              lineHeight: 1.38,
              margin: 0,
              letterSpacing: "-0.01em",
            }}
          >
            {currentPromptText}
          </h2>

          <QuestionMediaBlock imageUrl={current.imageUrl} audioUrl={current.audioUrl} />

          {false && <div style={{ marginTop: 12, fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
            Wybierz jedną odpowiedź. Po wyborze od razu zobaczysz informację zwrotną i możesz przejść dalej.
          </div>}
          <div style={{ marginTop: 12, fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>{answerHint}</div>
          {selectedTagFilters.length > 0 && (
            <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span className="soft-chip">Filtr sesji: {selectedTagFilters.join(", ")}</span>
              <span className="soft-chip">{filteredQuestionPool.length} pytań po filtrze</span>
            </div>
          )}

        </div>
        {currentQuestionType === "flashcard" && (
          <div className={`flashcard-stage ${flashcardRevealed || currentAnswer ? "revealed" : ""}`}>
            <div className="flashcard-stack">
              <div className={`flashcard-face ${flashcardRevealed || currentAnswer ? "back" : "front"}`}>
                <div className="flashcard-face-top">
                  <span className="flashcard-face-label">{flashcardRevealed || currentAnswer ? "Tyl karty" : "Przod karty"}</span>
                  <span className="flashcard-face-hint">
                    {flashcardRevealed || currentAnswer ? "Porownaj to z tym, co odtworzyles z pamieci." : "Najpierw odpowiedz sobie w glowie."}
                  </span>
                </div>

                <div className="flashcard-face-body">
                  {flashcardRevealed || currentAnswer ? current.answerBack || current.explanation : current.question}
                </div>

                <div className="flashcard-face-foot">
                  {flashcardRevealed || currentAnswer
                    ? current.explanation || "Zanotuj brakujacy fragment i wroc do karty jeszcze raz."
                    : "Sprobuj przywolac definicje, proces albo najwazniejsza zaleznosc, zanim odkryjesz rewers."}
                </div>
              </div>
            </div>

            {!flashcardRevealed && !currentAnswer && (
              <div className="flashcard-actions">
                <button onClick={revealFlashcard} style={{ ...s.btn("soft"), width: "fit-content" }}>
                  <IcoBook size={14} /> Pokaz odpowiedz
                </button>
              </div>
            )}

            {flashcardRevealed && !currentAnswer && (
              <div className="flashcard-actions">
                <div className="flashcard-judge-note">Oceń fiszkę od razu po odkryciu tylu karty.</div>
                <div className="flashcard-judge-buttons">
                  <button onClick={() => gradeFlashcard(true)} style={s.btn("soft")}>
                    <IcoCheck size={14} /> Umiem
                  </button>
                  <button onClick={() => gradeFlashcard(false)} style={s.btn("ghost")}>
                    <IcoRefresh size={14} /> Do poprawy
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {currentQuestionType === "multi_select" && (
          <div style={{ display: "grid", gap: 12 }}>
            {currentVisibleOptions.map((key) => {
              const reveal = Boolean(currentAnswer);
              const selectedKeys = reveal ? currentAnswer?.selected || [] : multiSelectDraft;
              const isSel = selectedKeys.includes(key);
              const isCorr = (current.correctAnswers || []).includes(key);

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
              } else if (isSel) {
                bg = C.accentSoft;
                border = C.accent2;
                color = C.textStrong;
                labelBg = C.accent;
                labelColor = "#fff";
              }

              return (
                <button
                  key={key}
                  onClick={() => toggleMultiSelectChoice(key)}
                  disabled={reveal}
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
                    cursor: reveal ? "default" : "pointer",
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

            {!currentAnswer && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <span className="soft-chip">{multiSelectDraft.length ? `${multiSelectDraft.length} zaznaczone` : "Nic nie zaznaczono"}</span>
                <button
                  onClick={submitMultiSelectAnswer}
                  disabled={!multiSelectDraft.length}
                  style={{ ...s.btn("soft"), opacity: multiSelectDraft.length ? 1 : 0.5 }}
                >
                  <IcoCheck size={14} /> Sprawdz
                </button>
              </div>
            )}
          </div>
        )}

        {currentQuestionType === "type_answer" && (
          <div style={{ ...s.card, padding: 20, background: "rgba(255,255,255,.86)", display: "grid", gap: 12 }}>
            <textarea
              value={typedAnswerDraft}
              onChange={(e) => updateTypeAnswerDraft(e.target.value)}
              rows={3}
              disabled={Boolean(currentAnswer)}
              placeholder="Wpisz odpowiedz"
              style={{ ...s.input, resize: "vertical", opacity: currentAnswer ? 0.78 : 1 }}
            />

            {!currentAnswer && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <span className="soft-chip">{String(typedAnswerDraft || "").trim() ? "Odpowiedz gotowa do sprawdzenia" : "Wpisz odpowiedz"}</span>
                <button
                  onClick={submitTypeAnswer}
                  disabled={!String(typedAnswerDraft || "").trim()}
                  style={{ ...s.btn("soft"), opacity: String(typedAnswerDraft || "").trim() ? 1 : 0.5 }}
                >
                  <IcoCheck size={14} /> Sprawdz
                </button>
              </div>
            )}
          </div>
        )}

        {currentQuestionType === "cloze_deletion" && (
          <div style={{ ...s.card, padding: 20, background: "rgba(255,255,255,.86)", display: "grid", gap: 12 }}>
            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>
              Uzupelnij {currentClozeEntries.length} {currentClozeEntries.length === 1 ? "brakujacy fragment" : "brakujace fragmenty"}.
            </div>

            <div className="generator-config-grid">
              {currentClozeEntries.map((entry, blankIndex) => (
                <div key={`${current.id}-blank-${blankIndex}`}>
                  <label style={s.label}>Luka {blankIndex + 1}</label>
                  <input
                    value={clozeDraft[blankIndex] || ""}
                    onChange={(e) => updateClozeDraft(blankIndex, e.target.value)}
                    disabled={Boolean(currentAnswer)}
                    placeholder={entry.hint || `Odpowiedz ${blankIndex + 1}`}
                    style={{ ...s.input, opacity: currentAnswer ? 0.78 : 1 }}
                  />
                </div>
              ))}
            </div>

            {!currentAnswer && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <span className="soft-chip">
                  {clozeDraft.filter((item) => String(item || "").trim()).length}/{currentClozeEntries.length} uzupelnionych
                </span>
                <button
                  onClick={submitClozeAnswer}
                  disabled={!clozeDraft.length || clozeDraft.some((item) => !String(item || "").trim())}
                  style={{
                    ...s.btn("soft"),
                    opacity: clozeDraft.length && clozeDraft.every((item) => String(item || "").trim()) ? 1 : 0.5,
                  }}
                >
                  <IcoCheck size={14} /> Sprawdz
                </button>
              </div>
            )}
          </div>
        )}

        {currentQuestionType === "single_choice" && <div style={{ display: "grid", gap: 12 }}>
          {currentVisibleOptions.map((key) => {
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
        </div>}

        {currentQuestionType === "single_choice" && selected && (
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
              <button onClick={askAIEnhanced} style={{ ...s.btn("soft"), marginTop: 12, fontSize: 12, padding: "8px 14px" }}>
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

        {currentQuestionType === "multi_select" && currentAnswer && (
          <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: currentAnswer.isCorrect ? C.success : C.error,
                marginBottom: 6,
              }}
            >
              {currentAnswer.isCorrect
                ? "OK - poprawny zestaw odpowiedzi."
                : `Poprawny wzorzec: ${formatQuestionAnswer(current, current.correctAnswers || current.correct)}`}
            </div>

            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{current.explanation}</div>

            {chatStatus === "idle" && (
              <button onClick={askAIEnhanced} style={{ ...s.btn("soft"), marginTop: 12, fontSize: 12, padding: "8px 14px" }}>
                <IcoChat size={13} /> Wyjasnij szerzej
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

        {currentQuestionType === "flashcard" && currentAnswer && (
          <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }} className="flashcard-review-card">
            <div
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: currentAnswer.isCorrect ? C.success : C.error,
                marginBottom: 6,
              }}
            >
              {currentAnswer.isCorrect ? "OK - oznaczone jako umiem." : "Do poprawy - wroc do tej fiszki w kolejnej sesji."}
            </div>

            <div className="flashcard-review-grid">
              <div className="flashcard-mini-face">
                <div className="flashcard-mini-label">Przod</div>
                <div className="flashcard-mini-copy">{current.question}</div>
              </div>

              <div className="flashcard-mini-face back">
                <div className="flashcard-mini-label">Tyl</div>
                <div className="flashcard-mini-copy">{current.answerBack || current.explanation}</div>
              </div>
            </div>

            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{current.explanation}</div>

            {chatStatus === "idle" && (
              <button onClick={askAIEnhanced} style={{ ...s.btn("soft"), marginTop: 12, fontSize: 12, padding: "8px 14px" }}>
                <IcoChat size={13} /> Wyjasnij szerzej
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

        {currentQuestionType === "type_answer" && currentAnswer && (
          <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: currentAnswer.isCorrect ? C.success : C.error,
                marginBottom: 6,
              }}
            >
              {currentAnswer.isCorrect
                ? "OK - odpowiedz pasuje do akceptowanego wzorca."
                : `Akceptowane odpowiedzi: ${formatQuestionAnswer(current, current.correctAnswers || []) || "Brak wzorca"}`}
            </div>

            <div style={{ padding: "12px 14px", borderRadius: 14, background: "#fff", border: `1px solid ${C.border}`, marginBottom: 10 }}>
              <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Twoja odpowiedz</div>
              <div style={{ fontSize: 15, color: C.textStrong, lineHeight: 1.65 }}>{String(currentAnswer.selected || "").trim() || "Brak odpowiedzi."}</div>
            </div>

            {!!String(current.answerBack || "").trim() && (
              <div style={{ padding: "12px 14px", borderRadius: 14, background: "#fff", border: `1px solid ${C.border}`, marginBottom: 10 }}>
                <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Wzorcowa odpowiedz</div>
                <div style={{ fontSize: 15, color: C.textStrong, lineHeight: 1.65 }}>{current.answerBack}</div>
              </div>
            )}

            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{current.explanation}</div>
          </div>
        )}

        {currentQuestionType === "cloze_deletion" && currentAnswer && (
          <div style={{ ...s.card, padding: "18px 20px", background: C.cardAlt }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 800,
                color: currentAnswer.isCorrect ? C.success : C.error,
                marginBottom: 6,
              }}
            >
              {currentAnswer.isCorrect ? "OK - wszystkie luki uzupelnione poprawnie." : "Czesc luk wymaga poprawy."}
            </div>

            <div style={{ padding: "12px 14px", borderRadius: 14, background: "#fff", border: `1px solid ${C.border}`, marginBottom: 10 }}>
              <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Pelne zdanie</div>
              <div style={{ fontSize: 15, color: C.textStrong, lineHeight: 1.65 }}>{current.answerBack || revealClozeText(current.question || "")}</div>
            </div>

            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{current.explanation}</div>
          </div>
        )}

        {false && <div style={{ ...s.cardSm, padding: 16, background: "rgba(255,255,255,.82)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <IcoTag size={15} />
            <div style={{ fontSize: 14, fontWeight: 700, color: C.textStrong }}>Tagi pytania</div>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {currentQuestionTags.length ? (
              currentQuestionTags.map((tag) => (
                <span key={tag} className="soft-chip">
                  #{tag}
                </span>
              ))
            ) : (
              <div style={{ fontSize: 13, color: C.textSub }}>To pytanie nie ma jeszcze żadnych tagów.</div>
            )}
          </div>

          <div style={{ marginTop: 12 }}>
            <label style={s.label}>Twoje tagi</label>
            <input
              value={questionTagDraft}
              onChange={(e) => setQuestionTagDraft(e.target.value)}
              placeholder="np. pgmp::scope review tricky"
              style={s.input}
            />
            <div className="field-help">Rozdzielaj tagi spacją, przecinkiem albo średnikiem. Zapis działa jak prywatne tagi Anki dla zalogowanego użytkownika.</div>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
            <button onClick={saveQuestionTags} style={s.btn("soft")}>
              <IcoTag size={14} /> Zapisz tagi
            </button>
            {!authUser && <span className="soft-chip">Zaloguj się w ustawieniach, aby zapisać tagi</span>}
          </div>

          <div
            style={{
              marginTop: 12,
              padding: 10,
              borderRadius: 14,
              background: toneForStatus(tagSaveState.status).bg,
              color: toneForStatus(tagSaveState.status).color,
              border: `1px solid ${toneForStatus(tagSaveState.status).border}`,
              fontSize: 12,
              lineHeight: 1.55,
            }}
          >
            {tagSaveState.message}
          </div>
        </div>}

        <div style={{ display: "flex", gap: 10, justifyContent: "space-between", flexWrap: "wrap" }}>
          <button onClick={prev} disabled={idx === 0} style={{ ...s.btn("ghost"), opacity: idx === 0 ? 0.45 : 1 }}>
            <IcoLeft size={14} /> Poprzednie
          </button>

          <div style={{ display: "flex", gap: 10 }}>
            <button onClick={() => startQuiz(undefined, quizLength)} style={s.btn("ghost")}>
              <IcoRefresh size={14} /> Restart
            </button>

            <button onClick={next} disabled={!canGoNext} style={{ ...s.btn("primary"), opacity: !canGoNext ? 0.55 : 1 }}>
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

  const PlanView = () => {
    const activePlan = studyPlan || localPlan;

    return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ ...s.card, padding: 22 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
          <div className="tinyLabel">Plan rozwoju</div>
          <span className="soft-chip">
            <IcoCloud size={12} />
            {activePlan?.source === "cloud" ? "Plan AI" : "Plan lokalny"}
          </span>
        </div>
        <div style={{ fontSize: 28, fontWeight: 700, color: C.textStrong, marginBottom: 8 }}>
          {activePlan?.readiness || "Plan nauki"}
        </div>
        <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.7 }}>{activePlan?.recommendation}</div>
        {studyPlanStatus === "loading" && (
          <div style={{ marginTop: 12, fontSize: 13, color: C.textSub }}>
            Cloud AI przygotowuje szczegółowy plan na podstawie wyników i historii sesji.
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div style={{ ...s.card, padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoTarget size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Co poprawić</div>
          </div>

          <div style={{ display: "grid", gap: 10 }}>
            {(activePlan?.improvements || []).length ? (
              activePlan.improvements.map((item, i) => (
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
            {(activePlan?.weeklyPlan || []).length ? (
              activePlan.weeklyPlan.map((x) => (
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
  };

  const DecksView = () => {
    const activeDeck = deckGroups.find((deck) => deck.name === activeDeckName) || deckGroups[0] || null;
    const deckCanStart = Boolean(activeDeck?.count);

    return (
      <div className="deck-view">
        <div className="deck-hero" style={{ ...s.card, padding: 18 }}>
          <div className="deck-hero-head">
            <div>
              <div className="tinyLabel" style={{ marginBottom: 8 }}>
                Biblioteka deckow
              </div>
              <div className="deck-hero-title">{activeDeck?.name || "Brak deckow"}</div>
              <div className="deck-hero-copy">
                Wybierz deck jak w Anki: rozwin liste, sprawdz sekcje i uruchom sesje dla calosci albo pojedynczej kategorii.
              </div>
            </div>

            <div className="deck-hero-actions">
              <button onClick={() => activeDeck && startDeckSession(activeDeck.name)} style={s.btn("ghost")} disabled={!activeDeck || !deckCanStart}>
                <IcoRight size={14} /> Start deck
              </button>
              {activeDeck && (
                <button onClick={() => setDeckActiveState(activeDeck.name, !activeDeck.isActive)} style={s.btn(activeDeck.isActive ? "soft" : "ghost")}>
                  {activeDeck.isActive ? <IcoCross size={14} /> : <IcoRefresh size={14} />}
                  {activeDeck.isActive ? "Dezaktywuj" : "Aktywuj"}
                </button>
              )}
              {activeDeck && (
                <button onClick={() => deleteDeckFromLibrary(activeDeck.name)} style={s.btn("ghost")}>
                  <IcoTrash size={14} /> Usun deck
                </button>
              )}
            </div>
          </div>

          <div className="deck-hero-metrics">
            <div className="deck-hero-metric">
              <span className="deck-hero-label">Aktywne karty</span>
              <strong>{activeDeck?.count || 0}</strong>
            </div>
            <div className="deck-hero-metric">
              <span className="deck-hero-label">Kategorie</span>
              <strong>{activeDeck?.activeCategories || 0}</strong>
            </div>
            <div className="deck-hero-metric">
              <span className="deck-hero-label">Ukryte</span>
              <strong>{activeDeck?.inactiveCount || 0}</strong>
            </div>
          </div>

          <div
            className="deck-library-status"
            style={{
              background: toneForStatus(deckLibraryStatus.status).bg,
              color: toneForStatus(deckLibraryStatus.status).color,
              border: `1px solid ${toneForStatus(deckLibraryStatus.status).border}`,
            }}
          >
            {deckLibraryStatus.message}
          </div>

          {selectedTagFilters.length > 0 && (
            <div className="field-help">
              Aktywne tagi zawezaja liczbe kart przy starcie sesji. Lista deckow pokazuje pelna zawartosc, a przycisk start respektuje biezace filtry.
            </div>
          )}
        </div>

        <div className="deck-board" style={{ ...s.card, padding: 14 }}>
          <div className="deck-section-label">DECKS</div>

          <div className="deck-list">
            {deckGroups.map((deck) => {
              const isActive = deck.name === activeDeckName;
              const isExpanded = Boolean(expandedDecks[deck.name]);

              return (
                <div key={deck.name} className={`deck-item ${isActive ? "active" : ""}`}>
                  <div className={`deck-row ${isActive ? "active" : ""}`}>
                    <button
                      type="button"
                      className="deck-toggle"
                      aria-label={isExpanded ? `Zwin ${deck.name}` : `Rozwin ${deck.name}`}
                      onClick={() => toggleDeckExpansion(deck.name)}
                    >
                      {isExpanded ? "-" : "+"}
                    </button>

                    <button type="button" className="deck-main" onClick={() => selectDeckFromLibrary(deck.name)}>
                      <div className="deck-copy">
                        <div className="deck-title">{deck.name}</div>
                        <div className="deck-subtitle">
                          {deck.count} aktywnych / {deck.inactiveCount} ukrytych
                          {isActive ? " - wybrany deck" : ""}
                        </div>
                      </div>

                      <div className="deck-right">
                        <span className={`deck-state-badge ${deck.isActive ? "active" : "inactive"}`}>{deck.isActive ? "Aktywny" : "Ukryty"}</span>
                        <span className="deck-count">{deck.count} kart</span>
                        <DeckProgressRing progress={getDeckProgress(deck.count)} active={isActive} />
                      </div>
                    </button>
                  </div>

                  {isExpanded && deck.categories.length > 0 && (
                    <div className="deck-subrows">
                      {deck.categories.map((category) => (
                        <button
                          key={`${deck.name}-${category.name}`}
                          type="button"
                          className="deck-subrow"
                          onClick={() => startDeckCategorySession(deck.name, category.name)}
                          disabled={!category.count}
                        >
                          <div className="deck-subrow-spacer" />
                          <div className="deck-copy">
                            <div className="deck-title">{category.name}</div>
                            <div className="deck-subtitle">
                              {category.count} aktywnych / {category.inactiveCount} ukrytych
                            </div>
                          </div>
                          <div className="deck-right">
                            <span className="deck-count">{category.count} kart</span>
                            <DeckProgressRing progress={getDeckProgress(category.count, deck.count)} />
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  };

  const GeneratorView = () => (
    <div className="generator-layout">
      <div style={{ ...s.card, padding: 18 }}>
        <div className="generator-head">
          <div>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Create New Questions
            </div>
            <div style={{ fontSize: 26, fontWeight: 700, color: C.textStrong, marginBottom: 6 }}>Generator materialow</div>
            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
              Wgraj material lub wklej tekst, wybierz typy pytan i od razu zbuduj nowa sesje.
            </div>
          </div>

          <button onClick={generateQuestionsFromMaterial} style={s.btn("primary")}>
            <IcoCloud size={14} /> Generate questions
          </button>
        </div>

        <div
          className="generator-upload"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const file = e.dataTransfer?.files?.[0];
            if (file) handleGeneratorFile(file);
          }}
        >
          <button type="button" onClick={() => generatorFileRef.current?.click()} style={s.btn("soft")}>
            <IcoUpload size={14} /> click to upload
          </button>

          <div style={{ fontSize: 14, color: C.textSub }}>or drag & drop files here</div>

          <div className="generator-file-types">
            {["PDF", "Power Point", "Word docx", "Anki import", "Audio file", "Video file", "Image", "TXT", "XLSX"].map((label) => (
              <span key={label} className="soft-chip">
                {label}
              </span>
            ))}
          </div>

          <input
            ref={generatorFileRef}
            type="file"
            accept=".txt,.md,.csv,.json,.xlsx,.xls,.pdf,.doc,.docx,.ppt,.pptx,image/*,audio/*,video/*"
            onChange={handleGeneratorFileChange}
            style={{ display: "none" }}
          />
        </div>

        <div className="generator-link-row">
          <input
            value={generatorLink}
            onChange={(e) => setGeneratorLink(e.target.value)}
            placeholder="or paste any link here"
            style={s.input}
          />
          <span className="soft-chip">Websites</span>
          <span className="soft-chip">YouTube</span>
          <span className="soft-chip">Google Docs</span>
        </div>

          <div className="generator-config-grid" style={{ marginTop: 16 }}>
          <div style={{ ...s.cardSm, padding: 16, background: "rgba(255,255,255,.82)" }}>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Material
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, marginBottom: 10 }}>
              {generatorSourceName || "Brak pliku"}
            </div>
            {generatorPageTexts.length > 0 && (
              <div className="generator-page-range">
                <div>
                  <label style={s.label}>page range</label>
                  <input
                    type="number"
                    min={1}
                    max={generatorPageTexts.length}
                    value={generatorPageStart}
                    onChange={(e) => setGeneratorPageStart(Math.max(1, Math.min(Number(e.target.value || 1), generatorPageTexts.length)))}
                    style={s.input}
                  />
                </div>
                <div>
                  <label style={s.label}>to</label>
                  <input
                    type="number"
                    min={generatorPageStart}
                    max={generatorPageTexts.length}
                    value={generatorPageEnd}
                    onChange={(e) =>
                      setGeneratorPageEnd(Math.max(generatorPageStart, Math.min(Number(e.target.value || generatorPageTexts.length), generatorPageTexts.length)))
                    }
                    style={s.input}
                  />
                </div>
                <div className="generator-page-meta">{generatorPageTexts.length} pages total</div>
              </div>
            )}
            <textarea
              value={generatorSourceText}
              onChange={(e) => {
                setGeneratorSourceText(e.target.value);
                setGeneratorPageTexts([]);
                setGeneratorPageStart(1);
                setGeneratorPageEnd(1);
              }}
              rows={10}
              placeholder="Wklej tutaj tekst materialu, jesli nie chcesz korzystac z uploadu."
              style={{ ...s.input, resize: "vertical" }}
            />
            <div className="field-help">
              Bez backendowego parsera najlepiej dzialaja materialy tekstowe, PDF, CSV i XLSX. Link jest traktowany jako etykieta zrodla, nie jako automatyczny scraper.
            </div>
            {generatorPageTexts.length > 0 && <div className="field-help">Do generowania zostanie uzyty zakres stron {generatorPageStart}-{generatorPageEnd}.</div>}
          </div>

          <div style={{ ...s.cardSm, padding: 16, background: "rgba(255,255,255,.82)" }}>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Generation Setup
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={s.label}>Deck docelowy</label>
              <select value={generatorDeckName} onChange={(e) => setGeneratorDeckName(e.target.value)} style={s.input}>
                {editorDecks.map((deck) => (
                  <option key={deck} value={deck}>
                    {deck}
                  </option>
                ))}
              </select>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={s.label}>Number of questions</label>
              <div className="generator-density-row">
                {[
                  ["low", "low"],
                  ["med", "med"],
                  ["high", "high"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setGeneratorDensity(value)}
                    className={`generator-density-btn ${generatorDensity === value ? "active" : ""}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="field-help">Szacowany wynik: {generatorQuestionCount} pytan.</div>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={s.label}>Question types</label>
              <div className="generator-types-grid">
                {QUESTION_TYPES.map((type) => {
                  const active = generatorQuestionTypes.includes(type.id);
                  return (
                    <label key={type.id} className={`generator-check ${active ? "active" : ""}`}>
                      <input type="checkbox" checked={active} onChange={() => toggleGeneratorQuestionType(type.id)} />
                      <span>{type.label}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div style={{ marginBottom: 14 }}>
              <label style={s.label}>Generation language</label>
              <select value={generatorLanguage} onChange={(e) => setGeneratorLanguage(e.target.value)} style={s.input}>
                <option value="Polish">Polish</option>
                <option value="English">English</option>
              </select>
            </div>

            <div
              style={{
                padding: 12,
                borderRadius: 14,
                background: toneForStatus(generatorStatus.status).bg,
                color: toneForStatus(generatorStatus.status).color,
                border: `1px solid ${toneForStatus(generatorStatus.status).border}`,
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              {generatorStatus.message}
            </div>
          </div>
        </div>
      </div>

      <div style={{ ...s.card, padding: 18 }}>
        <div className="generator-head" style={{ marginBottom: 14 }}>
          <div>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Preview
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong }}>Wygenerowane pytania</div>
          </div>

          {generatorQuestions.length > 0 && (
            <button onClick={() => startQuiz(generatorQuestions, generatorQuestions.length)} style={s.btn("ghost")}>
              <IcoRight size={14} /> Start generated deck
            </button>
          )}
        </div>

        <div className="generator-preview-list">
          {generatorQuestions.length ? (
            generatorQuestions.map((question) => (
              <div key={question.id} className="generator-preview-item">
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <span className="soft-chip">#{question.questionNo}</span>
                  <span className="soft-chip">{questionTypeLabel(question.questionType)}</span>
                  <span className="soft-chip">{question.deck}</span>
                </div>
                <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong, lineHeight: 1.5 }}>{getQuestionDisplayText(question)}</div>
                <div style={{ marginTop: 10, fontSize: 14, color: C.textSub, lineHeight: 1.6 }}>
                  {question.questionType === "flashcard"
                    ? question.answerBack
                    : question.questionType === "type_answer"
                    ? `Akceptowane: ${formatQuestionAnswer(question, question.correctAnswers || [])}`
                    : question.questionType === "cloze_deletion"
                    ? `Pelna tresc: ${question.answerBack || revealClozeText(question.question || "")}`
                    : `Poprawna odpowiedz: ${formatQuestionAnswer(question, question.correctAnswers || question.correct)}`}
                </div>
              </div>
            ))
          ) : (
            <div style={{ fontSize: 14, color: C.textSub, lineHeight: 1.65 }}>
              Po generowaniu zobaczysz tutaj preview paczki pytan, zanim zaczniesz nowa sesje.
            </div>
          )}
        </div>
      </div>
    </div>
  );

  const EditorView = () => (
    <div className="editor-layout">
      <div className="editor-sidebar" style={{ ...s.card, padding: 16 }}>
        <div className="editor-toolbar">
          <div>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              Biblioteka pytan
            </div>
            <div style={{ fontSize: 24, fontWeight: 700, color: C.textStrong }}>Edytor pytan</div>
          </div>

          <button onClick={createNewQuestion} style={s.btn("soft")}>
            <IcoEdit size={14} /> Nowe pytanie
          </button>
        </div>

        <div className="editor-filter-grid">
          <input value={editorSearch} onChange={(e) => setEditorSearch(e.target.value)} placeholder="Szukaj po tresci, decku, tagach..." style={s.input} />

          <select value={editorDeckFilter} onChange={(e) => setEditorDeckFilter(e.target.value)} style={s.input}>
            <option value="all">Wszystkie decki</option>
            {editorDecks.map((deck) => (
              <option key={deck} value={deck}>
                {deck}
              </option>
            ))}
          </select>
        </div>

        <div className="editor-list">
          {editorFilteredQuestions.length ? (
            editorFilteredQuestions.map((question) => {
              const active = String(question.id) === String(editorSelectedId);
              return (
                <button
                  key={question.id}
                  type="button"
                  className={`editor-item ${active ? "active" : ""}`}
                  onClick={() => setEditorSelectedId(String(question.id))}
                >
                  <div className="editor-item-head">
                    <strong>#{question.questionNo}</strong>
                    <span className="soft-chip">{questionTypeLabel(question.questionType)}</span>
                  </div>
                  <div className="editor-item-title">{getQuestionDisplayText(question)}</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                    <span className="soft-chip">{question.deck}</span>
                    <span className="soft-chip">{question.category}</span>
                  </div>
                </button>
              );
            })
          ) : (
            <div style={{ fontSize: 14, color: C.textSub }}>Brak pytan po tym filtrze.</div>
          )}
        </div>
      </div>

      <div className="editor-main" style={{ ...s.card, padding: 18 }}>
        {editorDraft && (
          <>
            <div className="editor-toolbar" style={{ marginBottom: 14 }}>
              <div>
                <div className="tinyLabel" style={{ marginBottom: 8 }}>
                  Formularz
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, color: C.textStrong }}>
                  {selectedEditorQuestion ? "Edycja pytania" : "Nowe pytanie"}
                </div>
              </div>

              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button onClick={createNewQuestion} style={s.btn("ghost")}>
                  <IcoRefresh size={14} /> Wyczyść
                </button>
                <button onClick={saveEditorQuestion} style={s.btn("soft")}>
                  <IcoCheck size={14} /> Zapisz
                </button>
                <button onClick={deleteEditorQuestion} disabled={!selectedEditorQuestion} style={{ ...s.btn("danger"), opacity: selectedEditorQuestion ? 1 : 0.5 }}>
                  <IcoCross size={14} /> Usun
                </button>
              </div>
            </div>

            <div
              style={{
                marginBottom: 14,
                padding: 12,
                borderRadius: 14,
                background: toneForStatus(editorStatus.status).bg,
                color: toneForStatus(editorStatus.status).color,
                border: `1px solid ${toneForStatus(editorStatus.status).border}`,
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              {editorStatus.message}
            </div>

            <div className="editor-form-grid">
              <div>
                <label style={s.label}>Numer</label>
                <input
                  type="number"
                  value={editorDraft.questionNo}
                  onChange={(e) => updateEditorField("questionNo", Number(e.target.value || 0))}
                  style={s.input}
                />
              </div>

              <div>
                <label style={s.label}>Typ pytania</label>
                <select value={editorDraft.questionType} onChange={(e) => applyEditorQuestionType(e.target.value)} style={s.input}>
                  {QUESTION_TYPES.map((type) => (
                    <option key={type.id} value={type.id}>
                      {type.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={s.label}>Deck</label>
                <select value={editorDraft.deck} onChange={(e) => updateEditorField("deck", e.target.value)} style={s.input}>
                  {editorDecks.map((deck) => (
                    <option key={deck} value={deck}>
                      {deck}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={s.label}>Kategoria</label>
                <input value={editorDraft.category} onChange={(e) => updateEditorField("category", e.target.value)} style={s.input} />
              </div>

              <div className="editor-field-span">
                <label style={s.label}>Pytanie / przod karty</label>
                <textarea value={editorDraft.question} onChange={(e) => updateEditorField("question", e.target.value)} rows={4} style={{ ...s.input, resize: "vertical" }} />
              </div>

              <div className="editor-field-span">
                <div className="editor-media-grid">
                  <div>
                    <label style={s.label}>Obraz URL</label>
                    <input
                      value={editorDraft.imageUrl || ""}
                      onChange={(e) => updateEditorField("imageUrl", e.target.value)}
                      placeholder="/media/question.jpg lub https://..."
                      style={s.input}
                    />
                  </div>

                  <div>
                    <label style={s.label}>Dzwiek URL</label>
                    <input
                      value={editorDraft.audioUrl || ""}
                      onChange={(e) => updateEditorField("audioUrl", e.target.value)}
                      placeholder="/media/question.mp3 lub https://..."
                      style={s.input}
                    />
                  </div>
                </div>
              </div>

              {editorDraft.questionType === "flashcard" ? (
                <div className="editor-field-span">
                  <label style={s.label}>Odpowiedz / tyl karty</label>
                  <textarea
                    value={editorDraft.answerBack}
                    onChange={(e) => updateEditorField("answerBack", e.target.value)}
                    rows={4}
                    style={{ ...s.input, resize: "vertical" }}
                  />
                </div>
              ) : editorDraft.questionType === "type_answer" || editorDraft.questionType === "cloze_deletion" ? (
                <div className="editor-field-span">
                  <div className="editor-media-grid">
                    <div>
                      <label style={s.label}>Akceptowane odpowiedzi</label>
                      <textarea
                        value={editorDraft.acceptedAnswersText || ""}
                        onChange={(e) => updateEditorField("acceptedAnswersText", e.target.value)}
                        rows={4}
                        placeholder={editorDraft.questionType === "cloze_deletion" ? "Jedna odpowiedz na luke, kazda w nowej linii" : "Jedna odpowiedz w linii lub kilka wariantow"}
                        style={{ ...s.input, resize: "vertical" }}
                      />
                      <div className="field-help">
                        {editorDraft.questionType === "cloze_deletion"
                          ? "W tresci pytania uzyj skladni {{c1::odpowiedz}} lub {{c1::odpowiedz::podpowiedz}}."
                          : "Mozesz podac kilka akceptowanych wariantow, po jednym w linii."}
                      </div>
                    </div>

                    <div>
                      <label style={s.label}>{editorDraft.questionType === "cloze_deletion" ? "Pelna wersja zdania" : "Wzorcowa odpowiedz"}</label>
                      <textarea
                        value={editorDraft.answerBack}
                        onChange={(e) => updateEditorField("answerBack", e.target.value)}
                        rows={4}
                        placeholder={editorDraft.questionType === "cloze_deletion" ? "Opcjonalnie. Zostaw puste, aby system uzyl pelnej tresci cloze." : "Opcjonalna odpowiedz wzorcowa"}
                        style={{ ...s.input, resize: "vertical" }}
                      />
                    </div>
                  </div>
                </div>
              ) : (
                <div className="editor-field-span">
                  <label style={s.label}>Opcje</label>
                  <div className="editor-option-grid">
                    {optionKeys.map((key) => {
                      const checked = parseAnswerKeys(editorDraft.correctAnswers || editorDraft.correct).includes(key);
                      return (
                        <div key={key} className="editor-option-card">
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                            <strong style={{ color: C.textStrong }}>{key}</strong>
                            <button
                              type="button"
                              onClick={() => updateEditorCorrectAnswer(key)}
                              style={{
                                ...s.btn(checked ? "soft" : "ghost"),
                                padding: "7px 10px",
                                minWidth: 104,
                              }}
                            >
                              {editorDraft.questionType === "multi_select" ? "Poprawna" : "Wybierz"}
                            </button>
                          </div>
                          <input value={editorDraft.options[key]} onChange={(e) => updateEditorOption(key, e.target.value)} placeholder={`Opcja ${key}`} style={s.input} />
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="editor-field-span">
                <label style={s.label}>Wyjasnienie</label>
                <textarea
                  value={editorDraft.explanation}
                  onChange={(e) => updateEditorField("explanation", e.target.value)}
                  rows={3}
                  style={{ ...s.input, resize: "vertical" }}
                />
              </div>

              <div>
                <label style={s.label}>Poziom</label>
                <select value={editorDraft.difficulty} onChange={(e) => updateEditorField("difficulty", e.target.value)} style={s.input}>
                  <option value="easy">easy</option>
                  <option value="medium">medium</option>
                  <option value="hard">hard</option>
                </select>
              </div>

              <div className="editor-field-span">
                <label style={s.label}>Tagi</label>
                <input
                  value={editorDraft.tagsText}
                  onChange={(e) => updateEditorField("tagsText", e.target.value)}
                  placeholder="anki::basic jungle::mcq tricky"
                  style={s.input}
                />
              </div>
            </div>

            {editorPreview && (
              <div className="editor-preview" style={{ ...s.cardSm, padding: 16 }}>
                <div className="tinyLabel" style={{ marginBottom: 10 }}>
                  Preview
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                  <span className="soft-chip">{questionTypeLabel(editorPreview.questionType)}</span>
                  <span className="soft-chip">{editorPreview.deck}</span>
                  <span className="soft-chip">{editorPreview.category}</span>
                </div>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong, lineHeight: 1.45 }}>
                  {getQuestionDisplayText(editorPreview) || "Podglad pytania pojawi sie tutaj."}
                </div>

                <QuestionMediaBlock imageUrl={editorPreview.imageUrl} audioUrl={editorPreview.audioUrl} compact />

                {editorPreview.questionType === "flashcard" ? (
                  <div style={{ marginTop: 14, padding: 14, borderRadius: 16, background: "#fff", border: `1px solid ${C.border}` }}>
                    <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Tyl karty</div>
                    <div style={{ fontSize: 15, color: C.textStrong, lineHeight: 1.7 }}>{editorPreview.answerBack || "Brak odpowiedzi."}</div>
                  </div>
                ) : editorPreview.questionType === "type_answer" ? (
                  <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
                    <div style={{ padding: "12px 14px", borderRadius: 16, border: `1px solid ${C.success}`, background: C.successBg, color: C.successText }}>
                      <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Akceptowane odpowiedzi</div>
                      <div style={{ fontSize: 15, lineHeight: 1.7 }}>{formatQuestionAnswer(editorPreview, editorPreview.correctAnswers || []) || "Brak wzorca."}</div>
                    </div>
                    {!!String(editorPreview.answerBack || "").trim() && (
                      <div style={{ padding: "12px 14px", borderRadius: 16, border: `1px solid ${C.border}`, background: "#fff", color: C.textStrong }}>
                        <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Wzorcowa odpowiedz</div>
                        <div style={{ fontSize: 15, lineHeight: 1.7 }}>{editorPreview.answerBack}</div>
                      </div>
                    )}
                  </div>
                ) : editorPreview.questionType === "cloze_deletion" ? (
                  <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
                    <div style={{ padding: "12px 14px", borderRadius: 16, border: `1px solid ${C.border}`, background: "#fff", color: C.textStrong }}>
                      <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Pelna tresc</div>
                      <div style={{ fontSize: 15, lineHeight: 1.7 }}>{editorPreview.answerBack || revealClozeText(editorPreview.question || "")}</div>
                    </div>
                    <div style={{ padding: "12px 14px", borderRadius: 16, border: `1px solid ${C.success}`, background: C.successBg, color: C.successText }}>
                      <div style={{ fontSize: 12, color: C.textSub, marginBottom: 6 }}>Luki</div>
                      <div style={{ fontSize: 15, lineHeight: 1.7 }}>{formatQuestionAnswer(editorPreview, editorPreview.correctAnswers || []) || "Brak luk."}</div>
                    </div>
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: 10, marginTop: 14 }}>
                    {getVisibleOptionKeys(editorPreview).map((key) => {
                      const correct = (editorPreview.correctAnswers || []).includes(key);
                      return (
                        <div
                          key={key}
                          style={{
                            padding: "12px 14px",
                            borderRadius: 16,
                            border: `1px solid ${correct ? C.success : C.border}`,
                            background: correct ? C.successBg : "#fff",
                            color: correct ? C.successText : C.textStrong,
                          }}
                        >
                          <strong style={{ marginRight: 8 }}>{key}</strong>
                          {editorPreview.options[key]}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );

  const SettingsView = () => (
    <div className="settings-grid">
      <div className="settings-main-card" style={{ ...s.card, padding: 16 }}>
        <div>
          <div className="tinyLabel" style={{ marginBottom: 8 }}>
            Konfiguracja quizu
          </div>
          <div style={{ fontSize: 24, fontWeight: 600, color: C.textStrong, marginBottom: 6 }}>Ustawienia nauki</div>
          <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.55, marginBottom: 14 }}>
            Minimum chaosu, maksimum skupienia. Wszystko w jednym spokojnym panelu.
          </div>

          <div className="settings-section-grid">
            <div>
              <label style={s.label}>Aktywny deck</label>
              <div style={{ ...s.input, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span>{selectedDeck === ALL_DECKS_LABEL ? "Wszystkie decki" : selectedDeck}</span>
                <span className="soft-chip">zakladka Decki</span>
              </div>
            </div>

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

            <div>
              <label style={s.label}>Zakres po tagach</label>
              <div style={{ ...s.input, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span>{filteredQuestionPool.length} pytan</span>
                <span className="soft-chip">{selectedTagFilters.length ? `${selectedTagFilters.length} tagi` : "bez filtra"}</span>
              </div>
            </div>
          </div>

          <div className="field-help" style={{ marginTop: 12 }}>
            Wybor i uruchamianie deckow przenioslem do osobnej zakladki `Decki`, a ustawienia zostaja tylko panelem konfiguracji.
          </div>
        </div>

        <div>
          <div className="settings-actions" style={{ marginTop: 10 }}>
            <button onClick={() => fileRef.current?.click()} style={s.btn("ghost")}>
              <IcoUpload size={14} /> Import pytań
            </button>

            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.txt" onChange={handleImport} style={{ display: "none" }} />
          </div>

          {importMsg && <div style={{ marginTop: 12, fontSize: 13, color: C.textSub }}>{importMsg}</div>}
        </div>
      </div>

      <div className="settings-stack">
        <div style={{ ...s.card, padding: 14, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <IcoUser size={16} />
              <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Konto</div>
            </div>
            {authUser && (
              <span className="soft-chip">
                <IcoCheck size={12} /> zalogowany
              </span>
            )}
          </div>

          {authUser ? (
            <>
              <div style={{ marginBottom: 12 }}>
                <label style={s.label}>E-mail</label>
                <input value={authUser.email || authEmail} readOnly style={{ ...s.input, background: C.cardAlt }} />
              </div>

              <div style={{ marginBottom: 12 }}>
                <label style={s.label}>Nazwa użytkownika</label>
                <input value={profileNameDraft} onChange={(e) => setProfileNameDraft(e.target.value)} placeholder="Jak pokazywać profil" style={s.input} />
              </div>

              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button onClick={handleProfileSave} style={s.btn("soft")}>
                  <IcoUser size={14} /> Zapisz profil
                </button>
                <button onClick={handleSignOut} style={s.btn("ghost")}>
                  <IcoLogout size={14} /> Wyloguj
                </button>
              </div>

              <div className="field-help">
                Profil zapisuje się w tabeli `profiles`, a wyniki i tagi są powiązane z tym kontem.
              </div>
            </>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button
                  type="button"
                  onClick={() => setAuthMode("login")}
                  style={{ ...s.btn(authMode === "login" ? "primary" : "ghost"), flex: 1, padding: "8px 10px" }}
                >
                  Logowanie
                </button>
                <button
                  type="button"
                  onClick={() => setAuthMode("register")}
                  style={{ ...s.btn(authMode === "register" ? "primary" : "ghost"), flex: 1, padding: "8px 10px" }}
                >
                  Rejestracja
                </button>
              </div>

              <div style={{ marginBottom: 12 }}>
                <label style={s.label}>E-mail</label>
                <input value={authEmail} onChange={(e) => setAuthEmail(e.target.value)} placeholder="twoj@email.com" style={s.input} />
              </div>

              <div style={{ marginBottom: 12 }}>
                <label style={s.label}>Hasło</label>
                <input type="password" value={authPassword} onChange={(e) => setAuthPassword(e.target.value)} placeholder="minimum 6 znaków" style={s.input} />
              </div>

              <div style={{ marginBottom: 12 }}>
                <label style={s.label}>Nazwa użytkownika</label>
                <input
                  value={profileNameDraft}
                  onChange={(e) => setProfileNameDraft(e.target.value)}
                  placeholder="opcjonalnie przy rejestracji"
                  style={s.input}
                />
              </div>

              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button onClick={handleAuthSubmit} style={s.btn("soft")}>
                  <IcoKey size={14} /> {authMode === "register" ? "Utwórz konto" : "Zaloguj"}
                </button>
              </div>

              <div className="field-help">Konto wykorzystuje Supabase Auth. Po zalogowaniu wyniki i tagi będą zapisywane w bazie.</div>
            </>
          )}

          <div
            style={{
              marginTop: 12,
              padding: 12,
              borderRadius: 14,
              background: toneForStatus(authStatus.status).bg,
              color: toneForStatus(authStatus.status).color,
              border: `1px solid ${toneForStatus(authStatus.status).border}`,
              fontSize: 13,
              lineHeight: 1.6,
            }}
          >
            {authStatus.message}
          </div>

          {userProfile && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              <span className="soft-chip">Profil: {userProfile.display_name || userProfile.email}</span>
              <span className="soft-chip">Tagi prywatne: {Object.keys(userTagMap).length}</span>
            </div>
          )}
        </div>

        <div style={{ ...s.card, padding: 14, minHeight: 0 }}>
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
            <label style={s.label}>Cloud API key</label>
            <input
              type="password"
              value={cloudApiKeyDraft}
              onChange={(e) => setCloudApiKeyDraft(e.target.value)}
              placeholder="sk-ant-... (opcjonalnie)"
              style={s.input}
            />
            <div className="field-help">
              To pole jest tymczasowe i nie jest zapisywane w aplikacji. Jeśli zostawisz je puste, Cloud użyje sekretu `ANTHROPIC_API_KEY` z Supabase Edge Function.
            </div>
            <div className="field-help">
              Klucz zaczynający się od `eyJ...` to zwykle Supabase anon JWT, nie klucz Cloud API.
            </div>
          </div>

          <div style={{ marginBottom: 12 }}>
            <label style={s.label}>Model</label>
            <input value={cloudModel} onChange={(e) => setCloudModel(e.target.value)} placeholder={DEFAULT_MODEL} style={s.input} />
          </div>

          <div className="field-help">
            {CLOUD_BROWSER_NOTICE}
          </div>

          <div className="field-help">
            Docelowo najlepiej ustawić klucz jako sekret Supabase, ale do szybkiego testu możesz wkleić go tutaj jednorazowo.
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
            <button onClick={checkCloudConnection} style={s.btn("soft")}>
              <IcoCloud size={14} /> Test Cloud AI
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

        <div style={{ ...s.card, padding: 14, minHeight: 0 }}>
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
            <div className="field-help">
              Jeśli chcesz mieć te dane stale bez ręcznego wpisywania, ustaw `VITE_SUPABASE_URL` i `VITE_SUPABASE_PUBLISHABLE_KEY` w `.env.local`.
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

        <div style={{ ...s.card, padding: 14, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoTag size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Tagi i filtry</div>
          </div>

          <div className="field-help">
            Tagi są prywatne dla użytkownika i działają jak w Anki: możesz przypisać wiele etykiet do pytania, a potem budować sesje po tagach.
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            <button
              type="button"
              onClick={() => setSelectedTagFilters([])}
              style={{
                ...s.btn(selectedTagFilters.length ? "ghost" : "soft"),
                padding: "8px 10px",
              }}
            >
              Wszystkie
            </button>
            {availableTags.map((tag) => {
              const active = selectedTagFilters.some((item) => item.toLowerCase() === tag.toLowerCase());
              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => toggleTagFilter(tag)}
                  style={{
                    ...s.btn(active ? "soft" : "ghost"),
                    padding: "8px 10px",
                  }}
                >
                  #{tag}
                </button>
              );
            })}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 14 }}>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Aktywne filtry</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{selectedTagFilters.length || "—"}</div>
            </div>
            <div style={{ ...s.metric, background: C.cardAlt }}>
              <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>Pytania po filtrze</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{filteredQuestionPool.length}</div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            {selectedTagFilters.length ? (
              selectedTagFilters.map((tag) => (
                <span key={tag} className="soft-chip">
                  #{tag}
                </span>
              ))
            ) : (
              <span className="soft-chip">Brak aktywnych filtrów</span>
            )}
          </div>
        </div>

        <div className="settings-summary-card" style={{ ...s.card, padding: 14, minHeight: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <IcoTrending size={16} />
            <div style={{ fontSize: 16, fontWeight: 700, color: C.textStrong }}>Stan i tempo</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[
              ["Baza pytań", questionPool.length],
              ["Sesje", history.length],
              ["Mastery", attemptDraft ? `${attemptDraft.mastery}%` : "—"],
              ["Śr. czas", answeredCount ? fmt(stats.avgResponseMs) : "—"],
            ].map(([label, value]) => (
              <div key={label} style={{ ...s.metric, background: C.cardAlt }}>
                <div style={{ fontSize: 11, color: C.textSub, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.textStrong }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
            <span className="soft-chip">
              <IcoCheck size={12} />
              {sbEnabled ? "Supabase gotowe" : "Tryb lokalny"}
            </span>
            <span className="soft-chip">
              <IcoCloud size={12} />
              {cloudApiEnabled ? "Cloud przez Edge Function" : "Cloud AI wyłączone"}
            </span>
            {attemptDraft && <span className="soft-chip">Wynik: {attemptDraft.score}/{attemptDraft.totalQuestions}</span>}
          </div>
        </div>
      </div>
    </div>
  );

  const LandingView = () => (
    <div className="landing-shell">
      <div className="landing-nav">
        <div className="landing-brand">
          <ZenQuizLogo size={54} />
          <div>
            <div className="landing-brand-title">Zen Quiz</div>
            <div className="landing-brand-copy">Pytania generowane z twoich materiałów w kilka sekund.</div>
          </div>
        </div>

        <div className="landing-auth-switch">
          <button type="button" className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")}>
            sign up
          </button>
          <span className="landing-auth-separator">|</span>
          <button type="button" className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")}>
            login
          </button>
        </div>
      </div>

      <div className="landing-main">
        <section className="landing-hero-card">
          <div className="landing-kicker">get practice questions made for you in seconds</div>
          <div className="landing-copy">
            Dodaj dokument lub URL, a Zen Quiz przygotuje pierwszą paczkę pytań do nauki i od razu ułoży ją w deckach.
          </div>

          <div
            className="landing-upload-panel"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const file = e.dataTransfer?.files?.[0];
              if (file) handleGeneratorFile(file);
            }}
          >
            <div className="landing-upload-title">{generatorSourceName ? generatorSourceName : "drag a file here"}</div>
            <div className="landing-upload-copy">{generatorSourceName ? "plik jest już gotowy do generatora" : "or click to select a file"}</div>
            <button type="button" onClick={() => generatorFileRef.current?.click()} style={s.btn("soft")}>
              <IcoUpload size={14} /> wybierz plik
            </button>
            <input
              ref={generatorFileRef}
              type="file"
              accept=".txt,.md,.csv,.json,.xlsx,.xls,.pdf,.doc,.docx,.ppt,.pptx,image/*,audio/*,video/*"
              onChange={handleGeneratorFileChange}
              style={{ display: "none" }}
            />
          </div>

          <div className="landing-or">or</div>

          <input
            value={generatorLink}
            onChange={(e) => setGeneratorLink(e.target.value)}
            placeholder="paste any link here"
            className="landing-link-input"
          />

          <div className="landing-support-block">
            <div className="landing-support-title">works on:</div>
            <div className="landing-support-grid">
              {[
                { label: "lecture slides", icon: <IcoLayers size={15} /> },
                { label: "YouTube videos", icon: <IcoPlay size={15} /> },
                { label: "PDFs", icon: <IcoFileText size={15} /> },
                { label: "video / audio files", icon: <IcoVideo size={15} /> },
                { label: "Word documents", icon: <IcoFileText size={15} /> },
                { label: "textbooks", icon: <IcoBook size={15} /> },
                { label: "PowerPoints", icon: <IcoLayers size={15} /> },
                { label: "notes", icon: <IcoEdit size={15} /> },
              ].map((item) => (
                <div key={item.label} className="landing-support-item">
                  <span className="landing-support-icon">{item.icon}</span>
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
          </div>

          <div
            className="landing-generator-status"
            style={{
              background: toneForStatus(generatorStatus.status).bg,
              color: toneForStatus(generatorStatus.status).color,
              border: `1px solid ${toneForStatus(generatorStatus.status).border}`,
            }}
          >
            {generatorStatus.message}
          </div>
        </section>

        <aside className="landing-auth-card">
          <div>
            <div className="tinyLabel" style={{ marginBottom: 8 }}>
              {authMode === "register" ? "Create Account" : "Welcome Back"}
            </div>
            <div className="landing-auth-title">{authMode === "register" ? "Załóż konto" : "Zaloguj się"}</div>
            <div className="landing-auth-copy">
              {authMode === "register"
                ? "Po rejestracji zapisujemy wyniki, tagi i decki użytkownika w Supabase."
                : "Zaloguj się, aby wrócić do generatora, wyników i swojej biblioteki pytań."}
            </div>
          </div>

          <form
            className="landing-auth-form"
            onSubmit={(e) => {
              e.preventDefault();
              handleAuthSubmit();
            }}
          >
            <div>
              <label style={s.label}>E-mail</label>
              <input value={authEmail} onChange={(e) => setAuthEmail(e.target.value)} placeholder="twoj@email.com" style={s.input} />
            </div>

            <div>
              <label style={s.label}>Hasło</label>
              <input
                type="password"
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                placeholder="minimum 6 znaków"
                style={s.input}
              />
            </div>

            {authMode === "register" && (
              <div>
                <label style={s.label}>Nazwa użytkownika</label>
                <input
                  value={profileNameDraft}
                  onChange={(e) => setProfileNameDraft(e.target.value)}
                  placeholder="jak mamy pokazywać twój profil"
                  style={s.input}
                />
              </div>
            )}

            <button type="submit" style={s.btn("primary")} disabled={authStatus.status === "loading"}>
              <IcoKey size={14} /> {authMode === "register" ? "Utwórz konto" : "Zaloguj"}
            </button>
          </form>

          {authStatus.status !== "idle" && <div
            className="landing-auth-status"
            style={{
              background: toneForStatus(authStatus.status).bg,
              color: toneForStatus(authStatus.status).color,
              border: `1px solid ${toneForStatus(authStatus.status).border}`,
            }}
          >
            {authStatus.message}
          </div>}

          {!sbEnabled && <div className="landing-config-card">
            <div className="landing-config-head">
              <span>Supabase connection</span>
              <span className={`landing-config-badge ${sbEnabled ? "ready" : ""}`}>{sbEnabled ? "gotowe" : "wymaga ustawień"}</span>
            </div>

            <div className="landing-config-grid">
              <div>
                <label style={s.label}>Supabase URL</label>
                <input value={supabaseUrl} onChange={(e) => setSupabaseUrl(e.target.value)} placeholder="https://twoj-projekt.supabase.co" style={s.input} />
              </div>

              <div>
                <label style={s.label}>Publishable / anon key</label>
                <input
                  type="password"
                  value={supabaseAnonKey}
                  onChange={(e) => setSupabaseAnonKey(e.target.value)}
                  placeholder="sb_publishable_... albo pełny JWT"
                  style={s.input}
                />
              </div>
            </div>

            <div className="field-help">
              Jeśli pola są już zapisane w `.env.local` albo localStorage, nie musisz ich ruszać. Ten panel jest tylko po to, żeby ekran startowy działał od razu.
            </div>
          </div>}
        </aside>
      </div>
    </div>
  );

  const renderTab = () => {
    if (activeTab === "quiz") return QuizView();
    if (activeTab === "decks") return DecksView();
    if (activeTab === "generator") return GeneratorView();
    if (activeTab === "editor") return EditorView();
    if (activeTab === "results") return ResultsView();
    if (activeTab === "calendar") return EnhancedCalendarView();
    if (activeTab === "plan") return PlanView();
    if (activeTab === "settings") return SettingsView();
    return QuizView();
  };

  if (!authUser) {
    return LandingView();
  }

  return (
    <>
      <div className="app-shell">
        <div className="app-frame">
          <aside className="sidebar">
            <div className="brand-panel">
              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <ZenQuizLogo size={60} />
                <div>
                  <div className="brand-title" style={{ fontSize: 26, fontWeight: 700, color: C.textStrong }}>Zen Quiz</div>
                  <div style={{ fontSize: 12, color: C.textSub, marginTop: 4, lineHeight: 1.45 }}>
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
                      <div style={{ fontSize: 22, fontWeight: 700, color: C.textStrong, lineHeight: 1.1 }}>Postęp nauki</div>
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

                <div className="sidebar-session-action">
                  <button
                    onClick={() => startQuiz(undefined, quizLength)}
                    style={{
                      ...s.btn("ghost"),
                      width: "100%",
                      justifyContent: "center",
                      padding: "10px 14px",
                    }}
                  >
                    <IcoRefresh size={14} /> Nowa sesja
                  </button>
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
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              {activeTab !== "quiz" && (
                <div className="workspace-header">
                  <div>
                    <div className="workspace-title" style={{ fontSize: 36, fontWeight: 700, color: C.textStrong }}>
                      {activeTabMeta.label}
                    </div>
                  </div>
                </div>
              )}

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
