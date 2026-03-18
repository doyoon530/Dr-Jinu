import logging
import re
from contextlib import nullcontext
from difflib import SequenceMatcher

from . import runtime
from .common import clamp_score, clamp_subscore, normalize_text, validate_user_text
from .config import (
    ANSWER_STOP_SEQUENCES,
    MAX_ANALYSIS_RETRY,
    REPETITION_SCORE_OPTIONS,
    ROLE_ANALYSIS_META,
    ROLE_ANALYSIS_ORDER,
    ROLE_ANALYSIS_PROMPTS,
    ROLE_ANALYSIS_RETRY_PROMPTS,
    answer_prompt,
    get_analysis_max_tokens,
    get_default_llm_provider,
    is_api_llm_configured,
    normalize_llm_provider,
    normalize_role_key,
    repetition_prompt,
)
from .history_service import (
    build_score_exclusion_reason,
    get_analysis_runtime_state,
    has_meaningful_feature_scores,
    should_include_analysis_score,
)
from .llm_service import (
    get_or_create_answer_chain,
    get_or_create_repetition_chain,
    get_or_create_role_analysis_chains,
    invoke_api_prompt,
)

logger = logging.getLogger(__name__)


def normalize_similarity_text(text: str) -> str:
    normalized = normalize_text(text).lower()
    normalized = re.sub(r"[^0-9a-z가-힣\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def compact_similarity_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_similarity_text(text))


def tokenize_similarity_text(text: str):
    normalized = normalize_similarity_text(text)
    return [token for token in normalized.split() if len(token) >= 2]


def build_char_ngrams(text: str, n: int = 2):
    compact = compact_similarity_text(text)
    if not compact:
        return set()
    if len(compact) < n:
        return {compact}

    return {compact[index : index + n] for index in range(len(compact) - n + 1)}


def calculate_overlap_ratio(left_values, right_values) -> float:
    left_set = set(left_values)
    right_set = set(right_values)

    if not left_set or not right_set:
        return 0.0

    return len(left_set & right_set) / max(len(left_set), len(right_set))


def calculate_question_similarity(
    previous_question: str, current_question: str
) -> dict:
    previous_compact = compact_similarity_text(previous_question)
    current_compact = compact_similarity_text(current_question)

    char_ratio = 0.0
    if previous_compact and current_compact:
        char_ratio = SequenceMatcher(None, previous_compact, current_compact).ratio()

    token_overlap = calculate_overlap_ratio(
        tokenize_similarity_text(previous_question),
        tokenize_similarity_text(current_question),
    )
    ngram_overlap = calculate_overlap_ratio(
        build_char_ngrams(previous_question), build_char_ngrams(current_question)
    )

    return {
        "char_ratio": round(char_ratio, 4),
        "token_overlap": round(token_overlap, 4),
        "ngram_overlap": round(ngram_overlap, 4),
    }


def normalize_repetition_score(score: int) -> int:
    try:
        parsed = int(score)
    except (TypeError, ValueError):
        return 0

    return min(
        REPETITION_SCORE_OPTIONS, key=lambda option: (abs(option - parsed), option)
    )


def trim_reason_question(text: str, max_length: int = 42) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1]}…"


def infer_repetition_score_from_similarity(metrics: dict, is_immediate: bool) -> int:
    char_ratio = float(metrics.get("char_ratio", 0.0))
    token_overlap = float(metrics.get("token_overlap", 0.0))
    ngram_overlap = float(metrics.get("ngram_overlap", 0.0))

    if char_ratio >= 0.9 or (token_overlap >= 0.8 and ngram_overlap >= 0.78):
        return 25 if is_immediate else 20

    if char_ratio >= 0.82 or (token_overlap >= 0.68 and ngram_overlap >= 0.64):
        return 20 if is_immediate else 15

    if char_ratio >= 0.72 or (token_overlap >= 0.54 and ngram_overlap >= 0.5):
        return 8

    return 0


def build_repetition_reason(
    score: int, matched_question: str, is_immediate: bool
) -> str:
    if score <= 0:
        return ""

    reference = trim_reason_question(matched_question)
    if score >= 25:
        return f"직전 질문 '{reference}'과 사실상 같은 의미의 질문이 답변 직후 다시 제시되어 질문 반복 경향이 매우 강하게 관찰됩니다."
    if score >= 20:
        return f"직전 질문 '{reference}'과 현재 질문의 핵심 요청이 거의 같아 질문 반복 경향이 뚜렷하게 관찰됩니다."
    if score >= 15:
        return f"이전 질문 '{reference}'과 현재 질문의 의미가 유사해 같은 질문이 다시 제시된 것으로 볼 수 있습니다."
    return f"이전 질문 '{reference}'과 표현이 일부 겹쳐 질문 반복 가능성이 약하게 관찰됩니다."


