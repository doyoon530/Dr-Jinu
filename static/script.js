let mediaRecorder = null;
let audioChunks = [];

let sessionId = localStorage.getItem("session_id") || null;

let scoreHistory = [];
let turnHistory = [];
let selectedTurnId = null;
let scoreChart = null;
let gaugeChart = null;
let radarChart = null;
let analysisRevealTimeout = null;

const startButton = document.getElementById("startRecord");
const stopButton = document.getElementById("stopRecord");
const resetButton = document.getElementById("resetHistory");
const chatWindow = document.getElementById("chatWindow");
const recordingIndicator = document.getElementById("recordingIndicator");
const aiThinking = document.getElementById("aiThinking");
const systemStateText = document.getElementById("systemStateText");
const processDetailEl = document.getElementById("processDetail");
const processSteps = Array.from(document.querySelectorAll(".process-step"));

const avgScoreEl = document.getElementById("avgScore");
const recentAvgScoreEl = document.getElementById("recentAvgScore");
const latestScoreEl = document.getElementById("latestScore");
const gaugeScoreEl = document.getElementById("gaugeScore");
const trendTextEl = document.getElementById("trendText");

const analysisJudgmentEl = document.getElementById("analysisJudgment");
const analysisScoreEl = document.getElementById("analysisScore");
const analysisRiskLevelEl = document.getElementById("analysisRiskLevel");
const analysisTrendEl = document.getElementById("analysisTrend");
const analysisReasonEl = document.getElementById("analysisReason");

const featureRepetitionValueEl = document.getElementById("featureRepetitionValue");
const featureMemoryValueEl = document.getElementById("featureMemoryValue");
const featureTimeValueEl = document.getElementById("featureTimeValue");
const featureIncoherenceValueEl = document.getElementById("featureIncoherenceValue");

const featureRepetitionBarEl = document.getElementById("featureRepetitionBar");
const featureMemoryBarEl = document.getElementById("featureMemoryBar");
const featureTimeBarEl = document.getElementById("featureTimeBar");
const featureIncoherenceBarEl = document.getElementById("featureIncoherenceBar");

const confidenceScoreEl = document.getElementById("confidenceScore");

const recallStatusEl = document.getElementById("recallStatus");
const recallLastResultEl = document.getElementById("recallLastResult");
const recallPromptEl = document.getElementById("recallPrompt");

const warningPopup = document.getElementById("warningPopup");
const warningPopupText = document.getElementById("warningPopupText");
const closeWarningPopupButton = document.getElementById("closeWarningPopup");

const processStepOrder = ["capture", "stt", "answer", "analysis", "render"];

document.addEventListener("DOMContentLoaded", async () => {
    bindEvents();
    resetProcessState("대기 중");
    await loadScoreHistory();
});

function bindEvents() {
    if (startButton) startButton.onclick = startRecording;
    if (stopButton) stopButton.onclick = stopRecording;
    if (resetButton) resetButton.onclick = resetHistory;
    if (closeWarningPopupButton) closeWarningPopupButton.onclick = hideWarningPopup;
}

function setThinkingMessage(text) {
    if (aiThinking) {
        aiThinking.innerText = text;
    }
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
        processDetailEl.innerText = detail || "처리 중";
    }
}

function setProcessError(detail) {
    processSteps.forEach((element) => {
        element.classList.remove("is-active");
    });

    const active = processSteps.find((element) => element.classList.contains("is-complete") === false);
    if (active) {
        active.classList.add("is-error");
    }

    if (processDetailEl) {
        processDetailEl.innerText = detail || "오류 발생";
    }
}

