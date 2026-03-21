let mediaRecorder = null;
let audioChunks = [];

let sessionId = localStorage.getItem("session_id") || null;
let analysisGeneration = Number(
  localStorage.getItem("analysis_generation") || 0,
);
let llmMode = localStorage.getItem("llm_mode") || "local";
let activeMobileTab = localStorage.getItem("mobile_active_tab") || "chat";
let llmProviderStatus = null;

let scoreHistory = [];
let turnHistory = [];
let selectedTurnId = null;
let scoreChart = null;
let gaugeChart = null;
let radarChart = null;
let recordingStream = null;
let audioContext = null;
let analyserNode = null;
let microphoneSource = null;
let voiceMeterFrame = null;
let isAnswerPending = false;
let recordButtonBusyLabel = "";
let pendingTurns = [];
let analysisTaskQueue = [];
let isAnalysisWorkerRunning = false;
let lastResetSummary = null;
let analysisSummaryToastTimer = null;
let lastRenderedSessionReport = null;
const helpPopoverRegistry = [];
let scoreCascadeTimers = [];
let lastMetricSnapshot = {
  averageScore: 0,
  recentAverageScore: 0,
  latestScore: 0,
  gaugeScore: 0,
  analysisScore: 0,
  confidence: 0,
};

const startButton = document.getElementById("startRecord");
const resetButton = document.getElementById("resetHistory");
const llmModeLocalButton = document.getElementById("llmModeLocal");
const llmModeApiButton = document.getElementById("llmModeApi");
const llmModeStatusEl = document.getElementById("llmModeStatus");
const llmModeHintEl = document.getElementById("llmModeHint");
const chatContainer = document.getElementById("chatContainer");
const chatWindow = document.getElementById("chatWindow");
const recordingIndicator = document.getElementById("recordingIndicator");
const aiThinking = document.getElementById("aiThinking");
const mobileProcessBadgeEl = document.getElementById("mobileProcessBadge");
const systemStateText = document.getElementById("systemStateText");
const processDetailEl = document.getElementById("processDetail");
const processSteps = Array.from(document.querySelectorAll(".process-step"));
const analysisRoleTrackEl = document.getElementById("analysisRoleTrack");
const analysisRoleChips = Array.from(
  document.querySelectorAll(".process-role-chip"),
);

const avgScoreEl = document.getElementById("avgScore");
const recentAvgScoreEl = document.getElementById("recentAvgScore");
const latestScoreEl = document.getElementById("latestScore");
const gaugeScoreEl = document.getElementById("gaugeScore");
const trendTextEl = document.getElementById("trendText");
const statusCardEl = document.getElementById("statusCard");
const latestScoreCardEl = latestScoreEl?.closest(".mini-card") || null;
const trendCardEl = trendTextEl?.closest(".mini-card") || null;
const gaugeChartCanvasEl = document.getElementById("gaugeChart");
const scoreChartCanvasEl = document.getElementById("scoreChart");
const gaugeCardEl = gaugeChartCanvasEl?.closest(".chart-card") || null;
const lineChartCardEl = scoreChartCanvasEl?.closest(".chart-card") || null;

const analysisJudgmentEl = document.getElementById("analysisJudgment");
const analysisScoreEl = document.getElementById("analysisScore");
const analysisRiskLevelEl = document.getElementById("analysisRiskLevel");
const analysisTrendEl = document.getElementById("analysisTrend");
const analysisReasonEl = document.getElementById("analysisReason");
const analysisStateBadgeEl = document.getElementById("analysisStateBadge");
const analysisEmptyHintEl = document.getElementById("analysisEmptyHint");
const analysisDetailCardEl =
  analysisStateBadgeEl?.closest(".analysis-card") || null;
const analysisReasonBoxEl = analysisReasonEl || null;
const analysisHeroCardEl = document.querySelector(".analysis-hero-card");

const featureRepetitionValueEl = document.getElementById(
  "featureRepetitionValue",
);
const featureMemoryValueEl = document.getElementById("featureMemoryValue");
const featureTimeValueEl = document.getElementById("featureTimeValue");
const featureIncoherenceValueEl = document.getElementById(
  "featureIncoherenceValue",
);

const featureRepetitionBarEl = document.getElementById("featureRepetitionBar");
const featureMemoryBarEl = document.getElementById("featureMemoryBar");
const featureTimeBarEl = document.getElementById("featureTimeBar");
const featureIncoherenceBarEl = document.getElementById(
  "featureIncoherenceBar",
);

const confidenceScoreEl = document.getElementById("confidenceScore");

const recallStatusEl = document.getElementById("recallStatus");
const recallLastResultEl = document.getElementById("recallLastResult");
const recallPromptEl = document.getElementById("recallPrompt");

const warningPopup = document.getElementById("warningPopup");
const warningPopupText = document.getElementById("warningPopupText");
const closeWarningPopupButton = document.getElementById("closeWarningPopup");
const openSessionReportButton = document.getElementById("openSessionReport");
const mobileOpenSessionReportButton = document.getElementById(
  "mobileOpenSessionReport",
);
const openSessionReportInlineButton = document.getElementById(
  "openSessionReportInline",
);
const mobileResetHistoryButton = document.getElementById("mobileResetHistory");
const sessionReportModal = document.getElementById("sessionReportModal");
const closeSessionReportButton = document.getElementById("closeSessionReport");
const closeSessionReportFooterButton = document.getElementById(
  "closeSessionReportFooter",
);
const printSessionReportButton = document.getElementById("printSessionReport");
const reportGeneratedAtEl = document.getElementById("reportGeneratedAt");
const reportHeadlineEl = document.getElementById("reportHeadline");
const reportSubtextEl = document.getElementById("reportSubtext");
const reportStatusBadgeEl = document.getElementById("reportStatusBadge");
const reportTurnCountEl = document.getElementById("reportTurnCount");
const reportIncludedCountEl = document.getElementById("reportIncludedCount");
const reportAverageScoreEl = document.getElementById("reportAverageScore");
const reportRecentAverageEl = document.getElementById("reportRecentAverage");
const reportLatestScoreEl = document.getElementById("reportLatestScore");
const reportPeakScoreEl = document.getElementById("reportPeakScore");
const reportLatestJudgmentEl = document.getElementById("reportLatestJudgment");
const reportLatestRiskEl = document.getElementById("reportLatestRisk");
const reportLatestTrendEl = document.getElementById("reportLatestTrend");
const reportLlmModeEl = document.getElementById("reportLlmMode");
const reportLatestReasonEl = document.getElementById("reportLatestReason");
const reportFeatureListEl = document.getElementById("reportFeatureList");
const reportRecallSummaryEl = document.getElementById("reportRecallSummary");
const reportTurnListEl = document.getElementById("reportTurnList");
const analysisSummaryToast = document.getElementById("analysisSummaryToast");
const summaryToastBadgeEl = document.getElementById("summaryToastBadge");
const summaryToastTitleEl = document.getElementById("summaryToastTitle");
const summaryToastScoreEl = document.getElementById("summaryToastScore");
const summaryToastRiskEl = document.getElementById("summaryToastRisk");
const summaryToastTrendEl = document.getElementById("summaryToastTrend");
const summaryToastReasonEl = document.getElementById("summaryToastReason");
const workspaceOverviewStateEl = document.getElementById(
  "workspaceOverviewState",
);
const workspaceOverviewCopyEl = document.getElementById(
  "workspaceOverviewCopy",
);
const workspaceOverviewLatestEl = document.getElementById(
  "workspaceOverviewLatest",
);
const workspaceOverviewLatestMetaEl = document.getElementById(
  "workspaceOverviewLatestMeta",
);
const workspaceOverviewRecallEl = document.getElementById(
  "workspaceOverviewRecall",
);
const workspaceOverviewRecallMetaEl = document.getElementById(
  "workspaceOverviewRecallMeta",
);
const analysisHeroBadgeEl = document.getElementById("analysisHeroBadge");
const analysisHeroScoreEl = document.getElementById("analysisHeroScore");
const analysisHeroTimestampEl = document.getElementById(
  "analysisHeroTimestamp",
);
const analysisHeroRiskEl = document.getElementById("analysisHeroRisk");
const analysisHeroTrendEl = document.getElementById("analysisHeroTrend");
const analysisHeroModeEl = document.getElementById("analysisHeroMode");
const analysisHeroSummaryEl = document.getElementById("analysisHeroSummary");
const timelineMetaEl = document.getElementById("timelineMeta");
const timelineTrendBadgeEl = document.getElementById("timelineTrendBadge");
const timelineTrendCopyEl = document.getElementById("timelineTrendCopy");
const timelineSparklineEl = document.getElementById("timelineSparkline");
const turnTimelineListEl = document.getElementById("turnTimelineList");
const sidebarMetricsDisclosureEl = document.getElementById(
  "sidebarMetricsDisclosure",
);
const historyDisclosureEl = document.getElementById("historyDisclosure");
const analysisDetailDisclosureEl = document.getElementById(
  "analysisDetailDisclosure",
);
const recallDisclosureEl = document.getElementById("recallDisclosure");
const mobileMenuToggleButton = document.getElementById("mobileMenuToggle");
const mobileMenuCloseButton = document.getElementById("mobileMenuClose");
const mobileMenuScrim = document.getElementById("mobileMenuScrim");
const mobileTabbarEl = document.getElementById("mobileTabbar");
const mobileTabButtons = Array.from(
  document.querySelectorAll("[data-mobile-tab-target]"),
);
const MOBILE_TAB_BREAKPOINT = 760;
let isMobileMenuOpen = false;
const THREE_D_ICON_PATHS = {
  status: {
    safe: "/static/3d-icons/status-safe.png",
    low: "/static/3d-icons/status-low.png",
    warning: "/static/3d-icons/status-warning.png",
    high: "/static/3d-icons/status-high.png",
    critical: "/static/3d-icons/status-critical.png",
  },
  metrics: "/static/3d-icons/metrics-chart.png",
  history: "/static/3d-icons/history-clock.png",
  detail: "/static/3d-icons/detail-puzzle.png",
  recall: "/static/3d-icons/recall-notebook.png",
  empty: "/static/3d-icons/empty-mic.png",
};

const processStepOrder = ["capture", "stt", "answer", "analysis", "render"];
const processStepLabels = {
  capture: "음성 수신",
  stt: "음성 인식",
  answer: "답변 생성",
  analysis: "위험도 분석",
  render: "화면 반영",
};
const analysisRoleOrder = [
  "repetition",
  "memory",
  "time_confusion",
  "incoherence",
];
const analysisRoleLabels = {
  repetition: "질문 반복",
  memory: "기억 혼란",
  time_confusion: "시간 / 상황 혼란",
  incoherence: "문장 비논리성",
};
const urlParams = new URLSearchParams(window.location.search);
const demoMode = normalizeText(urlParams.get("demo"));
const isDemoMode = Boolean(demoMode);
const captureMode = normalizeText(urlParams.get("capture"));
const isDocsCapture = captureMode === "docs";

if (isDocsCapture) {
  document.body.classList.add("is-docs-capture");
  document.body.dataset.captureMode = captureMode;
}

function isMobileTabViewport() {
  return window.innerWidth <= MOBILE_TAB_BREAKPOINT;
}

function applyMobileTabState() {
  const isEnabled = isMobileTabViewport() && !isDocsCapture;
  document.body.classList.toggle("is-mobile-tabbed", isEnabled);

  if (!isEnabled) {
    setMobileMenuOpen(false);
    document.body.removeAttribute("data-mobile-tab");
    mobileTabButtons.forEach((button) => {
      button.classList.remove("is-active");
      button.setAttribute("aria-selected", "false");
      button.setAttribute("tabindex", "-1");
    });
    return;
  }

  const availableTabs = new Set(["dashboard", "chat", "analysis", "manage"]);
  if (!availableTabs.has(activeMobileTab)) {
    activeMobileTab = "chat";
  }

  document.body.dataset.mobileTab = activeMobileTab;
  mobileTabButtons.forEach((button) => {
    const isActive = button.dataset.mobileTabTarget === activeMobileTab;
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
    button.setAttribute("tabindex", isActive ? "0" : "-1");
  });
}

function setMobileMenuOpen(nextOpen) {
  const shouldOpen =
    Boolean(nextOpen) && isMobileTabViewport() && !isDocsCapture;
  isMobileMenuOpen = shouldOpen;

  document.body.classList.toggle("is-mobile-menu-open", shouldOpen);

  if (mobileMenuToggleButton) {
    mobileMenuToggleButton.setAttribute(
      "aria-expanded",
      shouldOpen ? "true" : "false",
    );
  }

  if (mobileMenuScrim) {
    mobileMenuScrim.classList.toggle("hidden", !shouldOpen);
  }
}

function closeMobileMenu() {
  setMobileMenuOpen(false);
}

function toggleMobileMenu() {
  setMobileMenuOpen(!isMobileMenuOpen);
}

function setActiveMobileTab(
  tabName,
  { persist = true, scrollToTop = true } = {},
) {
  if (!tabName) {
    return;
  }

  activeMobileTab = tabName;

  if (persist) {
    localStorage.setItem("mobile_active_tab", activeMobileTab);
  }

  applyMobileTabState();

  if (scrollToTop && isMobileTabViewport()) {
    window.scrollTo({
      top: 0,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
    });
  }
}

function initializeMobileTabs() {
  if (!mobileTabbarEl || mobileTabButtons.length === 0) {
    return;
  }

  mobileTabbarEl.setAttribute("role", "tablist");

  mobileTabButtons.forEach((button) => {
    button.setAttribute("role", "tab");
    button.addEventListener("click", () => {
      const target = button.dataset.mobileTabTarget;
      if (target) {
        setActiveMobileTab(target);
        closeMobileMenu();
      }
    });
  });

  applyMobileTabState();
}

function clearScoreCascadeTimers() {
  scoreCascadeTimers.forEach((timerId) => window.clearTimeout(timerId));
  scoreCascadeTimers = [];
}

function pulseElement(element, className = "is-spotlight", duration = 900) {
  if (!element) {
    return;
  }

  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);

  window.setTimeout(() => {
    element.classList.remove(className);
  }, duration);
}

function scheduleCascadePulse(element, delay, className = "is-spotlight") {
  const timerId = window.setTimeout(() => {
    pulseElement(element, className);
  }, delay);
  scoreCascadeTimers.push(timerId);
}

function scheduleNumberAnimation(
  element,
  start,
  end,
  delay,
  duration = 700,
  isPercent = false,
  fixed = 0,
) {
  if (!element) {
    return;
  }

  const timerId = window.setTimeout(() => {
    animateNumber(element, start, end, duration, isPercent, fixed);
  }, delay);
  scoreCascadeTimers.push(timerId);
}

function resetRoleAnalysisTracker() {
  analysisRoleChips.forEach((chip) => {
    chip.classList.remove("is-active", "is-complete", "is-pulse");
    const stateEl = chip.querySelector("small");
    if (stateEl) {
      stateEl.innerText = "대기";
    }
  });
}

function updateRoleAnalysisTracker(
  roleResults = {},
  currentRole = null,
  completedCount = 0,
  totalCount = analysisRoleOrder.length,
  options = {},
) {
  const animateRole = options.animateRole;
  const finalized = Boolean(options.finalized);

  analysisRoleChips.forEach((chip) => {
    const role = chip.dataset.role;
    const result = role ? roleResults?.[role] : null;
    const stateEl = chip.querySelector("small");

    chip.classList.remove("is-active", "is-complete");

    if (finalized && role) {
      chip.classList.add("is-complete");
      if (stateEl) {
        const score =
          roleResults?.[role] &&
          Number.isFinite(Number(roleResults[role].score))
            ? `${Number(roleResults[role].score)}점`
            : "완료";
        stateEl.innerText = score;
      }
      return;
    }

    if (result) {
      chip.classList.add("is-complete");
      if (stateEl) {
        stateEl.innerText = `${Number(result.score ?? 0)}점`;
      }
      if (animateRole === role) {
        pulseElement(chip, "is-pulse", 760);
      }
      return;
    }

    if (role && role === currentRole) {
      chip.classList.add("is-active");
      if (stateEl) {
        stateEl.innerText = `${completedCount}/${totalCount} 진행`;
      }
      return;
    }

    if (stateEl) {
      stateEl.innerText = "대기";
    }
  });
}

function focusSelectedTurnFeedback(turnId) {
  if (!turnId) {
    return;
  }

  const timelineItem = turnTimelineListEl?.querySelector(
    `.analysis-timeline-item[data-turn-id="${CSS.escape(turnId)}"]`,
  );

  if (timelineItem) {
    timelineItem.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: "smooth",
    });
    pulseElement(timelineItem, "is-focus-tracked", 1000);
  }

  pulseElement(analysisDetailCardEl, "is-focus-tracked", 1100);
  pulseElement(analysisReasonBoxEl, "is-focus-tracked", 1100);
}

function triggerAnalysisScoreCascade(data) {
  clearScoreCascadeTimers();

  const cascadeTargets = [
    { element: latestScoreCardEl, delay: 40 },
    { element: statusCardEl, delay: 180 },
    { element: gaugeCardEl, delay: 320 },
    { element: lineChartCardEl, delay: 460 },
    { element: analysisDetailCardEl, delay: 600 },
    { element: analysisHeroCardEl, delay: 740 },
  ];

  cascadeTargets.forEach(({ element, delay }) => {
    scheduleCascadePulse(element, delay);
  });

  if ((data?.score ?? 0) > 0) {
    scheduleCascadePulse(analysisReasonBoxEl, 860, "is-focus-tracked");
  }
}

function setMobileProcessBadge(text, tone = "idle") {
  if (!mobileProcessBadgeEl) {
    return;
  }

  mobileProcessBadgeEl.innerText = text || "대기";
  mobileProcessBadgeEl.classList.remove("is-idle", "is-active", "is-error");
  mobileProcessBadgeEl.classList.add(`is-${tone}`);
}

function createDemoScoreHistory(scores = [], labels = []) {
  return scores.map((score, index) => ({
    score,
    time: labels[index] || `10:${String(index * 3 + 10).padStart(2, "0")}:00`,
  }));
}

function runWithViewTransition(callback) {
  const prefersReducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;

  if (
    prefersReducedMotion ||
    typeof document.startViewTransition !== "function"
  ) {
    callback();
    return;
  }

  try {
    document.startViewTransition(() => {
      callback();
    });
  } catch (error) {
    console.warn("View Transition 적용 실패:", error);
    callback();
  }
}

