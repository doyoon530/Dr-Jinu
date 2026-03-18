let mediaRecorder = null;
let audioChunks = [];

let sessionId = localStorage.getItem("session_id") || null;
let llmMode = localStorage.getItem("llm_mode") || "local";
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
const analysisStateBadgeEl = document.getElementById("analysisStateBadge");
const analysisEmptyHintEl = document.getElementById("analysisEmptyHint");

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
const analysisRoleOrder = ["repetition", "memory", "time_confusion", "incoherence"];
const analysisRoleLabels = {
    repetition: "질문 반복",
    memory: "기억 혼란",
    time_confusion: "시간 / 상황 혼란",
    incoherence: "문장 비논리성"
};

function setVoiceLevel(level = 0.06) {
    const normalizedLevel = Math.max(0.06, Math.min(1, Number(level) || 0.06));
    document.documentElement.style.setProperty("--voice-level", normalizedLevel.toFixed(3));
    document.documentElement.style.setProperty("--voice-core-opacity", (0.1 + (normalizedLevel * 0.14)).toFixed(3));
    document.documentElement.style.setProperty("--voice-halo-opacity", (0.06 + (normalizedLevel * 0.08)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-opacity", (0.08 + (normalizedLevel * 0.16)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-back-scale", (0.94 + (normalizedLevel * 0.14)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-back-peak", (1 + (normalizedLevel * 0.22)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-mid-scale", (1 + (normalizedLevel * 0.18)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-mid-peak", (1.06 + (normalizedLevel * 0.28)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-front-scale", (1.02 + (normalizedLevel * 0.22)).toFixed(3));
    document.documentElement.style.setProperty("--voice-wave-front-peak", (1.08 + (normalizedLevel * 0.34)).toFixed(3));
}

document.addEventListener("DOMContentLoaded", async () => {
    setVoiceLevel(0.06);
    bindEvents();
    updateRecordToggleButton();
    resetProcessState("대기 중입니다. 녹음을 시작하면 음성 입력을 기다립니다.");
    await loadLlmProviderStatus();
    await loadScoreHistory();
});

function bindEvents() {
    if (startButton) startButton.onclick = toggleRecording;
    if (resetButton) resetButton.onclick = resetHistory;
    if (llmModeLocalButton) llmModeLocalButton.onclick = () => setLlmMode("local");
    if (llmModeApiButton) llmModeApiButton.onclick = () => setLlmMode("api");
    if (closeWarningPopupButton) closeWarningPopupButton.onclick = hideWarningPopup;
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
        created_at: Number(pendingTurn?.created_at || Date.now())
    };
    const existingIndex = pendingTurns.findIndex((turn) => turn.client_turn_id === normalizedTurn.client_turn_id);

    if (existingIndex >= 0) {
        pendingTurns[existingIndex] = {
            ...pendingTurns[existingIndex],
            ...normalizedTurn
        };
        return pendingTurns[existingIndex];
    }

    pendingTurns.push(normalizedTurn);
    pendingTurns.sort((left, right) => Number(left.created_at || 0) - Number(right.created_at || 0));
    return normalizedTurn;
}

function updatePendingTurn(clientTurnId, updates = {}) {
    const existingTurn = pendingTurns.find((turn) => turn.client_turn_id === clientTurnId);
    if (!existingTurn) {
        return null;
    }

    Object.assign(existingTurn, updates, { is_pending: true });
    return existingTurn;
}

function removePendingTurn(clientTurnId) {
    pendingTurns = pendingTurns.filter((turn) => turn.client_turn_id !== clientTurnId);
}

function getPendingTurnBadge(turn) {
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
        turn_id: turn.client_turn_id
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
    const shouldShowLoading = Boolean(isAnalysisWorkerRunning || analysisTaskQueue.length > 0);
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

    isAnalysisWorkerRunning = true;
    syncBackgroundAnalysisState();

    try {
        while (analysisTaskQueue.length > 0) {
            await waitForAnswerIdle();
            const task = analysisTaskQueue.shift();
            if (!task) {
                continue;
            }

            await runAnalysisTask(task);
        }
    } finally {
        isAnalysisWorkerRunning = false;
        syncBackgroundAnalysisState();
    }
}

async function runAnalysisTask(task) {
    const roleResults = {};
    const totalRoles = analysisRoleOrder.length;

    updatePendingTurn(task.clientTurnId, { pending_status: "analyzing", pending_error_message: "" });
    renderConversationHistory({ preserveAnalysisCard: true });
    setAnalysisThinking(true);
    setAnalysisLoadingState(true);

    try {
        setProcessState("analysis", `"${task.questionPreview}"에 대한 점수 분석을 순서대로 진행하고 있습니다.`);
        setSystemState("위험도 분석 진행 중");
        setThinkingMessage("답변은 먼저 표시했고, 역할별 점수 분석만 백그라운드에서 이어서 처리하고 있습니다.");

        for (let index = 0; index < totalRoles; index += 1) {
            await waitForAnswerIdle();

            const role = analysisRoleOrder[index];
            const roleLabel = analysisRoleLabels[role] || "세부 분석";

            setProcessState("analysis", `${roleLabel} 점수를 계산하고 있습니다. (${index + 1}/${totalRoles})`);
            setSystemState(`${roleLabel} 분석 중`);
            setThinkingMessage(`${roleLabel}에 대한 점수와 근거를 정리하고 있습니다.`);

            const roleData = await requestRoleAnalysis(task.recognizedText, role, task.llmProvider);

            if (roleData?.session_id) {
                sessionId = roleData.session_id;
                localStorage.setItem("session_id", sessionId);
            }

            if (roleData?.error) {
                throw new Error(roleData.error);
            }

            roleResults[role] = {
                score: Number(roleData?.score ?? 0),
                reason: normalizeText(roleData?.reason || "")
            };

            applyProgressiveAnalysisPreview(roleResults, role, index + 1, totalRoles);
            applyProgressiveSummaryPreview(roleResults);
        }

        await waitForAnswerIdle();

        setProcessState("analysis", "세부 역할 점수를 모두 계산했고, 최종 점수와 추세를 반영하고 있습니다.");
        setSystemState("최종 결과 반영 중");
        setThinkingMessage("역할별 점수를 합산해 최종 판단과 누적 통계를 반영하고 있습니다.");

        const data = await requestFinalizeAnalysis(
            task.recognizedText,
            task.answerText,
            roleResults,
            task.llmProvider
        );

        if (data?.session_id) {
            sessionId = data.session_id;
            localStorage.setItem("session_id", sessionId);
        }

        if (data?.error) {
            throw new Error(data.error);
        }

        applyAnalysisResult(data, { finalizedClientTurnId: task.clientTurnId });
        setProcessState("render", "답변, 분석 카드, 추세 차트, 턴 기록까지 모두 최신 결과로 갱신했습니다.");
        setSystemState("분석 완료");
        setThinkingMessage("가장 최근 대화 기준의 분석 결과를 확인하고 있습니다.");
    } catch (error) {
        console.error(error);
        updatePendingTurn(task.clientTurnId, {
            pending_status: "failed",
            pending_error_message: error instanceof Error ? error.message : String(error || "")
        });
        renderConversationHistory({ preserveAnalysisCard: true });
        appendChatMessage("system", "점수 분석 중 오류가 발생했습니다. 답변은 유지하고 다음 질문은 계속 진행할 수 있습니다.");
        setSystemState("분석 일부 실패");
        setProcessError(`"${task.questionPreview}" 대화의 점수 분석을 끝까지 반영하지 못했습니다.`);
    } finally {
        syncBackgroundAnalysisState();
    }
}

function setRecordButtonBusyState(isBusy, label = "답변 생성 중...") {
    isAnswerPending = Boolean(isBusy);
    recordButtonBusyLabel = isAnswerPending ? String(label || "답변 생성 중...") : "";
    updateRecordToggleButton();
}

function updateRecordToggleButton() {
    if (!startButton) {
        return;
    }

    const isCurrentlyRecording = Boolean(mediaRecorder && mediaRecorder.state !== "inactive");

    startButton.classList.remove("primary-btn", "secondary-btn", "danger-btn", "is-recording", "is-processing");

    if (isAnswerPending) {
        startButton.innerText = recordButtonBusyLabel || "답변 생성 중...";
        startButton.disabled = true;
        startButton.classList.add("secondary-btn", "is-processing");
        return;
    }

    if (isCurrentlyRecording) {
        startButton.innerText = "녹음 중지";
        startButton.disabled = false;
        startButton.classList.add("danger-btn", "is-recording");
        return;
    }

    startButton.innerText = "녹음 시작";
    startButton.disabled = false;
    startButton.classList.add("primary-btn");
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

function getLlmProviderMeta(mode = llmMode) {
    if (!llmProviderStatus) {
        return null;
    }

    return llmProviderStatus[normalizeLlmMode(mode)] || null;
}

function getLlmModeLabel(mode = llmMode) {
    return normalizeLlmMode(mode) === "api" ? "외부 API" : "로컬 모델";
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
        llmModeLocalButton.classList.toggle("is-active", normalizedMode === "local");
        llmModeLocalButton.disabled = !localReady;
    }

    if (llmModeApiButton) {
        llmModeApiButton.classList.toggle("is-active", normalizedMode === "api");
        llmModeApiButton.disabled = !apiReady;
    }

    if (llmModeStatusEl) {
        llmModeStatusEl.innerText = normalizedMode === "api" ? "외부 API 모드" : "로컬 모드";
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
                    : "로컬 모델 파일을 찾지 못했습니다. MODEL_PATH 설정을 확인해 주세요."
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

    return true;
}

function renderChatEmptyState() {
    if (!chatWindow) {
        return;
    }

    chatWindow.innerHTML = "";

    const emptyState = document.createElement("div");
    emptyState.className = "chat-empty-state";
    emptyState.id = "chatEmptyState";
    emptyState.innerHTML = `
        <div class="chat-empty-kicker">Ready For Analysis</div>
        <h4>아직 대화 기록이 없습니다.</h4>
        <p>녹음을 시작하면 답변과 위험도 분석이 이곳에 차례대로 표시됩니다.</p>
        <p>대화가 쌓이면 메시지를 클릭해 해당 시점의 분석 결과를 다시 볼 수 있습니다.</p>
    `;

    chatWindow.appendChild(emptyState);
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
        const timeoutId = window.setTimeout(() => {
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
        }, Math.max(0, delay));

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
        processDetailEl.innerText = detail || "오류가 발생했습니다.";
    }
}

function resetProcessState(detail = "대기 중입니다.") {
    processSteps.forEach((element) => {
        element.classList.remove("is-active", "is-complete", "is-error");
    });

    if (processDetailEl) {
        processDetailEl.innerText = detail;
    }
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
        pendingTurns = [];
        analysisTaskQueue = [];
        isAnalysisWorkerRunning = false;
        selectedTurnId = null;
        setRecordButtonBusyState(false);
        syncBackgroundAnalysisState();
        if (chatWindow) {
            chatWindow.innerHTML = "";
        }

        renderChatEmptyState();
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
        const nextLevel = Math.min(1, 0.08 + (rms * 6.2));
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

        if (data.session_id) {
            sessionId = data.session_id;
            localStorage.setItem("session_id", sessionId);
        }

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
            risk_level: "Normal",
            trend: "데이터 부족",
            score_history: []
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
        analysisStateBadgeEl.classList.remove("is-idle", "is-complete", "is-warning", "is-excluded");
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

    if (numericScore < 20) return "Normal";
    if (numericScore < 40) return "Low Risk";
    if (numericScore < 60) return "Moderate Risk";
    if (numericScore < 80) return "High Risk";
    return "Very High Risk";
}

function buildProgressiveAnalysisPreview(roleResults) {
    const featureScores = {
        repetition: Number(roleResults.repetition?.score ?? 0),
        memory: Number(roleResults.memory?.score ?? 0),
        time_confusion: Number(roleResults.time_confusion?.score ?? 0),
        incoherence: Number(roleResults.incoherence?.score ?? 0)
    };
    const score = featureScores.repetition
        + featureScores.memory
        + featureScores.time_confusion
        + featureScores.incoherence;
    const reason = analysisRoleOrder
        .map((role) => normalizeText(roleResults[role]?.reason || ""))
        .filter(Boolean)
        .join(" ");

    return {
        score,
        featureScores,
        reason: reason || "역할별 분석 결과를 순차적으로 수집하고 있습니다."
    };
}

function applyProgressiveAnalysisPreview(roleResults, currentRole, completedCount, totalCount) {
    const preview = buildProgressiveAnalysisPreview(roleResults);
    const roleLabel = analysisRoleLabels[currentRole] || "세부 분석";

    if (analysisJudgmentEl) analysisJudgmentEl.innerText = "분석 중";
    if (analysisRiskLevelEl) analysisRiskLevelEl.innerText = getRiskLevelFromScore(preview.score);
    if (analysisTrendEl) analysisTrendEl.innerText = "진행 중";
    if (analysisReasonEl) {
        analysisReasonEl.innerText = preview.reason;
    }

    setAnalysisScoreDisplay(preview.score, true);
    updateFeatureBreakdown(preview.featureScores);
    updateConfidence(preview.featureScores, preview.score, true);
    setAnalysisStateBadge(
        `${roleLabel} 반영`,
        "complete",
        `${roleLabel} 점수를 반영했습니다. ${completedCount}/${totalCount} 단계 분석이 완료되었습니다.`
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
}

function updateAnalysisCard(data) {
    const scoreIncluded = isScoreIncluded(data);
    const riskLabel = scoreIncluded ? (data.risk_level || "Normal") : "반영 제외";
    const trendLabel = scoreIncluded ? (data.trend || "데이터 부족") : "반영 제외";
    const reasonText = scoreIncluded
        ? (data.reason || "분석 근거가 없습니다.")
        : (data.excluded_reason || data.reason || "이번 분석은 점수 통계에서 제외되었습니다.");
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
        ? (data.excluded_reason || "이번 분석은 평균과 추세 계산에서 제외되었습니다.")
        : "채팅 기록을 클릭하면 해당 시점의 분석 결과를 다시 볼 수 있습니다.";

    if (analysisJudgmentEl) analysisJudgmentEl.innerText = data.judgment || "없음";
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
    if (analysisReasonEl) analysisReasonEl.innerText = "아직 분석 결과가 없습니다. 녹음을 시작하면 판단, 점수, 근거가 이곳에 표시됩니다.";
    if (confidenceScoreEl) confidenceScoreEl.innerText = "-";
    setAnalysisStateBadge(
        "대기",
        "idle",
        "아직 분석 전입니다. 대화를 시작하면 판단, 점수, 근거가 차례대로 표시됩니다."
    );
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
    revealSummaryNumbers(data);
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
                .forEach((message) => appendChatMessage("system", message, answerOptions));
        }
    });

    const finalizedTurns = turns.filter((turn) => !turn?.is_pending && turn?.turn_id);

    if (finalizedTurns.length === 0) {
        selectedTurnId = null;
        setSelectedMessageState(null);
        return;
    }

    const latestTurnId = finalizedTurns[finalizedTurns.length - 1].turn_id;
    const shouldPreserveSelected = !options.preferLatestTurn
        && selectedTurnId
        && finalizedTurns.some((turn) => turn.turn_id === selectedTurnId);
    const targetTurnId = shouldPreserveSelected ? selectedTurnId : latestTurnId;

    selectTurnById(targetTurnId, { suppressSystemState: true });
}