function resetProcessState(detail = "대기 중") {
    processSteps.forEach((element) => {
        element.classList.remove("is-active", "is-complete", "is-error");
    });

    if (processDetailEl) {
        processDetailEl.innerText = detail;
    }
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = function (event) {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = async function () {
            setRecordingState(false);

            try {
                setSystemState("음성 인식 중...");
                setProcessState("capture", "음성 데이터를 서버로 전송하고 있습니다.");
                setSystemState("음성 처리 시작");
                setThinkingMessage("AI 분석 준비 중...");
                setAnalysisThinking(true);
                setAnalysisLoadingState(true);

                const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
                const formData = new FormData();
                formData.append("audio", audioBlob, "recording.wav");

                const transcribeUrl = sessionId
                    ? `/transcribe-audio?session_id=${encodeURIComponent(sessionId)}`
                    : "/transcribe-audio";

                const sttResponse = await fetch(transcribeUrl, {
                    method: "POST",
                    body: formData
                });

                const sttData = await sttResponse.json();

                if (sttData.error) {
                    appendChatMessage("system", sttData.error);
                    setSystemState("오류 발생");
                    setAnalysisThinking(false);
                    setAnalysisLoadingState(false);
                    return;
                }

                if (sttData.session_id) {
                    sessionId = sttData.session_id;
                    localStorage.setItem("session_id", sessionId);
                }

                const recognizedText = normalizeText(sttData.user_speech || "");

                if (!recognizedText) {
                    appendChatMessage("system", "음성 인식 결과가 없습니다. 다시 녹음해 주세요.");
                    setSystemState("음성 인식 실패");
                    setAnalysisThinking(false);
                    setAnalysisLoadingState(false);
                    return;
                }

                appendChatMessage("user", recognizedText);
                appendLoadingMessage("답변 생성 중...");
                setSystemState("답변 생성 중...");

                const analyzeUrl = sessionId
                    ? `/analyze-text?session_id=${encodeURIComponent(sessionId)}`
                    : "/analyze-text";

                const analyzeResponse = await fetch(analyzeUrl, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        message: recognizedText
                    })
                });

                const data = await analyzeResponse.json();

                if (data.error) {
                    removeLoadingMessage();
                    appendChatMessage("system", data.error);
                    setSystemState("오류 발생");
                    setAnalysisThinking(false);
                    setAnalysisLoadingState(false);
                    return;
                }

                if (data.session_id) {
                    sessionId = data.session_id;
                    localStorage.setItem("session_id", sessionId);
                }

                removeLoadingMessage();
                appendChatMessage("system", data.answer || data.sys_response || "");
                setSystemState("치매 분석 반영 중...");

                scoreHistory = Array.isArray(data.score_history) ? data.score_history : [];

                updateAnalysisCard(data);
                updateFeatureBreakdown(data.feature_scores || {});
                updateRecallCard(data.recall || {});
                renderAll(data);
                revealAnalysisWithCountUp(data);

                setSystemState("분석 완료");

                const scoreIncluded = isScoreIncluded(data);
                if ((scoreIncluded && (data.score ?? 0) >= 60) || (data.recent_average_score ?? 0) >= 60) {
                    const warningText = scoreIncluded
                        ? `현재 점수 ${data.score ?? 0}점, 최근 5회 평균 ${data.recent_average_score ?? 0}점으로 위험 구간에 해당합니다.`
                        : `이번 분석은 점수 통계에서 제외되었고, 최근 5회 평균 ${data.recent_average_score ?? 0}점이 위험 구간에 해당합니다.`;
                    showWarningPopup(warningText);
                }
            } catch (error) {
                console.error(error);
                removeLoadingMessage();
                appendChatMessage("system", "오류가 발생했습니다. 다시 시도해주세요.");
                setSystemState("오류 발생");
                setAnalysisThinking(false);
                setAnalysisLoadingState(false);
            } finally {
                audioChunks = [];
            }
        };

        mediaRecorder.start();
        setRecordingState(true);
    } catch (error) {
        console.error(error);
        alert("마이크 접근에 실패했습니다.");
    }
}

function stopRecording() {
    if (!mediaRecorder) {
        return;
    }

    mediaRecorder.stop();
}

async function resetHistory() {
    try {
        const url = sessionId
            ? `/reset-history?session_id=${encodeURIComponent(sessionId)}`
            : "/reset-history";

        const response = await fetch(url, {
            method: "POST"
        });

        const data = await response.json();

        if (data.session_id) {
            sessionId = data.session_id;
            localStorage.setItem("session_id", sessionId);
        }

        scoreHistory = [];
        turnHistory = [];
        selectedTurnId = null;
        if (chatWindow) {
            chatWindow.innerHTML = "";
        }

        resetAnalysisCard();
        updateFeatureBreakdown({});
        updateRecallCard(data.recall || {});
        updateConfidence({}, 0, false);
        renderAll(data);

        setSystemState("기록 초기화 완료");
    } catch (error) {
        console.error(error);
        alert("기록 초기화 중 오류가 발생했습니다.");
    }
}