function buildDemoTurn(overrides = {}) {
  const featureScores = {
    repetition: 0,
    memory: 0,
    time_confusion: 0,
    incoherence: 0,
    ...(overrides.feature_scores || {}),
  };
  const scoreIncluded = overrides.score_included !== false;
  const score = scoreIncluded
    ? Number(
        overrides.score ??
          featureScores.repetition +
            featureScores.memory +
            featureScores.time_confusion +
            featureScores.incoherence,
      )
    : 0;

  return {
    turn_id:
      overrides.turn_id ||
      `demo-turn-${Math.random().toString(36).slice(2, 8)}-${Date.now()}`,
    time: overrides.time || "10:12:00",
    user_text: overrides.user_text || "",
    answer: overrides.answer || "",
    judgment:
      overrides.judgment ||
      (scoreIncluded ? (score < 20 ? "정상" : "의심") : "판단 어려움"),
    score,
    reason: overrides.reason || "데모 화면을 위한 예시 분석 근거입니다.",
    feature_scores: featureScores,
    follow_up_messages: [...(overrides.follow_up_messages || [])],
    score_included: scoreIncluded,
    excluded_reason: overrides.excluded_reason || "",
    llm_provider: overrides.llm_provider || "local",
    risk_level: scoreIncluded
      ? overrides.risk_level || getRiskLevelFromScore(score)
      : "반영 제외",
    trend: overrides.trend || "안정",
    confidence: scoreIncluded
      ? (overrides.confidence ?? calculateConfidenceValue(featureScores, score))
      : 0,
    average_score: Number(overrides.average_score ?? score),
    recent_average_score: Number(overrides.recent_average_score ?? score),
  };
}

function buildDemoPendingTurn(overrides = {}) {
  return {
    client_turn_id:
      overrides.client_turn_id ||
      `demo-pending-${Math.random().toString(36).slice(2, 8)}`,
    user_text: overrides.user_text || "",
    answer: overrides.answer || "",
    created_at: Number(overrides.created_at || Date.now()),
    pending_status: overrides.pending_status || "queued",
    pending_error_message: overrides.pending_error_message || "",
    follow_up_messages: [...(overrides.follow_up_messages || [])],
    is_pending: true,
  };
}

function buildDemoAnalysisData(turn, summary, recall = {}) {
  return {
    judgment: turn.judgment,
    score: turn.score,
    reason: turn.reason,
    feature_scores: turn.feature_scores,
    score_included: turn.score_included,
    excluded_reason: turn.excluded_reason || "",
    risk_level: turn.risk_level,
    trend: turn.trend,
    average_score: summary.average_score,
    recent_average_score: summary.recent_average_score,
    score_history: scoreHistory,
    recall,
  };
}

function getDemoScenarioMap() {
  const historyLabels = ["10:10:12", "10:13:44", "10:16:05", "10:19:28"];
  const sharedScoreHistory = createDemoScoreHistory(
    [18, 28, 37, 46],
    historyLabels,
  );

  const overviewSummary = {
    average_score: 0,
    recent_average_score: 0,
    trend: "데이터 부족",
  };
  const monitoringSummary = {
    average_score: 32.3,
    recent_average_score: 36.3,
    trend: "상승",
  };

  const baselineTurn = buildDemoTurn({
    turn_id: "demo-turn-baseline",
    time: "10:13:44",
    user_text: "오늘 회의가 몇 시에 있는지 기억이 안 나.",
    answer:
      "회의 시간을 바로 떠올리기 어렵다면 일정표나 메일을 확인해 보시는 것이 좋겠습니다.",
    judgment: "의심",
    score: 28,
    reason:
      "시간 관련 정보를 바로 회상하지 못하는 표현이 나타났습니다. 최근 일정과 관련된 기억 인출이 다소 불안정한 모습으로 해석됩니다.",
    feature_scores: {
      repetition: 0,
      memory: 12,
      time_confusion: 12,
      incoherence: 4,
    },
    risk_level: "낮은 위험",
    trend: "안정",
    average_score: 23.0,
    recent_average_score: 23.0,
  });

  const finalTurn = buildDemoTurn({
    turn_id: "demo-turn-final",
    time: "10:19:28",
    user_text:
      "오늘 회의가 언제였는지 기억이 안 나. 아까 물어봤던 것 같은데 다시 알려줄 수 있어?",
    answer:
      "회의 시간이 바로 떠오르지 않는다면 일정 앱이나 메일 기록을 확인해 보는 것이 좋겠습니다.",
    judgment: "의심",
    score: 46,
    reason:
      "동일한 회의 시간 정보를 다시 확인하려는 반복 질문이 관찰됩니다. 기억 회상 어려움과 시간 관련 혼동 표현이 함께 나타나 인지 위험 신호가 강화된 것으로 해석됩니다.",
    feature_scores: {
      repetition: 15,
      memory: 14,
      time_confusion: 12,
      incoherence: 5,
    },
    risk_level: "주의",
    trend: "상승",
    average_score: monitoringSummary.average_score,
    recent_average_score: monitoringSummary.recent_average_score,
  });

  const overviewPending = buildDemoPendingTurn({
    client_turn_id: "demo-answer-turn",
    created_at: Date.now() - 2000,
    user_text: "오늘 수업이 몇 시였는지 기억이 안 나.",
    answer:
      "수업 시간을 바로 떠올리기 어렵다면 시간표나 알림을 확인해 보시는 것이 좋겠습니다.",
    pending_status: "queued",
  });

  const progressPending = buildDemoPendingTurn({
    client_turn_id: "demo-analysis-turn",
    created_at: Date.now() - 2000,
    user_text: "오늘 회의가 몇 시였지? 조금 전에 들은 것 같은데 다시 말해줄래?",
    answer:
      "회의 시간을 바로 떠올리기 어렵다면 메일이나 일정표를 다시 확인해 보시는 것이 좋겠습니다.",
    pending_status: "analyzing",
  });

  return {
    overview: {
      name: "overview",
      llmMode: "local",
      summary: overviewSummary,
      scoreHistory: [],
      turnHistory: [],
      pendingTurns: [],
      recall: { status: "idle", last_result: "없음", prompt: "" },
      process: {
        step: null,
        detail: "대기 중입니다. 녹음을 시작하면 음성 입력을 기다립니다.",
      },
      systemState: "준비 완료",
      thinkingText: "아직 분석 전입니다.",
      analysisState: "reset",
      voiceLevel: 0.06,
    },
    recording: {
      name: "recording",
      llmMode: "local",
      summary: overviewSummary,
      scoreHistory: [],
      turnHistory: [],
      pendingTurns: [],
      recall: { status: "idle", last_result: "없음", prompt: "" },
      process: {
        step: "capture",
        detail:
          "마이크가 연결되었고 사용자의 음성을 실시간으로 수집하고 있습니다.",
      },
      systemState: "음성 입력 수집 중",
      thinkingText: "음성 입력을 기다리고 있습니다.",
      analysisState: "reset",
      recording: true,
      voiceLevel: 0.58,
    },
    answer: {
      name: "answer",
      llmMode: "local",
      summary: monitoringSummary,
      scoreHistory: sharedScoreHistory.slice(0, 3),
      turnHistory: [baselineTurn],
      pendingTurns: [overviewPending],
      recall: { status: "idle", last_result: "없음", prompt: "" },
      process: {
        step: "answer",
        detail:
          "답변이 먼저 표시되었고, 역할별 위험도 분석을 준비하고 있습니다.",
      },
      systemState: "답변 생성 완료",
      thinkingText: "역할별 언어 특징 분석을 곧 시작합니다.",
      analysisState: "loading",
      voiceLevel: 0.06,
    },
    "analysis-progress": {
      name: "analysis-progress",
      llmMode: "local",
      summary: monitoringSummary,
      scoreHistory: sharedScoreHistory.slice(0, 3),
      turnHistory: [baselineTurn],
      pendingTurns: [progressPending],
      recall: { status: "idle", last_result: "없음", prompt: "" },
      process: {
        step: "analysis",
        detail:
          "질문 반복과 기억 혼란 항목을 반영했고, 나머지 역할 점수를 계속 분석하고 있습니다.",
      },
      systemState: "기억 혼란 분석 중",
      thinkingText:
        "역할별 결과를 모으는 동안 그래프와 점수 카드가 순차적으로 갱신되고 있습니다.",
      analysisState: "preview",
      analysisPreview: {
        roleResults: {
          repetition: {
            score: 15,
            reason: "이전 질문과 유사한 회의 시간 질문이 다시 등장했습니다.",
          },
          memory: {
            score: 12,
            reason: "기억이 나지 않는다는 표현이 반복적으로 나타났습니다.",
          },
        },
        currentRole: "memory",
        completedCount: 2,
        totalCount: 4,
      },
      analysisThinking: true,
      voiceLevel: 0.06,
    },
    final: {
      name: "final",
      llmMode: "local",
      summary: monitoringSummary,
      scoreHistory: sharedScoreHistory,
      turnHistory: [baselineTurn, finalTurn],
      pendingTurns: [],
      recall: {
        status: "memorize",
        last_result: "없음",
        prompt:
          "Recall Test: 지금 제시하는 기억 단어는 '사과'입니다. 다음 대화에서 이 단어를 기억해 보세요.",
      },
      process: {
        step: "render",
        detail:
          "답변과 분석 카드, 추세 차트, 턴 기록까지 모두 최신 결과로 갱신했습니다.",
      },
      systemState: "분석 완료",
      thinkingText: "가장 최근 대화 기준의 분석 결과를 확인하고 있습니다.",
      analysisState: "final",
      analysisTurn: finalTurn,
      voiceLevel: 0.06,
    },
    recall: {
      name: "recall",
      llmMode: "local",
      summary: monitoringSummary,
      scoreHistory: sharedScoreHistory,
      turnHistory: [
        baselineTurn,
        finalTurn,
        buildDemoTurn({
          turn_id: "demo-turn-recall",
          time: "10:22:04",
          user_text: "아까 제시한 기억 단어가 뭐였지?",
          answer:
            "Recall Test 질문에 답하는 단계입니다. 조금 전에 제시한 기억 단어를 떠올려 보세요.",
          judgment: "의심",
          score: 31,
          reason:
            "직전 제시 정보와 관련된 회상 질문이 진행 중인 상태를 보여주는 데모 장면입니다.",
          feature_scores: {
            repetition: 8,
            memory: 10,
            time_confusion: 8,
            incoherence: 5,
          },
          risk_level: "낮은 위험",
          trend: "상승",
          average_score: monitoringSummary.average_score,
          recent_average_score: monitoringSummary.recent_average_score,
        }),
      ],
      pendingTurns: [],
      recall: {
        status: "ask",
        last_result: "없음",
        prompt: "Recall Test: 제가 조금 전에 제시한 기억 단어가 무엇이었나요?",
      },
      process: {
        step: "render",
        detail: "분석 결과와 함께 기억 회상 테스트 단계가 표시되고 있습니다.",
      },
      systemState: "기억 회상 테스트 진행 중",
      thinkingText: "기억 단어 회상 여부를 확인하는 단계입니다.",
      analysisState: "final",
      analysisTurn: finalTurn,
      voiceLevel: 0.06,
    },
  };
}

function initializeDemoView() {
  if (!isDemoMode) {
    return false;
  }

  document.body.classList.add("is-demo-mode");
  if (chatContainer) {
    chatContainer.classList.add("is-demo-mode");
  }

  pendingTurns = [];
  analysisTaskQueue = [];
  isAnalysisWorkerRunning = false;
  setAnalysisGeneration(0);
  setRecordButtonBusyState(false);
  hideWarningPopup();
  applyDemoScenario(demoMode);
  return true;
}

function applyDemoScenario(name = demoMode) {
  const scenarios = getDemoScenarioMap();
  const scenario = scenarios[name] || scenarios.overview;
  const summary = scenario.summary || {
    average_score: 0,
    recent_average_score: 0,
    trend: "데이터 부족",
  };

  document.body.dataset.demoReady = "false";
  document.body.dataset.demoScenario = scenario.name;

  llmMode = normalizeLlmMode(scenario.llmMode || "local");
  localStorage.setItem("llm_mode", llmMode);
  renderLlmModeState();

  scoreHistory = Array.isArray(scenario.scoreHistory)
    ? scenario.scoreHistory.map((item) => ({ ...item }))
    : [];
  turnHistory = Array.isArray(scenario.turnHistory)
    ? scenario.turnHistory.map((turn) => ({
        ...turn,
        feature_scores: { ...(turn.feature_scores || {}) },
        follow_up_messages: [...(turn.follow_up_messages || [])],
      }))
    : [];
  pendingTurns = Array.isArray(scenario.pendingTurns)
    ? scenario.pendingTurns.map((turn) => ({
        ...turn,
        follow_up_messages: [...(turn.follow_up_messages || [])],
        is_pending: true,
      }))
    : [];
  selectedTurnId = null;

  setAnalysisLoadingState(false);
  setAnalysisThinking(false);
  setRecordingState(false);
  stopVoiceAmbient();
  setVoiceLevel(Number(scenario.voiceLevel ?? 0.06));

  renderAll({
    average_score: summary.average_score,
    recent_average_score: summary.recent_average_score,
    trend: summary.trend,
  });
  updateRecallCard(
    scenario.recall || { status: "idle", last_result: "없음", prompt: "" },
  );

  if (turnHistory.length > 0 || pendingTurns.length > 0) {
    renderConversationHistory({
      preferLatestTurn: true,
      preserveAnalysisCard: true,
    });
  } else {
    renderChatEmptyState();
    resetAnalysisCard();
  }

  if (scenario.analysisState === "loading") {
    if (analysisJudgmentEl) analysisJudgmentEl.innerText = "분석 중";
    if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = "대기";
    if (analysisTrendEl) analysisTrendEl.innerText = "진행 중";
    if (analysisReasonEl) {
      analysisReasonEl.innerText =
        "답변은 먼저 생성되었고, 역할별 위험도 분석을 순차적으로 시작하고 있습니다.";
    }
    setAnalysisScoreDisplay("-", false);
    updateFeatureBreakdown({});
    updateConfidence({}, 0, false);
    setAnalysisStateBadge(
      "분석 대기",
      "idle",
      "답변 표시 후 역할별 분석이 시작되는 장면입니다.",
    );
    setAnalysisLoadingState(true);
  } else if (scenario.analysisState === "preview" && scenario.analysisPreview) {
    applyProgressiveAnalysisPreview(
      scenario.analysisPreview.roleResults,
      scenario.analysisPreview.currentRole,
      scenario.analysisPreview.completedCount,
      scenario.analysisPreview.totalCount,
    );
    applyProgressiveSummaryPreview(scenario.analysisPreview.roleResults);
  } else if (scenario.analysisState === "final" && scenario.analysisTurn) {
    applyTurnAnalysis(scenario.analysisTurn);
  } else {
    resetAnalysisCard();
    updateFeatureBreakdown({});
    updateConfidence({}, 0, false);
  }

  if (scenario.recording) {
    setRecordingState(true);
  }

  if (scenario.analysisThinking) {
    setAnalysisThinking(true);
  }

  if (scenario.process?.step) {
    setProcessState(scenario.process.step, scenario.process.detail || "");
  } else {
    resetProcessState(
      scenario.process?.detail ||
        "대기 중입니다. 녹음을 시작하면 음성 입력을 기다립니다.",
    );
  }

  setSystemState(scenario.systemState || "데모 화면");
  setThinkingMessage(scenario.thinkingText || "문서 촬영용 데모 상태입니다.");

  window.setTimeout(() => {
    document.body.dataset.demoReady = "true";
    window.dispatchEvent(
      new CustomEvent("ncai:demo-ready", { detail: { name: scenario.name } }),
    );
  }, 80);

  return scenario;
}

window.__ncaiDemo = {
  isDemoMode,
  applyScenario: applyDemoScenario,
  scenarioNames: Object.keys(getDemoScenarioMap()),
};

function setVoiceLevel(level = 0.06) {
  const normalizedLevel = Math.max(0.06, Math.min(1, Number(level) || 0.06));
  document.documentElement.style.setProperty(
    "--voice-level",
    normalizedLevel.toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-core-opacity",
    (0.1 + normalizedLevel * 0.14).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-halo-opacity",
    (0.06 + normalizedLevel * 0.08).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-opacity",
    (0.08 + normalizedLevel * 0.16).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-back-scale",
    (0.94 + normalizedLevel * 0.14).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-back-peak",
    (1 + normalizedLevel * 0.22).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-mid-scale",
    (1 + normalizedLevel * 0.18).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-mid-peak",
    (1.06 + normalizedLevel * 0.28).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-front-scale",
    (1.02 + normalizedLevel * 0.22).toFixed(3),
  );
  document.documentElement.style.setProperty(
    "--voice-wave-front-peak",
    (1.08 + normalizedLevel * 0.34).toFixed(3),
  );
}

document.addEventListener("DOMContentLoaded", async () => {
  setVoiceLevel(0.06);
  normalizeCollapsibleLayout();
  injectThreeDIcons();
  bindEvents();
  initializeMobileTabs();
  initializeDisclosureSurfaces();
  positionTimelineCardNearRecall();
  setupAnalysisHelpPopovers();
  updateSessionReportButtonState();
  refreshSessionReportSurface();
  updateRecordToggleButton();
  resetProcessState("대기 중입니다. 녹음을 시작하면 음성 입력을 기다립니다.");
  if (initializeDemoView()) {
    return;
  }
  await loadLlmProviderStatus();
  await loadScoreHistory();
});

function bindEvents() {
  if (startButton) startButton.onclick = toggleRecording;
  if (resetButton) resetButton.onclick = resetHistory;
  if (openSessionReportButton)
    openSessionReportButton.onclick = openSessionReport;
  if (mobileOpenSessionReportButton)
    mobileOpenSessionReportButton.onclick = openSessionReport;
  if (openSessionReportInlineButton)
    openSessionReportInlineButton.onclick = openSessionReport;
  if (mobileResetHistoryButton) mobileResetHistoryButton.onclick = resetHistory;
  if (mobileMenuToggleButton) mobileMenuToggleButton.onclick = toggleMobileMenu;
  if (mobileMenuCloseButton) mobileMenuCloseButton.onclick = closeMobileMenu;
  if (mobileMenuScrim) mobileMenuScrim.onclick = closeMobileMenu;
  if (llmModeLocalButton)
    llmModeLocalButton.onclick = () => setLlmMode("local");
  if (llmModeApiButton) llmModeApiButton.onclick = () => setLlmMode("api");
  if (closeWarningPopupButton)
    closeWarningPopupButton.onclick = hideWarningPopup;
  if (closeSessionReportButton)
    closeSessionReportButton.onclick = closeSessionReport;
  if (closeSessionReportFooterButton)
    closeSessionReportFooterButton.onclick = closeSessionReport;
  if (printSessionReportButton)
    printSessionReportButton.onclick = printSessionReport;
  if (sessionReportModal) {
    sessionReportModal.onclick = (event) => {
      if (event.target === sessionReportModal) {
        closeSessionReport();
      }
    };
  }
  window.addEventListener("keydown", handleGlobalKeydown);
  window.addEventListener("resize", () => {
    repositionActiveHelpPopovers();
    applyMobileTabState();
  });
  window.addEventListener("scroll", repositionActiveHelpPopovers, true);
}

function normalizeCollapsibleLayout() {
  if (
    analysisDetailDisclosureEl &&
    analysisDetailCardEl &&
    analysisDetailDisclosureEl.contains(analysisDetailCardEl)
  ) {
    analysisDetailDisclosureEl.parentElement?.insertBefore(
      analysisDetailCardEl,
      analysisDetailDisclosureEl,
    );
  }

  analysisHeroCardEl?.classList.add("is-primary-surface");
  analysisDetailCardEl?.classList.add("is-primary-surface");
}

