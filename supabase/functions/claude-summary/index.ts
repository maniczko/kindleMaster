const DEFAULT_MODEL = "claude-sonnet-4-5-20250929";
const ANTHROPIC_URL = "https://api.anthropic.com/v1/messages";
const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Cache-Control": "no-store",
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
    },
  });
}

function getErrorMessage(error: unknown) {
  const message = String((error as Error)?.message || error || "Unknown error").trim();
  return message.length > 500 ? `${message.slice(0, 497)}...` : message;
}

async function callAnthropic({ apiKey, model, prompt, maxTokens = 350 }: { apiKey: string; model: string; prompt: string; maxTokens?: number }) {
  const response = await fetch(ANTHROPIC_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: model || DEFAULT_MODEL,
      max_tokens: maxTokens,
      temperature: 0.3,
      messages: [{ role: "user", content: prompt }],
    }),
  });

  const text = await response.text();
  let data: any = null;

  try {
    data = text ? JSON.parse(text) : null;
  } catch {}

  if (!response.ok) {
    throw new Error(data?.error?.message || data?.error || text || `Anthropic HTTP ${response.status}`);
  }

  const content = (data?.content || []).filter((item: any) => item?.type === "text").map((item: any) => item.text).join("\n").trim();
  if (!content) throw new Error("Anthropic returned empty content");

  return content;
}