function setRecordingState(isRecording) {
    if (isRecording) {
        if (startButton) startButton.disabled = true;
        if (stopButton) stopButton.disabled = false;
        if (recordingIndicator) recordingIndicator.classList.remove("hidden");
        setSystemState("녹음 중");
    } else {
        if (startButton) startButton.disabled = false;
        if (stopButton) stopButton.disabled = true;
        if (recordingIndicator) recordingIndicator.classList.add("hidden");
    }
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
}

function getAnalysisCards() {
    return Array.from(document.querySelectorAll(".analysis-card"));
}

function setSkeletonLoading(isLoading) {
    const cards = getAnalysisCards();

    cards.forEach((card) => {
        if (isLoading) {
            card.classList.add("is-skeleton");
        } else {
            card.classList.remove("is-skeleton");
        }
    });
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
        recallPromptEl
    ];

    targets.forEach((el) => {
        if (!el) return;
        el.style.opacity = isLoading ? "0.55" : "1";
        el.style.transition = "opacity 0.2s ease";
    });

    setSkeletonLoading(isLoading);
}

function appendChatMessage(type, text, options = {}) {
    if (!chatWindow) {
        return null;
    }

    const message = document.createElement("div");
    message.classList.add("message", "message-enter");

    if (type === "user") {
        message.classList.add("user-message");
    } else {
        message.classList.add("system-message");
    }

    message.innerText = text;

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

        if (data.session_id) {
            sessionId = data.session_id;
            localStorage.setItem("session_id", sessionId);
        }

        scoreHistory = Array.isArray(data.score_history) ? data.score_history : [];
        turnHistory = Array.isArray(data.turn_history) ? data.turn_history : [];
        updateRecallCard(data.recall || {});
        renderAll(data);
        updateConfidence({}, 0, false);

        if (turnHistory.length > 0) {
            renderTurnHistory(turnHistory);
        }
    } catch (error) {
        console.error("점수 기록 로딩 실패:", error);
        renderAll({
            average_score: 0,
            recent_average_score: 0,
            risk_level: "Normal",
            trend: "데이터 부족",
            score_history: []
        });
    }
}

function isScoreIncluded(data) {
    return data?.score_included !== false;
}

function setAnalysisScoreDisplay(score, scoreIncluded = true) {
    if (!analysisScoreEl) {
        return;
    }

    analysisScoreEl.innerText = scoreIncluded ? String(score ?? 0) : "-";
}

function updateAnalysisCard(data) {
    const scoreIncluded = isScoreIncluded(data);
    const riskLabel = scoreIncluded ? (data.risk_level || "Normal") : "반영 제외";
    const trendLabel = scoreIncluded ? (data.trend || "데이터 부족") : "반영 제외";
    const reasonText = scoreIncluded
        ? (data.reason || "분석 근거가 없습니다.")
        : (data.excluded_reason || data.reason || "이번 분석은 점수 통계에서 제외되었습니다.");

    if (analysisJudgmentEl) analysisJudgmentEl.innerText = data.judgment || "없음";
    if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = riskLabel;
    if (analysisTrendEl) analysisTrendEl.innerText = trendLabel;
    if (analysisReasonEl) analysisReasonEl.innerText = reasonText;
}

function resetAnalysisCard() {
    if (analysisJudgmentEl) analysisJudgmentEl.innerText = "없음";
    if (analysisScoreEl) analysisScoreEl.innerText = "0";
    if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = "Normal";
    if (analysisTrendEl) analysisTrendEl.innerText = "데이터 부족";
    if (analysisReasonEl) analysisReasonEl.innerText = "아직 분석 결과가 없습니다.";
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
        risk_level: turn.risk_level || "Normal",
        trend: turn.trend || "데이터 부족",
        reason: turn.reason || "분석 근거가 없습니다.",
        score_included: scoreIncluded,
        excluded_reason: turn.excluded_reason || ""
    });
    updateFeatureBreakdown(turn.feature_scores || {});
    updateConfidence(
        scoreIncluded ? (turn.feature_scores || {}) : {},
        scoreIncluded ? (turn.score ?? 0) : 0,
        scoreIncluded
    );
    setAnalysisScoreDisplay(turn.score, scoreIncluded);
}

function selectTurnById(turnId, options = {}) {
    const turn = turnHistory.find((item) => item.turn_id === turnId);
    if (!turn) {
        return;
    }

    selectedTurnId = turnId;
    setSelectedMessageState(turnId);
    applyTurnAnalysis(turn);

    if (!options.suppressSystemState) {
        setSystemState("선택한 대화의 분석 결과를 보고 있습니다.");
    }
}