function buildJsonHeaders(provider = llmMode) {
    return {
        "Content-Type": "application/json",
        "X-LLM-Provider": normalizeLlmMode(provider)
    };
}

async function requestAnswerFirst(recognizedText, provider = llmMode) {
    const normalizedProvider = normalizeLlmMode(provider);
    const answerUrl = sessionId
        ? `/generate-answer?session_id=${encodeURIComponent(sessionId)}`
        : "/generate-answer";

    const answerResponse = await fetch(answerUrl, {
        method: "POST",
        headers: buildJsonHeaders(normalizedProvider),
        body: JSON.stringify({
            message: recognizedText,
            llm_provider: normalizedProvider
        })
    });

    return answerResponse.json();
}

async function requestRoleAnalysis(recognizedText, role, provider = llmMode) {
    const normalizedProvider = normalizeLlmMode(provider);
    const analyzeRoleUrl = sessionId
        ? `/analyze-role?session_id=${encodeURIComponent(sessionId)}`
        : "/analyze-role";

    const roleResponse = await fetch(analyzeRoleUrl, {
        method: "POST",
        headers: buildJsonHeaders(normalizedProvider),
        body: JSON.stringify({
            message: recognizedText,
            role,
            llm_provider: normalizedProvider
        })
    });

    return roleResponse.json();
}

