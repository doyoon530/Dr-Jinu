import random
import uuid
from collections import deque
from datetime import datetime

from flask import jsonify, request

from . import runtime
from .common import clamp_score, normalize_text
from .config import (
    ANALYSIS_CONTEXT_TURNS,
    MAX_HISTORY_TURNS,
    MAX_SCORE_HISTORY,
    RECALL_WORDS,
    RECENT_WINDOW,
    REPETITION_CONTEXT_TURNS,
    normalize_llm_provider,
)
from .history_repair_service import (
    RISK_EXCLUDED,
    calculate_confidence_from_feature_scores,
    calculate_trend_from_score_values,
    get_risk_level_from_score,
    repair_turn_history_state,
)

NO_PREVIOUS_CONTEXT = "?? ?? ??"
RECALL_RESULT_NONE = "??"
RECALL_RESULT_CORRECT = "??"
RECALL_RESULT_INCORRECT = "??"
JUDGMENT_UNCERTAIN = "?? ???"
JUDGMENT_NORMAL = "??"
JUDGMENT_SUSPECTED = "??"



def build_default_recall_state() -> dict:
    return {
        "status": "idle",
        "target_word": "",
        "prompt": "",
        "last_result": RECALL_RESULT_NONE,
        "introduced_turn": 0,
    }


def get_or_create_session_id() -> str:
    session_id = request.headers.get("X-Session-Id") or request.args.get("session_id")

    if not session_id:
        session_id = str(uuid.uuid4())

    runtime.conversation_store.setdefault(session_id, deque(maxlen=MAX_HISTORY_TURNS * 2))
    runtime.score_store.setdefault(session_id, deque(maxlen=MAX_SCORE_HISTORY))
    runtime.turn_store.setdefault(session_id, deque(maxlen=MAX_SCORE_HISTORY))
    runtime.session_generation_store.setdefault(session_id, 0)
    runtime.recall_store.setdefault(session_id, build_default_recall_state())
    return session_id


def reset_session(session_id: str) -> None:
    runtime.conversation_store[session_id] = deque(maxlen=MAX_HISTORY_TURNS * 2)
    runtime.score_store[session_id] = deque(maxlen=MAX_SCORE_HISTORY)
    runtime.turn_store[session_id] = deque(maxlen=MAX_SCORE_HISTORY)
    runtime.analysis_runtime_cache.pop(session_id, None)
    runtime.repair_cache.pop(session_id, None)
    runtime.recall_store[session_id] = build_default_recall_state()