function renderTurnHistory(turns) {
    if (!chatWindow) {
        return;
    }

    chatWindow.innerHTML = "";

    turns.forEach((turn) => {
        appendChatMessage("user", turn.user_text || "", { turnId: turn.turn_id });
        appendChatMessage("system", turn.answer || "", { turnId: turn.turn_id });

        if (Array.isArray(turn.follow_up_messages)) {
            turn.follow_up_messages
                .filter((message) => normalizeText(message))
                .forEach((message) => appendChatMessage("system", message, { turnId: turn.turn_id }));
        }
    });

    if (turns.length > 0) {
        selectTurnById(turns[turns.length - 1].turn_id, { suppressSystemState: true });
    }
}

function updateFeatureBreakdown(featureScores) {
    const repetition = Number(featureScores.repetition ?? 0);
    const memory = Number(featureScores.memory ?? 0);
    const timeConfusion = Number(featureScores.time_confusion ?? 0);
    const incoherence = Number(featureScores.incoherence ?? 0);

    if (featureRepetitionValueEl) featureRepetitionValueEl.innerText = repetition;
    if (featureMemoryValueEl) featureMemoryValueEl.innerText = memory;
    if (featureTimeValueEl) featureTimeValueEl.innerText = timeConfusion;
    if (featureIncoherenceValueEl) featureIncoherenceValueEl.innerText = incoherence;

    if (featureRepetitionBarEl) featureRepetitionBarEl.style.width = `${(repetition / 25) * 100}%`;
    if (featureMemoryBarEl) featureMemoryBarEl.style.width = `${(memory / 25) * 100}%`;
    if (featureTimeBarEl) featureTimeBarEl.style.width = `${(timeConfusion / 30) * 100}%`;
    if (featureIncoherenceBarEl) featureIncoherenceBarEl.style.width = `${(incoherence / 20) * 100}%`;

    updateRadarChart(repetition, memory, timeConfusion, incoherence);
}

function updateRecallCard(recall) {
    const statusMap = {
        idle: "대기",
        memorize: "단어 제시",
        ask: "회상 질문"
    };

    if (recallStatusEl) recallStatusEl.innerText = statusMap[recall.status] || "대기";
    if (recallLastResultEl) recallLastResultEl.innerText = recall.last_result || "없음";

    if (recallPromptEl) {
        if (recall.prompt) {
            recallPromptEl.innerText = recall.prompt;
        } else {
            recallPromptEl.innerText = "아직 진행 중인 기억 테스트가 없습니다.";
        }
    }
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
        true
    );
}

function revealSummaryNumbers(data) {
    const averageScore = Number(data.average_score ?? 0);
    const recentAverageScore = Number(data.recent_average_score ?? averageScore);
    const latestScore = scoreHistory.length > 0
        ? scoreHistory[scoreHistory.length - 1].score
        : 0;
    const scoreIncluded = isScoreIncluded(data);
    const confidenceValue = scoreIncluded
        ? calculateConfidenceValue(data.feature_scores || {}, data.score ?? 0)
        : 0;

    if (avgScoreEl) {
        animateNumber(avgScoreEl, extractNumber(avgScoreEl.innerText), averageScore, 700, false, 1);
    }
    if (recentAvgScoreEl) {
        animateNumber(recentAvgScoreEl, extractNumber(recentAvgScoreEl.innerText), recentAverageScore, 700, false, 1);
    }
    if (latestScoreEl) {
        animateNumber(latestScoreEl, extractNumber(latestScoreEl.innerText), latestScore, 700, false);
    }
    if (gaugeScoreEl) {
        animateNumber(gaugeScoreEl, extractNumber(gaugeScoreEl.innerText), Math.round(recentAverageScore), 700, false);
    }
    if (analysisScoreEl) {
        if (scoreIncluded) {
            animateNumber(analysisScoreEl, extractNumber(analysisScoreEl.innerText), Number(data.score ?? 0), 750, false);
        } else {
            analysisScoreEl.innerText = "-";
        }
    }
    if (confidenceScoreEl) {
        if (scoreIncluded) {
            animateNumber(confidenceScoreEl, extractNumber(confidenceScoreEl.innerText), confidenceValue, 750, true);
        } else {
            confidenceScoreEl.innerText = "-";
        }
    }
}

