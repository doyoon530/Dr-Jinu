import re

from .common import clamp_score, clamp_subscore, normalize_text
from .config import REPETITION_SCORE_OPTIONS, ROLE_ANALYSIS_META


JUDGMENT_UNCERTAIN = "판단 어려움"
JUDGMENT_NORMAL = "정상"
JUDGMENT_SUSPECTED = "의심"


def _normalize_repetition_score(score: int) -> int:
    try:
        parsed = int(score)
    except (TypeError, ValueError):
        return 0

    return min(
        REPETITION_SCORE_OPTIONS, key=lambda option: (abs(option - parsed), option)
    )


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

    score = _normalize_repetition_score(score_match.group(1) if score_match else 0)
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


def get_default_reason() -> str:
    return (
        "입력 문장에서 충분한 분석 근거를 안정적으로 생성하지 못했습니다. "
        "조금 더 구체적인 대화나 추가 입력을 바탕으로 다시 평가할 필요가 있습니다."
    )


def get_analysis_fallback_text() -> str:
    return (
        "판단: 판단 어려움\n"
        "최종점수: 0\n"
        "질문반복점수: 0\n"
        "기억혼란점수: 0\n"
        "시간혼란점수: 0\n"
        "문장비논리점수: 0\n"
        f"근거: {get_default_reason()}"
    )


def split_sentences(text: str):
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+|(?<=요\.)\s+", text.strip())
        if part.strip()
    ]


def is_invalid_reason_text(reason: str) -> bool:
    if not reason or not reason.strip():
        return True

    normalized = reason.strip()
    invalid_patterns = [
        r"^두\s*문장\s*이상$",
        r"^2\s*문장\s*이상$",
        r"^작성하세요\.?$",
        r"^근거를\s*추출하지\s*못했습니다\.?$",
        r"^출력\s*형식이\s*불완전합니다\.?$",
    ]

    if any(re.fullmatch(pattern, normalized) for pattern in invalid_patterns):
        return True

    blocked_phrases = [
        "두 문장 이상",
        "2문장 이상",
        "작성하세요",
        "출력 형식",
    ]
    return any(phrase in normalized for phrase in blocked_phrases)


def looks_like_score_listing(reason: str) -> bool:
    if not reason:
        return False

    keywords = [
        "질문반복점수",
        "질문 반복 점수",
        "기억혼란점수",
        "기억 혼란 점수",
        "시간혼란점수",
        "시간/상황 혼란 점수",
        "문장비논리점수",
        "문장 비논리성 점수",
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
            "같은 의미의 질문이 반복되어 질문 반복 경향이 비교적 분명하게 관찰됩니다."
        )
    elif repetition > 0:
        observations.append("질문 표현이 일부 반복되는 경향이 관찰됩니다.")

    if memory >= 15:
        observations.append(
            "최근에 제시된 정보나 바로 앞 대화 내용을 유지하는 데 어려움이 드러납니다."
        )
    elif memory > 0:
        observations.append("기억을 떠올리는 과정에서 약간의 어려움이 보입니다.")

    if time_confusion >= 15:
        observations.append("시간이나 현재 상황을 파악하는 데 혼란이 드러납니다.")
    elif time_confusion > 0:
        observations.append("시간 또는 상황 인식에서 경미한 흔들림이 관찰됩니다.")

    if incoherence >= 10:
        observations.append("문장 연결이 다소 불안정하고 논리 흐름이 매끄럽지 않습니다.")
    elif incoherence > 0:
        observations.append("일부 문장에서 의미 연결이 약하게 흔들립니다.")

    if not observations:
        return (
            "현재 입력에서는 질문 반복, 기억 혼란, 시간 혼란, 문장 비논리성이 뚜렷하게 드러나지 않습니다. "
            "다만 단일 대화만으로는 변화 양상을 충분히 판단하기 어려워 추가 관찰이 필요합니다."
        )

    summary = " ".join(observations[:2])
    if total >= 40:
        closing = "여러 지표가 함께 나타나 인지적 부담 신호가 비교적 뚜렷하게 해석됩니다."
    elif total >= 20:
        closing = "일부 위험 신호가 관찰되어 추가 관찰이 필요한 상태로 보입니다."
    else:
        closing = "현재 단계에서는 전반적 위험도가 높다고 단정하기는 어렵습니다."

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

            if not re.search(r"[.!?]$", observation):
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
                r"최종\s*점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
                r"최종점수(?:\s*\([^)]*\))?\s*[:：]?\s*(\d+)",
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
        return JUDGMENT_NORMAL
    if score < 40:
        return JUDGMENT_SUSPECTED
    return JUDGMENT_UNCERTAIN


