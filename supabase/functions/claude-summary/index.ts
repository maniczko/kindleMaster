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

function buildStudyPlanPrompt(payload: Record<string, unknown>) {
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

    if (action === "generate_questions") {
      const prompt = buildQuestionGenerationPrompt((body?.payload as Record<string, unknown>) || {});
      const text = await callAnthropic({
        apiKey: anthropicApiKey,
        model,
        prompt,
        maxTokens: 2200,
      });

      return json(normalizeGeneratedQuestions(extractJsonObject(text)));
    }

    return json({ error: "Unsupported action" }, 400);
  } catch (error) {
    return json({ error: getErrorMessage(error) }, 500);
  }
});