async function requestFinalizeAnalysis(recognizedText, answerText, roleResults, provider = llmMode) {
    const normalizedProvider = normalizeLlmMode(provider);
    const finalizeUrl = sessionId
        ? `/finalize-analysis?session_id=${encodeURIComponent(sessionId)}`
        : "/finalize-analysis";

    const finalizeResponse = await fetch(finalizeUrl, {
        method: "POST",
        headers: buildJsonHeaders(normalizedProvider),
        body: JSON.stringify({
            message: recognizedText,
            answer: answerText,
            role_results: roleResults,
            llm_provider: normalizedProvider
        })
    });

    return finalizeResponse.json();
}

function applyAnalysisResult(data, options = {}) {
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
    updateRecallCard(data.recall || {});
    renderAll(data);
    revealAnalysisWithCountUp(data);

    if (data?.turn && data.turn.turn_id) {
        const existingTurnIndex = turnHistory.findIndex((item) => item.turn_id === data.turn.turn_id);
        if (existingTurnIndex >= 0) {
            turnHistory[existingTurnIndex] = data.turn;
        } else {
            turnHistory.push(data.turn);
        }
    }

    renderConversationHistory({ preferLatestTurn: true, preserveAnalysisCard: true });

    if ((scoreIncluded && (data.score ?? 0) >= 60) || (data.recent_average_score ?? 0) >= 60) {
        const warningText = scoreIncluded
            ? `현재 점수 ${data.score ?? 0}점과 최근 5회 평균 ${data.recent_average_score ?? 0}점으로 위험 구간에 해당합니다.`
            : `이번 분석은 점수 통계에서 제외되었지만 최근 5회 평균 ${data.recent_average_score ?? 0}점이 위험 구간에 해당합니다.`;
        showWarningPopup(warningText);
    }
}