def build_repetition_context(previous_turns) -> str:
    if not previous_turns:
        return "이전 사용자 질문 없음"

    context_lines = []
    for index, turn in enumerate(previous_turns, start=1):
        user_text = normalize_text(turn.get("user_text", ""))
        answer_text = normalize_text(turn.get("answer", ""))
        if not user_text:
            continue

        context_lines.append(f"[이전 사용자 질문 {index}] {user_text}")
        if answer_text:
            context_lines.append(f"[당시 AI 답변 {index}] {answer_text}")

    if not context_lines:
        return "이전 사용자 질문 없음"

    return "\n".join(context_lines)


def analyze_repetition_by_similarity(question: str, previous_turns) -> dict:
    normalized_question = normalize_text(question)
    if not normalized_question or not previous_turns:
        return {
            "score": 0,
            "matched_question": "",
            "reason": "",
            "source": "heuristic",
        }

    best_result = {
        "score": 0,
        "matched_question": "",
        "reason": "",
        "source": "heuristic",
    }

    total_turns = len(previous_turns)
    for index, turn in enumerate(previous_turns):
        previous_question = normalize_text(turn.get("user_text", ""))
        if not previous_question:
            continue

        metrics = calculate_question_similarity(previous_question, normalized_question)
        is_immediate = index == total_turns - 1
        score = infer_repetition_score_from_similarity(
            metrics, is_immediate=is_immediate
        )

        if score < best_result["score"]:
            continue

        if score == best_result["score"] and score > 0:
            current_char_ratio = metrics["char_ratio"]
            best_char_ratio = float(best_result.get("char_ratio", 0.0))
            if current_char_ratio <= best_char_ratio and not is_immediate:
                continue

        best_result = {
            "score": score,
            "matched_question": previous_question,
            "reason": build_repetition_reason(score, previous_question, is_immediate),
            "source": "heuristic",
            "char_ratio": metrics["char_ratio"],
        }

    best_result.pop("char_ratio", None)
    return best_result


def parse_repetition_chain_response(response_text: str) -> dict:
    cleaned = str(response_text or "").strip()
    if not cleaned:
        return {
            "score": 0,
            "matched_question": "",
            "reason": "",
            "source": "llm",
        }

    score_match = re.search(r"질문반복점수\s*[:：]?\s*(\d+)", cleaned)
    target_match = re.search(r"반복대상\s*[:：]?\s*(.+?)(?:\n|$)", cleaned)
    reason_match = re.search(r"근거\s*[:：]?\s*(.+)", cleaned, re.DOTALL)

    score = normalize_repetition_score(score_match.group(1) if score_match else 0)
    matched_question = normalize_text(target_match.group(1) if target_match else "")
    reason = normalize_text(reason_match.group(1) if reason_match else "")

    if matched_question in {"없음", "해당 없음", "없습니다"}:
        matched_question = ""

    return {
        "score": score,
        "matched_question": matched_question,
        "reason": reason,
        "source": "llm",
    }


def merge_reason_text(existing_reason: str, extra_reason: str) -> str:
    normalized_existing = normalize_text(existing_reason)
    normalized_extra = normalize_text(extra_reason)

    if not normalized_extra:
        return normalized_existing
    if not normalized_existing:
        return normalized_extra
    if normalized_extra in normalized_existing:
        return normalized_existing

    return f"{normalized_extra} {normalized_existing}"


def detect_repetition_signal(
    question: str, previous_turns, use_llm: bool = True
) -> dict:
    heuristic_result = analyze_repetition_by_similarity(question, previous_turns)
    best_result = dict(heuristic_result)

    if not use_llm or not previous_turns:
        return best_result

    try:
        repetition_chain = get_or_create_repetition_chain()
        with runtime.analysis_llm_lock:
            response = repetition_chain.invoke(
                {
                    "recent_user_questions": build_repetition_context(previous_turns),
                    "question": normalize_text(question),
                }
            )
        llm_result = parse_repetition_chain_response(response.get("text", ""))

        if llm_result["score"] > best_result["score"]:
            best_result = llm_result
        elif llm_result["score"] == best_result["score"]:
            if not best_result.get("matched_question") and llm_result.get(
                "matched_question"
            ):
                best_result["matched_question"] = llm_result["matched_question"]
            if len(llm_result.get("reason", "")) > len(best_result.get("reason", "")):
                best_result["reason"] = llm_result["reason"]
                best_result["source"] = llm_result["source"]
    except Exception as e:
        logger.warning("질문 반복 전용 판별 실패: %s", e, exc_info=True)

    if best_result["score"] > 0 and not best_result.get("reason"):
        matched_question = best_result.get("matched_question") or heuristic_result.get(
            "matched_question", ""
        )
        best_result["reason"] = build_repetition_reason(
            best_result["score"],
            matched_question,
            matched_question == normalize_text(previous_turns[-1].get("user_text", ""))
            if previous_turns
            else False,
        )

    if not best_result.get("matched_question"):
        best_result["matched_question"] = heuristic_result.get("matched_question", "")

    return best_result


def get_default_reason() -> str:
    return (
        "입력 문장에서 충분한 분석 근거를 안정적으로 생성하지 못했습니다. "
        "추가적인 대화 입력을 통해 다시 평가할 필요가 있습니다."
    )


