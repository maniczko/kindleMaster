import React, { useEffect, useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import {
  BarChart3,
  Brain,
  CheckCircle,
  ChevronRight,
  Clock3,
  Flame,
  History,
  LayoutDashboard,
  RefreshCw,
  Settings,
  Sparkles,
  Target,
  XCircle,
} from "lucide-react";

const QUESTIONS = [
  {
    id: "q1",
    prompt: "Który element architektury React odpowiada za przechowywanie lokalnego stanu komponentu?",
    options: [
      { key: "A", text: "Props przekazywane z rodzica" },
      { key: "B", text: "Hook useState" },
      { key: "C", text: "Klasa CSS komponentu" },
      { key: "D", text: "Atrybut key na liście" },
    ],
    correct: "B",
  },
  {
    id: "q2",
    prompt: "Dlaczego `key` w listach React powinien być stabilny pomiędzy renderami?",
    options: [
      { key: "A", text: "Żeby przeglądarka szybciej pobierała fonty" },
      { key: "B", text: "Żeby React poprawnie śledził elementy i ich stan" },
      { key: "C", text: "Żeby działały klasy Tailwinda" },
      { key: "D", text: "Żeby useEffect uruchamiał się tylko raz" },
    ],
    correct: "B",
  },
  {
    id: "q3",
    prompt: "Które podejście najlepiej poprawia czytelność interfejsu quizu na ekranie desktopowym?",
    options: [
      { key: "A", text: "Upychanie większej liczby statystyk nad pytaniem" },
      { key: "B", text: "Zwiększenie liczby gradientów i ozdobników" },
      { key: "C", text: "Ograniczenie szerokości treści i dodanie większych odstępów" },
      { key: "D", text: "Ukrycie hover state na odpowiedziach" },
    ],
    correct: "C",
  },
];

const TABS = [
  { id: "quiz", label: "Sesja", icon: LayoutDashboard },
  { id: "history", label: "Historia", icon: History },
  { id: "settings", label: "Ustawienia", icon: Settings },
];

const cardClass = "rounded-2xl border border-slate-200 bg-white shadow-sm";
const mutedMetricClass = "text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400";

function formatSeconds(value) {
  if (!Number.isFinite(value) || value <= 0) return "0.0 s";
  return `${value.toFixed(1)} s`;
}

function formatSessionClock(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function computeStreak(questions, answers) {
  let streak = 0;
  for (const question of questions) {
    const answer = answers[question.id];
    if (!answer) break;
    if (answer.isCorrect) streak += 1;
    else streak = 0;
  }
  return streak;
}

function StatCard({ icon: Icon, label, value, caption }) {
  return (
    <div className={`${cardClass} p-4`}>
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-600">
        <Icon className="h-5 w-5" />
      </div>
      <div className={mutedMetricClass}>{label}</div>
      <div className="mt-2 text-2xl font-bold text-slate-900">{value}</div>
      <div className="mt-1 text-sm text-slate-400">{caption}</div>
    </div>
  );
}

function TabButton({ active, icon: Icon, label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`relative inline-flex items-center gap-2 pb-4 text-sm transition ${
        active ? "font-semibold text-indigo-600" : "font-medium text-slate-400 hover:text-slate-600"
      }`}
    >
      <Icon className={`h-4 w-4 ${active ? "text-indigo-600" : "text-slate-400"}`} />
      <span>{label}</span>
      <span
        className={`absolute inset-x-0 bottom-0 h-0.5 rounded-full transition-all duration-200 ${
          active ? "bg-indigo-600 opacity-100" : "bg-transparent opacity-0"
        }`}
      />
    </button>
  );
}

function ProgressSegments({ questions, answers, currentIndex }) {
  return (
    <div className="grid grid-cols-3 gap-2">
      {questions.map((question, index) => {
        const answer = answers[question.id];
        const isCurrent = index === currentIndex;
        const fillClass = answer
          ? answer.isCorrect
            ? "bg-emerald-500"
            : "bg-rose-500"
          : "bg-indigo-600";
        const widthClass = answer ? "w-full" : isCurrent ? "w-2/5" : "w-0";

        return (
          <div key={question.id} className="h-2 overflow-hidden rounded-full bg-slate-200">
            <div className={`h-full rounded-full transition-all duration-300 ${fillClass} ${widthClass}`} />
          </div>
        );
      })}
    </div>
  );
}

function AnswerButton({ option, currentQuestion, locked, selectedOption, onSelect }) {
  const isSelected = selectedOption === option.key;
  const isCorrectOption = option.key === currentQuestion.correct;
  const showCorrect = locked && isCorrectOption;
  const showWrong = locked && isSelected && !isCorrectOption;

  let toneClass = "border-slate-200 bg-white text-slate-700 hover:border-indigo-200 hover:bg-indigo-50/70";
  let badgeClass = "border-indigo-200 bg-indigo-50 text-indigo-600";
  let StateIcon = null;

  if (showCorrect) {
    toneClass = "border-emerald-200 bg-emerald-50 text-slate-900";
    badgeClass = "border-emerald-200 bg-emerald-100 text-emerald-600";
    StateIcon = CheckCircle;
  } else if (showWrong) {
    toneClass = "border-rose-200 bg-rose-50 text-slate-900";
    badgeClass = "border-rose-200 bg-rose-100 text-rose-600";
    StateIcon = XCircle;
  } else if (isSelected) {
    toneClass = "border-indigo-300 bg-indigo-50 text-slate-900";
    badgeClass = "border-indigo-300 bg-indigo-100 text-indigo-600";
  }

  return (
    <button
      type="button"
      disabled={locked}
      onClick={() => onSelect(option.key)}
      className={`group flex w-full items-center justify-between gap-4 rounded-2xl border px-4 py-3 text-left shadow-sm transition-all duration-200 ${toneClass} ${
        locked ? "cursor-default" : "hover:-translate-y-0.5"
      }`}
    >
      <div className="flex min-w-0 items-center gap-4">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full border text-sm font-semibold ${badgeClass}`}>
          {option.key}
        </div>
        <div className="min-w-0 text-base text-slate-700">{option.text}</div>
      </div>

      {StateIcon ? <StateIcon className={`h-5 w-5 shrink-0 ${showCorrect ? "text-emerald-500" : "text-rose-500"}`} /> : null}
    </button>
  );
}

function QuizTab({
  questions,
  answers,
  currentIndex,
  sessionSeconds,
  questionSeconds,
  selectedOption,
  locked,
  complete,
  onSelect,
  onNext,
}) {
  const currentQuestion = questions[currentIndex];
  const currentAnswer = currentQuestion ? answers[currentQuestion.id] : null;

  if (complete) {
    const correctCount = Object.values(answers).filter((item) => item.isCorrect).length;
    const accuracy = Math.round((correctCount / questions.length) * 100);

    return (
      <div className="mx-auto max-w-2xl space-y-6">
        <div className={`${cardClass} p-8 text-center`}>
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
            <Sparkles className="h-7 w-7" />
          </div>
          <div className="text-sm font-medium text-slate-400">Sesja zakończona</div>
          <h2 className="mt-2 text-3xl font-semibold text-slate-900">Dobry rytm nauki</h2>
          <p className="mt-3 text-base text-slate-500">
            Trafiłeś {correctCount} z {questions.length} pytań. Skuteczność tej sesji to {accuracy}% przy czasie {formatSessionClock(sessionSeconds)}.
          </p>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div className={`${cardClass} p-5`}>
            <div className={mutedMetricClass}>Celność</div>
            <div className="mt-2 text-2xl font-bold text-slate-900">{accuracy}%</div>
          </div>
          <div className={`${cardClass} p-5`}>
            <div className={mutedMetricClass}>Tempo</div>
            <div className="mt-2 text-2xl font-bold text-slate-900">{formatSessionClock(sessionSeconds)}</div>
          </div>
          <div className={`${cardClass} p-5`}>
            <div className={mutedMetricClass}>Zakres</div>
            <div className="mt-2 text-2xl font-bold text-slate-900">{questions.length}</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-400">
          <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 shadow-sm ring-1 ring-slate-200">
            <Brain className="h-4 w-4 text-indigo-600" />
            <span>
              Pytanie {currentIndex + 1} z {questions.length}
            </span>
          </div>

          <div className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 shadow-sm ring-1 ring-slate-200">
            <Clock3 className="h-4 w-4 text-indigo-600" />
            <span>{formatSeconds(questionSeconds)}</span>
          </div>
        </div>

        <ProgressSegments questions={questions} answers={answers} currentIndex={currentIndex} />
      </div>

      <div key={currentQuestion.id} className={`${cardClass} animate-fade-in-up p-8 sm:p-10`}>
        <div className="mb-6 inline-flex items-center gap-2 rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium uppercase tracking-[0.18em] text-indigo-600">
          <Sparkles className="h-3.5 w-3.5" />
          Dzisiejsza sesja
        </div>

        <h1 className="text-xl font-semibold leading-tight text-slate-900 sm:text-2xl">{currentQuestion.prompt}</h1>

        <div className="mt-8 space-y-3">
          {currentQuestion.options.map((option) => (
            <AnswerButton
              key={option.key}
              option={option}
              currentQuestion={currentQuestion}
              locked={locked}
              selectedOption={selectedOption}
              onSelect={onSelect}
            />
          ))}
        </div>

        {locked ? (
          <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-slate-200 pt-6">
            <div className="text-sm text-slate-500">
              {currentAnswer?.isCorrect ? "Świetnie, ta odpowiedź jest poprawna." : `Poprawna odpowiedź to ${currentQuestion.correct}.`}
            </div>
            <button
              type="button"
              onClick={onNext}
              className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-700"
            >
              {currentIndex === questions.length - 1 ? "Zobacz wynik" : "Dalej"}
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className={`${cardClass} p-5`}>
          <div className={mutedMetricClass}>Czas sesji</div>
          <div className="mt-2 text-2xl font-bold text-slate-900">{formatSessionClock(sessionSeconds)}</div>
        </div>
        <div className={`${cardClass} p-5`}>
          <div className={mutedMetricClass}>Puls sesji</div>
          <div className="mt-2 text-sm leading-6 text-slate-500">
            Zachowaj stałe tempo. Najszybciej utrwalasz materiał, kiedy przechodzisz dalej od razu po otrzymaniu feedbacku.
          </div>
        </div>
      </div>
    </div>
  );
}

function HistoryTab({ answers, questions, sessionSeconds }) {
  const rows = questions.map((question, index) => {
    const answer = answers[question.id];
    return {
      id: question.id,
      title: question.prompt,
      status: !answer ? "Oczekuje" : answer.isCorrect ? "Trafione" : "Do poprawy",
      tone: !answer ? "bg-slate-100 text-slate-500" : answer.isCorrect ? "bg-emerald-50 text-emerald-600" : "bg-rose-50 text-rose-600",
      meta: answer ? `${answer.selected} - ${formatSeconds(answer.durationMs / 1000)}` : `Pozycja ${index + 1}`,
    };
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className={`${cardClass} p-6`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-sm font-medium text-slate-400">Przebieg sesji</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-900">Oś odpowiedzi</h2>
          </div>
          <div className="rounded-full bg-slate-100 px-3 py-1.5 text-sm text-slate-500">{formatSessionClock(sessionSeconds)}</div>
        </div>
      </div>

      <div className="space-y-3">
        {rows.map((row) => (
          <div key={row.id} className={`${cardClass} flex flex-wrap items-start justify-between gap-4 p-5`}>
            <div className="max-w-2xl">
              <div className="text-base font-medium text-slate-900">{row.title}</div>
              <div className="mt-2 text-sm text-slate-400">{row.meta}</div>
            </div>
            <div className={`rounded-full px-3 py-1 text-sm font-medium ${row.tone}`}>{row.status}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SettingsTab() {
  const blocks = [
    {
      title: "Kolorystyka",
      text: "Slate jako tło, białe karty, indigo dla akcentu i wyraźne stany sukcesu oraz błędu.",
    },
    {
      title: "Typografia",
      text: "Czytelna hierarchia nagłówków, krótkie etykiety i więcej pustej przestrzeni wokół pytania.",
    },
    {
      title: "Interakcje",
      text: "Hover, natychmiastowy feedback odpowiedzi i subtelne przejścia między krokami sesji.",
    },
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className={`${cardClass} p-6`}>
        <div className="text-sm font-medium text-slate-400">Warstwa produktu</div>
        <h2 className="mt-2 text-2xl font-semibold text-slate-900">Założenia interfejsu</h2>
        <p className="mt-3 max-w-2xl text-base leading-7 text-slate-500">
          Ten widok upraszcza naukę do jednej ścieżki: fokus na pytaniu, lekkie statystyki po lewej i wyraźny feedback bez technicznego szumu.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {blocks.map((block) => (
          <div key={block.title} className={`${cardClass} p-5`}>
            <div className={mutedMetricClass}>{block.title}</div>
            <div className="mt-3 text-base leading-7 text-slate-600">{block.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ZenQuizApp() {
  const [activeTab, setActiveTab] = useState("quiz");
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answers, setAnswers] = useState({});
  const [selectedOption, setSelectedOption] = useState("");
  const [locked, setLocked] = useState(false);
  const [complete, setComplete] = useState(false);
  const [sessionSeconds, setSessionSeconds] = useState(0);
  const [questionStartedAt, setQuestionStartedAt] = useState(Date.now());

  useEffect(() => {
    const previousClassName = document.body.className;
    const previousMargin = document.body.style.margin;
    const previousFontFamily = document.body.style.fontFamily;

    document.body.className = `${previousClassName} bg-slate-50`.trim();
    document.body.style.margin = "0";
    document.body.style.fontFamily = 'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

    return () => {
      document.body.className = previousClassName;
      document.body.style.margin = previousMargin;
      document.body.style.fontFamily = previousFontFamily;
    };
  }, []);

  useEffect(() => {
    if (complete) return undefined;

    const timer = window.setInterval(() => {
      setSessionSeconds((value) => value + 1);
    }, 1000);

    return () => window.clearInterval(timer);
  }, [complete]);

  useEffect(() => {
    setSelectedOption("");
    setLocked(false);
    setQuestionStartedAt(Date.now());
  }, [currentIndex]);

  const answeredCount = Object.keys(answers).length;

  const sessionMetrics = useMemo(() => {
    const answerList = Object.values(answers);
    const correctCount = answerList.filter((item) => item.isCorrect).length;
    const accuracy = answerList.length ? Math.round((correctCount / answerList.length) * 100) : 0;
    const averageSeconds = answerList.length
      ? answerList.reduce((sum, item) => sum + item.durationMs, 0) / answerList.length / 1000
      : 0;

    return {
      answeredCount,
      accuracy,
      averageSeconds,
      streak: computeStreak(QUESTIONS, answers),
    };
  }, [answers, answeredCount]);

  const liveQuestionSeconds = useMemo(() => (Date.now() - questionStartedAt) / 1000, [questionStartedAt, sessionSeconds]);

  const handleSelect = (optionKey) => {
    if (locked || complete) return;

    const currentQuestion = QUESTIONS[currentIndex];
    const durationMs = Date.now() - questionStartedAt;
    const nextAnswer = {
      selected: optionKey,
      isCorrect: optionKey === currentQuestion.correct,
      durationMs,
    };

    setSelectedOption(optionKey);
    setAnswers((previous) => ({
      ...previous,
      [currentQuestion.id]: nextAnswer,
    }));
    setLocked(true);
  };

  const handleNext = () => {
    if (currentIndex === QUESTIONS.length - 1) {
      setComplete(true);
      return;
    }

    setCurrentIndex((value) => value + 1);
  };

  const handleReset = () => {
    setActiveTab("quiz");
    setCurrentIndex(0);
    setAnswers({});
    setSelectedOption("");
    setLocked(false);
    setComplete(false);
    setSessionSeconds(0);
    setQuestionStartedAt(Date.now());
  };

  const sidebarStats = [
    {
      icon: BarChart3,
      label: "Przebieg",
      value: `${sessionMetrics.answeredCount}/${QUESTIONS.length}`,
      caption: "pytania gotowe",
    },
    {
      icon: Target,
      label: "Celność",
      value: `${sessionMetrics.accuracy}%`,
      caption: "trafione odpowiedzi",
    },
    {
      icon: Clock3,
      label: "Tempo",
      value: formatSeconds(sessionMetrics.averageSeconds),
      caption: "srednio / pytanie",
    },
    {
      icon: Flame,
      label: "Passa",
      value: sessionMetrics.streak,
      caption: "kolejno bez błędu",
    },
  ];

  return (
    <>
      <style>{`
        .animate-fade-in-up {
          animation: fadeInUp 320ms ease both;
        }

        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(18px);
          }

          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>

      <div className="min-h-screen bg-slate-50 text-slate-900">
        <div className="mx-auto max-w-[1600px] px-4 py-4 sm:px-6 sm:py-6 lg:pl-[308px]">
          <aside className="mb-6 w-full rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm lg:fixed lg:bottom-6 lg:left-6 lg:top-6 lg:mb-0 lg:w-[260px] lg:overflow-y-auto">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-600">
                <Brain className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold text-slate-900">Zen Quiz</div>
                <div className="text-xs text-slate-400">spokojna sesja nauki</div>
              </div>
            </div>

            <div className="mt-6 grid gap-3">
              {sidebarStats.map((item) => (
                <StatCard key={item.label} icon={item.icon} label={item.label} value={item.value} caption={item.caption} />
              ))}
            </div>

            <button
              type="button"
              onClick={handleReset}
              className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-indigo-200 hover:text-indigo-600"
            >
              <RefreshCw className="h-4 w-4" />
              Nowa sesja
            </button>

            <div className="mt-4 rounded-2xl bg-slate-100 p-4">
              <div className={mutedMetricClass}>Czas teraz</div>
              <div className="mt-2 text-2xl font-bold text-slate-900">{formatSessionClock(sessionSeconds)}</div>
              <div className="mt-2 text-sm text-slate-500">Bieżąca sesja utrzymuje stabilne tempo i live feedback.</div>
            </div>
          </aside>

          <main className="min-w-0">
            <div className="rounded-[28px] border border-slate-200 bg-white px-5 pb-12 pt-5 shadow-sm sm:px-8">
              <div className="border-b border-slate-200">
                <nav className="flex flex-wrap items-end gap-6">
                  {TABS.map((tab) => (
                    <TabButton
                      key={tab.id}
                      active={activeTab === tab.id}
                      icon={tab.icon}
                      label={tab.label}
                      onClick={() => setActiveTab(tab.id)}
                    />
                  ))}
                </nav>
              </div>

              <div className="py-8 sm:py-10">
                {activeTab === "quiz" ? (
                  <QuizTab
                    questions={QUESTIONS}
                    answers={answers}
                    currentIndex={currentIndex}
                    sessionSeconds={sessionSeconds}
                    questionSeconds={liveQuestionSeconds}
                    selectedOption={selectedOption}
                    locked={locked}
                    complete={complete}
                    onSelect={handleSelect}
                    onNext={handleNext}
                  />
                ) : null}

                {activeTab === "history" ? (
                  <HistoryTab answers={answers} questions={QUESTIONS} sessionSeconds={sessionSeconds} />
                ) : null}

                {activeTab === "settings" ? <SettingsTab /> : null}
              </div>
            </div>
          </main>
        </div>
      </div>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<ZenQuizApp />);