async function handleRecognizedTextFlow(recognizedText) {
    const questionPreview = buildStatusPreview(recognizedText);
    const clientTurnId = createClientTurnId();
    const providerSnapshot = normalizeLlmMode(llmMode);

    appendChatMessage("user", recognizedText);
    appendLoadingMessage("답변 생성 중...");
    setRecordButtonBusyState(true, "답변 생성 중...");
    setProcessState("stt", `음성 인식이 끝났고 "${questionPreview}" 내용을 바탕으로 답변 초안을 준비하고 있습니다.`);
    setSystemState(`${getLlmModeLabel(providerSnapshot)}로 질문 의도 해석 중`);
    setThinkingMessage(`인식된 문장을 정리하고 ${getLlmModeLabel(providerSnapshot)} 기준으로 응답 의도를 파악하고 있습니다.`);

    const stopAnswerNarration = startStatusNarration([
        {
            delay: 900,
            step: "stt",
            detail: `인식 문장을 정리하고, "${questionPreview}"에 대한 답변 초안을 구성하고 있습니다.`,
            system: "답변 초안 정리 중",
            thinking: "짧고 자연스러운 1차 응답이 나오도록 문장을 정돈하고 있습니다."
        },
        {
            delay: 1800,
            step: "answer",
            detail: "답변 문장을 마무리하면서, 이어질 위험도 분석에 사용할 기본 정보를 함께 정리하고 있습니다.",
            system: "답변 문장 정리 중",
            thinking: "답변이 너무 길어지지 않도록 핵심만 정리하고 있습니다."
        }
    ]);

    let answerData;
    try {
        answerData = await requestAnswerFirst(recognizedText, providerSnapshot);
    } finally {
        stopAnswerNarration();
    }

    if (answerData?.session_id) {
        sessionId = answerData.session_id;
        localStorage.setItem("session_id", sessionId);
    }

    if (answerData?.error) {
        removeLoadingMessage();
        setRecordButtonBusyState(false);
        appendChatMessage("system", answerData.error);
        setSystemState("오류 발생");
        setProcessError("답변 생성 단계에서 문제가 발생해 다음 분석 단계로 넘어가지 못했습니다.");
        syncBackgroundAnalysisState();
        return;
    }

    const answerText = normalizeText(answerData?.answer || "") || "응답을 생성하지 못했습니다.";

    removeLoadingMessage();
    upsertPendingTurn({
        client_turn_id: clientTurnId,
        user_text: recognizedText,
        answer: answerText,
        follow_up_messages: [],
        pending_status: "queued",
        llm_provider: providerSnapshot,
        created_at: Date.now()
    });
    renderConversationHistory({ preserveAnalysisCard: true });
    setRecordButtonBusyState(false);

    setAnalysisStateBadge(
        "분석 대기",
        "idle",
        "답변은 먼저 표시했고, 점수 분석은 백그라운드에서 순서대로 이어집니다."
    );
    setProcessState("answer", "답변을 먼저 표시했고, 점수 분석은 백그라운드 큐에 등록했습니다.");
    setSystemState("답변 완료, 분석 대기 중");
    setThinkingMessage("다음 질문 녹음은 바로 이어서 할 수 있고, 점수 분석은 뒤에서 순서대로 진행됩니다.");

    enqueueAnalysisTask({
        clientTurnId,
        recognizedText,
        answerText,
        llmProvider: providerSnapshot,
        questionPreview
    });
}