function revealAnalysisWithCountUp(data) {
    if (analysisRevealTimeout) {
        clearTimeout(analysisRevealTimeout);
    }

    analysisRevealTimeout = setTimeout(() => {
        setAnalysisThinking(false);
        setAnalysisLoadingState(false);
        revealSummaryNumbers(data);
    }, 140);
}

function renderAll(data) {
    const averageScore = Number(data.average_score ?? 0);
    const recentAverageScore = Number(data.recent_average_score ?? averageScore);
    const latestScore = scoreHistory.length > 0
        ? scoreHistory[scoreHistory.length - 1].score
        : 0;

    updateSummary(averageScore, recentAverageScore, latestScore, data.trend || "데이터 부족");
    updateStatusCard(recentAverageScore);
    updateLineChart(recentAverageScore);
    updateGaugeChart(recentAverageScore);
}

function updateSummary(averageScore, recentAverageScore, latestScore, trend) {
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
            color: "#2fd18b"
        };
    }

    if (score < 40) {
        return {
            text: "낮은 위험",
            desc: "경미한 변화가 보입니다.",
            cssClass: "risk-low",
            color: "#79c9ff"
        };
    }

    if (score < 60) {
        return {
            text: "주의",
            desc: "지속 관찰이 필요합니다.",
            cssClass: "risk-warning",
            color: "#ffb347"
        };
    }

    if (score < 80) {
        return {
            text: "위험",
            desc: "상당한 위험 신호가 있습니다.",
            cssClass: "risk-high",
            color: "#ff7b7b"
        };
    }

    return {
        text: "매우 위험",
        desc: "즉각적인 관찰이 필요합니다.",
        cssClass: "risk-critical",
        color: "#ff4f73"
    };
}

function updateStatusCard(recentAverageScore) {
    const statusCard = document.getElementById("statusCard");
    const riskText = document.getElementById("riskText");
    const riskDescription = document.getElementById("riskDescription");

    if (!statusCard || !riskText || !riskDescription) {
        return;
    }

    const risk = getRiskInfo(recentAverageScore);

    statusCard.classList.remove("risk-safe", "risk-low", "risk-warning", "risk-high", "risk-critical");
    statusCard.classList.add(risk.cssClass);

    riskText.innerText = risk.text;
    riskDescription.innerText = risk.desc;
}

function buildThresholdDataset(value, label) {
    return {
        label: label,
        data: scoreHistory.map(() => value),
        borderColor: value === 30 ? "rgba(255, 179, 71, 0.5)" : "rgba(255, 79, 115, 0.5)",
        borderWidth: 1,
        borderDash: [6, 6],
        pointRadius: 0,
        fill: false
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

    if (scoreChart) {
        scoreChart.destroy();
    }

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
                    fill: false
                },
                buildThresholdDataset(30, "주의 기준"),
                buildThresholdDataset(60, "위험 기준")
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 900,
                easing: "easeOutQuart"
            },
            plugins: {
                legend: {
                    labels: {
                        color: "#d7e3f8"
                    }
                }
            },
            scales: {
                x: {
                    ticks: {
                        color: "#9cb0d3"
                    },
                    grid: {
                        color: "rgba(145, 164, 205, 0.12)"
                    }
                },
                y: {
                    min: 0,
                    max: 100,
                    ticks: {
                        stepSize: 20,
                        color: "#9cb0d3"
                    },
                    grid: {
                        color: "rgba(145, 164, 205, 0.12)"
                    }
                }
            }
        }
    });
}

function updateGaugeChart(recentAverageScore) {
    const canvas = document.getElementById("gaugeChart");
    if (!canvas) {
        return;
    }

    const ctx = canvas.getContext("2d");
    const safeScore = Math.max(0, Math.min(100, recentAverageScore));
    const risk = getRiskInfo(safeScore);

    if (gaugeChart) {
        gaugeChart.destroy();
    }

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
                    cutout: "76%"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                animateRotate: true,
                duration: 900
            },
            plugins: {
                tooltip: {
                    enabled: false
                },
                legend: {
                    display: false
                }
            }
        }
    });
}