function buildThreeDIcon(src, alt, className) {
  const image = document.createElement("img");
  image.src = src;
  image.alt = alt;
  image.loading = "lazy";
  image.decoding = "async";
  image.className = className;
  return image;
}

function injectThreeDIcons() {
  if (!statusCardEl?.querySelector(".status-card-visual")) {
    const visual = document.createElement("div");
    visual.className = "status-card-visual";
    visual.appendChild(
      buildThreeDIcon(
        THREE_D_ICON_PATHS.status.safe,
        "위험 상태 아이콘",
        "status-card-visual-image",
      ),
    );
    statusCardEl.appendChild(visual);
  }

  const disclosureVisualConfigs = [
    {
      detailsEl: sidebarMetricsDisclosureEl,
      src: THREE_D_ICON_PATHS.metrics,
      alt: "상세 지표 아이콘",
    },
    {
      detailsEl: historyDisclosureEl,
      src: THREE_D_ICON_PATHS.history,
      alt: "기록 타임라인 아이콘",
    },
    {
      detailsEl: analysisDetailDisclosureEl,
      src: THREE_D_ICON_PATHS.detail,
      alt: "세부 분석 아이콘",
    },
    {
      detailsEl: recallDisclosureEl,
      src: THREE_D_ICON_PATHS.recall,
      alt: "기억 회상 테스트 아이콘",
    },
  ];

  disclosureVisualConfigs.forEach(({ detailsEl, src, alt }) => {
    const summaryEl = detailsEl?.querySelector(".panel-disclosure-summary");
    if (!summaryEl || summaryEl.querySelector(".panel-disclosure-visual")) {
      return;
    }

    summaryEl.classList.add("has-3d-icon");
    const visual = document.createElement("span");
    visual.className = "panel-disclosure-visual";
    visual.appendChild(
      buildThreeDIcon(src, alt, "panel-disclosure-visual-image"),
    );
    summaryEl.insertBefore(visual, summaryEl.firstChild);
  });
}

function setDisclosureOpenState(detailsEl, shouldOpen) {
  if (!detailsEl) {
    return;
  }

  detailsEl.dataset.syncing = "true";
  detailsEl.open = Boolean(shouldOpen);
  window.requestAnimationFrame(() => {
    delete detailsEl.dataset.syncing;
  });
}

function registerDisclosureSurface(detailsEl) {
  if (!detailsEl) {
    return;
  }

  detailsEl.addEventListener("toggle", () => {
    if (detailsEl.dataset.syncing === "true") {
      return;
    }

    detailsEl.dataset.userToggled = "true";

    if (detailsEl.open) {
      window.requestAnimationFrame(() => {
        detailsEl.scrollIntoView({
          block: "nearest",
          inline: "nearest",
          behavior: "smooth",
        });
      });
    }
  });
}

function initializeDisclosureSurfaces() {
  [
    sidebarMetricsDisclosureEl,
    historyDisclosureEl,
    analysisDetailDisclosureEl,
    recallDisclosureEl,
  ].forEach(registerDisclosureSurface);

  setDisclosureOpenState(sidebarMetricsDisclosureEl, false);
  setDisclosureOpenState(historyDisclosureEl, false);
  setDisclosureOpenState(analysisDetailDisclosureEl, false);
  setDisclosureOpenState(recallDisclosureEl, false);
}

function positionTimelineCardNearRecall() {
  const timelineCard =
    historyDisclosureEl || document.querySelector(".timeline-card");
  const recallCard =
    recallDisclosureEl || document.querySelector(".recall-card");

  if (!timelineCard || !recallCard || !recallCard.parentElement) {
    return;
  }

  if (timelineCard.nextElementSibling === recallCard) {
    return;
  }

  recallCard.parentElement.insertBefore(timelineCard, recallCard);
}

function isPopoverSupported() {
  return typeof HTMLElement !== "undefined" &&
    typeof HTMLElement.prototype.showPopover === "function"
    ? true
    : false;
}

function hideAllHelpPopovers(exceptPopover = null) {
  helpPopoverRegistry.forEach(({ popover }) => {
    if (!popover || popover === exceptPopover) {
      return;
    }

    if (popover.matches(":popover-open")) {
      popover.hidePopover();
    }
  });
}

function positionHelpPopover(trigger, popover) {
  if (!trigger || !popover || !popover.matches(":popover-open")) {
    return;
  }

  const triggerRect = trigger.getBoundingClientRect();
  const popoverRect = popover.getBoundingClientRect();
  const margin = 14;
  const top = Math.min(
    window.innerHeight - popoverRect.height - margin,
    Math.max(margin, triggerRect.bottom + 10),
  );
  const left = Math.min(
    window.innerWidth - popoverRect.width - margin,
    Math.max(margin, triggerRect.right - popoverRect.width),
  );

  popover.style.top = `${top}px`;
  popover.style.left = `${left}px`;
}

function showHelpPopover(trigger, popover) {
  if (!isPopoverSupported() || !trigger || !popover) {
    return;
  }

  hideAllHelpPopovers(popover);

  if (!popover.matches(":popover-open")) {
    popover.showPopover();
  }

  positionHelpPopover(trigger, popover);
}

function repositionActiveHelpPopovers() {
  if (!isPopoverSupported()) {
    return;
  }

  helpPopoverRegistry.forEach(({ trigger, popover }) => {
    if (popover?.matches(":popover-open")) {
      positionHelpPopover(trigger, popover);
    }
  });
}

function ensureTitleTools(titleRow) {
  if (!titleRow) {
    return null;
  }

  let tools = titleRow.querySelector(":scope > .analysis-title-tools");
  if (tools) {
    return tools;
  }

  tools = document.createElement("div");
  tools.className = "analysis-title-tools";

  Array.from(titleRow.children)
    .filter((child) => !child.classList.contains("analysis-title"))
    .forEach((child) => {
      tools.appendChild(child);
    });

  titleRow.appendChild(tools);
  return tools;
}

function attachHelpPopover(card, title, description) {
  if (!card || !title || !description) {
    return;
  }

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "analysis-help-trigger";
  trigger.setAttribute("aria-label", `${title} 도움말`);
  trigger.innerHTML = '<span aria-hidden="true">?</span>';

  const titleRow = card.querySelector(":scope > .analysis-title-row");
  if (titleRow) {
    const tools = ensureTitleTools(titleRow);
    tools?.insertBefore(trigger, tools.firstChild || null);
  } else {
    trigger.classList.add("is-floating");
    card.appendChild(trigger);
  }

  if (!isPopoverSupported()) {
    trigger.title = `${title}: ${description}`;
    return;
  }

  const popover = document.createElement("div");
  popover.className = "analysis-help-popover";
  popover.setAttribute("popover", "auto");
  popover.innerHTML = `
    <div class="analysis-help-popover-kicker">도움말</div>
    <div class="analysis-help-popover-title">${escapeHtml(title)}</div>
    <div class="analysis-help-popover-copy">${escapeHtml(description)}</div>
  `;
  document.body.appendChild(popover);

  let hideTimer = null;
  const clearHideTimer = () => {
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
  };
  const scheduleHide = () => {
    clearHideTimer();
    hideTimer = setTimeout(() => {
      if (popover.matches(":popover-open")) {
        popover.hidePopover();
      }
    }, 120);
  };

  trigger.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();

    if (popover.matches(":popover-open")) {
      popover.hidePopover();
      return;
    }

    showHelpPopover(trigger, popover);
  });
  trigger.addEventListener("mouseenter", () => {
    clearHideTimer();
    showHelpPopover(trigger, popover);
  });
  trigger.addEventListener("mouseleave", scheduleHide);
  trigger.addEventListener("focus", () => showHelpPopover(trigger, popover));
  trigger.addEventListener("blur", scheduleHide);
  popover.addEventListener("mouseenter", clearHideTimer);
  popover.addEventListener("mouseleave", scheduleHide);
  popover.addEventListener("toggle", () => {
    if (popover.matches(":popover-open")) {
      positionHelpPopover(trigger, popover);
    }
  });

  helpPopoverRegistry.push({ trigger, popover });
}

function setupAnalysisHelpPopovers() {
  const heroCard = document.querySelector(".analysis-hero-card");
  const timelineCard = document.querySelector(".timeline-card");
  const analysisCard = analysisStateBadgeEl?.closest(".analysis-card");
  const confidenceCard = confidenceScoreEl?.closest(".analysis-card");
  const recallCard = document.querySelector(".recall-card");

  attachHelpPopover(
    heroCard,
    "세션 요약",
    "최근 반영 점수, 위험도, 추세, 분석 모드를 한눈에 보여주는 상단 요약 카드입니다.",
  );
  attachHelpPopover(
    timelineCard,
    "턴 타임라인",
    "최근 대화 턴의 점수 흐름과 선택 가능한 기록 목록을 제공합니다. 턴을 누르면 해당 시점 분석 결과를 다시 볼 수 있습니다.",
  );
  attachHelpPopover(
    analysisCard,
    "AI 분석 결과",
    "선택된 턴 또는 최신 턴 기준으로 판단, 의심 점수, 위험도, 추세, 근거를 상세하게 보여줍니다.",
  );
  attachHelpPopover(
    confidenceCard,
    "분석 신뢰도",
    "언어 특징 점수와 총점을 바탕으로 계산한 휴리스틱 지표입니다. 높을수록 분석 근거가 더 뚜렷하다고 해석합니다.",
  );
  attachHelpPopover(
    recallCard,
    "기억 회상 테스트",
    "세션 중 제시된 단어를 이후 턴에서 다시 회상하는지 확인하는 보조 평가 영역입니다.",
  );
}