def is_analysis_format_complete(text: str) -> bool:
    if not text or not text.strip():
        return False

    required_patterns = [
        r"판단:\s*(.+)",
        r"(?:최종점수|의심점수):\s*(\d+)",
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
    total_match = re.search(r"(?:최종점수|의심점수):\s*(\d+)", text)
    if not total_match:
        return False

    declared_total = clamp_score(int(total_match.group(1)))
    return declared_total == parsed["total"]


def force_analysis_format(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        return get_analysis_fallback_text()

    cleaned = raw_text.strip()
    scores = parse_analysis_scores(cleaned)

    judgment_match = re.search(r"판단:\s*(.+)", cleaned)
    reason_match = re.search(r"근거:\s*(.+)", cleaned, re.DOTALL)

    judgment = (
        judgment_match.group(1).strip()
        if judgment_match
        else infer_judgment_from_score(scores["total"])
    )
    if judgment not in {JUDGMENT_NORMAL, JUDGMENT_SUSPECTED, JUDGMENT_UNCERTAIN}:
        judgment = infer_judgment_from_score(scores["total"])

    if (
        (judgment == JUDGMENT_NORMAL and scores["total"] >= 20)
        or (judgment == JUDGMENT_SUSPECTED and scores["total"] >= 40)
        or (judgment == JUDGMENT_SUSPECTED and scores["total"] < 20)
    ):
        judgment = infer_judgment_from_score(scores["total"])

    reason_text = normalize_reason_text(
        reason_match.group(1).strip() if reason_match else "",
        scores,
    )

    return (
        f"판단: {judgment}\n"
        f"최종점수: {scores['total']}\n"
        f"질문반복점수: {scores['repetition']}\n"
        f"기억혼란점수: {scores['memory']}\n"
        f"시간혼란점수: {scores['time_confusion']}\n"
        f"문장비논리점수: {scores['incoherence']}\n"
        f"근거: {reason_text}"
    )


def extract_analysis_fields(response_text: str) -> dict:
    if not response_text:
        return {
            "judgment": JUDGMENT_UNCERTAIN,
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

    judgment = (
        judgment_match.group(1).strip()
        if judgment_match
        else infer_judgment_from_score(parsed["total"])
    )
    if judgment not in {JUDGMENT_NORMAL, JUDGMENT_SUSPECTED, JUDGMENT_UNCERTAIN}:
        judgment = infer_judgment_from_score(parsed["total"])

    if (
        (judgment == JUDGMENT_NORMAL and parsed["total"] >= 20)
        or (judgment == JUDGMENT_SUSPECTED and parsed["total"] >= 40)
        or (judgment == JUDGMENT_SUSPECTED and parsed["total"] < 20)
    ):
        judgment = infer_judgment_from_score(parsed["total"])

    reason = normalize_reason_text(
        reason_match.group(1).strip() if reason_match else "",
        parsed,
    )

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
        int(score_match.group(1)) if score_match else 0,
        meta["max_score"],
    )
    reason = normalize_reason_text(
        reason_match.group(1).strip() if reason_match else "",
        {
            "repetition": score if role_key == "repetition" else 0,
            "memory": score if role_key == "memory" else 0,
            "time_confusion": score if role_key == "time_confusion" else 0,
            "incoherence": score if role_key == "incoherence" else 0,
        },
    )

    return {
        "role": role_key,
        "score": score,
        "reason": reason or get_default_reason(),
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


def build_short_input_result() -> dict:
    reason = (
        "대화 내용이 너무 짧아 언어적 특징을 분석하기 어렵습니다. "
        "조금 더 구체적인 입력이 필요합니다."
    )
    excluded_reason = "입력이 너무 짧아 이번 대화는 점수 통계에서 제외되었습니다."

    return {
        "full_text": (
            "답변: 질문 내용이 너무 짧아 답변하기 어렵습니다.\n\n"
            "판단: 판단 어려움\n"
            "최종점수: 반영 제외\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}\n"
            f"점수반영: {excluded_reason}"
        ),
        "answer": "질문 내용이 너무 짧아 답변하기 어렵습니다.",
        "judgment": JUDGMENT_UNCERTAIN,
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
    excluded_reason = "분석 중 오류가 발생해 이번 대화는 점수 통계에서 제외되었습니다."

    return {
        "full_text": (
            "답변: 응답 생성 중 문제가 발생했습니다.\n\n"
            "판단: 판단 어려움\n"
            "최종점수: 반영 제외\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}\n"
            f"점수반영: {excluded_reason}"
        ),
        "answer": "응답 생성 중 문제가 발생했습니다.",
        "judgment": JUDGMENT_UNCERTAIN,
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
        f"최종점수: {score_text}\n"
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