def get_analysis_fallback_text() -> str:
    return (
        "판단: 판단 어려움\n"
        "의심점수: 0\n"
        "질문반복점수: 0\n"
        "기억혼란점수: 0\n"
        "시간혼란점수: 0\n"
        "문장비논리점수: 0\n"
        f"근거: {get_default_reason()}"
    )


def get_feature_analysis_fallback_text() -> str:
    return (
        "기억혼란점수: 0\n"
        "시간혼란점수: 0\n"
        "문장비논리점수: 0\n"
        f"근거: {get_default_reason()}"
    )


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def is_invalid_reason_text(reason: str) -> bool:
    if not reason or not reason.strip():
        return True

    reason = reason.strip()

    invalid_patterns = [
        r"^한\s*문장\s*이상$",
        r"^두\s*문장\s*이상$",
        r"^작성하십시오\.?$",
        r"^근거를\s*추출하지\s*못했습니다\.?$",
        r"^출력\s*형식이\s*불완전합니다\.?$",
    ]

    for pattern in invalid_patterns:
        if re.fullmatch(pattern, reason):
            return True

    blocked_phrases = ["한 문장 이상", "두 문장 이상", "작성하십시오", "출력 형식"]

    return any(phrase in reason for phrase in blocked_phrases)


def looks_like_score_listing(reason: str) -> bool:
    if not reason:
        return False

    keywords = [
        "질문반복점수",
        "질문 반복 점수",
        "기억혼란점수",
        "기억 혼란 점수",
        "시간혼란점수",
        "시간/상황 혼란점수",
        "문장비논리점수",
        "문장 비논리성점수",
    ]
    keyword_hits = sum(1 for keyword in keywords if keyword in reason)

    return keyword_hits >= 2 or ("->" in reason and keyword_hits >= 1)


def build_reason_from_scores(scores: dict) -> str:
    repetition = int(scores.get("repetition", 0))
    memory = int(scores.get("memory", 0))
    time_confusion = int(scores.get("time_confusion", 0))
    incoherence = int(scores.get("incoherence", 0))
    total = clamp_score(repetition + memory + time_confusion + incoherence)

    observations = []

    if repetition >= 15:
        observations.append(
            "같은 의미의 질문이 반복되어 질문 반복 경향이 비교적 뚜렷하게 관찰됩니다."
        )
    elif repetition > 0:
        observations.append("질문 표현의 일부 반복이 관찰됩니다.")

    if memory >= 15:
        observations.append(
            "최근에 제시된 정보를 바로 떠올리지 못하거나 기억이 흐려지는 표현이 나타납니다."
        )
    elif memory > 0:
        observations.append(
            "기억을 떠올리는 데 어려움을 보이는 표현이 일부 확인됩니다."
        )

    if time_confusion >= 15:
        observations.append("시간이나 현재 상황을 혼동하는 표현이 나타납니다.")
    elif time_confusion > 0:
        observations.append("시간 또는 상황 인식의 경미한 혼란이 관찰됩니다.")

    if incoherence >= 10:
        observations.append("문장 전개가 다소 비논리적으로 이어지는 구간이 확인됩니다.")
    elif incoherence > 0:
        observations.append("일부 문장에서 논리 연결이 약해 보입니다.")

    if not observations:
        return (
            "현재 입력에서는 질문 반복, 기억 혼란, 시간 혼란, 문장 비논리성이 뚜렷하게 관찰되지 않습니다. "
            "다만 단일 대화만으로는 변동이 있을 수 있어 이후 대화와 함께 종합적으로 보는 것이 좋습니다."
        )

    summary = " ".join(observations[:2])
    if total >= 20:
        closing = "이 특징들이 누적되어 인지 위험도 점수가 상승한 것으로 해석됩니다."
    else:
        closing = "다만 현재 단계에서는 전반적 위험도가 높다고 단정할 수준은 아닙니다."

    return f"{summary} {closing}"


def normalize_reason_text(reason: str, scores: dict) -> str:
    cleaned = str(reason or "").strip()
    cleaned = re.sub(r"\r\n?", "\n", cleaned)

    if looks_like_score_listing(cleaned):
        observations = []
        for raw_line in cleaned.splitlines():
            line = re.sub(r"[*_`#]+", "", raw_line).strip(" -\t")
            if not line:
                continue

            if "->" in line:
                observation = line.split("->", 1)[1].strip()
            elif "=>" in line:
                observation = line.split("=>", 1)[1].strip()
            else:
                match = re.search(r":\s*(.+)$", line)
                if not match:
                    continue
                observation = match.group(1).strip()

            observation = re.sub(r"^\d+\s*", "", observation).strip()
            normalized_observation = observation.rstrip(". ").strip()
            if not normalized_observation or normalized_observation in {
                "없음",
                "해당 없음",
                "없습니다",
            }:
                continue

            if not re.search(r"[.!?。]$", observation):
                observation = f"{observation}."

            if observation not in observations:
                observations.append(observation)

        if len(observations) >= 2:
            return " ".join(observations[:3])

        return build_reason_from_scores(scores)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip()

    if is_invalid_reason_text(cleaned) or len(split_sentences(cleaned)) < 2:
        return build_reason_from_scores(scores)

    return cleaned