function handleGlobalKeydown(event) {
  if (event.key !== "Escape") {
    return;
  }

  if (sessionReportModal && !sessionReportModal.classList.contains("hidden")) {
    closeSessionReport();
    return;
  }

  if (isMobileMenuOpen) {
    closeMobileMenu();
    return;
  }

  hideWarningPopup();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatTurnTime(value) {
  const text = normalizeText(value);
  if (text) {
    return text;
  }

  return new Date().toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function getReportRecallSummary() {
  const status = normalizeText(recallStatusEl?.innerText || "대기");
  const lastResult = normalizeText(recallLastResultEl?.innerText || "없음");
  const prompt = normalizeText(recallPromptEl?.innerText || "");

  if (!prompt) {
    return `상태: ${status} / 최근 결과: ${lastResult}`;
  }

  return `상태: ${status} / 최근 결과: ${lastResult} / ${prompt}`;
}

function buildFeatureSnapshot(featureScores = {}) {
  return [
    {
      key: "repetition",
      label: "질문 반복",
      score: Number(featureScores.repetition ?? 0),
      max: 25,
    },
    {
      key: "memory",
      label: "기억 혼란",
      score: Number(featureScores.memory ?? 0),
      max: 25,
    },
    {
      key: "time_confusion",
      label: "시간 / 상황 혼란",
      score: Number(featureScores.time_confusion ?? 0),
      max: 30,
    },
    {
      key: "incoherence",
      label: "문장 비논리성",
      score: Number(featureScores.incoherence ?? 0),
      max: 20,
    },
  ];
}

function buildSessionReportData() {
  const finalizedTurns = Array.isArray(turnHistory) ? [...turnHistory] : [];
  const includedTurns = finalizedTurns.filter((turn) => isScoreIncluded(turn));
  const latestTurn =
    finalizedTurns.length > 0
      ? finalizedTurns[finalizedTurns.length - 1]
      : null;
  const latestIncludedTurn =
    includedTurns.length > 0 ? includedTurns[includedTurns.length - 1] : null;
  const peakTurn = includedTurns.reduce((highest, turn) => {
    if (!highest) {
      return turn;
    }

    return Number(turn.score ?? 0) > Number(highest.score ?? 0)
      ? turn
      : highest;
  }, null);
  const averageScore =
    scoreHistory.length > 0
      ? scoreHistory.reduce((sum, item) => sum + Number(item.score ?? 0), 0) /
        scoreHistory.length
      : 0;
  const recentScores = scoreHistory.slice(-5);
  const recentAverage =
    recentScores.length > 0
      ? recentScores.reduce((sum, item) => sum + Number(item.score ?? 0), 0) /
        recentScores.length
      : averageScore;
  const latestFeatureTurn = latestIncludedTurn || latestTurn;
  const latestFeatureSnapshot = buildFeatureSnapshot(
    latestFeatureTurn?.feature_scores || {},
  );
  const latestRiskLabel = localizeRiskLevel(
    latestIncludedTurn?.risk_level || latestTurn?.risk_level || "-",
  );
  const reportStatus = !latestTurn
    ? { label: "대기", tone: "idle" }
    : latestTurn.score_included === false
      ? { label: "점수 미반영", tone: "excluded" }
      : latestTurn.judgment === "의심"
        ? { label: "주의 관찰", tone: "warning" }
        : { label: "분석 완료", tone: "complete" };

  return {
    sessionId: sessionId || "-",
    generatedAt: new Date().toLocaleString("ko-KR"),
    turnCount: finalizedTurns.length,
    includedCount: includedTurns.length,
    averageScore,
    recentAverage,
    latestTurn,
    latestIncludedTurn,
    peakTurn,
    latestRiskLabel,
    reportStatus,
    latestFeatureSnapshot,
    llmModeLabel: getLlmModeLabel(latestTurn?.llm_provider || llmMode),
    recallSummary: getReportRecallSummary(),
    turnsForReport: finalizedTurns.slice(-6).reverse(),
  };
}

function renderSessionReportModal() {
  const report = buildSessionReportData();
  lastRenderedSessionReport = report;

  if (reportGeneratedAtEl) {
    reportGeneratedAtEl.innerText = `생성 시각: ${report.generatedAt} / 세션 ID: ${report.sessionId}`;
  }

  if (reportStatusBadgeEl) {
    reportStatusBadgeEl.innerText = report.reportStatus.label;
    reportStatusBadgeEl.classList.remove(
      "is-idle",
      "is-complete",
      "is-warning",
      "is-excluded",
    );
    reportStatusBadgeEl.classList.add(`is-${report.reportStatus.tone}`);
  }

  if (reportHeadlineEl) {
    reportHeadlineEl.innerText =
      report.turnCount > 0
        ? "대화 세션 종합 요약"
        : "아직 생성된 세션 리포트가 없습니다.";
  }

  if (reportSubtextEl) {
    if (report.turnCount > 0) {
      const peakLabel = report.peakTurn
        ? `최고 위험 점수 ${report.peakTurn.score}점`
        : "최고 위험 점수 없음";
      reportSubtextEl.innerText = `총 ${report.turnCount}개의 대화를 기준으로 요약했습니다. 점수 반영 ${report.includedCount}건, ${peakLabel} 기준으로 세션 상태를 정리합니다.`;
    } else {
      reportSubtextEl.innerText =
        "녹음을 시작하면 판단, 점수, 추세, 언어 특징이 이 리포트에 누적됩니다.";
    }
  }

  if (reportTurnCountEl) reportTurnCountEl.innerText = String(report.turnCount);
  if (reportIncludedCountEl)
    reportIncludedCountEl.innerText = String(report.includedCount);
  if (reportAverageScoreEl)
    reportAverageScoreEl.innerText = report.averageScore.toFixed(1);
  if (reportRecentAverageEl)
    reportRecentAverageEl.innerText = report.recentAverage.toFixed(1);
  if (reportLatestScoreEl) {
    reportLatestScoreEl.innerText = report.latestIncludedTurn
      ? String(report.latestIncludedTurn.score ?? 0)
      : "-";
  }
  if (reportPeakScoreEl) {
    reportPeakScoreEl.innerText = report.peakTurn
      ? String(report.peakTurn.score ?? 0)
      : "-";
  }

  if (reportLatestJudgmentEl) {
    reportLatestJudgmentEl.innerText = report.latestTurn?.judgment || "-";
  }
  if (reportLatestRiskEl) {
    reportLatestRiskEl.innerText = report.latestRiskLabel;
  }
  if (reportLatestTrendEl) {
    reportLatestTrendEl.innerText = report.latestTurn?.trend || "-";
  }
  if (reportLlmModeEl) {
    reportLlmModeEl.innerText = report.llmModeLabel;
  }
  if (reportLatestReasonEl) {
    reportLatestReasonEl.innerText =
      report.latestTurn?.reason ||
      "최신 분석 근거가 아직 없습니다. 대화를 시작하면 이곳에 요약이 표시됩니다.";
  }

  if (reportFeatureListEl) {
    reportFeatureListEl.innerHTML = report.latestFeatureSnapshot
      .map(
        (feature) => `
          <div class="report-feature-item">
            <span>${escapeHtml(feature.label)}</span>
            <div class="report-feature-track">
              <div class="report-feature-fill" style="width: ${Math.max(0, Math.min(100, (feature.score / feature.max) * 100))}%"></div>
            </div>
            <strong class="report-feature-score">${feature.score}</strong>
          </div>
        `,
      )
      .join("");
  }

  if (reportRecallSummaryEl) {
    reportRecallSummaryEl.innerText = report.recallSummary;
  }

  if (reportTurnListEl) {
    if (report.turnsForReport.length === 0) {
      reportTurnListEl.innerHTML = `
        <div class="report-turn-item">
          <div class="report-turn-copy">
            <span>아직 저장된 턴 기록이 없습니다. 녹음을 시작하면 최신 6개 턴을 이곳에서 확인할 수 있습니다.</span>
          </div>
        </div>
      `;
    } else {
      reportTurnListEl.innerHTML = report.turnsForReport
        .map((turn, index) => {
          const scoreLabel =
            turn.score_included === false
              ? "반영 제외"
              : `${turn.score ?? 0}점`;
          return `
            <div class="report-turn-item">
              <div class="report-turn-head">
                <div>
                  <div class="report-turn-title">턴 ${report.turnsForReport.length - index}</div>
                  <div class="report-turn-meta">${escapeHtml(formatTurnTime(turn.time))} · ${escapeHtml(turn.judgment || "-")} · ${escapeHtml(localizeRiskLevel(turn.risk_level || "-"))}</div>
                </div>
                <div class="report-turn-score">${escapeHtml(scoreLabel)}</div>
              </div>
              <div class="report-turn-copy">
                <span><strong>사용자:</strong> ${escapeHtml(turn.user_text || "")}</span>
                <span><strong>답변:</strong> ${escapeHtml(turn.answer || "")}</span>
              </div>
            </div>
          `;
        })
        .join("");
    }
  }

  updateSessionReportButtonState();
  return report;
}

function updateSessionReportButtonState() {
  const isDisabled = turnHistory.length === 0;

  if (openSessionReportButton) {
    openSessionReportButton.disabled = isDisabled;
  }

  if (mobileOpenSessionReportButton) {
    mobileOpenSessionReportButton.disabled = isDisabled;
  }

  if (openSessionReportInlineButton) {
    openSessionReportInlineButton.disabled = isDisabled;
  }
}

function refreshWorkspaceOverviewSurface() {
  const latestTurn =
    turnHistory.length > 0 ? turnHistory[turnHistory.length - 1] : null;
  const latestIncludedTurn = [...turnHistory]
    .filter((turn) => isScoreIncluded(turn))
    .slice(-1)[0];
  const pendingCount = pendingTurns.filter((turn) =>
    ["queued", "analyzing"].includes(turn?.pending_status || "queued"),
  ).length;
  const recallStatus = normalizeText(recallStatusEl?.innerText || "대기");
  const recallLastResult = normalizeText(
    recallLastResultEl?.innerText || "없음",
  );
  const recallPrompt = normalizeText(recallPromptEl?.innerText || "");
  const currentState = normalizeText(systemStateText?.innerText || "");
  const currentThinking = normalizeText(aiThinking?.innerText || "");

  let stateTitle = "분석 준비 완료";
  let stateCopy = `현재 모드: ${getLlmModeLabel(llmMode)} · 녹음을 시작하면 답변과 역할별 분석 흐름이 차례대로 진행됩니다.`;

  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    stateTitle = "음성 입력 수집 중";
    stateCopy = `현재 모드: ${getLlmModeLabel(llmMode)} · 마이크에서 사용자 발화를 실시간으로 수집하고 있습니다.`;
  } else if (isAnswerPending) {
    stateTitle = "답변 생성 중";
    stateCopy = `현재 모드: ${getLlmModeLabel(llmMode)} · ${recordButtonBusyLabel || "질문에 대한 1차 답변을 정리하고 있습니다."}`;
  } else if (pendingCount > 0 || isAnalysisWorkerRunning) {
    stateTitle = "백그라운드 분석 진행 중";
    stateCopy = `현재 모드: ${getLlmModeLabel(llmMode)} · ${pendingCount || analysisTaskQueue.length || 1}건 대화의 역할별 점수를 순차적으로 반영하고 있습니다.`;
  } else if (latestTurn) {
    stateTitle = currentState || "최신 결과 반영 완료";
    stateCopy =
      currentThinking ||
      `현재 모드: ${getLlmModeLabel(llmMode)} · 가장 최근 대화 기준의 결과가 안정적으로 반영된 상태입니다.`;
  }

  if (workspaceOverviewStateEl) {
    workspaceOverviewStateEl.innerText = stateTitle;
  }
  if (workspaceOverviewCopyEl) {
    workspaceOverviewCopyEl.innerText = stateCopy;
  }

  if (workspaceOverviewLatestEl) {
    workspaceOverviewLatestEl.innerText = latestTurn
      ? `${latestTurn.judgment || "분석 완료"} · ${localizeRiskLevel(
          latestTurn.risk_level || getRiskLevelFromScore(latestTurn.score ?? 0),
        )}`
      : "아직 분석 기록 없음";
  }
  if (workspaceOverviewLatestMetaEl) {
    workspaceOverviewLatestMetaEl.innerText = latestTurn
      ? latestTurn.score_included === false
        ? "이번 대화는 기록으로 저장했고 점수 통계에서는 제외했습니다."
        : `최신 점수 ${latestTurn.score ?? 0}점 · 추세 ${latestTurn.trend || "데이터 부족"} · 대화 ${turnHistory.length}건`
      : "최신 점수와 위험도, 추세가 이곳에 요약됩니다.";
  }

  if (latestIncludedTurn && latestTurn && latestTurn !== latestIncludedTurn) {
    if (workspaceOverviewLatestMetaEl) {
      workspaceOverviewLatestMetaEl.innerText = `최신 반영 점수 ${latestIncludedTurn.score ?? 0}점 · 추세 ${latestIncludedTurn.trend || "데이터 부족"} · 최근 대화는 통계에서 제외되었습니다.`;
    }
  }

  if (workspaceOverviewRecallEl) {
    workspaceOverviewRecallEl.innerText = recallStatus || "대기";
  }
  if (workspaceOverviewRecallMetaEl) {
    workspaceOverviewRecallMetaEl.innerText = recallPrompt
      ? `최근 결과 ${recallLastResult} · ${buildStatusPreview(recallPrompt, 52)}`
      : `최근 결과 ${recallLastResult} · 아직 진행 중인 회상 테스트가 없습니다.`;
  }
}

function getTurnBadgeMeta(turn) {
  if (!turn) {
    return { label: "대기", tone: "idle" };
  }

  if (turn.score_included === false) {
    return { label: "점수 미반영", tone: "excluded" };
  }

  if (turn.judgment === "의심") {
    return { label: "주의 관찰", tone: "warning" };
  }

  return { label: "분석 완료", tone: "complete" };
}

function getRiskColor(score, scoreIncluded = true) {
  if (!scoreIncluded) {
    return "#ff7b7b";
  }

  return getRiskInfo(Number(score ?? 0)).color;
}

function getTimelineTrendSnapshot() {
  const scorePoints = Array.isArray(scoreHistory)
    ? scoreHistory
        .filter((item) => Number.isFinite(Number(item?.score)))
        .slice(-6)
        .map((item, index) => ({
          score: Number(item.score),
          time: formatTurnTime(item.time),
          index,
        }))
    : [];

  if (scorePoints.length > 0) {
    return scorePoints;
  }

  return [...turnHistory]
    .filter(
      (turn) => isScoreIncluded(turn) && Number.isFinite(Number(turn?.score)),
    )
    .slice(-6)
    .map((turn, index) => ({
      score: Number(turn.score),
      time: formatTurnTime(turn.time),
      index,
    }));
}

function buildTimelineSparklineMarkup(points) {
  if (!Array.isArray(points) || points.length === 0) {
    return `
      <div class="analysis-timeline-sparkline-empty">
        반영된 점수가 누적되면 이곳에 최근 흐름이 표시됩니다.
      </div>
    `;
  }

  const width = 280;
  const height = 110;
  const paddingX = 12;
  const paddingTop = 12;
  const paddingBottom = 18;
  const scores = points.map((point) => point.score);
  const minScore = Math.min(...scores);
  const maxScore = Math.max(...scores);
  const scoreRange = Math.max(maxScore - minScore, 12);
  const domainMin = Math.max(0, minScore - 6);
  const domainMax = Math.min(
    100,
    Math.max(maxScore + 6, domainMin + scoreRange),
  );
  const drawableWidth = width - paddingX * 2;
  const drawableHeight = height - paddingTop - paddingBottom;

  const pointCoordinates = points.map((point, index) => {
    const x =
      points.length === 1
        ? width / 2
        : paddingX + (drawableWidth * index) / (points.length - 1);
    const normalizedScore =
      (point.score - domainMin) / Math.max(domainMax - domainMin, 1);
    const y = paddingTop + drawableHeight * (1 - normalizedScore);

    return {
      ...point,
      x: Number(x.toFixed(2)),
      y: Number(y.toFixed(2)),
    };
  });

  const polylinePoints = pointCoordinates
    .map((point) => `${point.x},${point.y}`)
    .join(" ");
  const baselineY = height - paddingBottom;
  const areaPath = [
    `M ${pointCoordinates[0].x} ${baselineY}`,
    ...pointCoordinates.map((point) => `L ${point.x} ${point.y}`),
    `L ${pointCoordinates[pointCoordinates.length - 1].x} ${baselineY}`,
    "Z",
  ].join(" ");
  const gridLines = [0.25, 0.5, 0.75]
    .map((ratio) => {
      const y = Number((paddingTop + drawableHeight * ratio).toFixed(2));
      return `<line class="analysis-timeline-grid-line" x1="${paddingX}" y1="${y}" x2="${
        width - paddingX
      }" y2="${y}"></line>`;
    })
    .join("");
  const pointDots = pointCoordinates
    .map((point, index) => {
      const isLatest = index === pointCoordinates.length - 1;
      const radius = isLatest ? 5.5 : 4;
      const latestFill = isLatest
        ? ` style="fill: ${getRiskColor(point.score, true)};"`
        : "";
      return `<circle class="analysis-timeline-point${
        isLatest ? " is-latest" : ""
      }" cx="${point.x}" cy="${point.y}" r="${radius}"${latestFill}></circle>`;
    })
    .join("");

  return `
    <svg
      class="analysis-timeline-sparkline-svg"
      viewBox="0 0 ${width} ${height}"
      aria-label="최근 반영 점수 흐름"
      role="img"
    >
      <defs>
          <linearGradient id="timelineSparkAreaGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#82d6f6" stop-opacity="0.34"></stop>
            <stop offset="100%" stop-color="#82d6f6" stop-opacity="0.02"></stop>
          </linearGradient>
      </defs>
      ${gridLines}
      <path class="analysis-timeline-area" d="${areaPath}"></path>
      <polyline class="analysis-timeline-line" points="${polylinePoints}"></polyline>
      ${pointDots}
    </svg>
    <div class="analysis-timeline-scale">
      <span>${escapeHtml(pointCoordinates[0].time || "초기 반영")}</span>
      <span>${escapeHtml(
        pointCoordinates[pointCoordinates.length - 1].time || "최근 반영",
      )}</span>
    </div>
  `;
}

function renderTimelineTrendSummary() {
  const snapshot = getTimelineTrendSnapshot();

  if (timelineMetaEl) {
    timelineMetaEl.innerText =
      turnHistory.length > 0
        ? `최근 ${Math.min(turnHistory.length, 8)}개 턴`
        : "기록 없음";
  }

  if (timelineTrendBadgeEl) {
    timelineTrendBadgeEl.classList.remove(
      "is-idle",
      "is-rise",
      "is-fall",
      "is-steady",
    );
  }

  if (!timelineSparklineEl || !timelineTrendBadgeEl || !timelineTrendCopyEl) {
    return;
  }

  if (snapshot.length === 0) {
    timelineTrendBadgeEl.innerText = "데이터 부족";
    timelineTrendBadgeEl.classList.add("is-idle");
    timelineTrendCopyEl.innerText =
      "최근 반영 점수가 2건 이상 쌓이면 상승/하락 흐름을 함께 보여줍니다.";
    timelineSparklineEl.innerHTML = buildTimelineSparklineMarkup([]);
    return;
  }

  if (snapshot.length === 1) {
    timelineTrendBadgeEl.innerText = "단일 기록";
    timelineTrendBadgeEl.classList.add("is-idle");
    timelineTrendCopyEl.innerText = `최근 반영 점수 ${snapshot[0].score}점이 첫 기록으로 저장되었습니다.`;
    timelineSparklineEl.innerHTML = buildTimelineSparklineMarkup(snapshot);
    return;
  }

  const firstScore = snapshot[0].score;
  const latestScore = snapshot[snapshot.length - 1].score;
  const delta = latestScore - firstScore;
  const peakScore = Math.max(...snapshot.map((item) => item.score));

  let tone = "is-steady";
  let label = "안정";
  let copy = `최근 ${snapshot.length}회 반영 점수는 ${latestScore}점이며 큰 변동 없이 유지되고 있습니다.`;

  if (delta >= 6) {
    tone = "is-rise";
    label = "상승";
    copy = `최근 ${snapshot.length}회 반영 점수는 ${firstScore}점에서 ${latestScore}점으로 상승했습니다. 최고 ${peakScore}점까지 올라간 흐름입니다.`;
  } else if (delta <= -6) {
    tone = "is-fall";
    label = "하락";
    copy = `최근 ${snapshot.length}회 반영 점수는 ${firstScore}점에서 ${latestScore}점으로 낮아졌습니다. 최근 상태가 이전보다 안정적으로 유지되고 있습니다.`;
  }

  timelineTrendBadgeEl.innerText = label;
  timelineTrendBadgeEl.classList.add(tone);
  timelineTrendCopyEl.innerText = copy;
  timelineSparklineEl.innerHTML = buildTimelineSparklineMarkup(snapshot);
}

function renderAnalysisHeroSurface() {
  const latestTurn =
    turnHistory.length > 0 ? turnHistory[turnHistory.length - 1] : null;
  const latestIncludedTurn = [...turnHistory]
    .filter((turn) => isScoreIncluded(turn))
    .slice(-1)[0];
  const summaryTurn = latestIncludedTurn || latestTurn;
  const badgeMeta = getTurnBadgeMeta(summaryTurn);

  if (analysisHeroBadgeEl) {
    analysisHeroBadgeEl.innerText = badgeMeta.label;
    analysisHeroBadgeEl.classList.remove(
      "is-idle",
      "is-complete",
      "is-warning",
      "is-excluded",
      "is-cancelled",
    );
    analysisHeroBadgeEl.classList.add(`is-${badgeMeta.tone}`);
  }

  if (!summaryTurn) {
    if (analysisHeroScoreEl) analysisHeroScoreEl.innerText = "-";
    if (analysisHeroTimestampEl)
      analysisHeroTimestampEl.innerText = "아직 분석된 대화가 없습니다.";
    if (analysisHeroRiskEl) analysisHeroRiskEl.innerText = "분석 전";
    if (analysisHeroTrendEl) analysisHeroTrendEl.innerText = "-";
    if (analysisHeroModeEl)
      analysisHeroModeEl.innerText = getLlmModeLabel(llmMode);
    if (analysisHeroSummaryEl) {
      analysisHeroSummaryEl.innerText =
        "대화를 시작하면 최신 세션 상태와 핵심 분석 결과를 이 카드에서 바로 확인할 수 있습니다.";
    }
    return;
  }

  const scoreIncluded = isScoreIncluded(summaryTurn);
  const riskLabel = scoreIncluded
    ? localizeRiskLevel(
        summaryTurn.risk_level || getRiskLevelFromScore(summaryTurn.score ?? 0),
      )
    : "반영 제외";
  const heroScoreText = scoreIncluded ? String(summaryTurn.score ?? 0) : "-";
  const timestampText = `${formatTurnTime(summaryTurn.time)} · ${
    summaryTurn.score_included === false ? "최근 기록" : "최신 반영"
  }`;
  const summaryText = scoreIncluded
    ? `${riskLabel} 상태로 분류되었고, 추세는 ${summaryTurn.trend || "데이터 부족"}입니다. ${normalizeText(summaryTurn.reason || "최신 분석 근거가 아직 없습니다.")}`
    : normalizeText(
        summaryTurn.excluded_reason ||
          summaryTurn.reason ||
          "이번 대화는 통계에서 제외했지만 기록은 세션에 유지했습니다.",
      );

  if (analysisHeroScoreEl) {
    analysisHeroScoreEl.innerText = heroScoreText;
    analysisHeroScoreEl.style.color = scoreIncluded
      ? getRiskColor(summaryTurn.score, true)
      : "#ffd9df";
  }
  if (analysisHeroTimestampEl) {
    analysisHeroTimestampEl.innerText = timestampText;
  }
  if (analysisHeroRiskEl) analysisHeroRiskEl.innerText = riskLabel;
  if (analysisHeroTrendEl)
    analysisHeroTrendEl.innerText = summaryTurn.trend || "데이터 부족";
  if (analysisHeroModeEl) {
    analysisHeroModeEl.innerText = getLlmModeLabel(
      summaryTurn.llm_provider || llmMode,
    );
  }
  if (analysisHeroSummaryEl) {
    analysisHeroSummaryEl.innerText =
      summaryText.length > 160
        ? `${summaryText.slice(0, 160)}...`
        : summaryText;
  }
}

function renderTurnTimelineSurface() {
  if (!turnTimelineListEl) {
    return;
  }

  const finalizedTurns = Array.isArray(turnHistory) ? [...turnHistory] : [];

  if (finalizedTurns.length === 0) {
    turnTimelineListEl.innerHTML = `
      <div class="analysis-timeline-empty">
        아직 누적된 대화가 없습니다. 녹음을 시작하면 턴별 점수 흐름이 여기에 쌓입니다.
      </div>
    `;
    return;
  }

  const turns = finalizedTurns.slice(-8).reverse();
  turnTimelineListEl.innerHTML = turns
    .map((turn, index) => {
      const scoreIncluded = isScoreIncluded(turn);
      const scoreLabel = scoreIncluded ? `${turn.score ?? 0}점` : "반영 제외";
      const riskLabel = localizeRiskLevel(
        turn.risk_level || getRiskLevelFromScore(turn.score ?? 0),
      );
      const badgeMeta = getTurnBadgeMeta(turn);
      const dotColor = getRiskColor(turn.score, scoreIncluded);
      const isSelected = selectedTurnId === turn.turn_id;
      const preview = buildStatusPreview(turn.user_text || "", 54);

      return `
        <button
          class="analysis-timeline-item${isSelected ? " is-selected" : ""}"
          type="button"
          data-turn-id="${escapeHtml(turn.turn_id || "")}"
        >
          <div class="analysis-timeline-head">
            <div class="analysis-timeline-title">
              <span class="analysis-timeline-dot" style="background: ${dotColor}; box-shadow: 0 0 0 6px ${dotColor}1f;"></span>
              <span class="analysis-timeline-label">턴 ${finalizedTurns.length - index}</span>
            </div>
            <span class="analysis-timeline-score">${escapeHtml(scoreLabel)}</span>
          </div>
          <div class="analysis-timeline-meta-row">
            <span>${escapeHtml(formatTurnTime(turn.time))}</span>
            <span>${escapeHtml(badgeMeta.label)} · ${escapeHtml(riskLabel)}</span>
          </div>
          <div class="analysis-timeline-copy">${escapeHtml(preview)}</div>
        </button>
      `;
    })
    .join("");

  turnTimelineListEl
    .querySelectorAll(".analysis-timeline-item[data-turn-id]")
    .forEach((item) => {
      item.addEventListener("click", () => {
        const turnId = item.getAttribute("data-turn-id");
        if (turnId) {
          selectTurnById(turnId);
        }
      });
    });
}

function refreshSessionReportSurface() {
  updateSessionReportButtonState();
  refreshWorkspaceOverviewSurface();
  renderAnalysisHeroSurface();
  renderTimelineTrendSummary();
  renderTurnTimelineSurface();

  if (sessionReportModal && !sessionReportModal.classList.contains("hidden")) {
    renderSessionReportModal();
  }
}

function openSessionReport() {
  if (turnHistory.length === 0) {
    return;
  }

  runWithViewTransition(() => {
    renderSessionReportModal();
    if (!sessionReportModal) {
      return;
    }

    sessionReportModal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  });
}

function closeSessionReport() {
  if (!sessionReportModal) {
    return;
  }

  runWithViewTransition(() => {
    sessionReportModal.classList.add("hidden");
    document.body.style.overflow = "";
  });
}

function buildPrintableSessionReportHtml(report) {
  const featureItems = report.latestFeatureSnapshot
    .map(
      (feature) => `
        <tr>
          <td>${escapeHtml(feature.label)}</td>
          <td>${feature.score}</td>
          <td>${feature.max}</td>
        </tr>
      `,
    )
    .join("");

  const turnItems =
    report.turnsForReport.length > 0
      ? report.turnsForReport
          .map(
            (turn, index) => `
              <tr>
                <td>${report.turnsForReport.length - index}</td>
                <td>${escapeHtml(formatTurnTime(turn.time))}</td>
                <td>${escapeHtml(turn.judgment || "-")}</td>
                <td>${escapeHtml(localizeRiskLevel(turn.risk_level || "-"))}</td>
                <td>${escapeHtml(turn.score_included === false ? "반영 제외" : `${turn.score ?? 0}점`)}</td>
                <td>${escapeHtml(turn.user_text || "")}</td>
              </tr>
            `,
          )
          .join("")
      : `<tr><td colspan="6">저장된 턴 기록이 없습니다.</td></tr>`;

  return `<!doctype html>
  <html lang="ko">
    <head>
      <meta charset="UTF-8" />
      <title>세션 리포트</title>
      <style>
        body { font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif; margin: 32px; color: #172033; }
        h1 { margin: 0 0 8px; font-size: 28px; }
        p { margin: 4px 0; line-height: 1.6; }
        .meta { color: #50627f; margin-bottom: 20px; }
        .section { margin-top: 26px; }
        .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 12px; }
        .card { border: 1px solid #d6deec; border-radius: 14px; padding: 14px; }
        .label { font-size: 12px; color: #63738d; margin-bottom: 8px; }
        .value { font-size: 26px; font-weight: 700; }
        .reason { border: 1px solid #d6deec; border-radius: 14px; padding: 14px; margin-top: 12px; white-space: pre-wrap; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { border: 1px solid #d6deec; padding: 10px; text-align: left; vertical-align: top; }
        th { background: #eff4fb; }
      </style>
    </head>
    <body>
      <h1>인지 위험도 모니터링 세션 리포트</h1>
      <p class="meta">생성 시각: ${escapeHtml(report.generatedAt)} / 세션 ID: ${escapeHtml(report.sessionId)}</p>
      <p>${escapeHtml(reportSubtextEl?.innerText || "")}</p>

      <div class="section">
        <h2>핵심 지표</h2>
        <div class="cards">
          <div class="card"><div class="label">분석 대화 수</div><div class="value">${report.turnCount}</div></div>
          <div class="card"><div class="label">점수 반영 수</div><div class="value">${report.includedCount}</div></div>
          <div class="card"><div class="label">전체 평균</div><div class="value">${report.averageScore.toFixed(1)}</div></div>
          <div class="card"><div class="label">최근 5회 평균</div><div class="value">${report.recentAverage.toFixed(1)}</div></div>
          <div class="card"><div class="label">최신 점수</div><div class="value">${report.latestIncludedTurn ? (report.latestIncludedTurn.score ?? 0) : "-"}</div></div>
          <div class="card"><div class="label">최고 위험 점수</div><div class="value">${report.peakTurn ? (report.peakTurn.score ?? 0) : "-"}</div></div>
        </div>
      </div>

      <div class="section">
        <h2>최신 분석 요약</h2>
        <p>판단: ${escapeHtml(report.latestTurn?.judgment || "-")}</p>
        <p>위험도: ${escapeHtml(report.latestRiskLabel)}</p>
        <p>추세: ${escapeHtml(report.latestTurn?.trend || "-")}</p>
        <p>모드: ${escapeHtml(report.llmModeLabel)}</p>
        <div class="reason">${escapeHtml(report.latestTurn?.reason || "최신 분석 근거가 아직 없습니다.")}</div>
      </div>

      <div class="section">
        <h2>언어 특징 요약</h2>
        <table>
          <thead><tr><th>특징</th><th>점수</th><th>기준 최대치</th></tr></thead>
          <tbody>${featureItems}</tbody>
        </table>
        <p style="margin-top: 12px;">기억 회상 테스트: ${escapeHtml(report.recallSummary)}</p>
      </div>

      <div class="section">
        <h2>턴별 분석 기록</h2>
        <table>
          <thead>
            <tr><th>턴</th><th>시각</th><th>판단</th><th>위험도</th><th>점수</th><th>사용자 발화</th></tr>
          </thead>
          <tbody>${turnItems}</tbody>
        </table>
      </div>
    </body>
  </html>`;
}

function printSessionReport() {
  const report = lastRenderedSessionReport || renderSessionReportModal();
  const popup = window.open("", "_blank", "width=1080,height=860");

  if (!popup) {
    alert("출력 창을 열지 못했습니다. 팝업 차단 설정을 확인해 주세요.");
    return;
  }

  popup.document.open();
  popup.document.write(buildPrintableSessionReportHtml(report));
  popup.document.close();
  popup.focus();
  window.setTimeout(() => {
    popup.print();
  }, 250);
}

function triggerAnalysisCompletionAnimation(data) {
  if (!analysisSummaryToast) {
    return;
  }

  const scoreIncluded = isScoreIncluded(data);
  const score = scoreIncluded ? Number(data.score ?? 0) : 0;
  const riskLabel = scoreIncluded
    ? localizeRiskLevel(data.risk_level || "정상")
    : "반영 제외";
  const trendLabel = scoreIncluded ? data.trend || "데이터 부족" : "반영 제외";
  const reasonText = scoreIncluded
    ? normalizeText(data.reason || "최신 분석 결과를 반영했습니다.")
    : normalizeText(
        data.excluded_reason ||
          data.reason ||
          "이번 분석은 점수 통계에서 제외되었지만 기록으로는 남겨두었습니다.",
      );
  const shortReason =
    reasonText.length > 100 ? `${reasonText.slice(0, 100)}...` : reasonText;

  if (summaryToastBadgeEl) {
    summaryToastBadgeEl.innerText = scoreIncluded ? "최신 분석" : "점수 미반영";
  }
  if (summaryToastTitleEl) {
    summaryToastTitleEl.innerText = scoreIncluded
      ? `${data.judgment || "분석 완료"} · ${riskLabel}`
      : "분석 기록은 저장했고 점수 통계에서는 제외했습니다.";
  }
  if (summaryToastRiskEl) {
    summaryToastRiskEl.innerText = riskLabel;
  }
  if (summaryToastTrendEl) {
    summaryToastTrendEl.innerText = trendLabel;
  }
  if (summaryToastReasonEl) {
    summaryToastReasonEl.innerText = shortReason;
  }

  if (analysisSummaryToastTimer) {
    window.clearTimeout(analysisSummaryToastTimer);
    analysisSummaryToastTimer = null;
  }

  analysisSummaryToast.classList.remove("hidden", "is-visible");
  void analysisSummaryToast.offsetWidth;
  analysisSummaryToast.classList.add("is-visible");

  if (summaryToastScoreEl && scoreIncluded) {
    animateNumber(summaryToastScoreEl, 0, score, 750, false);
  } else if (summaryToastScoreEl) {
    summaryToastScoreEl.innerText = "-";
  }

  analysisSummaryToastTimer = window.setTimeout(() => {
    analysisSummaryToast.classList.add("hidden");
    analysisSummaryToast.classList.remove("is-visible");
  }, 3900);
}

function hideAnalysisCompletionAnimation() {
  if (!analysisSummaryToast) {
    return;
  }

  if (analysisSummaryToastTimer) {
    window.clearTimeout(analysisSummaryToastTimer);
    analysisSummaryToastTimer = null;
  }

  analysisSummaryToast.classList.add("hidden");
  analysisSummaryToast.classList.remove("is-visible");
}
function createClientTurnId() {
  return `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function upsertPendingTurn(pendingTurn) {
  const normalizedTurn = {
    ...pendingTurn,
    is_pending: true,
    pending_status: pendingTurn?.pending_status || "queued",
    pending_error_message: pendingTurn?.pending_error_message || "",
    created_at: Number(pendingTurn?.created_at || Date.now()),
  };
  const existingIndex = pendingTurns.findIndex(
    (turn) => turn.client_turn_id === normalizedTurn.client_turn_id,
  );

  if (existingIndex >= 0) {
    pendingTurns[existingIndex] = {
      ...pendingTurns[existingIndex],
      ...normalizedTurn,
    };
    return pendingTurns[existingIndex];
  }

  pendingTurns.push(normalizedTurn);
  pendingTurns.sort(
    (left, right) =>
      Number(left.created_at || 0) - Number(right.created_at || 0),
  );
  return normalizedTurn;
}

function updatePendingTurn(clientTurnId, updates = {}) {
  const existingTurn = pendingTurns.find(
    (turn) => turn.client_turn_id === clientTurnId,
  );
  if (!existingTurn) {
    return null;
  }

  Object.assign(existingTurn, updates, { is_pending: true });
  return existingTurn;
}

function removePendingTurn(clientTurnId) {
  pendingTurns = pendingTurns.filter(
    (turn) => turn.client_turn_id !== clientTurnId,
  );
}

function getPendingTurnBadge(turn) {
  if (turn?.pending_status === "cancelled") {
    return "분석 취소";
  }

  if (turn?.pending_status === "failed") {
    return "분석 실패";
  }

  if (turn?.pending_status === "analyzing") {
    return "분석 중";
  }

  return "분석 대기";
}

function getMergedTurnHistory() {
  const finalizedTurns = Array.isArray(turnHistory) ? [...turnHistory] : [];
  const queuedTurns = pendingTurns.map((turn) => ({
    ...turn,
    turn_id: turn.client_turn_id,
  }));
  return [...finalizedTurns, ...queuedTurns];
}

function renderConversationHistory(options = {}) {
  renderTurnHistory(getMergedTurnHistory(), options);
}

function waitForAnswerIdle() {
  if (!isAnswerPending) {
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const timerId = window.setInterval(() => {
      if (!isAnswerPending) {
        window.clearInterval(timerId);
        resolve();
      }
    }, 120);
  });
}

function syncBackgroundAnalysisState() {
  const shouldShowLoading = Boolean(
    isAnalysisWorkerRunning || analysisTaskQueue.length > 0,
  );
  setAnalysisThinking(shouldShowLoading);
  setAnalysisLoadingState(shouldShowLoading);
}

function enqueueAnalysisTask(task) {
  analysisTaskQueue.push(task);
  syncBackgroundAnalysisState();
  void processAnalysisQueue();
}

async function processAnalysisQueue() {
  if (isAnalysisWorkerRunning) {
    return;
  }

  const workerGeneration = normalizeAnalysisGeneration(analysisGeneration);
  isAnalysisWorkerRunning = true;
  syncBackgroundAnalysisState();

  try {
    while (
      analysisTaskQueue.length > 0 &&
      workerGeneration === normalizeAnalysisGeneration(analysisGeneration)
    ) {
      await waitForAnswerIdle();
      if (
        workerGeneration !== normalizeAnalysisGeneration(analysisGeneration)
      ) {
        break;
      }
      const task = analysisTaskQueue.shift();
      if (!task) {
        continue;
      }

      await runAnalysisTask(task);
    }
  } finally {
    if (workerGeneration === normalizeAnalysisGeneration(analysisGeneration)) {
      isAnalysisWorkerRunning = false;
    }
    syncBackgroundAnalysisState();
  }
}

async function runAnalysisTask(task) {
  if (
    normalizeAnalysisGeneration(task.analysisGeneration) !==
    normalizeAnalysisGeneration(analysisGeneration)
  ) {
    return;
  }

  const roleResults = {};
  const totalRoles = analysisRoleOrder.length;

  updatePendingTurn(task.clientTurnId, {
    pending_status: "analyzing",
    pending_error_message: "",
  });
  renderConversationHistory({ preserveAnalysisCard: true });
  setAnalysisThinking(true);
  setAnalysisLoadingState(true);

  try {
    setProcessState(
      "analysis",
      `"${task.questionPreview}"에 대한 점수 분석을 순서대로 진행하고 있습니다.`,
    );
    resetRoleAnalysisTracker();
    setSystemState("위험도 분석 진행 중");
    setThinkingMessage(
      "답변은 먼저 표시했고, 역할별 점수 분석만 백그라운드에서 이어서 처리하고 있습니다.",
    );

    for (let index = 0; index < totalRoles; index += 1) {
      await waitForAnswerIdle();
      if (
        normalizeAnalysisGeneration(task.analysisGeneration) !==
        normalizeAnalysisGeneration(analysisGeneration)
      ) {
        return;
      }

      const role = analysisRoleOrder[index];
      const roleLabel = analysisRoleLabels[role] || "세부 분석";

      setProcessState(
        "analysis",
        `${roleLabel} 점수를 계산하고 있습니다. (${index + 1}/${totalRoles})`,
      );
      setSystemState(`${roleLabel} 분석 중`);
      setThinkingMessage(
        `${roleLabel}에 대한 점수와 근거를 정리하고 있습니다.`,
      );
      updateRoleAnalysisTracker(roleResults, role, index, totalRoles);

      const roleData = await requestRoleAnalysis(
        task.recognizedText,
        role,
        task.llmProvider,
        task.analysisGeneration,
      );

      syncSessionMetadata(roleData);

      if (isStaleGeneration(task.analysisGeneration, roleData)) {
        return;
      }

      if (roleData?.error) {
        throw new Error(roleData.error);
      }

      roleResults[role] = {
        score: Number(roleData?.score ?? 0),
        reason: normalizeText(roleData?.reason || ""),
      };

      applyProgressiveAnalysisPreview(roleResults, role, index + 1, totalRoles);
      applyProgressiveSummaryPreview(roleResults);
    }

    await waitForAnswerIdle();
    if (
      normalizeAnalysisGeneration(task.analysisGeneration) !==
      normalizeAnalysisGeneration(analysisGeneration)
    ) {
      return;
    }

    setProcessState(
      "analysis",
      "세부 역할 점수를 모두 계산했고, 최종 점수와 추세를 반영하고 있습니다.",
    );
    setSystemState("최종 결과 반영 중");
    setThinkingMessage(
      "역할별 점수를 합산해 최종 판단과 누적 통계를 반영하고 있습니다.",
    );

    const data = await requestFinalizeAnalysis(
      task.recognizedText,
      task.answerText,
      roleResults,
      task.llmProvider,
      task.analysisGeneration,
    );

    syncSessionMetadata(data);

    if (isStaleGeneration(task.analysisGeneration, data)) {
      return;
    }

    if (data?.error) {
      throw new Error(data.error);
    }

    applyAnalysisResult(data, { finalizedClientTurnId: task.clientTurnId });
    setProcessState(
      "render",
      "답변, 분석 카드, 추세 차트, 턴 기록까지 모두 최신 결과로 갱신했습니다.",
    );
    setSystemState("분석 완료");
    setThinkingMessage("가장 최근 대화 기준의 분석 결과를 확인하고 있습니다.");
  } catch (error) {
    console.error(error);
    updatePendingTurn(task.clientTurnId, {
      pending_status: "failed",
      pending_error_message:
        error instanceof Error ? error.message : String(error || ""),
    });
    renderConversationHistory({ preserveAnalysisCard: true });
    appendChatMessage(
      "system",
      "점수 분석 중 오류가 발생했습니다. 답변은 유지하고 다음 질문은 계속 진행할 수 있습니다.",
    );
    setSystemState("분석 일부 실패");
    setProcessError(
      `"${task.questionPreview}" 대화의 점수 분석을 끝까지 반영하지 못했습니다.`,
    );
  } finally {
    syncBackgroundAnalysisState();
  }
}

function setRecordButtonBusyState(isBusy, label = "답변 생성 중...") {
  isAnswerPending = Boolean(isBusy);
  recordButtonBusyLabel = isAnswerPending
    ? String(label || "답변 생성 중...")
    : "";
  updateRecordToggleButton();
}

function updateRecordToggleButton() {
  if (!startButton) {
    return;
  }

  const isCurrentlyRecording = Boolean(
    mediaRecorder && mediaRecorder.state !== "inactive",
  );

  startButton.classList.remove(
    "primary-btn",
    "secondary-btn",
    "danger-btn",
    "is-recording",
    "is-processing",
  );
  startButton.dataset.recordState = "idle";

  if (isAnswerPending) {
    startButton.innerText = recordButtonBusyLabel || "답변 생성 중...";
    startButton.disabled = true;
    startButton.classList.add("secondary-btn", "is-processing");
    startButton.dataset.recordState = "processing";
    return;
  }

  if (isCurrentlyRecording) {
    startButton.innerText = "녹음 중지";
    startButton.disabled = false;
    startButton.classList.add("danger-btn", "is-recording");
    startButton.dataset.recordState = "recording";
    return;
  }

  startButton.innerText = "녹음 시작";
  startButton.disabled = false;
  startButton.classList.add("primary-btn");
  startButton.dataset.recordState = "idle";
}

function toggleRecording() {
  if (isAnswerPending) {
    return;
  }

  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    stopRecording();
    return;
  }

  startRecording();
}
function normalizeLlmMode(mode) {
  return mode === "api" ? "api" : "local";
}

function normalizeAnalysisGeneration(value) {
  const parsed = Number(value ?? 0);

  if (!Number.isFinite(parsed) || parsed < 0) {
    return 0;
  }

  return Math.floor(parsed);
}

function setAnalysisGeneration(nextGeneration) {
  analysisGeneration = normalizeAnalysisGeneration(nextGeneration);
  localStorage.setItem("analysis_generation", String(analysisGeneration));
  return analysisGeneration;
}

function syncSessionMetadata(data) {
  if (data?.session_id) {
    sessionId = data.session_id;
    localStorage.setItem("session_id", sessionId);
  }

  if (Object.prototype.hasOwnProperty.call(data || {}, "analysis_generation")) {
    setAnalysisGeneration(data.analysis_generation);
  }
}

function isStaleGeneration(expectedGeneration, data) {
  if (data?.stale) {
    return true;
  }

  const normalizedExpected = normalizeAnalysisGeneration(expectedGeneration);
  const responseGeneration = normalizeAnalysisGeneration(
    data?.analysis_generation ?? normalizedExpected,
  );

  return (
    normalizedExpected !== normalizeAnalysisGeneration(analysisGeneration) ||
    responseGeneration !== normalizeAnalysisGeneration(analysisGeneration)
  );
}

function getLlmProviderMeta(mode = llmMode) {
  if (!llmProviderStatus) {
    return null;
  }

  return llmProviderStatus[normalizeLlmMode(mode)] || null;
}

function getLlmModeLabel(mode = llmMode) {
  return normalizeLlmMode(mode) === "api" ? "외부 API" : "로컬 모델";
}

function localizeRiskLevel(value) {
  const normalized = normalizeText(value);
  const key = normalized.toLowerCase();

  if (!normalized) {
    return "분석 전";
  }

  if (key === "normal" || normalized === "정상") {
    return "정상";
  }
  if (key === "low risk" || normalized === "낮은 위험") {
    return "낮은 위험";
  }
  if (key === "moderate risk" || normalized === "주의") {
    return "주의";
  }
  if (key === "high risk" || normalized === "위험") {
    return "위험";
  }
  if (
    key === "very high risk" ||
    key === "critical risk" ||
    normalized === "매우 위험"
  ) {
    return "매우 위험";
  }

  return normalized;
}

function isLlmModeAvailable(mode = llmMode) {
  const meta = getLlmProviderMeta(mode);
  if (!meta) {
    return normalizeLlmMode(mode) === "local";
  }

  return meta.ready !== false;
}

function renderLlmModeState() {
  const normalizedMode = normalizeLlmMode(llmMode);
  const localReady = isLlmModeAvailable("local");
  const apiReady = isLlmModeAvailable("api");

  if (llmModeLocalButton) {
    llmModeLocalButton.classList.toggle(
      "is-active",
      normalizedMode === "local",
    );
    llmModeLocalButton.disabled = !localReady;
  }

  if (llmModeApiButton) {
    llmModeApiButton.classList.toggle("is-active", normalizedMode === "api");
    llmModeApiButton.disabled = !apiReady;
  }

  if (llmModeStatusEl) {
    llmModeStatusEl.innerText =
      normalizedMode === "api" ? "외부 API 모드" : "로컬 모드";
  }

  if (llmModeHintEl) {
    if (normalizedMode === "api") {
      llmModeHintEl.innerText = apiReady
        ? "외부 API를 통해 답변과 언어 특징 분석을 수행합니다."
        : "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 입력해 주세요.";
    } else {
      llmModeHintEl.innerText = localReady
        ? "현재 컴퓨터에 있는 로컬 모델로 답변과 분석을 수행합니다."
        : "로컬 모델 파일을 찾지 못했습니다. MODEL_PATH 설정을 확인해 주세요.";
    }
  }
}

async function loadLlmProviderStatus() {
  try {
    const response = await fetch("/health");
    const data = await response.json();
    llmProviderStatus = data.llm_provider || null;
  } catch (error) {
    console.error("LLM provider status load failed:", error);
    llmProviderStatus = null;
  }

  const preferredMode = normalizeLlmMode(llmMode);
  if (!isLlmModeAvailable(preferredMode)) {
    llmMode = isLlmModeAvailable("local") ? "local" : "api";
  }

  localStorage.setItem("llm_mode", llmMode);
  renderLlmModeState();
}

function setLlmMode(mode, options = {}) {
  const normalizedMode = normalizeLlmMode(mode);
  const silent = Boolean(options.silent);

  if (!isLlmModeAvailable(normalizedMode)) {
    renderLlmModeState();
    if (!silent) {
      alert(
        normalizedMode === "api"
          ? "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 설정해 주세요."
          : "로컬 모델 파일을 찾지 못했습니다. MODEL_PATH 설정을 확인해 주세요.",
      );
    }
    return false;
  }

  llmMode = normalizedMode;
  localStorage.setItem("llm_mode", llmMode);
  renderLlmModeState();

  if (!silent) {
    setSystemState(llmMode === "api" ? "외부 API 모드 선택" : "로컬 모드 선택");
  }

  refreshSessionReportSurface();

  return true;
}

function renderChatEmptyState(options = {}) {
  if (!chatWindow) {
    return;
  }

  const resetSummary = options.resetSummary || null;
  chatWindow.innerHTML = "";

  const emptyState = document.createElement("div");
  emptyState.className = "chat-empty-state";
  emptyState.id = "chatEmptyState";
  if (resetSummary?.wasCancelled) {
    emptyState.classList.add("is-reset-notice");
    emptyState.innerHTML = `
          <div class="chat-empty-kicker is-cancelled">분석 취소됨</div>
          <h4>기록을 초기화했고 진행 중 분석도 취소했습니다.</h4>
          <p>${resetSummary.primaryText}</p>
          <p>이제 새 녹음을 시작하면 완전히 초기 상태에서 다시 답변과 분석을 진행합니다.</p>
      `;
  } else {
    emptyState.innerHTML = `
          <div class="chat-empty-kicker">분석 준비 완료</div>
          <h4>아직 대화 기록이 없습니다.</h4>
          <p>녹음을 시작하면 답변과 위험도 분석이 이곳에 차례대로 표시됩니다.</p>
          <p>대화가 쌓이면 메시지를 클릭해 해당 시점의 분석 결과를 다시 볼 수 있습니다.</p>
      `;
  }

  chatWindow.appendChild(emptyState);
  refreshSessionReportSurface();
}

function clearChatEmptyState() {
  const emptyState = document.getElementById("chatEmptyState");
  if (emptyState) {
    emptyState.remove();
  }
}

function setThinkingMessage(text) {
  if (aiThinking) {
    aiThinking.innerText = text;
  }
  refreshWorkspaceOverviewSurface();
}

function buildStatusPreview(text, maxLength = 34) {
  const normalized = normalizeText(text);
  if (!normalized) {
    return "인식된 문장";
  }

  if (normalized.length <= maxLength) {
    return normalized;
  }

  return `${normalized.slice(0, maxLength).trim()}...`;
}

function startStatusNarration(sequence = []) {
  const timeoutIds = [];
  let isActive = true;

  sequence.forEach((item) => {
    const delay = Number(item?.delay ?? 0);
    const timeoutId = window.setTimeout(
      () => {
        if (!isActive) {
          return;
        }

        if (item?.step) {
          setProcessState(item.step, item.detail || "");
        } else if (item?.detail && processDetailEl) {
          processDetailEl.innerText = item.detail;
        }

        if (typeof item?.system === "string") {
          setSystemState(item.system);
        }

        if (typeof item?.thinking === "string") {
          setThinkingMessage(item.thinking);
        }
      },
      Math.max(0, delay),
    );

    timeoutIds.push(timeoutId);
  });

  return () => {
    isActive = false;
    timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
  };
}

function setProcessState(step, detail = "") {
  const activeIndex = processStepOrder.indexOf(step);

  processSteps.forEach((element) => {
    const currentStep = element.dataset.step;
    const currentIndex = processStepOrder.indexOf(currentStep);

    element.classList.remove("is-active", "is-complete", "is-error");

    if (activeIndex === -1) {
      return;
    }

    if (currentIndex < activeIndex) {
      element.classList.add("is-complete");
    } else if (currentIndex === activeIndex) {
      element.classList.add("is-active");
    }
  });

  if (processDetailEl) {
    processDetailEl.innerText = detail || "처리 중입니다.";
  }

  setMobileProcessBadge(processStepLabels[step] || "처리 중", "active");
}

function setProcessError(detail) {
  processSteps.forEach((element) => {
    element.classList.remove("is-active");
  });

  const active = processSteps.find(
    (element) => element.classList.contains("is-complete") === false,
  );
  if (active) {
    active.classList.add("is-error");
  }

  if (processDetailEl) {
    processDetailEl.innerText = detail || "오류가 발생했습니다.";
  }

  setMobileProcessBadge("오류", "error");
}

function resetProcessState(detail = "대기 중입니다.") {
  processSteps.forEach((element) => {
    element.classList.remove("is-active", "is-complete", "is-error");
  });

  resetRoleAnalysisTracker();

  if (processDetailEl) {
    processDetailEl.innerText = detail;
  }

  setMobileProcessBadge("대기", "idle");
}

function stopRecording() {
  if (!mediaRecorder) {
    return;
  }

  if (mediaRecorder.state === "inactive") {
    return;
  }

  mediaRecorder.stop();
  setRecordingState(false);
  cleanupRecordingStream();
  stopVoiceAmbient();
  updateRecordToggleButton();
}

async function resetHistory() {
  try {
    if (isMobileTabViewport()) {
      setActiveMobileTab("chat", { persist: true, scrollToTop: false });
    }
    closeSessionReport();
    hideAnalysisCompletionAnimation();
    clearScoreCascadeTimers();
    const resetSummary = buildResetCancellationSummary();
    lastResetSummary = resetSummary;

    if (resetSummary.wasCancelled) {
      markPendingAnalysisAsCancelled(resetSummary);
      await new Promise((resolve) => window.setTimeout(resolve, 220));
    }

    const url = sessionId
      ? `/reset-history?session_id=${encodeURIComponent(sessionId)}`
      : "/reset-history";

    const response = await fetch(url, {
      method: "POST",
    });

    const data = await response.json();

    syncSessionMetadata(data);

    scoreHistory = [];
    turnHistory = [];
    pendingTurns = [];
    analysisTaskQueue = [];
    isAnalysisWorkerRunning = false;
    selectedTurnId = null;
    lastMetricSnapshot = {
      averageScore: 0,
      recentAverageScore: 0,
      latestScore: 0,
      gaugeScore: 0,
      analysisScore: 0,
      confidence: 0,
    };
    [
      sidebarMetricsDisclosureEl,
      historyDisclosureEl,
      analysisDetailDisclosureEl,
      recallDisclosureEl,
    ].forEach((detailsEl) => {
      if (!detailsEl) {
        return;
      }

      delete detailsEl.dataset.userToggled;
      setDisclosureOpenState(detailsEl, false);
    });
    setRecordButtonBusyState(false);
    syncBackgroundAnalysisState();
    if (chatWindow) {
      chatWindow.innerHTML = "";
    }

    renderChatEmptyState({ resetSummary: lastResetSummary });
    applyResetSummaryState(lastResetSummary);
    updateFeatureBreakdown({});
    updateRecallCard(data.recall || {});
    updateConfidence({}, 0, false);
    renderAll(data);

    applyResetSummaryState(lastResetSummary);
    setSystemState(
      resetSummary.wasCancelled
        ? "기록 초기화 및 분석 취소 완료"
        : "기록 초기화 완료",
    );
    setThinkingMessage(
      resetSummary.wasCancelled
        ? "이전 요청은 모두 반영하지 않도록 정리했고, 새 대화부터 다시 분석합니다."
        : "기록을 비웠습니다. 새 녹음을 시작하면 다시 분석을 진행합니다.",
    );
  } catch (error) {
    console.error(error);
    alert("기록 초기화 중 오류가 발생했습니다.");
  }
}

function cleanupRecordingStream() {
  if (!recordingStream) {
    return;
  }

  recordingStream.getTracks().forEach((track) => track.stop());
  recordingStream = null;
}

function stopVoiceAmbient(resetLevel = true) {
  if (voiceMeterFrame) {
    cancelAnimationFrame(voiceMeterFrame);
    voiceMeterFrame = null;
  }

  if (microphoneSource) {
    microphoneSource.disconnect();
    microphoneSource = null;
  }

  analyserNode = null;

  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }

  if (resetLevel) {
    setVoiceLevel(0.06);
  }
}

async function startVoiceAmbient(stream) {
  stopVoiceAmbient(false);
  setVoiceLevel(0.14);

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    return;
  }

  audioContext = new AudioContextClass();

  if (audioContext.state === "suspended") {
    await audioContext.resume();
  }

  analyserNode = audioContext.createAnalyser();
  analyserNode.fftSize = 256;
  analyserNode.smoothingTimeConstant = 0.84;

  microphoneSource = audioContext.createMediaStreamSource(stream);
  microphoneSource.connect(analyserNode);

  const timeDomainData = new Uint8Array(analyserNode.frequencyBinCount);

  const tick = () => {
    if (!analyserNode) {
      return;
    }

    analyserNode.getByteTimeDomainData(timeDomainData);

    let sum = 0;
    for (let index = 0; index < timeDomainData.length; index += 1) {
      const centered = (timeDomainData[index] - 128) / 128;
      sum += centered * centered;
    }

    const rms = Math.sqrt(sum / timeDomainData.length);
    const nextLevel = Math.min(1, 0.08 + rms * 6.2);
    setVoiceLevel(nextLevel);
    voiceMeterFrame = requestAnimationFrame(tick);
  };

  tick();
}

function setRecordingState(isRecording) {
  document.body.classList.toggle("is-recording", isRecording);
  if (chatContainer) {
    chatContainer.classList.toggle("is-recording", isRecording);
  }

  if (isRecording) {
    if (recordingIndicator) recordingIndicator.classList.remove("hidden");
    setSystemState("녹음 중");
  } else {
    if (recordingIndicator) recordingIndicator.classList.add("hidden");
  }

  updateRecordToggleButton();
}

function setAnalysisThinking(isThinking) {
  if (!aiThinking) {
    return;
  }

  if (isThinking) {
    aiThinking.classList.remove("hidden");
  } else {
    aiThinking.classList.add("hidden");
  }
}

function setSystemState(text) {
  if (systemStateText) {
    systemStateText.innerText = text;
  }
  refreshWorkspaceOverviewSurface();
}

function getAnalysisCards() {
  return Array.from(document.querySelectorAll(".analysis-card"));
}

function setSkeletonLoading(isLoading) {
  getAnalysisCards().forEach((card) => {
    card.classList.toggle("is-loading", isLoading);
  });
}

function appendChatMessage(type, text, options = {}) {
  if (!chatWindow) {
    return null;
  }

  clearChatEmptyState();

  const message = document.createElement("div");
  message.classList.add("message", "message-enter");

  if (type === "user") {
    message.classList.add("user-message");
  } else {
    message.classList.add("system-message");
  }

  const content = document.createElement("div");
  content.className = "message-content";
  content.innerText = text;
  message.appendChild(content);

  if (options.badge) {
    const meta = document.createElement("div");
    meta.className = "message-meta";

    const badge = document.createElement("span");
    badge.className = "message-badge";
    if (options.badge === "분석 취소") {
      badge.classList.add("is-cancelled");
    } else if (options.badge === "분석 실패") {
      badge.classList.add("is-failed");
    }
    badge.innerText = options.badge;

    meta.appendChild(badge);
    message.appendChild(meta);
  }

  if (options.turnId) {
    message.dataset.turnId = options.turnId;
    message.classList.add("history-message");
    message.addEventListener("click", () => {
      selectTurnById(options.turnId);
    });
  }

  chatWindow.appendChild(message);
  scrollChatToBottom();
  return message;
}

function appendLoadingMessage(text = "답변 생성 중...") {
  if (!chatWindow) {
    return;
  }

  clearChatEmptyState();
  removeLoadingMessage();

  const message = document.createElement("div");
  message.classList.add("message", "system-message", "message-enter");
  message.id = "loadingMessage";
  message.innerText = text;
  chatWindow.appendChild(message);
  scrollChatToBottom();
}

function removeLoadingMessage() {
  const loading = document.getElementById("loadingMessage");
  if (loading) {
    loading.remove();
  }
}

function scrollChatToBottom() {
  if (!chatWindow) {
    return;
  }
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

async function loadScoreHistory() {
  try {
    const url = sessionId
      ? `/score-history?session_id=${encodeURIComponent(sessionId)}`
      : "/score-history";

    const response = await fetch(url);
    const data = await response.json();

    syncSessionMetadata(data);

    pendingTurns = [];
    analysisTaskQueue = [];
    isAnalysisWorkerRunning = false;
    setRecordButtonBusyState(false);
    scoreHistory = Array.isArray(data.score_history) ? data.score_history : [];
    turnHistory = Array.isArray(data.turn_history) ? data.turn_history : [];
    updateRecallCard(data.recall || {});
    renderAll(data);
    updateConfidence({}, 0, false);
    syncBackgroundAnalysisState();

    if (turnHistory.length > 0) {
      renderConversationHistory({ preferLatestTurn: true });
    } else {
      renderChatEmptyState();
      resetAnalysisCard();
    }
  } catch (error) {
    console.error("점수 기록 로딩 실패:", error);
    renderAll({
      average_score: 0,
      recent_average_score: 0,
      risk_level: "정상",
      trend: "데이터 부족",
      score_history: [],
    });
    renderChatEmptyState();
    resetAnalysisCard();
  }
}

function isScoreIncluded(data) {
  return data?.score_included !== false;
}

function setAnalysisStateBadge(label, tone = "idle", hintText = "") {
  if (analysisStateBadgeEl) {
    analysisStateBadgeEl.innerText = label;
    analysisStateBadgeEl.classList.remove(
      "is-idle",
      "is-complete",
      "is-warning",
      "is-excluded",
      "is-cancelled",
    );
    analysisStateBadgeEl.classList.add(`is-${tone}`);
  }

  if (analysisEmptyHintEl) {
    analysisEmptyHintEl.innerText = hintText;
    analysisEmptyHintEl.classList.toggle("is-hidden", !hintText);
  }
}

function setAnalysisScoreDisplay(score, scoreIncluded = true) {
  if (!analysisScoreEl) {
    return;
  }

  analysisScoreEl.innerText = scoreIncluded ? String(score ?? 0) : "-";
}

function getRiskLevelFromScore(score) {
  const numericScore = Number(score ?? 0);

  if (numericScore < 20) return "정상";
  if (numericScore < 40) return "낮은 위험";
  if (numericScore < 60) return "주의";
  if (numericScore < 80) return "위험";
  return "매우 위험";
}

function buildProgressiveAnalysisPreview(roleResults) {
  const featureScores = {
    repetition: Number(roleResults.repetition?.score ?? 0),
    memory: Number(roleResults.memory?.score ?? 0),
    time_confusion: Number(roleResults.time_confusion?.score ?? 0),
    incoherence: Number(roleResults.incoherence?.score ?? 0),
  };
  const score =
    featureScores.repetition +
    featureScores.memory +
    featureScores.time_confusion +
    featureScores.incoherence;
  const reason = analysisRoleOrder
    .map((role) => normalizeText(roleResults[role]?.reason || ""))
    .filter(Boolean)
    .join(" ");

  return {
    score,
    featureScores,
    reason: reason || "역할별 분석 결과를 순차적으로 수집하고 있습니다.",
  };
}

function applyProgressiveAnalysisPreview(
  roleResults,
  currentRole,
  completedCount,
  totalCount,
) {
  const preview = buildProgressiveAnalysisPreview(roleResults);
  const roleLabel = analysisRoleLabels[currentRole] || "세부 분석";

  if (analysisJudgmentEl) analysisJudgmentEl.innerText = "분석 중";
  if (analysisRiskLevelEl)
    analysisRiskLevelEl.innerText = getRiskLevelFromScore(preview.score);
  if (analysisTrendEl) analysisTrendEl.innerText = "진행 중";
  if (analysisReasonEl) {
    analysisReasonEl.innerText = preview.reason;
  }

  setAnalysisScoreDisplay(preview.score, true);
  updateFeatureBreakdown(preview.featureScores);
  updateConfidence(preview.featureScores, preview.score, true);
  updateRoleAnalysisTracker(
    roleResults,
    currentRole,
    completedCount,
    totalCount,
    {
      animateRole: currentRole,
    },
  );
  setAnalysisStateBadge(
    `${roleLabel} 반영`,
    "complete",
    `${roleLabel} 점수를 반영했습니다. ${completedCount}/${totalCount} 단계 분석이 완료되었습니다.`,
  );
}

function applyProgressiveSummaryPreview(roleResults) {
  const preview = buildProgressiveAnalysisPreview(roleResults);

  if (latestScoreEl) {
    latestScoreEl.innerText = String(Math.round(preview.score));
  }

  if (gaugeScoreEl) {
    gaugeScoreEl.innerText = String(Math.round(preview.score));
  }

  updateGaugeChart(preview.score);
  pulseElement(latestScoreCardEl, "is-spotlight", 760);
  pulseElement(gaugeCardEl, "is-spotlight", 760);
}

function updateAnalysisCard(data) {
  const scoreIncluded = isScoreIncluded(data);
  const riskLabel = scoreIncluded
    ? localizeRiskLevel(data.risk_level || "정상")
    : "반영 제외";
  const trendLabel = scoreIncluded ? data.trend || "데이터 부족" : "반영 제외";
  const reasonText = scoreIncluded
    ? data.reason || "분석 근거가 없습니다."
    : data.excluded_reason ||
      data.reason ||
      "이번 분석은 점수 통계에서 제외되었습니다.";
  const badgeLabel = !scoreIncluded
    ? "점수 미반영"
    : data.judgment === "의심"
      ? "주의 관찰"
      : "분석 완료";
  const badgeTone = !scoreIncluded
    ? "excluded"
    : data.judgment === "의심"
      ? "warning"
      : "complete";
  const hintText = !scoreIncluded
    ? data.excluded_reason || "이번 분석은 평균과 추세 계산에서 제외되었습니다."
    : "채팅 기록을 클릭하면 해당 시점의 분석 결과를 다시 볼 수 있습니다.";

  if (analysisJudgmentEl)
    analysisJudgmentEl.innerText = data.judgment || "없음";
  if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = riskLabel;
  if (analysisTrendEl) analysisTrendEl.innerText = trendLabel;
  if (analysisReasonEl) analysisReasonEl.innerText = reasonText;
  setAnalysisStateBadge(badgeLabel, badgeTone, hintText);
}

function resetAnalysisCard() {
  if (analysisJudgmentEl) analysisJudgmentEl.innerText = "대기";
  if (analysisScoreEl) analysisScoreEl.innerText = "-";
  if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = "분석 전";
  if (analysisTrendEl) analysisTrendEl.innerText = "-";
  if (analysisReasonEl)
    analysisReasonEl.innerText =
      "아직 분석 결과가 없습니다. 녹음을 시작하면 판단, 점수, 근거가 이곳에 표시됩니다.";
  if (confidenceScoreEl) confidenceScoreEl.innerText = "-";
  setAnalysisStateBadge(
    "대기",
    "idle",
    "아직 분석 전입니다. 대화를 시작하면 판단, 점수, 근거가 차례대로 표시됩니다.",
  );
  resetRoleAnalysisTracker();
}

function buildResetCancellationSummary() {
  const pendingAnalysisCount = pendingTurns.filter((turn) =>
    ["queued", "analyzing"].includes(turn?.pending_status || "queued"),
  ).length;
  const pendingAnswerCount = isAnswerPending ? 1 : 0;
  const totalCancelled = pendingAnalysisCount + pendingAnswerCount;

  if (totalCancelled <= 0) {
    return {
      wasCancelled: false,
      totalCancelled: 0,
      primaryText: "기존 기록을 지우고 빈 상태로 초기화했습니다.",
    };
  }

  const parts = [];
  if (pendingAnalysisCount > 0) {
    parts.push(`점수 분석 ${pendingAnalysisCount}건`);
  }
  if (pendingAnswerCount > 0) {
    parts.push(`답변 생성 ${pendingAnswerCount}건`);
  }

  return {
    wasCancelled: true,
    totalCancelled,
    pendingAnalysisCount,
    pendingAnswerCount,
    primaryText: `진행 중이던 ${parts.join("과 ")}을 반영하지 않고 취소 처리했습니다.`,
  };
}

function markPendingAnalysisAsCancelled(summary) {
  if (!summary?.wasCancelled) {
    return;
  }

  pendingTurns = pendingTurns.map((turn) => ({
    ...turn,
    pending_status: "cancelled",
    pending_error_message: summary.primaryText,
  }));

  renderConversationHistory({ preserveAnalysisCard: true });
  setAnalysisStateBadge(
    "분석 취소",
    "cancelled",
    `${summary.primaryText} 기록 초기화가 끝나면 이 안내만 남기고 이전 대화는 모두 비웁니다.`,
  );
  if (analysisJudgmentEl) analysisJudgmentEl.innerText = "취소됨";
  if (analysisScoreEl) analysisScoreEl.innerText = "-";
  if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = "초기화 중";
  if (analysisTrendEl) analysisTrendEl.innerText = "-";
  if (analysisReasonEl) analysisReasonEl.innerText = summary.primaryText;
  if (confidenceScoreEl) confidenceScoreEl.innerText = "-";
  setSystemState("진행 중 분석 취소 중");
  setThinkingMessage(
    "이전 대화에 대한 답변과 점수 분석을 중단하고 기록을 초기화하고 있습니다.",
  );
  setProcessState(
    "analysis",
    `${summary.primaryText} 현재 기록을 비운 뒤 새 대화부터 다시 분석합니다.`,
  );
}

function applyResetSummaryState(summary) {
  setAnalysisStateBadge(
    summary?.wasCancelled ? "분석 취소" : "초기화 완료",
    summary?.wasCancelled ? "cancelled" : "idle",
    summary?.wasCancelled
      ? `${summary.primaryText} 새 녹음을 시작하면 완전히 초기 상태에서 다시 분석합니다.`
      : "기록을 비웠습니다. 새 대화를 시작하면 판단, 점수, 근거가 다시 표시됩니다.",
  );

  if (analysisJudgmentEl) {
    analysisJudgmentEl.innerText = summary?.wasCancelled ? "취소됨" : "대기";
  }
  if (analysisScoreEl) analysisScoreEl.innerText = "-";
  if (analysisRiskLevelEl) {
    analysisRiskLevelEl.innerText = summary?.wasCancelled
      ? "초기화 완료"
      : "분석 전";
  }
  if (analysisTrendEl) analysisTrendEl.innerText = "-";
  if (analysisReasonEl) {
    analysisReasonEl.innerText = summary?.wasCancelled
      ? summary.primaryText
      : "기록을 초기화했습니다. 새 녹음을 시작하면 최신 대화 기준으로 다시 분석합니다.";
  }
  if (confidenceScoreEl) confidenceScoreEl.innerText = "-";
}

function setSelectedMessageState(turnId) {
  const messages = Array.from(document.querySelectorAll(".history-message"));

  messages.forEach((message) => {
    if (message.dataset.turnId === turnId) {
      message.classList.add("is-selected");
    } else {
      message.classList.remove("is-selected");
    }
  });
}

function applyTurnAnalysis(turn) {
  if (!turn) {
    return;
  }

  const scoreIncluded = isScoreIncluded(turn);
  updateAnalysisCard({
    judgment: turn.judgment,
    risk_level: localizeRiskLevel(turn.risk_level || "정상"),
    trend: turn.trend || "데이터 부족",
    reason: turn.reason || "분석 근거가 없습니다.",
    score_included: scoreIncluded,
    excluded_reason: turn.excluded_reason || "",
  });
  updateFeatureBreakdown(turn.feature_scores || {});
  updateConfidence(
    scoreIncluded ? turn.feature_scores || {} : {},
    scoreIncluded ? (turn.score ?? 0) : 0,
    scoreIncluded,
  );
  setAnalysisScoreDisplay(turn.score, scoreIncluded);
  updateRoleAnalysisTracker(
    Object.fromEntries(
      analysisRoleOrder.map((role) => [
        role,
        { score: Number(turn.feature_scores?.[role] ?? 0) },
      ]),
    ),
    null,
    analysisRoleOrder.length,
    analysisRoleOrder.length,
    { finalized: true },
  );
}

function selectTurnById(turnId, options = {}) {
  const turn = turnHistory.find((item) => item.turn_id === turnId);
  if (!turn) {
    return;
  }

  if (isMobileTabViewport()) {
    setActiveMobileTab("analysis", {
      persist: true,
      scrollToTop: false,
    });
  }

  runWithViewTransition(() => {
    selectedTurnId = turnId;
    setSelectedMessageState(turnId);
    applyTurnAnalysis(turn);

    if (!options.suppressSystemState) {
      setSystemState("선택한 대화의 분석 결과를 보고 있습니다.");
    }

    renderTurnTimelineSurface();
    focusSelectedTurnFeedback(turnId);
  });
}

function updateFeatureBreakdown(featureScores) {
  const repetition = Number(featureScores.repetition ?? 0);
  const memory = Number(featureScores.memory ?? 0);
  const timeConfusion = Number(featureScores.time_confusion ?? 0);
  const incoherence = Number(featureScores.incoherence ?? 0);

  if (featureRepetitionValueEl) featureRepetitionValueEl.innerText = repetition;
  if (featureMemoryValueEl) featureMemoryValueEl.innerText = memory;
  if (featureTimeValueEl) featureTimeValueEl.innerText = timeConfusion;
  if (featureIncoherenceValueEl)
    featureIncoherenceValueEl.innerText = incoherence;

  if (featureRepetitionBarEl)
    featureRepetitionBarEl.style.width = `${(repetition / 25) * 100}%`;
  if (featureMemoryBarEl)
    featureMemoryBarEl.style.width = `${(memory / 25) * 100}%`;
  if (featureTimeBarEl)
    featureTimeBarEl.style.width = `${(timeConfusion / 30) * 100}%`;
  if (featureIncoherenceBarEl)
    featureIncoherenceBarEl.style.width = `${(incoherence / 20) * 100}%`;

  updateRadarChart(repetition, memory, timeConfusion, incoherence);
}

function updateRecallCard(recall) {
  const statusMap = {
    idle: "대기",
    memorize: "단어 제시",
    ask: "회상 질문",
  };
  const normalizedStatus = recall.status || "idle";

  if (recallStatusEl)
    recallStatusEl.innerText = statusMap[normalizedStatus] || "대기";
  if (recallLastResultEl)
    recallLastResultEl.innerText = recall.last_result || "없음";

  if (recallPromptEl) {
    if (recall.prompt) {
      recallPromptEl.innerText = recall.prompt;
    } else {
      recallPromptEl.innerText = "아직 진행 중인 기억 테스트가 없습니다.";
    }
  }

  if (recallDisclosureEl && recallDisclosureEl.dataset.userToggled !== "true") {
    const hasRecallResult = normalizeText(recall.last_result || "") !== "없음";
    const shouldOpen = normalizedStatus !== "idle" || hasRecallResult;
    setDisclosureOpenState(recallDisclosureEl, shouldOpen);
  }

  refreshWorkspaceOverviewSurface();
}

function calculateConfidenceValue(featureScores, totalScore) {
  const repetition = Number(featureScores.repetition ?? 0);
  const memory = Number(featureScores.memory ?? 0);
  const timeConfusion = Number(featureScores.time_confusion ?? 0);
  const incoherence = Number(featureScores.incoherence ?? 0);

  let confidence = 55;

  if (memory > 0) confidence += 8;
  if (timeConfusion > 0) confidence += 8;
  if (repetition > 0) confidence += 6;
  if (incoherence > 0) confidence += 6;
  if (totalScore >= 40) confidence += 8;
  if (totalScore >= 60) confidence += 4;

  return Math.max(0, Math.min(95, confidence));
}

function updateConfidence(featureScores, totalScore, shouldDisplay = true) {
  if (!confidenceScoreEl) {
    return;
  }

  if (!shouldDisplay) {
    confidenceScoreEl.innerText = "-";
    return;
  }

  const confidence = calculateConfidenceValue(featureScores, totalScore);

  animateNumber(
    confidenceScoreEl,
    extractNumber(confidenceScoreEl.innerText),
    confidence,
    750,
    true,
  );
}

function revealSummaryNumbers(data) {
  const averageScore = Number(data.average_score ?? 0);
  const recentAverageScore = Number(data.recent_average_score ?? averageScore);
  const latestScore =
    scoreHistory.length > 0 ? scoreHistory[scoreHistory.length - 1].score : 0;
  const scoreIncluded = isScoreIncluded(data);
  const confidenceValue = scoreIncluded
    ? calculateConfidenceValue(data.feature_scores || {}, data.score ?? 0)
    : 0;

  clearScoreCascadeTimers();

  scheduleNumberAnimation(
    avgScoreEl,
    lastMetricSnapshot.averageScore,
    averageScore,
    0,
    640,
    false,
    1,
  );
  scheduleNumberAnimation(
    recentAvgScoreEl,
    lastMetricSnapshot.recentAverageScore,
    recentAverageScore,
    70,
    680,
    false,
    1,
  );
  scheduleNumberAnimation(
    latestScoreEl,
    lastMetricSnapshot.latestScore,
    latestScore,
    150,
    700,
    false,
  );
  scheduleNumberAnimation(
    gaugeScoreEl,
    lastMetricSnapshot.gaugeScore,
    Math.round(recentAverageScore),
    270,
    760,
    false,
  );

  if (scoreIncluded) {
    scheduleNumberAnimation(
      analysisScoreEl,
      lastMetricSnapshot.analysisScore,
      Number(data.score ?? 0),
      390,
      780,
      false,
    );
    scheduleNumberAnimation(
      confidenceScoreEl,
      lastMetricSnapshot.confidence,
      confidenceValue,
      520,
      760,
      true,
    );
  } else {
    if (analysisScoreEl) {
      analysisScoreEl.innerText = "-";
    }
    if (confidenceScoreEl) {
      confidenceScoreEl.innerText = "-";
    }
  }

  lastMetricSnapshot = {
    averageScore,
    recentAverageScore,
    latestScore,
    gaugeScore: Math.round(recentAverageScore),
    analysisScore: scoreIncluded ? Number(data.score ?? 0) : 0,
    confidence: scoreIncluded ? confidenceValue : 0,
  };
}

function revealAnalysisWithCountUp(data) {
  revealSummaryNumbers(data);
  triggerAnalysisScoreCascade(data);
}

function setAnalysisLoadingState(isLoading) {
  const targets = [
    analysisScoreEl,
    analysisJudgmentEl,
    analysisRiskLevelEl,
    analysisTrendEl,
    analysisReasonEl,
    confidenceScoreEl,
    featureRepetitionValueEl,
    featureMemoryValueEl,
    featureTimeValueEl,
    featureIncoherenceValueEl,
    recallStatusEl,
    recallLastResultEl,
    recallPromptEl,
  ];

  targets.forEach((el) => {
    if (!el) return;
    el.style.opacity = isLoading ? "0.55" : "1";
    el.style.transition = "opacity 0.2s ease";
  });

  setSkeletonLoading(isLoading);
  updateRecordToggleButton();
}

function renderTurnHistory(turns, options = {}) {
  if (!chatWindow) {
    return;
  }

  chatWindow.innerHTML = "";

  if (!Array.isArray(turns) || turns.length === 0) {
    renderChatEmptyState();
    if (!options.preserveAnalysisCard) {
      resetAnalysisCard();
    }
    selectedTurnId = null;
    setSelectedMessageState(null);
    refreshSessionReportSurface();
    return;
  }

  turns.forEach((turn) => {
    const isPending = Boolean(turn?.is_pending);
    const userOptions = {};
    const answerOptions = {};

    if (!isPending && turn?.turn_id) {
      userOptions.turnId = turn.turn_id;
      answerOptions.turnId = turn.turn_id;
    }

    if (isPending) {
      userOptions.badge = getPendingTurnBadge(turn);
    } else if (turn?.score_included === false) {
      userOptions.badge = "점수 미반영";
    }

    appendChatMessage("user", turn?.user_text || "", userOptions);
    appendChatMessage("system", turn?.answer || "", answerOptions);

    if (Array.isArray(turn?.follow_up_messages)) {
      turn.follow_up_messages
        .filter((message) => normalizeText(message))
        .forEach((message) =>
          appendChatMessage("system", message, answerOptions),
        );
    }
  });

  const finalizedTurns = turns.filter(
    (turn) => !turn?.is_pending && turn?.turn_id,
  );

  if (finalizedTurns.length === 0) {
    selectedTurnId = null;
    setSelectedMessageState(null);
    return;
  }

  const latestTurnId = finalizedTurns[finalizedTurns.length - 1].turn_id;
  const shouldPreserveSelected =
    !options.preferLatestTurn &&
    selectedTurnId &&
    finalizedTurns.some((turn) => turn.turn_id === selectedTurnId);
  const targetTurnId = shouldPreserveSelected ? selectedTurnId : latestTurnId;

  selectTurnById(targetTurnId, { suppressSystemState: true });
  refreshSessionReportSurface();
}

function buildJsonHeaders(provider = llmMode, generation = analysisGeneration) {
  return {
    "Content-Type": "application/json",
    "X-LLM-Provider": normalizeLlmMode(provider),
    "X-Analysis-Generation": String(normalizeAnalysisGeneration(generation)),
  };
}

function buildMultipartHeaders(
  provider = llmMode,
  generation = analysisGeneration,
) {
  return {
    "X-LLM-Provider": normalizeLlmMode(provider),
    "X-Analysis-Generation": String(normalizeAnalysisGeneration(generation)),
  };
}

async function requestAnswerFirst(
  recognizedText,
  provider = llmMode,
  generation = analysisGeneration,
) {
  const normalizedProvider = normalizeLlmMode(provider);
  const answerUrl = sessionId
    ? `/generate-answer?session_id=${encodeURIComponent(sessionId)}`
    : "/generate-answer";

  const answerResponse = await fetch(answerUrl, {
    method: "POST",
    headers: buildJsonHeaders(normalizedProvider, generation),
    body: JSON.stringify({
      message: recognizedText,
      llm_provider: normalizedProvider,
      analysis_generation: normalizeAnalysisGeneration(generation),
    }),
  });

  return answerResponse.json();
}

async function requestRoleAnalysis(
  recognizedText,
  role,
  provider = llmMode,
  generation = analysisGeneration,
) {
  const normalizedProvider = normalizeLlmMode(provider);
  const analyzeRoleUrl = sessionId
    ? `/analyze-role?session_id=${encodeURIComponent(sessionId)}`
    : "/analyze-role";

  const roleResponse = await fetch(analyzeRoleUrl, {
    method: "POST",
    headers: buildJsonHeaders(normalizedProvider, generation),
    body: JSON.stringify({
      message: recognizedText,
      role,
      llm_provider: normalizedProvider,
      analysis_generation: normalizeAnalysisGeneration(generation),
    }),
  });

  return roleResponse.json();
}

async function requestFinalizeAnalysis(
  recognizedText,
  answerText,
  roleResults,
  provider = llmMode,
  generation = analysisGeneration,
) {
  const normalizedProvider = normalizeLlmMode(provider);
  const finalizeUrl = sessionId
    ? `/finalize-analysis?session_id=${encodeURIComponent(sessionId)}`
    : "/finalize-analysis";

  const finalizeResponse = await fetch(finalizeUrl, {
    method: "POST",
    headers: buildJsonHeaders(normalizedProvider, generation),
    body: JSON.stringify({
      message: recognizedText,
      answer: answerText,
      role_results: roleResults,
      llm_provider: normalizedProvider,
      analysis_generation: normalizeAnalysisGeneration(generation),
    }),
  });

  return finalizeResponse.json();
}

function applyAnalysisResult(data, options = {}) {
  syncSessionMetadata(data);

  if (data?.llm_provider) {
    setLlmMode(data.llm_provider, { silent: true });
  }

  if (Array.isArray(data?.score_history)) {
    scoreHistory = data.score_history;
  }
  if (Array.isArray(data?.turn_history)) {
    turnHistory = data.turn_history;
  }
  if (options.finalizedClientTurnId) {
    removePendingTurn(options.finalizedClientTurnId);
  }

  const scoreIncluded = isScoreIncluded(data);

  updateAnalysisCard(data);
  updateFeatureBreakdown(data.feature_scores || {});
  updateRoleAnalysisTracker(
    Object.fromEntries(
      analysisRoleOrder.map((role) => [
        role,
        { score: Number(data.feature_scores?.[role] ?? 0) },
      ]),
    ),
    null,
    analysisRoleOrder.length,
    analysisRoleOrder.length,
    { finalized: true },
  );
  updateRecallCard(data.recall || {});
  renderAll(data);
  revealAnalysisWithCountUp(data);

  if (data?.turn && data.turn.turn_id) {
    const existingTurnIndex = turnHistory.findIndex(
      (item) => item.turn_id === data.turn.turn_id,
    );
    if (existingTurnIndex >= 0) {
      turnHistory[existingTurnIndex] = data.turn;
    } else {
      turnHistory.push(data.turn);
    }
  }

  renderConversationHistory({
    preferLatestTurn: true,
    preserveAnalysisCard: true,
  });
  refreshSessionReportSurface();
  triggerAnalysisCompletionAnimation(data);

  if (
    (scoreIncluded && (data.score ?? 0) >= 60) ||
    (data.recent_average_score ?? 0) >= 60
  ) {
    const warningText = scoreIncluded
      ? `현재 점수 ${data.score ?? 0}점과 최근 5회 평균 ${data.recent_average_score ?? 0}점으로 위험 구간에 해당합니다.`
      : `이번 분석은 점수 통계에서 제외되었지만 최근 5회 평균 ${data.recent_average_score ?? 0}점이 위험 구간에 해당합니다.`;
    showWarningPopup(warningText);
  }
}

async function handleRecognizedTextFlow(
  recognizedText,
  generationSnapshot = analysisGeneration,
) {
  const questionPreview = buildStatusPreview(recognizedText);
  const clientTurnId = createClientTurnId();
  const providerSnapshot = normalizeLlmMode(llmMode);

  appendChatMessage("user", recognizedText);
  appendLoadingMessage("답변 생성 중...");
  setRecordButtonBusyState(true, "답변 생성 중...");
  setProcessState(
    "stt",
    `음성 인식이 끝났고 "${questionPreview}" 내용을 바탕으로 답변 초안을 준비하고 있습니다.`,
  );
  setSystemState(`${getLlmModeLabel(providerSnapshot)}로 질문 의도 해석 중`);
  setThinkingMessage(
    `인식된 문장을 정리하고 ${getLlmModeLabel(providerSnapshot)} 기준으로 응답 의도를 파악하고 있습니다.`,
  );

  const stopAnswerNarration = startStatusNarration([
    {
      delay: 900,
      step: "stt",
      detail: `인식 문장을 정리하고, "${questionPreview}"에 대한 답변 초안을 구성하고 있습니다.`,
      system: "답변 초안 정리 중",
      thinking: "짧고 자연스러운 1차 응답이 나오도록 문장을 정돈하고 있습니다.",
    },
    {
      delay: 1800,
      step: "answer",
      detail:
        "답변 문장을 마무리하면서, 이어질 위험도 분석에 사용할 기본 정보를 함께 정리하고 있습니다.",
      system: "답변 문장 정리 중",
      thinking: "답변이 너무 길어지지 않도록 핵심만 정리하고 있습니다.",
    },
  ]);

  let answerData;
  try {
    answerData = await requestAnswerFirst(
      recognizedText,
      providerSnapshot,
      generationSnapshot,
    );
  } finally {
    stopAnswerNarration();
  }

  syncSessionMetadata(answerData);

  if (isStaleGeneration(generationSnapshot, answerData)) {
    return;
  }

  if (answerData?.error) {
    removeLoadingMessage();
    setRecordButtonBusyState(false);
    appendChatMessage("system", answerData.error);
    setSystemState("오류 발생");
    setProcessError(
      "답변 생성 단계에서 문제가 발생해 다음 분석 단계로 넘어가지 못했습니다.",
    );
    syncBackgroundAnalysisState();
    return;
  }

  const answerText =
    normalizeText(answerData?.answer || "") || "응답을 생성하지 못했습니다.";

  removeLoadingMessage();
  upsertPendingTurn({
    client_turn_id: clientTurnId,
    user_text: recognizedText,
    answer: answerText,
    follow_up_messages: [],
    pending_status: "queued",
    llm_provider: providerSnapshot,
    created_at: Date.now(),
  });
  renderConversationHistory({ preserveAnalysisCard: true });
  setRecordButtonBusyState(false);

  setAnalysisStateBadge(
    "분석 대기",
    "idle",
    "답변은 먼저 표시했고, 점수 분석은 백그라운드에서 순서대로 이어집니다.",
  );
  setProcessState(
    "answer",
    "답변을 먼저 표시했고, 점수 분석은 백그라운드 큐에 등록했습니다.",
  );
  setSystemState("답변 완료, 분석 대기 중");
  setThinkingMessage(
    "다음 질문 녹음은 바로 이어서 할 수 있고, 점수 분석은 뒤에서 순서대로 진행됩니다.",
  );

  enqueueAnalysisTask({
    clientTurnId,
    recognizedText,
    answerText,
    llmProvider: providerSnapshot,
    questionPreview,
    analysisGeneration: normalizeAnalysisGeneration(generationSnapshot),
  });
}

async function startRecording() {
  try {
    const recordingGeneration = normalizeAnalysisGeneration(analysisGeneration);
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordingStream = stream;
    await startVoiceAmbient(stream);

    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.ondataavailable = function (event) {
      audioChunks.push(event.data);
    };

    mediaRecorder.onstop = async function () {
      setRecordingState(false);
      let stopCaptureNarration = () => {};

      try {
        setRecordButtonBusyState(true, "음성 처리 중...");
        setProcessState(
          "capture",
          "녹음을 종료했고, 음성 데이터를 정리한 뒤 서버로 전송하고 있습니다.",
        );
        setSystemState("음성 전송 중");
        setThinkingMessage(
          "녹음 데이터를 정리하고 음성 인식에 사용할 파일을 준비하고 있습니다.",
        );
        setAnalysisThinking(true);
        setAnalysisLoadingState(true);

        stopCaptureNarration = startStatusNarration([
          {
            delay: 800,
            step: "capture",
            detail:
              "음성 길이와 형식을 확인하고, 서버에서 음성 인식을 시작할 준비를 하고 있습니다.",
            system: "STT 준비 중",
            thinking: "업로드된 음성에서 발화 문장을 추출하고 있습니다.",
          },
          {
            delay: 1700,
            step: "stt",
            detail:
              "서버에서 발화 문장을 추출하고 있으며, 텍스트가 준비되면 곧바로 답변 생성으로 넘어갑니다.",
            system: "음성 인식 중",
            thinking:
              "음성 구간을 문장 단위로 정리하고 텍스트로 변환하고 있습니다.",
          },
        ]);

        const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
        const formData = new FormData();
        formData.append("audio", audioBlob, "recording.wav");

        const transcribeUrl = sessionId
          ? `/transcribe-audio?session_id=${encodeURIComponent(sessionId)}`
          : "/transcribe-audio";

        const sttResponse = await fetch(transcribeUrl, {
          method: "POST",
          headers: buildMultipartHeaders(llmMode, recordingGeneration),
          body: formData,
        });

        const sttData = await sttResponse.json();
        stopCaptureNarration();

        syncSessionMetadata(sttData);

        if (isStaleGeneration(recordingGeneration, sttData)) {
          return;
        }

        if (sttData?.error) {
          setRecordButtonBusyState(false);
          appendChatMessage("system", sttData.error);
          setSystemState("오류 발생");
          setProcessError(
            "음성 인식 단계에서 문제가 발생해 발화 문장을 추출하지 못했습니다.",
          );
          syncBackgroundAnalysisState();
          return;
        }

        const recognizedText = normalizeText(sttData?.user_speech || "");

        if (!recognizedText) {
          setRecordButtonBusyState(false);
          appendChatMessage(
            "system",
            "음성 인식 결과가 없습니다. 다시 녹음해 주세요.",
          );
          setSystemState("음성 인식 실패");
          setProcessError(
            "인식된 문장이 없어 답변 생성과 위험도 분석을 진행할 수 없습니다.",
          );
          syncBackgroundAnalysisState();
          return;
        }

        setProcessState(
          "stt",
          `음성 인식이 완료되었습니다. 인식 문장: "${buildStatusPreview(recognizedText)}"`,
        );
        setSystemState("인식 문장 확인 완료");
        setThinkingMessage("인식된 문장을 바탕으로 답변 생성을 시작합니다.");
        await handleRecognizedTextFlow(recognizedText, recordingGeneration);
      } catch (error) {
        console.error(error);
        stopCaptureNarration();
        removeLoadingMessage();
        setRecordButtonBusyState(false);
        appendChatMessage("system", "오류가 발생했습니다. 다시 시도해 주세요.");
        setSystemState("오류 발생");
        setProcessError(
          "음성 전송부터 분석 반영까지 이어지는 처리 과정에서 예외가 발생했습니다.",
        );
        syncBackgroundAnalysisState();
      } finally {
        stopCaptureNarration();
        audioChunks = [];
        mediaRecorder = null;
        cleanupRecordingStream();
        stopVoiceAmbient();
      }
    };

    mediaRecorder.start();
    setRecordingState(true);
    resetProcessState("녹음을 시작했고, 사용자 발화를 기다리고 있습니다.");
    setProcessState(
      "capture",
      "마이크가 연결되었고 사용자의 음성을 실시간으로 수집하고 있습니다.",
    );
    setSystemState("음성 입력 수집 중");
  } catch (error) {
    console.error(error);
    cleanupRecordingStream();
    stopVoiceAmbient();
    setRecordButtonBusyState(false);
    setRecordingState(false);
    alert("마이크 접근 권한을 확인한 뒤 다시 시도해 주세요.");
  }
}

function renderAll(data) {
  const averageScore = Number(data.average_score ?? 0);
  const recentAverageScore = Number(data.recent_average_score ?? averageScore);
  const latestScore =
    scoreHistory.length > 0 ? scoreHistory[scoreHistory.length - 1].score : 0;

  if (avgScoreEl) {
    avgScoreEl.innerText = averageScore.toFixed(1);
  }
  if (recentAvgScoreEl) {
    recentAvgScoreEl.innerText = recentAverageScore.toFixed(1);
  }
  if (latestScoreEl) {
    latestScoreEl.innerText = String(Math.round(latestScore));
  }
  if (gaugeScoreEl) {
    gaugeScoreEl.innerText = String(Math.round(recentAverageScore));
  }

  updateSummary(data.trend || "데이터 부족");
  updateStatusCard(recentAverageScore);
  updateLineChart(recentAverageScore);
  updateGaugeChart(recentAverageScore);
  refreshSessionReportSurface();
}

function updateSummary(trend) {
  if (trendTextEl) {
    trendTextEl.innerText = trend;
  }
}

function getRiskInfo(score) {
  if (score < 20) {
    return {
      text: "정상",
      desc: "안정적인 상태입니다.",
      cssClass: "risk-safe",
      color: "#1897d1",
      iconKey: "safe",
      iconAlt: "정상 상태 아이콘",
    };
  }

  if (score < 40) {
    return {
      text: "낮은 위험",
      desc: "경미한 변화가 보입니다.",
      cssClass: "risk-low",
      color: "#82d6f6",
      iconKey: "low",
      iconAlt: "낮은 위험 상태 아이콘",
    };
  }

  if (score < 60) {
    return {
      text: "주의",
      desc: "지속 관찰이 필요합니다.",
      cssClass: "risk-warning",
      color: "#c5b47b",
      iconKey: "warning",
      iconAlt: "주의 상태 아이콘",
    };
  }

  if (score < 80) {
    return {
      text: "위험",
      desc: "상당한 위험 신호가 있습니다.",
      cssClass: "risk-high",
      color: "#8b7752",
      iconKey: "high",
      iconAlt: "위험 상태 아이콘",
    };
  }

  return {
    text: "매우 위험",
    desc: "즉각적인 관찰이 필요합니다.",
    cssClass: "risk-critical",
    color: "#ff4f73",
    iconKey: "critical",
    iconAlt: "매우 위험 상태 아이콘",
  };
}

function updateStatusCard(recentAverageScore) {
  const statusCard = document.getElementById("statusCard");
  const riskText = document.getElementById("riskText");
  const riskDescription = document.getElementById("riskDescription");
  const statusVisual = statusCard?.querySelector(".status-card-visual");
  const statusVisualImage = statusVisual?.querySelector(
    ".status-card-visual-image",
  );

  if (!statusCard || !riskText || !riskDescription) {
    return;
  }

  const risk = getRiskInfo(recentAverageScore);

  statusCard.classList.remove(
    "risk-safe",
    "risk-low",
    "risk-warning",
    "risk-high",
    "risk-critical",
  );
  statusCard.classList.add(risk.cssClass);

  riskText.innerText = risk.text;
  riskDescription.innerText = risk.desc;

  if (
    statusVisualImage &&
    typeof THREE_D_ICON_PATHS.status === "object" &&
    THREE_D_ICON_PATHS.status[risk.iconKey]
  ) {
    statusVisualImage.src = THREE_D_ICON_PATHS.status[risk.iconKey];
    statusVisualImage.alt = risk.iconAlt;
  }
}

function buildThresholdDataset(value, label) {
  return {
    label: label,
    data: scoreHistory.map(() => value),
    borderColor:
      value === 30 ? "rgba(255, 179, 71, 0.5)" : "rgba(255, 79, 115, 0.5)",
    borderWidth: 1,
    borderDash: [6, 6],
    pointRadius: 0,
    fill: false,
  };
}

function updateLineChart(recentAverageScore) {
  const canvas = document.getElementById("scoreChart");
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");

  const labels = scoreHistory.map((item) => item.time);
  const scores = scoreHistory.map((item) => item.score);
  const risk = getRiskInfo(recentAverageScore);

  if (!scoreChart) {
    scoreChart = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "치매 의심 점수",
            data: scores,
            borderColor: risk.color,
            backgroundColor: risk.color,
            borderWidth: 3,
            pointRadius: 4,
            pointHoverRadius: 6,
            tension: 0.35,
            fill: false,
          },
          buildThresholdDataset(30, "주의 기준"),
          buildThresholdDataset(60, "위험 기준"),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          duration: 900,
          easing: "easeOutQuart",
        },
        plugins: {
          legend: {
            labels: {
              color: "#d7e3f8",
            },
          },
        },
        scales: {
          x: {
            ticks: {
              color: "#9cb0d3",
            },
            grid: {
              color: "rgba(145, 164, 205, 0.12)",
            },
          },
          y: {
            min: 0,
            max: 100,
            ticks: {
              stepSize: 20,
              color: "#9cb0d3",
            },
            grid: {
              color: "rgba(145, 164, 205, 0.12)",
            },
          },
        },
      },
    });
    return;
  }

  scoreChart.data.labels = labels;
  scoreChart.data.datasets[0].data = scores;
  scoreChart.data.datasets[0].borderColor = risk.color;
  scoreChart.data.datasets[0].backgroundColor = risk.color;
  scoreChart.data.datasets[1].data = scoreHistory.map(() => 30);
  scoreChart.data.datasets[2].data = scoreHistory.map(() => 60);
  scoreChart.update();
}

function updateGaugeChart(recentAverageScore) {
  const canvas = document.getElementById("gaugeChart");
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");
  const safeScore = Math.max(0, Math.min(100, recentAverageScore));
  const risk = getRiskInfo(safeScore);

  if (!gaugeChart) {
    gaugeChart = new Chart(ctx, {
      type: "doughnut",
      data: {
        datasets: [
          {
            data: [safeScore, 100 - safeScore],
            backgroundColor: [risk.color, "rgba(255, 255, 255, 0.08)"],
            borderWidth: 0,
            circumference: 180,
            rotation: 270,
            cutout: "76%",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: {
          animateRotate: true,
          duration: 900,
        },
        plugins: {
          tooltip: {
            enabled: false,
          },
          legend: {
            display: false,
          },
        },
      },
    });
    return;
  }

  gaugeChart.data.datasets[0].data = [safeScore, 100 - safeScore];
  gaugeChart.data.datasets[0].backgroundColor = [
    risk.color,
    "rgba(255, 255, 255, 0.08)",
  ];
  gaugeChart.update();
}

function updateRadarChart(repetition, memory, timeConfusion, incoherence) {
  const canvas = document.getElementById("radarChart");
  if (!canvas) {
    return;
  }

  const ctx = canvas.getContext("2d");

  const radarData = [repetition, memory, timeConfusion, incoherence];

  if (!radarChart) {
    radarChart = new Chart(ctx, {
      type: "radar",
      data: {
        labels: ["질문 반복", "기억 혼란", "시간 혼란", "문장 비논리성"],
        datasets: [
          {
            label: "언어 특징 점수",
            data: radarData,
            borderColor: "#d7b26d",
            backgroundColor: "rgba(121, 201, 255, 0.14)",
            borderWidth: 2,
            pointBackgroundColor: "#82d6f6",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          r: {
            min: 0,
            max: 30,
            ticks: {
              backdropColor: "transparent",
              color: "#9cb0d3",
            },
            grid: {
              color: "rgba(145, 164, 205, 0.16)",
            },
            angleLines: {
              color: "rgba(145, 164, 205, 0.16)",
            },
            pointLabels: {
              color: "#dce8ff",
              font: {
                size: 12,
              },
            },
          },
        },
        plugins: {
          legend: {
            labels: {
              color: "#d7e3f8",
            },
          },
        },
      },
    });
    return;
  }

  radarChart.data.datasets[0].data = radarData;
  radarChart.update();
}

function animateNumber(
  element,
  start,
  end,
  duration = 700,
  isPercent = false,
  fixed = 0,
) {
  if (!element) {
    return;
  }

  let startTime = null;

  function update(currentTime) {
    if (!startTime) {
      startTime = currentTime;
    }

    const progress = Math.min((currentTime - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = start + (end - start) * eased;

    if (fixed > 0) {
      element.innerText = `${value.toFixed(fixed)}${isPercent ? "%" : ""}`;
    } else {
      element.innerText = `${Math.round(value)}${isPercent ? "%" : ""}`;
    }

    if (progress < 1) {
      requestAnimationFrame(update);
    }
  }

  requestAnimationFrame(update);
}

function extractNumber(text) {
  const numeric = parseFloat(String(text).replace(/[^0-9.]/g, ""));
  return Number.isNaN(numeric) ? 0 : numeric;
}

function normalizeText(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

function showWarningPopup(message) {
  if (!warningPopup || !warningPopupText) {
    return;
  }

  warningPopupText.innerText = message;
  warningPopup.classList.remove("hidden");
}

function hideWarningPopup() {
  if (!warningPopup) {
    return;
  }

  warningPopup.classList.add("hidden");
}