def normalize_analysis_generation(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0

    return max(0, parsed)


def get_analysis_generation(session_id: str | None) -> int:
    if not session_id:
        return 0

    return runtime.session_generation_store.setdefault(session_id, 0)


def bump_analysis_generation(session_id: str | None) -> int:
    if not session_id:
        return 0

    next_generation = get_analysis_generation(session_id) + 1
    runtime.session_generation_store[session_id] = next_generation
    return next_generation


def get_requested_analysis_generation(payload=None):
    raw_value = None

    if isinstance(payload, dict):
        raw_value = payload.get("analysis_generation")

    if raw_value in {None, ""}:
        raw_value = request.headers.get("X-Analysis-Generation")

    if raw_value in {None, ""}:
        return None

    return normalize_analysis_generation(raw_value)


def is_current_analysis_generation(
    session_id: str | None, requested_generation
) -> bool:
    if requested_generation is None:
        return True

    return get_analysis_generation(session_id) == normalize_analysis_generation(
        requested_generation
    )


def add_to_history(session_id: str, role: str, content: str) -> None:
    if session_id not in runtime.conversation_store:
        runtime.conversation_store[session_id] = deque(maxlen=MAX_HISTORY_TURNS * 2)
    runtime.conversation_store[session_id].append({"role": role, "content": content})


def build_analysis_context_from_turns(
    turns, max_turns: int = ANALYSIS_CONTEXT_TURNS
) -> str:
    if not turns:
        return "이전 대화 없음"

    recent_turns = list(turns)[-max_turns:]
    context_lines = []

    for index, turn in enumerate(recent_turns, start=1):
        user_text = normalize_text(turn.get("user_text", ""))
        answer_text = normalize_text(turn.get("answer", ""))

        if not user_text and not answer_text:
            continue

        context_lines.append(f"[이전 대화 {index}]")
        if user_text:
            context_lines.append(f"사용자: {user_text}")
        if answer_text:
            context_lines.append(f"AI: {answer_text}")

    return "\n".join(context_lines) if context_lines else "이전 대화 없음"


def build_analysis_context(
    session_id: str | None, max_turns: int = ANALYSIS_CONTEXT_TURNS
) -> str:
    if not session_id:
        return "이전 대화 없음"

    turns = runtime.turn_store.get(session_id, [])
    return build_analysis_context_from_turns(turns, max_turns=max_turns)


def get_recent_user_turns(turns, max_turns: int = REPETITION_CONTEXT_TURNS):
    if not turns:
        return []

    recent_turns = []
    for turn in turns:
        user_text = normalize_text(turn.get("user_text", ""))
        if not user_text:
            continue

        recent_turns.append(
            {"user_text": user_text, "answer": normalize_text(turn.get("answer", ""))}
        )

    return recent_turns[-max_turns:] if len(recent_turns) > max_turns else recent_turns


def get_analysis_runtime_state(session_id: str | None) -> dict:
    if not session_id:
        return {
            "analysis_context": "이전 대화 없음",
            "previous_turns": [],
            "turn_count": 0,
        }

    turns = list(runtime.turn_store.get(session_id, []))
    turn_count = len(turns)
    cached = runtime.analysis_runtime_cache.get(session_id)
    if cached and cached.get("turn_count") == turn_count:
        return cached

    runtime_state = {
        "analysis_context": build_analysis_context_from_turns(turns),
        "previous_turns": get_recent_user_turns(turns),
        "turn_count": turn_count,
    }
    runtime.analysis_runtime_cache[session_id] = runtime_state
    return runtime_state


def add_turn_history(
    session_id: str,
    user_text: str,
    answer: str,
    judgment: str,
    score: int,
    reason: str,
    feature_scores: dict,
    follow_up_messages=None,
    score_included: bool = True,
    excluded_reason: str = "",
    llm_provider: str = "local",
) -> dict:
    if session_id not in runtime.turn_store:
        runtime.turn_store[session_id] = deque(maxlen=MAX_SCORE_HISTORY)

    turn = {
        "turn_id": str(uuid.uuid4()),
        "time": datetime.now().strftime("%H:%M:%S"),
        "user_text": user_text,
        "answer": answer,
        "judgment": judgment,
        "score": clamp_score(score),
        "reason": reason,
        "feature_scores": {
            "repetition": int(feature_scores.get("repetition", 0)),
            "memory": int(feature_scores.get("memory", 0)),
            "time_confusion": int(feature_scores.get("time_confusion", 0)),
            "incoherence": int(feature_scores.get("incoherence", 0)),
        },
        "follow_up_messages": follow_up_messages or [],
        "score_included": bool(score_included),
        "excluded_reason": str(excluded_reason or ""),
        "llm_provider": normalize_llm_provider(llm_provider),
    }

    runtime.turn_store[session_id].append(turn)
    # Invalidate repair cache — a new turn means next repair must re-run from scratch
    runtime.repair_cache.pop(session_id, None)
    turn["average_score"] = get_average_score(session_id)
    turn["recent_average_score"] = get_recent_average_score(session_id)

    if turn["score_included"]:
        turn["risk_level"] = get_risk_level_from_score(turn["score"])
        turn["trend"] = get_score_trend(session_id)
        turn["confidence"] = calculate_confidence_from_feature_scores(
            turn["feature_scores"], turn["score"]
        )
    else:
        turn["risk_level"] = RISK_EXCLUDED
        turn["trend"] = RISK_EXCLUDED
        turn["confidence"] = 0

    return turn


def repair_session_analysis_history(session_id: str) -> None:
    turns = runtime.turn_store.get(session_id, [])
    if not turns:
        return

    turn_count = len(turns)
    # Skip the O(n) repair if it already ran for this exact turn count.
    # repair is called 3+ times per add_turn_history (average, recent_avg, trend).
    if runtime.repair_cache.get(session_id) == turn_count:
        return

    _, repaired_score_history = repair_turn_history_state(
        turns,
        runtime.score_store.get(session_id, []),
        recent_window=RECENT_WINDOW,
    )
    runtime.score_store[session_id] = deque(
        repaired_score_history[-MAX_SCORE_HISTORY:],
        maxlen=MAX_SCORE_HISTORY,
    )
    runtime.repair_cache[session_id] = turn_count


def get_turn_history(session_id: str):
    repair_session_analysis_history(session_id)
    return list(runtime.turn_store.get(session_id, []))


def get_user_turn_count(session_id: str) -> int:
    history = runtime.conversation_store.get(session_id, [])
    return sum(1 for item in history if item["role"] == "user")


def add_score_history(session_id: str, score: int) -> None:
    if session_id not in runtime.score_store:
        runtime.score_store[session_id] = deque(maxlen=MAX_SCORE_HISTORY)
    runtime.score_store[session_id].append(
        {"score": clamp_score(score), "time": datetime.now().strftime("%H:%M:%S")}
    )


def get_score_history(session_id: str):
    repair_session_analysis_history(session_id)
    return list(runtime.score_store.get(session_id, []))


def get_average_score(session_id: str) -> float:
    history = get_score_history(session_id)
    if not history:
        return 0.0

    avg = sum(item["score"] for item in history) / len(history)
    return round(avg, 1)


def get_recent_average_score(session_id: str, window: int = RECENT_WINDOW) -> float:
    history = get_score_history(session_id)
    if not history:
        return 0.0

    recent = history[-window:]
    avg = sum(item["score"] for item in recent) / len(recent)
    return round(avg, 1)


def get_score_trend(session_id: str, window: int = RECENT_WINDOW) -> str:
    history = get_score_history(session_id)
    return calculate_trend_from_score_values(
        [item["score"] for item in history], window
    )


def get_recall_state(session_id: str) -> dict:
    return runtime.recall_store.setdefault(session_id, build_default_recall_state())


def evaluate_recall_answer(session_id: str, user_input: str) -> str:
    state = get_recall_state(session_id)
    if state["status"] != "ask":
        return ""

    target_word = state["target_word"].strip()
    normalized_input = normalize_text(user_input)

    if target_word and target_word in normalized_input:
        feedback = f"Recall Test 결과: 정답입니다. 기억 단어는 '{target_word}'였습니다."
        state["last_result"] = RECALL_RESULT_CORRECT
    else:
        feedback = f"Recall Test 결과: 오답입니다. 기억 단어는 '{target_word}'였습니다."
        state["last_result"] = RECALL_RESULT_INCORRECT

    state["status"] = "idle"
    state["prompt"] = ""
    state["target_word"] = ""
    state["introduced_turn"] = 0
    return feedback


def maybe_advance_recall_test(session_id: str) -> str:
    state = get_recall_state(session_id)
    user_turn_count = get_user_turn_count(session_id)

    if state["status"] == "idle" and user_turn_count >= 3 and user_turn_count % 3 == 0:
        target_word = random.choice(RECALL_WORDS)
        state["status"] = "memorize"
        state["target_word"] = target_word
        state["introduced_turn"] = user_turn_count
        state["prompt"] = (
            f"Recall Test: 지금 제시하는 기억 단어는 '{target_word}'입니다. "
            "다음 대화에서 이 단어를 기억해 보세요."
        )
        return state["prompt"]

    if (
        state["status"] == "memorize"
        and user_turn_count == state["introduced_turn"] + 1
    ):
        state["status"] = "ask"
        state["prompt"] = "Recall Test: 제가 조금 전에 제시한 기억 단어가 무엇이었나요?"
        return state["prompt"]

    return ""


def serialize_recall_state(session_id: str) -> dict:
    state = get_recall_state(session_id)
    return {
        "status": state["status"],
        "prompt": state["prompt"],
        "last_result": state["last_result"],
        "target_word": state["target_word"] if state["status"] == "memorize" else "",
    }


def build_chat_response(
    session_id: str,
    user_speech: str,
    sys_response: str,
    answer: str,
    judgment: str,
    score: int,
    reason: str,
    feature_scores: dict,
    follow_up_messages=None,
    turn=None,
    score_included: bool = True,
    excluded_reason: str = "",
    llm_provider: str = "local",
):
    metrics = build_analysis_metrics_payload(session_id)

    return jsonify(
        {
            "session_id": session_id,
            "analysis_generation": get_analysis_generation(session_id),
            "user_speech": user_speech,
            "sys_response": sys_response,
            "answer": answer,
            "follow_up_messages": follow_up_messages or [],
            "judgment": judgment,
            "score": clamp_score(score),
            "reason": reason,
            "feature_scores": feature_scores,
            "llm_provider": normalize_llm_provider(llm_provider),
            "score_included": bool(score_included),
            "excluded_reason": str(excluded_reason or ""),
            "average_score": metrics["average_score"],
            "recent_average_score": metrics["recent_average_score"],
            "risk_level": metrics["risk_level"],
            "trend": metrics["trend"],
            # Incremental: send only the newest score entry instead of the full array.
            # The full arrays are served by GET /score-history (page load).
            # The frontend appends new_score_entry to its local scoreHistory,
            # and already has incremental turn-merge logic for data.turn.
            "new_score_entry": (
                {"score": clamp_score(turn["score"]), "time": turn.get("time", "")}
                if score_included and turn is not None
                else None
            ),
            "turn": turn,
            "recall": metrics["recall"],
        }
    )


def build_analysis_metrics_payload(session_id: str) -> dict:
    recent_average_score = get_recent_average_score(session_id)
    return {
        "average_score": get_average_score(session_id),
        "recent_average_score": recent_average_score,
        "risk_level": get_risk_level_from_score(recent_average_score),
        "trend": get_score_trend(session_id),
        "score_history": get_score_history(session_id),
        "turn_history": get_turn_history(session_id),
        "recall": serialize_recall_state(session_id),
    }


def build_analysis_detail_payload(
    session_id: str,
    *,
    stt_result: str,
    answer: str,
    judgment: str,
    reason: str,
    score_total: int,
    feature_scores: dict,
    llm_provider: str = "local",
    turn=None,
    score_included: bool = True,
    excluded_reason: str = "",
) -> dict:
    metrics = build_analysis_metrics_payload(session_id)
    return {
        "session_id": session_id,
        "analysis_generation": get_analysis_generation(session_id),
        "stt_result": stt_result,
        "answer": answer,
        "judgment": judgment,
        "reason": reason,
        "score_total": clamp_score(score_total),
        "session_score": metrics["average_score"],
        "recent_session_score": metrics["recent_average_score"],
        "score_repeat": int(feature_scores.get("repetition", 0)),
        "score_memory": int(feature_scores.get("memory", 0)),
        "score_time_confusion": int(feature_scores.get("time_confusion", 0)),
        "score_incoherence": int(feature_scores.get("incoherence", 0)),
        "risk_level": metrics["risk_level"],
        "trend": metrics["trend"],
        "score_history": metrics["score_history"],
        "turn_history": metrics["turn_history"],
        "recall": metrics["recall"],
        "turn": turn,
        "llm_provider": normalize_llm_provider(llm_provider),
        "score_included": bool(score_included),
        "excluded_reason": str(excluded_reason or ""),
    }


def build_empty_analysis_detail_payload(
    session_id: str,
    *,
    llm_provider: str = "local",
    reason: str,
    excluded_reason: str,
) -> dict:
    return build_analysis_detail_payload(
        session_id,
        stt_result="",
        answer="",
        judgment=JUDGMENT_UNCERTAIN,
        reason=reason,
        score_total=0,
        feature_scores={
            "repetition": 0,
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0,
        },
        llm_provider=llm_provider,
        turn=None,
        score_included=False,
        excluded_reason=excluded_reason,
    )


def finalize_analysis_response(
    session_id: str,
    user_input: str,
    answer_text: str,
    fields: dict,
    llm_provider: str = "local",
):
    from .analysis_service import build_full_text

    recall_feedback = evaluate_recall_answer(session_id, user_input)
    add_to_history(session_id, "user", user_input)
    follow_up_messages = []

    if recall_feedback:
        follow_up_messages.append(recall_feedback)

    recall_prompt = maybe_advance_recall_test(session_id)
    if recall_prompt:
        follow_up_messages.append(recall_prompt)

    full_text = build_full_text(answer_text, fields)
    history_full_text = full_text
    if follow_up_messages:
        history_full_text = f"{history_full_text}\n\n" + "\n\n".join(follow_up_messages)

    add_to_history(session_id, "assistant", history_full_text)
    if fields.get("score_included", True):
        add_score_history(session_id, fields["score"])

    turn = add_turn_history(
        session_id=session_id,
        user_text=user_input,
        answer=answer_text,
        judgment=fields["judgment"],
        score=fields["score"],
        reason=fields["reason"],
        feature_scores=fields["feature_scores"],
        follow_up_messages=follow_up_messages,
        score_included=fields.get("score_included", True),
        excluded_reason=fields.get("excluded_reason", ""),
        llm_provider=llm_provider,
    )
    runtime.analysis_runtime_cache.pop(session_id, None)

    return build_chat_response(
        session_id=session_id,
        user_speech=user_input,
        sys_response=full_text,
        answer=answer_text,
        judgment=fields["judgment"],
        score=fields["score"],
        reason=fields["reason"],
        feature_scores=fields["feature_scores"],
        follow_up_messages=follow_up_messages,
        turn=turn,
        score_included=fields.get("score_included", True),
        excluded_reason=fields.get("excluded_reason", ""),
        llm_provider=llm_provider,
    )