def parse_analysis_scores(text: str) -> dict:
    normalized = str(text or "")
    normalized = re.sub(r"[*_`#>\-]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)

    def extract_int(patterns, default: int = 0) -> int:
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return default

    repetition = clamp_subscore(
        extract_int(
            [
                r"질문\s*반복\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"질문반복점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"질문\s*반복(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
            ]
        ),
        25,
    )
    memory = clamp_subscore(
        extract_int(
            [
                r"기억\s*혼란\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"기억혼란점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"기억\s*혼란(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
            ]
        ),
        25,
    )
    time_confusion = clamp_subscore(
        extract_int(
            [
                r"시간\s*/?\s*상황\s*혼란(?:\s*점수)?(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"시간\s*혼란\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"시간혼란점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
            ]
        ),
        30,
    )
    incoherence = clamp_subscore(
        extract_int(
            [
                r"문장\s*비논리성(?:\s*점수)?(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"문장비논리점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"문장\s*비논리성\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
            ]
        ),
        20,
    )

    subtotal = repetition + memory + time_confusion + incoherence
    declared_total = clamp_score(
        extract_int(
            [
                r"의심\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"의심점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
            ]
        )
    )
    total = subtotal if subtotal > 0 else declared_total

    return {
        "repetition": repetition,
        "memory": memory,
        "time_confusion": time_confusion,
        "incoherence": incoherence,
        "total": clamp_score(total),
    }


def infer_judgment_from_score(score: int) -> str:
    if score < 20:
        return "정상"
    return "의심"


def is_analysis_format_complete(text: str) -> bool:
    if not text or not text.strip():
        return False

    required_patterns = [
        r"판단:\s*(.+)",
        r"의심점수:\s*(\d+)",
        r"질문반복점수:\s*(\d+)",
        r"기억혼란점수:\s*(\d+)",
        r"시간혼란점수:\s*(\d+)",
        r"문장비논리점수:\s*(\d+)",
        r"근거:\s*(.+)",
    ]

    for pattern in required_patterns:
        if not re.search(pattern, text, re.DOTALL):
            return False

    reason_match = re.search(r"근거:\s*(.+)", text, re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else ""

    if is_invalid_reason_text(reason):
        return False

    if len(split_sentences(reason)) < 2:
        return False

    parsed = parse_analysis_scores(text)
    total_match = re.search(r"의심점수:\s*(\d+)", text)
    if not total_match:
        return False

    declared_total = clamp_score(int(total_match.group(1)))
    if declared_total != parsed["total"]:
        return False

    return True


def force_analysis_format(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return get_analysis_fallback_text()

    cleaned = raw_text.strip()

    judgment = "판단 어려움"
    reason_text = ""
    scores = parse_analysis_scores(cleaned)

    judgment_match = re.search(r"판단:\s*(.+)", cleaned)
    reason_match = re.search(r"근거:\s*(.+)", cleaned, re.DOTALL)

    if judgment_match:
        judgment = judgment_match.group(1).strip()

    if reason_match:
        reason_text = reason_match.group(1).strip()

    if judgment not in {"정상", "의심", "판단 어려움"}:
        judgment = infer_judgment_from_score(scores["total"])

    if (judgment == "정상" and scores["total"] >= 20) or (
        judgment == "의심" and scores["total"] < 20
    ):
        judgment = infer_judgment_from_score(scores["total"])

    reason_text = normalize_reason_text(reason_text, scores)

    total = scores["total"]

    return (
        f"판단: {judgment}\n"
        f"의심점수: {total}\n"
        f"질문반복점수: {scores['repetition']}\n"
        f"기억혼란점수: {scores['memory']}\n"
        f"시간혼란점수: {scores['time_confusion']}\n"
        f"문장비논리점수: {scores['incoherence']}\n"
        f"근거: {reason_text}"
    )


def extract_analysis_fields(response_text: str) -> dict:
    if not response_text:
        return {
            "judgment": "판단 어려움",
            "score": 0,
            "reason": get_default_reason(),
            "feature_scores": {
                "repetition": 0,
                "memory": 0,
                "time_confusion": 0,
                "incoherence": 0,
            },
        }

    parsed = parse_analysis_scores(response_text)
    reason_match = re.search(r"근거:\s*(.+)", response_text, re.DOTALL)
    judgment_match = re.search(r"판단:\s*(.+)", response_text)

    reason = reason_match.group(1).strip() if reason_match else get_default_reason()
    judgment = (
        judgment_match.group(1).strip()
        if judgment_match
        else infer_judgment_from_score(parsed["total"])
    )

    reason = normalize_reason_text(reason, parsed)

    if judgment not in {"정상", "의심", "판단 어려움"}:
        judgment = infer_judgment_from_score(parsed["total"])

    if (judgment == "정상" and parsed["total"] >= 20) or (
        judgment == "의심" and parsed["total"] < 20
    ):
        judgment = infer_judgment_from_score(parsed["total"])

    return {
        "judgment": judgment,
        "score": parsed["total"],
        "reason": reason,
        "feature_scores": {
            "repetition": parsed["repetition"],
            "memory": parsed["memory"],
            "time_confusion": parsed["time_confusion"],
            "incoherence": parsed["incoherence"],
        },
    }


def parse_feature_analysis_scores(text: str) -> dict:
    parsed = parse_analysis_scores(text)
    return {
        "memory": parsed["memory"],
        "time_confusion": parsed["time_confusion"],
        "incoherence": parsed["incoherence"],
    }


def is_feature_analysis_complete(text: str) -> bool:
    if not text or not text.strip():
        return False

    required_patterns = [
        r"기억혼란점수:\s*(\d+)",
        r"시간혼란점수:\s*(\d+)",
        r"문장비논리점수:\s*(\d+)",
        r"근거:\s*(.+)",
    ]

    for pattern in required_patterns:
        if not re.search(pattern, text, re.DOTALL):
            return False

    reason_match = re.search(r"근거:\s*(.+)", text, re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else ""

    if is_invalid_reason_text(reason):
        return False

    if len(split_sentences(reason)) < 2:
        return False

    return True


def force_feature_analysis_format(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return get_feature_analysis_fallback_text()

    cleaned = raw_text.strip()
    scores = parse_feature_analysis_scores(cleaned)
    reason_match = re.search(r"근거:\s*(.+)", cleaned, re.DOTALL)
    reason_text = reason_match.group(1).strip() if reason_match else ""

    scores_for_reason = {
        "repetition": 0,
        "memory": scores["memory"],
        "time_confusion": scores["time_confusion"],
        "incoherence": scores["incoherence"],
    }
    reason_text = normalize_reason_text(reason_text, scores_for_reason)

    return (
        f"기억혼란점수: {scores['memory']}\n"
        f"시간혼란점수: {scores['time_confusion']}\n"
        f"문장비논리점수: {scores['incoherence']}\n"
        f"근거: {reason_text}"
    )


def extract_feature_analysis_fields(response_text: str) -> dict:
    if not response_text:
        return {
            "reason": get_default_reason(),
            "feature_scores": {
                "repetition": 0,
                "memory": 0,
                "time_confusion": 0,
                "incoherence": 0,
            },
        }

    parsed = parse_feature_analysis_scores(response_text)
    reason_match = re.search(r"근거:\s*(.+)", response_text, re.DOTALL)
    scores_for_reason = {
        "repetition": 0,
        "memory": parsed["memory"],
        "time_confusion": parsed["time_confusion"],
        "incoherence": parsed["incoherence"],
    }
    reason = reason_match.group(1).strip() if reason_match else get_default_reason()
    reason = normalize_reason_text(reason, scores_for_reason)

    return {
        "reason": reason,
        "feature_scores": scores_for_reason,
    }


def get_role_analysis_fallback_text(role_key: str) -> str:
    meta = ROLE_ANALYSIS_META[role_key]
    return f"{meta['score_label']}: 0\n근거: {get_default_reason()}"


def parse_single_role_analysis(role_key: str, response_text: str) -> dict:
    meta = ROLE_ANALYSIS_META[role_key]
    normalized = str(response_text or "")
    normalized = re.sub(r"\r\n?", "\n", normalized)
    score_match = re.search(rf"{meta['score_label']}\s*[:：]?\s*(\d+)", normalized)
    reason_match = re.search(r"근거\s*[:：]?\s*(.+)", normalized, re.DOTALL)

    score = clamp_subscore(
        int(score_match.group(1)) if score_match else 0, meta["max_score"]
    )
    reason = reason_match.group(1).strip() if reason_match else get_default_reason()
    reason = normalize_reason_text(
        reason,
        {
            "repetition": 0,
            "memory": score if role_key == "memory" else 0,
            "time_confusion": score if role_key == "time_confusion" else 0,
            "incoherence": score if role_key == "incoherence" else 0,
        },
    )

    return {
        "role": role_key,
        "score": score,
        "reason": reason,
    }


def is_single_role_analysis_complete(role_key: str, text: str) -> bool:
    if not text or not text.strip():
        return False

    meta = ROLE_ANALYSIS_META[role_key]
    if not re.search(rf"{meta['score_label']}\s*[:：]?\s*(\d+)", text):
        return False
    reason_match = re.search(r"근거\s*[:：]?\s*(.+)", text, re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else ""

    if is_invalid_reason_text(reason):
        return False

    return len(split_sentences(reason)) >= 2


def force_single_role_analysis_format(role_key: str, raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return get_role_analysis_fallback_text(role_key)

    parsed = parse_single_role_analysis(role_key, raw_text)
    meta = ROLE_ANALYSIS_META[role_key]
    return f"{meta['score_label']}: {parsed['score']}\n근거: {parsed['reason']}"


def generate_single_role_analysis(
    role_key: str,
    question: str,
    session_id: str | None = None,
    conversation_context: str | None = None,
    provider: str | None = None,
) -> dict:
    question = normalize_text(question)
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())
    if normalized_provider == "api" and not is_api_llm_configured():
        raise RuntimeError(
            "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 설정해주세요."
        )
    if not validate_user_text(question):
        return {
            "role": role_key,
            "score": 0,
            "reason": get_default_reason(),
        }

    if conversation_context is None:
        conversation_context = get_analysis_runtime_state(session_id)[
            "analysis_context"
        ]
    previous_response = ""
    primary_chain = None
    retry_chain = None
    if normalized_provider == "local":
        primary_chain, retry_chain = get_or_create_role_analysis_chains(role_key)
    local_lock = (
        runtime.analysis_llm_lock if normalized_provider == "local" else nullcontext()
    )

    for attempt in range(MAX_ANALYSIS_RETRY):
        try:
            if normalized_provider == "api":
                prompt = (
                    ROLE_ANALYSIS_PROMPTS[role_key]
                    if attempt == 0
                    else ROLE_ANALYSIS_RETRY_PROMPTS[role_key]
                )
                payload = {
                    "conversation_context": conversation_context,
                    "question": question,
                }
                if attempt > 0:
                    payload["previous_response"] = previous_response

                response = invoke_api_prompt(
                    prompt,
                    payload,
                    model_kind="analysis",
                    temperature=0.0,
                    max_tokens=get_analysis_max_tokens(),
                )
            elif attempt == 0:
                with local_lock:
                    response = primary_chain.invoke(
                        {
                            "conversation_context": conversation_context,
                            "question": question,
                        }
                    )
            else:
                with local_lock:
                    response = retry_chain.invoke(
                        {
                            "conversation_context": conversation_context,
                            "question": question,
                            "previous_response": previous_response,
                        }
                    )

            raw_text = response.get("text", "").strip()
            previous_response = raw_text
            if is_single_role_analysis_complete(role_key, raw_text):
                return parse_single_role_analysis(
                    role_key, force_single_role_analysis_format(role_key, raw_text)
                )
        except Exception as e:
            logger.warning(
                "%s 분석 재시도 %s 실패: %s",
                role_key,
                attempt + 1,
                e,
                exc_info=True,
            )

    if previous_response:
        return parse_single_role_analysis(
            role_key, force_single_role_analysis_format(role_key, previous_response)
        )

    if normalized_provider == "api":
        raise RuntimeError(
            f"{ROLE_ANALYSIS_META[role_key]['title']} API 분석에 실패했습니다."
        )

    return parse_single_role_analysis(
        role_key, get_role_analysis_fallback_text(role_key)
    )


def generate_repetition_role_analysis(
    question: str,
    session_id: str | None = None,
    previous_turns=None,
    provider: str | None = None,
) -> dict:
    question = normalize_text(question)
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())
    if normalized_provider == "api" and not is_api_llm_configured():
        raise RuntimeError(
            "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 설정해주세요."
        )
    if previous_turns is None:
        previous_turns = get_analysis_runtime_state(session_id)["previous_turns"]

    if not validate_user_text(question) or not previous_turns:
        return {
            "role": "repetition",
            "score": 0,
            "reason": "",
        }

    try:
        if normalized_provider == "api":
            response = invoke_api_prompt(
                repetition_prompt,
                {
                    "recent_user_questions": build_repetition_context(previous_turns),
                    "question": question,
                },
                model_kind="analysis",
                temperature=0.0,
                max_tokens=220,
            )
        else:
            repetition_chain = get_or_create_repetition_chain()
            with runtime.analysis_llm_lock:
                response = repetition_chain.invoke(
                    {
                        "recent_user_questions": build_repetition_context(
                            previous_turns
                        ),
                        "question": question,
                    }
                )
        parsed = parse_repetition_chain_response(response.get("text", ""))
        return {
            "role": "repetition",
            "score": clamp_subscore(int(parsed.get("score", 0)), 25),
            "reason": normalize_text(parsed.get("reason", "")),
        }
    except Exception as e:
        logger.warning("repetition 분석 실패: %s", e, exc_info=True)
        if normalized_provider == "api":
            raise RuntimeError("질문 반복 API 분석에 실패했습니다.") from e
        return {
            "role": "repetition",
            "score": 0,
            "reason": "",
        }


def generate_role_analysis_result(
    role_key: str,
    question: str,
    session_id: str | None = None,
    provider: str | None = None,
) -> dict:
    normalized_role = normalize_role_key(role_key)
    if normalized_role == "repetition":
        return generate_repetition_role_analysis(
            question, session_id=session_id, provider=provider
        )
    if normalized_role in ROLE_ANALYSIS_META:
        return generate_single_role_analysis(
            normalized_role, question, session_id=session_id, provider=provider
        )
    raise ValueError(f"Unsupported analysis role: {role_key}")


def build_fields_from_role_results(role_results: dict) -> dict:
    feature_scores = {
        "repetition": clamp_subscore(
            int(role_results.get("repetition", {}).get("score", 0)), 25
        ),
        "memory": clamp_subscore(
            int(role_results.get("memory", {}).get("score", 0)), 25
        ),
        "time_confusion": clamp_subscore(
            int(role_results.get("time_confusion", {}).get("score", 0)), 30
        ),
        "incoherence": clamp_subscore(
            int(role_results.get("incoherence", {}).get("score", 0)), 20
        ),
    }

    ordered_reasons = [
        normalize_text(role_results.get("repetition", {}).get("reason", "")),
        normalize_text(role_results.get("memory", {}).get("reason", "")),
        normalize_text(role_results.get("time_confusion", {}).get("reason", "")),
        normalize_text(role_results.get("incoherence", {}).get("reason", "")),
    ]

    reason = ""
    for item in ordered_reasons:
        reason = merge_reason_text(reason, item)

    total_score = clamp_score(sum(feature_scores.values()))
    judgment = infer_judgment_from_score(total_score)

    if not has_meaningful_feature_scores(feature_scores) and not reason:
        judgment = "판단 어려움"
        reason = get_default_reason()
    elif not reason:
        reason = build_reason_from_scores(feature_scores)

    fields = {
        "judgment": judgment,
        "score": total_score,
        "reason": reason,
        "feature_scores": feature_scores,
    }
    fields["score_included"] = should_include_analysis_score(
        fields["judgment"],
        fields["score"],
        fields["feature_scores"],
    )
    fields["excluded_reason"] = build_score_exclusion_reason(
        fields["judgment"],
        fields["score"],
        fields["reason"],
        fields["feature_scores"],
    )
    return fields


def normalize_role_results_payload(raw_results) -> dict:
    normalized = {}
    source = raw_results if isinstance(raw_results, dict) else {}

    for role_key in ROLE_ANALYSIS_ORDER:
        role_payload = source.get(role_key) if isinstance(source, dict) else None
        if not isinstance(role_payload, dict):
            role_payload = {}
        normalized[role_key] = {
            "role": role_key,
            "score": int(role_payload.get("score", 0) or 0),
            "reason": normalize_text(role_payload.get("reason", "")),
        }

    return normalized


def build_short_input_result() -> dict:
    reason = (
        "대화 내용이 너무 짧아 언어적 특징을 분석하기 어렵습니다. "
        "조금 더 구체적인 입력이 필요합니다."
    )
    excluded_reason = "입력이 너무 짧아 이번 대화는 점수 통계에서 제외했습니다."

    return {
        "full_text": (
            "답변: 질문 내용이 너무 짧아 답변하기 어렵습니다.\n\n"
            "판단: 판단 어려움\n"
            "의심점수: 반영 제외\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}\n"
            f"점수반영: {excluded_reason}"
        ),
        "answer": "질문 내용이 너무 짧아 답변하기 어렵습니다.",
        "judgment": "판단 어려움",
        "score": 0,
        "reason": reason,
        "feature_scores": {
            "repetition": 0,
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0,
        },
        "score_included": False,
        "excluded_reason": excluded_reason,
    }


def build_error_result() -> dict:
    reason = "응답 생성 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
    excluded_reason = "분석 중 오류가 발생해 이번 대화는 점수 통계에서 제외했습니다."

    return {
        "full_text": (
            "답변: 응답 생성 중 문제가 발생했습니다.\n\n"
            "판단: 판단 어려움\n"
            "의심점수: 반영 제외\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}\n"
            f"점수반영: {excluded_reason}"
        ),
        "answer": "응답 생성 중 문제가 발생했습니다.",
        "judgment": "판단 어려움",
        "score": 0,
        "reason": reason,
        "feature_scores": {
            "repetition": 0,
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0,
        },
        "score_included": False,
        "excluded_reason": excluded_reason,
    }


def build_full_text(answer_text: str, fields: dict) -> str:
    score_text = fields["score"] if fields.get("score_included", True) else "반영 제외"
    exclusion_line = ""
    if fields.get("score_included", True) is False and fields.get("excluded_reason"):
        exclusion_line = f"\n점수반영: {fields['excluded_reason']}"

    return (
        f"답변: {answer_text}\n\n"
        f"판단: {fields['judgment']}\n"
        f"의심점수: {score_text}\n"
        f"질문반복점수: {fields['feature_scores']['repetition']}\n"
        f"기억혼란점수: {fields['feature_scores']['memory']}\n"
        f"시간혼란점수: {fields['feature_scores']['time_confusion']}\n"
        f"문장비논리점수: {fields['feature_scores']['incoherence']}\n"
        f"근거: {fields['reason']}"
        f"{exclusion_line}"
    )


def sanitize_answer_text(raw_text: str) -> str:
    text = normalize_text(raw_text)
    if not text:
        return ""

    leaked_prefixes = [
        "라고 했을 때, 가장 적절한 답변은 무엇일까요?",
        "가장 적절한 답변은 무엇일까요?",
        "위 발화에 대해 사용자에게 바로 보여줄 최종 답변만 작성하세요.",
        "위 발화에 대해 사용자에게 바로 보여줄 최종 답변만 작성하세요",
        "최종 답변만 작성하세요.",
        "최종 답변만 작성하세요",
    ]
    for leaked_prefix in leaked_prefixes:
        if leaked_prefix in text:
            text = text.split(leaked_prefix)[-1].strip()

    text = re.sub(
        r"(?is)^\s*(?:질문|사용자 질문|사용자 발화|user)\s*:\s*.*?(?=(?:assistant|ai|답변)\s*:)",
        "",
        text,
    ).strip()

    role_parts = re.split(r"(?is)\b(?:assistant|ai|답변)\s*:\s*", text)
    if len(role_parts) > 1:
        text = role_parts[-1].strip()

    text = re.sub(r"(?is)^\s*(?:assistant|ai|답변)\s*:\s*", "", text).strip()
    text = re.sub(
        r"(?is)^\s*(?:라고 했을 때,\s*)?(?:가장 적절한 답변은 무엇일까요\?\s*)+",
        "",
        text,
    ).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_answer_result(question: str, provider: str | None = None) -> dict:
    question = normalize_text(question)
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())

    if not validate_user_text(question):
        result = build_short_input_result()
        result["llm_provider"] = normalized_provider
        return result

    try:
        if normalized_provider == "api":
            answer_response = invoke_api_prompt(
                answer_prompt,
                {"question": question},
                model_kind="answer",
                temperature=0.2,
                max_tokens=256,
                stop=ANSWER_STOP_SEQUENCES,
            )
        else:
            answer_response = get_or_create_answer_chain().invoke(
                {"question": question}
            )

        answer_text = sanitize_answer_text(answer_response.get("text", ""))

        if not answer_text:
            answer_text = "질문에 대한 답변을 생성하지 못했습니다."

        return {
            "answer": answer_text,
            "is_answer_only": True,
            "llm_provider": normalized_provider,
        }

    except Exception as e:
        logger.warning("LLM 응답 생성 실패: %s", e, exc_info=True)
        error_result = build_error_result()
        if normalized_provider == "api" and not is_api_llm_configured():
            error_result["answer"] = (
                "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 입력해주세요."
            )
            error_result["reason"] = (
                "외부 API 설정이 없어 API 모드로 응답을 생성하지 못했습니다."
            )
            error_result["excluded_reason"] = (
                "외부 API 설정이 완료되지 않아 이번 대화는 점수 통계에서 제외했습니다."
            )
        error_result["llm_provider"] = normalized_provider
        return error_result


def generate_analysis_result(
    question: str, session_id: str | None = None, provider: str | None = None
) -> dict:
    question = normalize_text(question)
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())

    if not validate_user_text(question):
        short_input_result = build_short_input_result()
        short_input_result["llm_provider"] = normalized_provider
        return {
            "judgment": short_input_result["judgment"],
            "score": short_input_result["score"],
            "reason": short_input_result["reason"],
            "feature_scores": short_input_result["feature_scores"],
            "score_included": short_input_result["score_included"],
            "excluded_reason": short_input_result["excluded_reason"],
            "llm_provider": normalized_provider,
        }

    runtime_state = get_analysis_runtime_state(session_id)
    role_results = {
        "repetition": generate_repetition_role_analysis(
            question,
            session_id=session_id,
            previous_turns=runtime_state["previous_turns"],
            provider=normalized_provider,
        ),
        "memory": generate_single_role_analysis(
            "memory",
            question,
            session_id=session_id,
            conversation_context=runtime_state["analysis_context"],
            provider=normalized_provider,
        ),
        "time_confusion": generate_single_role_analysis(
            "time_confusion",
            question,
            session_id=session_id,
            conversation_context=runtime_state["analysis_context"],
            provider=normalized_provider,
        ),
        "incoherence": generate_single_role_analysis(
            "incoherence",
            question,
            session_id=session_id,
            conversation_context=runtime_state["analysis_context"],
            provider=normalized_provider,
        ),
    }
    fields = build_fields_from_role_results(role_results)
    fields["llm_provider"] = normalized_provider
    return fields


def get_response_from_llama(
    question: str, session_id: str | None = None, provider: str | None = None
) -> dict:
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())
    answer_result = generate_answer_result(question, provider=normalized_provider)

    if all(
        key in answer_result
        for key in ["full_text", "judgment", "score", "reason", "feature_scores"]
    ):
        answer_result["llm_provider"] = normalized_provider
        return answer_result

    fields = generate_analysis_result(
        question, session_id=session_id, provider=normalized_provider
    )
    full_text = build_full_text(answer_result["answer"], fields)

    return {
        "full_text": full_text,
        "answer": answer_result["answer"],
        "judgment": fields["judgment"],
        "score": fields["score"],
        "reason": fields["reason"],
        "feature_scores": fields["feature_scores"],
        "score_included": fields.get("score_included", True),
        "excluded_reason": fields.get("excluded_reason", ""),
        "llm_provider": normalized_provider,
    }