function updateRadarChart(repetition, memory, timeConfusion, incoherence) {
    const canvas = document.getElementById("radarChart");
    if (!canvas) {
        return;
    }

    const ctx = canvas.getContext("2d");

    if (radarChart) {
        radarChart.destroy();
    }

    radarChart = new Chart(ctx, {
        type: "radar",
        data: {
            labels: ["질문 반복", "기억 혼란", "시간 혼란", "문장 비논리성"],
            datasets: [
                {
                    label: "언어 특징 점수",
                    data: [repetition, memory, timeConfusion, incoherence],
                    borderColor: "#d7b26d",
                    backgroundColor: "rgba(121, 201, 255, 0.14)",
                    borderWidth: 2,
                    pointBackgroundColor: "#79c9ff"
                }
            ]
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
                        color: "#9cb0d3"
                    },
                    grid: {
                        color: "rgba(145, 164, 205, 0.16)"
                    },
                    angleLines: {
                        color: "rgba(145, 164, 205, 0.16)"
                    },
                    pointLabels: {
                        color: "#dce8ff",
                        font: {
                            size: 12
                        }
                    }
                }
            },
            plugins: {
                legend: {
                    labels: {
                        color: "#d7e3f8"
                    }
                }
            }
        }
    });
}

function animateNumber(element, start, end, duration = 700, isPercent = false, fixed = 0) {
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
    return String(text || "").replace(/\s+/g, " ").trim();
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

async function requestAnswerFirst(recognizedText) {
    const answerUrl = sessionId
        ? `/generate-answer?session_id=${encodeURIComponent(sessionId)}`
        : "/generate-answer";

    const answerResponse = await fetch(answerUrl, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            message: recognizedText
        })
    });

    return answerResponse.json();
}

async function requestAnalysisAfterAnswer(recognizedText, answerText) {
    const analyzeUrl = sessionId
        ? `/analyze-text?session_id=${encodeURIComponent(sessionId)}`
        : "/analyze-text";

    const analyzeResponse = await fetch(analyzeUrl, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            message: recognizedText,
            answer: answerText
        })
    });

    return analyzeResponse.json();
}

function applyAnalysisResult(data) {
    scoreHistory = Array.isArray(data.score_history) ? data.score_history : [];
    turnHistory = Array.isArray(data.turn_history) ? data.turn_history : turnHistory;
    const scoreIncluded = isScoreIncluded(data);

    updateAnalysisCard(data);
    updateFeatureBreakdown(data.feature_scores || {});
    updateRecallCard(data.recall || {});
    renderAll(data);
    revealAnalysisWithCountUp(data);

    if (Array.isArray(data.follow_up_messages)) {
        data.follow_up_messages
            .filter((message) => normalizeText(message))
            .forEach((message) => appendChatMessage("system", message));
    }

    if (data.turn && data.turn.turn_id) {
        const existingTurnIndex = turnHistory.findIndex((item) => item.turn_id === data.turn.turn_id);
        if (existingTurnIndex >= 0) {
            turnHistory[existingTurnIndex] = data.turn;
        } else {
            turnHistory.push(data.turn);
        }
        renderTurnHistory(turnHistory);
    }

    if ((scoreIncluded && (data.score ?? 0) >= 60) || (data.recent_average_score ?? 0) >= 60) {
        const warningText = scoreIncluded
            ? `현재 점수 ${data.score ?? 0}점, 최근 5회 평균 ${data.recent_average_score ?? 0}점으로 위험 구간에 해당합니다.`
            : `이번 분석은 점수 통계에서 제외되었고, 최근 5회 평균 ${data.recent_average_score ?? 0}점이 위험 구간에 해당합니다.`;
        showWarningPopup(warningText);
    }
}