function buildTrainingPrompt(payload: Record<string, unknown>) {
  return [
    "Jesteś trenerem przygotowującym do nauki testowej.",
    "Napisz krótkie, konkretne podsumowanie treningu po polsku.",
    "Struktura: 1) Co poszło dobrze 2) Co poszło źle 3) Na co zwrócić uwagę 4) Obszary do poprawy.",
    "Maksymalnie 180 słów.",
    "Bądź praktyczny i zwięzły.",
    "Dane sesji:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildQuestionExplanationPrompt(payload: Record<string, unknown>) {
  return [
    "Jestes cierpliwym tutorem wyjasniajacym pojedyncza odpowiedz w quizie.",
    "Masz wyjasnic szerzej, ale konkretnie i praktycznie.",
    "Zwroc zwykly tekst po polsku, bez JSON, bez markdown i bez tabel.",
    "Struktura odpowiedzi:",
    "1. Werdykt: 1 lub 2 zdania o tym, czy odpowiedz byla trafna i co to znaczy merytorycznie.",
    "2. Dlaczego poprawna odpowiedz jest poprawna: 2 do 4 zdan, jasno i rzeczowo.",
    "3. Gdzie rozumowanie uzytkownika bylo dobre albo gdzie sie rozjechalo: 2 do 4 zdan.",
    "4. Na co uwazac w podobnych pytaniach: 2 lub 3 konkretne wskazowki.",
    "5. Mini powtorka: 2 lub 3 krotkie punkty na koniec.",
    "Jesli pytanie jest typu flashcard, type_answer albo cloze_deletion, dopasuj komentarz do tego typu odpowiedzi.",
    "Jesli odpowiedz byla poprawna, nadal wyjasnij merytoryke zamiast ograniczac sie do pochwal.",
    "Nie zmyslaj faktow spoza payload. Opieraj sie tylko na tresci pytania, odpowiedziach i dostarczonym wyjasnieniu referencyjnym.",
    "Cel: 180 do 320 slow.",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildQuestionPracticalUsePrompt(payload: Record<string, unknown>) {
  return [
    "Jestes tutorem, ktory przeklada pojedyncze pytanie z quizu na realna praktyke.",
    "Masz pokazac, jak wykorzystac te wiedze poza samym zapamietaniem definicji.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"mode":"vocabulary|technical_rule|exam_application|general_concept","title":"...","usageSummary":"...","realWorldApplications":["..."],"examples":["...","..."],"examApplication":"...","practiceTask":"..."}',
    "Zasady:",
    "- mode dobierz na podstawie payload",
    "- usageSummary: 2 do 4 zdan, konkretnie i praktycznie",
    "- realWorldApplications: 2 do 4 realnych zastosowan, a nie ogolnikow",
    "- examples: dokladnie 2 elementy",
    "- jesli to slowko, fraza albo termin jezykowy, examples musza byc 2 naturalnymi zdaniami z uzyciem tego slowa lub frazy",
    "- jesli to regula techniczna, realWorldApplications maja pokazac konkretne systemy, decyzje, zadania lub momenty pracy, gdzie to stosujesz",
    "- jesli to temat egzaminacyjny lub procesowy, examApplication ma pokazac realny scenariusz z pracy, projektu, programu albo decyzji managerskiej",
    "- practiceTask: 1 krotkie cwiczenie do wykonania od razu po odpowiedzi",
    "- nie zmyslaj faktow spoza payload; opieraj sie tylko na dostarczonej tresci i najbardziej wiarygodnym praktycznym przełozeniu z tych danych",
    "- odpowiedz ma byc po polsku, ale examples moga zawierac slowo obcojezyczne jesli takie jest w materiale",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildStudyPlanPrompt(payload: Record<string, unknown>) {
  return [
    "Jestes trenerem przygotowujacym szczegolowy plan nauki po polsku.",
    "Masz przygotowac adaptacyjny plan powtorek w stylu Anki, ale z komentarzem AI.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"readiness":"...","recommendation":"...","improvements":["..."],"focusAreas":[{"category":"...","priority":"Wysoki|Sredni|Niski","accuracy":62,"reviewCount":18,"weakHits":3,"deck":"PgMP","reason":"...","suggestion":"..."}],"reviewQueue":[{"label":"Dzisiaj","dueDate":"2026-03-13","dueLabel":"13 mar","category":"...","priority":"Wysoki","duration":"30m","deck":"PgMP","reason":"...","task":"..."}],"weeklyPlan":[{"day":"Pon","date":"2026-03-13","dateLabel":"13 mar","task":"...","duration":"25m","focusCategory":"...","priority":"Wysoki","deck":"PgMP","note":"..."}]}',
    "Zasady:",
    "- recommendation: 1 zwiezly akapit po polsku",
    "- improvements: 3 do 5 konkretnych punktow",
    "- focusAreas: 3 do 6 najwazniejszych kategorii lub zakresow do poprawy",
    "- reviewQueue: 4 do 8 najblizszych powtorek, ulozonych jak kolejka spaced repetition",
    "- weeklyPlan: 7 dni od Pon do Nd",
    "- plan ma byc praktyczny i oparty na najslabszych obszarach, decku, trendzie wynikow i historii powtorek",
    "- accuracy, reviewCount i weakHits maja byc liczbami",
    "- priority ustaw jako Wysoki, Sredni albo Niski",
    "- dueDate i date zwracaj w formacie YYYY-MM-DD",
    "- dueLabel i dateLabel zwracaj po polsku, krotko, np. 13 mar",
    "- suggestion ma zawierac konkretna wskazowke jak sie uczyc tego obszaru",
    "- task i note maja byc gotowe do wpisania do kalendarza jako blok nauki",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");

  return [
    "Jestes trenerem przygotowujacym szczegolowy plan nauki po polsku.",
    "Na podstawie wynikow przygotuj realistyczny plan tygodniowy.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"readiness":"...","recommendation":"...","improvements":["..."],"weeklyPlan":[{"day":"Pon","task":"...","duration":"25m"}]}',
    "Zasady:",
    "- recommendation: 1 zwięzly akapit",
    "- improvements: 3 do 5 konkretnych punktow",
    "- weeklyPlan: 7 dni od Pon do Nd",
    "- plan ma byc praktyczny i oparty na najslabszych obszarach oraz trendzie wynikow",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildExamReadinessPrompt(payload: Record<string, unknown>) {
  return [
    "Jestes ekspertem przygotowujacym do egzaminu zawodowego i masz ocenic realna gotowosc decku.",
    "Masz porownac cel egzaminacyjny, opis wymagan, zrodla oraz metryki decku.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"readyScore":68,"coverageScore":71,"confidence":"Wysoka|Srednia|Niska","summary":"...","strengths":["..."],"knowledgeGaps":["..."],"nextMilestones":["..."],"categoryReadiness":[{"category":"...","readiness":62,"gap":18,"priority":"Wysoki|Sredni|Niski","verdict":"...","missing":"..."}]}',
    "Zasady:",
    "- readyScore i coverageScore maja byc liczbami 0-100",
    "- summary: jeden zwiezly akapit po polsku, konkretny i biznesowy",
    "- strengths: 2 do 4 najwazniejsze mocne strony",
    "- knowledgeGaps: 3 do 5 najwazniejszych luk merytorycznych",
    "- nextMilestones: 3 do 5 konkretnych krokow na najblizsze dni",
    "- categoryReadiness: 3 do 8 kategorii, uporzadkuj od najslabszej",
    "- verdict ma opisywac czy obszar jest gotowy, blisko celu albo daleko od celu",
    "- missing ma mowic czego merytorycznie brakuje lub jaki blok powtorek zamknac",
    "- nie zmyslaj faktow spoza payload; jesli brakuje danych, zaznacz to praktycznie",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildQuestionGenerationPrompt(payload: Record<string, unknown>) {
  return [
    "Jestes systemem generujacym pytania do nauki z dostarczonego materialu.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"questions":[{"questionType":"single_choice|multi_select|flashcard|cloze_deletion|type_answer","question":"...","options":{"A":"...","B":"...","C":"...","D":"..."},"correctAnswers":["..."],"answerBack":"...","explanation":"...","difficulty":"easy|medium|hard","category":"...","tags":["..."]}]}',
    "Zasady:",
    "- generuj tylko typy wskazane w payload.questionTypes",
    "- single_choice: 4 opcje, 1 poprawna odpowiedz, correctAnswers = ['A']",
    "- multi_select: 4 opcje, 2 lub 3 poprawne odpowiedzi, correctAnswers = ['A','C']",
    "- flashcard: bez opcji, answerBack wymagane",
    "- cloze_deletion: pytanie z markerami {{c1::odpowiedz}} lub {{c1::odpowiedz::podpowiedz}}",
    "- type_answer: bez opcji, correctAnswers to lista akceptowanych odpowiedzi tekstowych",
    "- explanation ma byc krotkie i praktyczne",
    "- tags maja byc zwięzłe i przydatne do filtrowania",
    "- liczba pytan ma byc bliska payload.questionCount",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildKeyPointExtractionPrompt(payload: Record<string, unknown>) {
  return [
    "Jestes analitykiem materialu edukacyjnego.",
    "Na podstawie materialu wybierz tylko najwazniejsze tresci, z ktorych warto tworzyc pytania.",
    "Priorytet: definicje, etapy, role, reguly, zaleznosci przyczynowo-skutkowe, wyjatki, porownania, wymagania, ryzyka i kluczowe liczby.",
    "Pomijaj ciekawostki, wstepy, ozdobniki, marginalne detale i tresci bez wartosci do nauki lub egzaminu.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Format: {"keyPoints":[{"topic":"...","importance":"high|medium","evidence":"...","reason":"..."}]}',
    "Zasady:",
    "- maksymalnie 12 punktow",
    "- evidence ma byc krotkim cytatem lub wierna parafraza z materialu",
    "- jesli material jest slaby, zwroc mniej punktow zamiast dopelniac liste na sile",
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function buildReviewedQuestionGenerationPrompt(payload: Record<string, unknown>, keyPoints: Array<Record<string, unknown>> = []) {
  return [
    "Jestes systemem generujacym pytania do nauki z dostarczonego materialu.",
    "Masz korzystac tylko z tresci waznych, istotnych i jednoznacznie wspartych materialem.",
    "Najpierw oprzyj sie na sekcji keyPoints, a dopiero potem na reszcie materialu.",
    "Jesli material nie wspiera jednoznacznej odpowiedzi, nie tworz pytania.",
    "Pomijaj pytania trywialne, poboczne, zbyt ogolne albo o malo istotne detale.",
    "Zwroc tylko poprawny JSON bez markdown i bez dodatkowego komentarza.",
    'Wymagany format: {"questions":[{"questionType":"single_choice|multi_select|flashcard|cloze_deletion|type_answer","question":"...","options":{"A":"...","B":"...","C":"...","D":"..."},"correctAnswers":["..."],"answerBack":"...","explanation":"...","difficulty":"easy|medium|hard","category":"...","tags":["..."]}]}',
    "Zasady:",
    "- wszystkie pola tekstowe question, options, answerBack, explanation, category i tags zwracaj w jezyku payload.language",
    "- jesli payload.language to Polish, nie uzywaj angielskiego w pytaniach, odpowiedziach ani uzasadnieniach",
    "- questionType ma byc dokladnie jedna z wartosci z payload.questionTypes, bez synonimow i bez innych nazw typow",
    "- generuj tylko typy wskazane w payload.questionTypes",
    "- single_choice: 4 opcje, 1 poprawna odpowiedz, correctAnswers = ['A']",
    "- multi_select: 4 opcje, 2 lub 3 poprawne odpowiedzi, correctAnswers = ['A','C']",
    "- flashcard: bez opcji, answerBack wymagane",
    "- cloze_deletion: pytanie z markerami {{c1::odpowiedz}} lub {{c1::odpowiedz::podpowiedz}}",
    "- type_answer: bez opcji, correctAnswers to lista akceptowanych odpowiedzi tekstowych",
    "- explanation ma byc krotkie, praktyczne i ma wyjasniac dlaczego tresc jest wazna",
    "- tags maja byc zwiezle i przydatne do filtrowania",
    "- liczba pytan ma byc bliska payload.questionCount",
    "- lepiej zwrocic mniej pytan niz tworzyc pytania o malo istotne fragmenty",
    "- pytania maja testowac kluczowe pojecia, zaleznosci, etapy, role, decyzje, ryzyka lub definicje z materialu",
    "- nie mieszaj typow pytan: kazdy rekord ma odpowiadac swojemu questionType takze struktura danych",
    "keyPoints:",
    JSON.stringify(keyPoints, null, 2),
    "Dane wejsciowe:",
    JSON.stringify(payload, null, 2),
  ].join("\n");
}

function extractJsonObject(text: string) {
  const fencedMatch = text.match(/```json\s*([\s\S]*?)```/i) || text.match(/```\s*([\s\S]*?)```/i);
  const candidate = fencedMatch ? fencedMatch[1] : text;
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");

  if (start === -1 || end === -1 || end <= start) {
    throw new Error("Cloud returned invalid JSON");
  }

  return JSON.parse(candidate.slice(start, end + 1));
}

function normalizeStudyPlan(data: any) {
  const safeRecommendation = String(data?.recommendation || "").trim();
  const safeImprovements = Array.isArray(data?.improvements)
    ? data.improvements.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 6)
    : [];
  const safeFocusAreas = Array.isArray(data?.focusAreas)
    ? data.focusAreas
        .map((item: any) => ({
          category: String(item?.category || "").trim(),
          priority: String(item?.priority || "Sredni").trim() || "Sredni",
          accuracy: Number(item?.accuracy || 0),
          reviewCount: Number(item?.reviewCount || 0),
          weakHits: Number(item?.weakHits || 0),
          deck: String(item?.deck || "").trim(),
          reason: String(item?.reason || "").trim(),
          suggestion: String(item?.suggestion || "").trim(),
        }))
        .filter((item: any) => item.category)
        .slice(0, 6)
    : [];
  const safeReviewQueue = Array.isArray(data?.reviewQueue)
    ? data.reviewQueue
        .map((item: any) => ({
          label: String(item?.label || "").trim(),
          dueDate: String(item?.dueDate || "").trim(),
          dueLabel: String(item?.dueLabel || "").trim(),
          category: String(item?.category || "").trim(),
          priority: String(item?.priority || "Sredni").trim() || "Sredni",
          duration: String(item?.duration || "").trim(),
          deck: String(item?.deck || "").trim(),
          reason: String(item?.reason || "").trim(),
          task: String(item?.task || "").trim(),
        }))
        .filter((item: any) => item.task)
        .slice(0, 10)
    : [];
  const safeWeeklyPlan = Array.isArray(data?.weeklyPlan)
    ? data.weeklyPlan
        .map((item: any) => ({
          day: String(item?.day || "").trim(),
          date: String(item?.date || "").trim(),
          dateLabel: String(item?.dateLabel || "").trim(),
          task: String(item?.task || "").trim(),
          duration: String(item?.duration || "").trim(),
          focusCategory: String(item?.focusCategory || "").trim(),
          priority: String(item?.priority || "Sredni").trim() || "Sredni",
          deck: String(item?.deck || "").trim(),
          note: String(item?.note || "").trim(),
        }))
        .filter((item: any) => item.day && item.task)
        .slice(0, 7)
    : [];

  if (!safeRecommendation) throw new Error("Cloud returned invalid study plan");

  return {
    ok: true,
    readiness: String(data?.readiness || "Plan AI").trim() || "Plan AI",
    recommendation: safeRecommendation,
    improvements: safeImprovements,
    focusAreas: safeFocusAreas,
    reviewQueue: safeReviewQueue,
    weeklyPlan: safeWeeklyPlan,
  };

  const recommendation = String(data?.recommendation || "").trim();
  const improvements = Array.isArray(data?.improvements)
    ? data.improvements.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 6)
    : [];
  const weeklyPlan = Array.isArray(data?.weeklyPlan)
    ? data.weeklyPlan
        .map((item: any) => ({
          day: String(item?.day || "").trim(),
          task: String(item?.task || "").trim(),
          duration: String(item?.duration || "").trim(),
        }))
        .filter((item: any) => item.day && item.task)
        .slice(0, 7)
    : [];

  if (!recommendation) throw new Error("Cloud returned invalid study plan");

  return {
    ok: true,
    readiness: String(data?.readiness || "Plan AI").trim() || "Plan AI",
    recommendation,
    improvements,
    weeklyPlan,
  };
}

function normalizeExamReadiness(data: any) {
  const summary = String(data?.summary || "").trim();
  const categoryReadiness = Array.isArray(data?.categoryReadiness)
    ? data.categoryReadiness
        .map((item: any) => ({
          category: String(item?.category || "").trim(),
          readiness: Math.max(0, Math.min(100, Number(item?.readiness || 0) || 0)),
          gap: Math.max(0, Number(item?.gap || 0) || 0),
          priority: String(item?.priority || "Sredni").trim() || "Sredni",
          verdict: String(item?.verdict || "").trim(),
          missing: String(item?.missing || "").trim(),
        }))
        .filter((item: any) => item.category)
        .slice(0, 8)
    : [];

  if (!summary) throw new Error("Cloud returned invalid exam readiness report");

  return {
    ok: true,
    readyScore: Math.max(0, Math.min(100, Number(data?.readyScore || 0) || 0)),
    coverageScore: Math.max(0, Math.min(100, Number(data?.coverageScore || 0) || 0)),
    confidence: String(data?.confidence || "Srednia").trim() || "Srednia",
    summary,
    strengths: Array.isArray(data?.strengths) ? data.strengths.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 5) : [],
    knowledgeGaps: Array.isArray(data?.knowledgeGaps)
      ? data.knowledgeGaps.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 6)
      : [],
    nextMilestones: Array.isArray(data?.nextMilestones)
      ? data.nextMilestones.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 5)
      : [],
    categoryReadiness,
  };
}

function normalizeQuestionPracticalUse(data: any) {
  const usageSummary = String(data?.usageSummary || "").trim();
  if (!usageSummary) throw new Error("Cloud returned invalid practical guidance");

  return {
    ok: true,
    mode: String(data?.mode || "general_concept").trim() || "general_concept",
    title: String(data?.title || "Praktyczne wykorzystanie").trim() || "Praktyczne wykorzystanie",
    usageSummary,
    realWorldApplications: Array.isArray(data?.realWorldApplications)
      ? data.realWorldApplications.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 4)
      : [],
    examples: Array.isArray(data?.examples)
      ? data.examples.map((item: any) => String(item || "").trim()).filter(Boolean).slice(0, 2)
      : [],
    examApplication: String(data?.examApplication || "").trim(),
    practiceTask: String(data?.practiceTask || "").trim(),
  };
}

function normalizeKeyPoints(data: any) {
  const keyPoints = Array.isArray(data?.keyPoints)
    ? data.keyPoints
        .map((item: any) => ({
          topic: String(item?.topic || "").trim(),
          importance: String(item?.importance || "medium").trim() || "medium",
          evidence: String(item?.evidence || "").trim(),
          reason: String(item?.reason || "").trim(),
        }))
        .filter((item: any) => item.topic && item.evidence)
        .slice(0, 12)
    : [];

  return { keyPoints };
}

function normalizeGeneratedQuestions(data: any) {
  const questions = Array.isArray(data?.questions)
    ? data.questions
        .map((item: any) => ({
          questionType: String(item?.questionType || item?.type || "").trim(),
          question: String(item?.question || "").trim(),
          options: typeof item?.options === "object" && item?.options ? item.options : {},
          correctAnswers: Array.isArray(item?.correctAnswers)
            ? item.correctAnswers.map((value: any) => String(value || "").trim()).filter(Boolean)
            : String(item?.correctAnswer || item?.answer || "")
                .split(/[\n,;|]+/)
                .map((value) => String(value || "").trim())
                .filter(Boolean),
          answerBack: String(item?.answerBack || "").trim(),
          explanation: String(item?.explanation || "").trim(),
          difficulty: String(item?.difficulty || "medium").trim(),
          category: String(item?.category || "Generator").trim(),
          tags: Array.isArray(item?.tags) ? item.tags.map((value: any) => String(value || "").trim()).filter(Boolean) : [],
        }))
        .filter((item: any) => item.question)
    : [];

  if (!questions.length) throw new Error("Cloud returned empty questions batch");
  return { ok: true, questions };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  if (req.method !== "POST") return json({ error: "Method not allowed" }, 405);

  try {
    const body = await req.json().catch(() => ({}));
    const requestApiKey = String(body?.apiKey || "").trim();
    const anthropicApiKey = requestApiKey || Deno.env.get("ANTHROPIC_API_KEY")?.trim();
    if (!anthropicApiKey) {
      return json({ error: "Missing ANTHROPIC_API_KEY secret in Supabase Edge Functions or apiKey in request body." }, 500);
    }

    const action = String(body?.action || "").trim();
    const model = String(body?.model || DEFAULT_MODEL).trim() || DEFAULT_MODEL;

    if (action === "health") {
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt: "Reply with exactly: OK",
        maxTokens: 12,
      });

      return json({
        ok: true,
        message: `Cloud AI działa przez Edge Function. Model odpowiedział: ${text}`,
      });
    }

    if (action === "training_summary") {
      const prompt = buildTrainingPrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 350,
      });

      return json({
        ok: true,
        title: "Podsumowanie AI",
        text,
      });
    }

    if (action === "question_explanation") {
      const prompt = buildQuestionExplanationPrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 800,
      });

      return json({
        ok: true,
        title: "Wyjasnienie AI",
        text,
      });
    }

    if (action === "question_practical_use") {
      const prompt = buildQuestionPracticalUsePrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 850,
      });

      return json(normalizeQuestionPracticalUse(extractJsonObject(text)));
    }

    if (action === "study_plan") {
      const prompt = buildStudyPlanPrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 650,
      });

      return json(normalizeStudyPlan(extractJsonObject(text)));
    }

    if (action === "exam_readiness") {
      const prompt = buildExamReadinessPrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 900,
      });

      return json(normalizeExamReadiness(extractJsonObject(text)));
    }

    if (action === "generate_questions") {
      const payload = (body?.payload as Record<string, unknown>) || {};
      let keyPoints: Array<Record<string, unknown>> = [];

      try {
        const keyPointsText = await callAnthropic({
          apiKey: anthropicApiKey,
          model,
          prompt: buildKeyPointExtractionPrompt(payload),
          maxTokens: 900,
        });
        keyPoints = normalizeKeyPoints(extractJsonObject(keyPointsText)).keyPoints;
      } catch {}

      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt: buildReviewedQuestionGenerationPrompt(payload, keyPoints),
        maxTokens: 2200,
      });

      return json({
        ...normalizeGeneratedQuestions(extractJsonObject(text)),
        keyPoints,
      });
    }

    return json({ error: "Unsupported action" }, 400);
  } catch (error) {
    return json({ error: getErrorMessage(error) }, 500);
  }
});