async function startRecording() {
    try {
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
                setProcessState("capture", "녹음을 종료했고, 음성 데이터를 정리한 뒤 서버로 전송하고 있습니다.");
                setSystemState("음성 전송 중");
                setThinkingMessage("녹음 데이터를 정리하고 음성 인식에 사용할 파일을 준비하고 있습니다.");
                setAnalysisThinking(true);
                setAnalysisLoadingState(true);

                stopCaptureNarration = startStatusNarration([
                    {
                        delay: 800,
                        step: "capture",
                        detail: "음성 길이와 형식을 확인하고, 서버에서 음성 인식을 시작할 준비를 하고 있습니다.",
                        system: "STT 준비 중",
                        thinking: "업로드된 음성에서 발화 문장을 추출하고 있습니다."
                    },
                    {
                        delay: 1700,
                        step: "stt",
                        detail: "서버에서 발화 문장을 추출하고 있으며, 텍스트가 준비되면 곧바로 답변 생성으로 넘어갑니다.",
                        system: "음성 인식 중",
                        thinking: "음성 구간을 문장 단위로 정리하고 텍스트로 변환하고 있습니다."
                    }
                ]);

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
                stopCaptureNarration();

                if (sttData?.error) {
                    setRecordButtonBusyState(false);
                    appendChatMessage("system", sttData.error);
                    setSystemState("오류 발생");
                    setProcessError("음성 인식 단계에서 문제가 발생해 발화 문장을 추출하지 못했습니다.");
                    syncBackgroundAnalysisState();
                    return;
                }

                if (sttData?.session_id) {
                    sessionId = sttData.session_id;
                    localStorage.setItem("session_id", sessionId);
                }

                const recognizedText = normalizeText(sttData?.user_speech || "");

                if (!recognizedText) {
                    setRecordButtonBusyState(false);
                    appendChatMessage("system", "음성 인식 결과가 없습니다. 다시 녹음해 주세요.");
                    setSystemState("음성 인식 실패");
                    setProcessError("인식된 문장이 없어 답변 생성과 위험도 분석을 진행할 수 없습니다.");
                    syncBackgroundAnalysisState();
                    return;
                }

                setProcessState("stt", `음성 인식이 완료되었습니다. 인식 문장: "${buildStatusPreview(recognizedText)}"`);
                setSystemState("인식 문장 확인 완료");
                setThinkingMessage("인식된 문장을 바탕으로 답변 생성을 시작합니다.");
                await handleRecognizedTextFlow(recognizedText);
            } catch (error) {
                console.error(error);
                stopCaptureNarration();
                removeLoadingMessage();
                setRecordButtonBusyState(false);
                appendChatMessage("system", "오류가 발생했습니다. 다시 시도해 주세요.");
                setSystemState("오류 발생");
                setProcessError("음성 전송부터 분석 반영까지 이어지는 처리 과정에서 예외가 발생했습니다.");
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
        setProcessState("capture", "마이크가 연결되었고 사용자의 음성을 실시간으로 수집하고 있습니다.");
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
    const latestScore = scoreHistory.length > 0
        ? scoreHistory[scoreHistory.length - 1].score
        : 0;

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
        return;
    }

    gaugeChart.data.datasets[0].data = [safeScore, 100 - safeScore];
    gaugeChart.data.datasets[0].backgroundColor = [risk.color, "rgba(255, 255, 255, 0.08)"];
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
        return;
    }

    radarChart.data.datasets[0].data = radarData;
    radarChart.update();
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