async function handleRecognizedTextFlow(recognizedText) {
    appendChatMessage("user", recognizedText);
    appendLoadingMessage("응답 생성 중...");
    setProcessState("stt", "음성 인식이 마무리되었고, 응답은 LLM에서 생성 중입니다.");
    setSystemState("응답 생성 중...");
    setThinkingMessage("응답을 LLM이 생성 중...");

    const answerData = await requestAnswerFirst(recognizedText);

    if (answerData.error) {
        removeLoadingMessage();
        appendChatMessage("system", answerData.error);
        setSystemState("오류 발생");
        setProcessError("응답 생성 중 문제가 발생했습니다.");
        setAnalysisThinking(false);
        setAnalysisLoadingState(false);
        return;
    }

    if (answerData.session_id) {
        sessionId = answerData.session_id;
        localStorage.setItem("session_id", sessionId);
    }

    removeLoadingMessage();
    appendChatMessage("system", answerData.answer || "");

    setProcessState("answer", "응답을 먼저 보여주고, 위험도 분석은 다음 단계로 진행합니다.");
    setSystemState("위험도 분석 중...");
    setThinkingMessage("분석용 LLM으로 언어 특징과 점수를 계산 중...");

    const data = await requestAnalysisAfterAnswer(recognizedText, answerData.answer || "");

    if (data.error) {
        appendChatMessage("system", data.error);
        setSystemState("오류 발생");
        setProcessError("위험도 분석 중 문제가 발생했습니다.");
        setAnalysisThinking(false);
        setAnalysisLoadingState(false);
        return;
    }

    if (data.session_id) {
        sessionId = data.session_id;
        localStorage.setItem("session_id", sessionId);
    }

    setProcessState("analysis", "카드와 차트에서 분석 결과를 반영 중...");
    applyAnalysisResult(data);
    setProcessState("render", "답변과 분석 표시가 모두 완료되었습니다.");
    setSystemState("분석 완료");
    setThinkingMessage("AI 언어 패턴 분석 중...");
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        mediaRecorder = new MediaRecorder(stream);

        mediaRecorder.ondataavailable = function (event) {
            audioChunks.push(event.data);
        };

        mediaRecorder.onstop = async function () {
            setRecordingState(false);

            try {
                setProcessState("capture", "음성 데이터를 서버로 보내고 있습니다.");
                setSystemState("음성 처리 시작");
                setThinkingMessage("AI 분석 준비 중...");
                setAnalysisThinking(true);
                setAnalysisLoadingState(true);

                const audioBlob = new Blob(audioChunks, { type: "audio/wav" });
                const formData = new FormData();
                formData.append("audio", audioBlob, "recording.wav");

                const transcribeUrl = sessionId
                    ? `/transcribe-audio?session_id=${encodeURIComponent(sessionId)}`
                    : "/transcribe-audio";

                const sttResponse = await fetch(transcribeUrl, {
                    method: "POST",
                    body: formData
                });

                const sttData = await sttResponse.json();

                if (sttData.error) {
                    appendChatMessage("system", sttData.error);
                    setSystemState("?ㅻ쪟 諛쒖깮");
                    setProcessError("?뚯꽦 ?몄떇 以?臾몄젣媛 諛쒖깮?덉뒿?덈떎.");
                    setAnalysisThinking(false);
                    setAnalysisLoadingState(false);
                    return;
                }

                if (sttData.session_id) {
                    sessionId = sttData.session_id;
                    localStorage.setItem("session_id", sessionId);
                }

                const recognizedText = normalizeText(sttData.user_speech || "");

                if (!recognizedText) {
                    appendChatMessage("system", "?뚯꽦 ?몄떇 寃곌낵媛 ?놁뒿?덈떎. ?ㅼ떆 ?뱀쓬??二쇱꽭??");
                    setSystemState("?뚯꽦 ?몄떇 ?ㅽ뙣");
                    setProcessError("?몄떇??臾몄옄媛 ?놁뼱 ?ㅼ쓬 ?④퀎濡??ㅽ뻾?븷 ???놁뒿?덈떎.");
                    setAnalysisThinking(false);
                    setAnalysisLoadingState(false);
                    return;
                }

                await handleRecognizedTextFlow(recognizedText);
            } catch (error) {
                console.error(error);
                removeLoadingMessage();
                appendChatMessage("system", "?ㅻ쪟媛 諛쒖깮?덉뒿?덈떎. ?ㅼ떆 ?쒕룄?댁＜?몄슂.");
                setSystemState("?ㅻ쪟 諛쒖깮");
                setProcessError("?쟾泥?泥섎━ ?좏젙?먯꽌 ?덈쇅媛 諛쒖깮?덉뒿?덈떎.");
                setAnalysisThinking(false);
                setAnalysisLoadingState(false);
            } finally {
                audioChunks = [];
            }
        };

        mediaRecorder.start();
        setRecordingState(true);
        resetProcessState("녹음을 시작했습니다.");
        setProcessState("capture", "사용자 음성을 수집하고 있습니다.");
    } catch (error) {
        console.error(error);
        alert("留덉씠???묎렐???ㅽ뙣?덉뒿?덈떎.");
    }
}
