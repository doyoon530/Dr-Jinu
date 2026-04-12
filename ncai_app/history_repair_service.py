from .common import clamp_score, clamp_subscore
from .config import RECENT_WINDOW
from .analysis_format_service import (
    JUDGMENT_NORMAL,
    JUDGMENT_SUSPECTED,
    JUDGMENT_UNCERTAIN,
    infer_judgment_from_score,
    normalize_reason_text,
    parse_analysis_scores,
)


RISK_EXCLUDED = "반영 제외"
TREND_INSUFFICIENT = "데이터 부족"
TREND_STABLE = "안정"
TREND_UP = "상승"
TREND_DOWN = "하락"


def calculate_trend_from_score_values(scores, window: int = RECENT_WINDOW) -> str:
    if len(scores) < 2:
        return TREND_INSUFFICIENT

    recent = scores[-window:]
    if len(recent) < 2:
        return TREND_STABLE

    diff = recent[-1] - recent[0]
    if diff >= 10:
        return TREND_UP
    if diff <= -10:
        return TREND_DOWN
    return TREND_STABLE


def calculate_confidence_from_feature_scores(
    feature_scores: dict, total_score: int
) -> int:
    repetition = int(feature_scores.get("repetition", 0))
    memory = int(feature_scores.get("memory", 0))
    time_confusion = int(feature_scores.get("time_confusion", 0))
    incoherence = int(feature_scores.get("incoherence", 0))

    confidence = 55
    if memory > 0:
        confidence += 8
    if time_confusion > 0:
        confidence += 8
    if repetition > 0:
        confidence += 6
    if incoherence > 0:
        confidence += 6
    if total_score >= 40:
        confidence += 8
    if total_score >= 60:
        confidence += 4

    return max(0, min(95, confidence))


def has_meaningful_feature_scores(feature_scores: dict) -> bool:
    if not isinstance(feature_scores, dict):
        return False

    return any(
        int(feature_scores.get(key, 0)) > 0
        for key in ["repetition", "memory", "time_confusion", "incoherence"]
    )


def should_include_analysis_score(
    judgment: str, score: int, feature_scores: dict
) -> bool:
    normalized_judgment = str(judgment or "").strip()
    normalized_score = clamp_score(int(score or 0))

    return not (
        normalized_judgment == JUDGMENT_UNCERTAIN
        and normalized_score == 0
        and not has_meaningful_feature_scores(feature_scores)
    )


def build_score_exclusion_reason(
    judgment: str, score: int, reason: str, feature_scores: dict
) -> str:
    if should_include_analysis_score(judgment, score, feature_scores):
        return ""

    normalized_reason = str(reason or "").strip()
    if "너무 짧아" in normalized_reason or "입력이 필요" in normalized_reason:
        return "입력이 너무 짧아 이번 대화는 점수 통계에서 제외되었습니다."
    if "음성 인식 결과" in normalized_reason:
        return "음성 인식 결과가 없어 이번 대화는 점수 통계에서 제외되었습니다."
    if "입력된 대화가 없습니다" in normalized_reason:
        return "분석할 대화가 없어 이번 대화는 점수 통계에서 제외되었습니다."
    if "문제가 발생" in normalized_reason or "오류" in normalized_reason:
        return "분석 중 오류가 발생해 이번 대화는 점수 통계에서 제외되었습니다."

    return "분석 결과가 불안정해 이번 대화는 점수 통계에서 제외되었습니다."


def get_risk_level_from_score(score: float) -> str:
    if score < 20:
        return "Normal"
    if score < 40:
        return "Low Risk"
    if score < 60:
        return "Moderate Risk"
    if score < 80:
        return "High Risk"
    return "Very High Risk"


def repair_turn_history_state(turns, existing_scores, recent_window: int = RECENT_WINDOW):
    if not turns:
        return [], []

    running_scores = []
    repaired_score_history = []

    for index, turn in enumerate(turns):
        feature_scores = turn.get("feature_scores") or {}
        repaired_feature_scores = {
            "repetition": clamp_subscore(int(feature_scores.get("repetition", 0)), 25),
            "memory": clamp_subscore(int(feature_scores.get("memory", 0)), 25),
            "time_confusion": clamp_subscore(
                int(feature_scores.get("time_confusion", 0)), 30
            ),
            "incoherence": clamp_subscore(
                int(feature_scores.get("incoherence", 0)), 20
            ),
        }

        current_score = clamp_score(int(turn.get("score", 0)))
        current_subtotal = sum(repaired_feature_scores.values())
        parsed_from_reason = parse_analysis_scores(turn.get("reason", ""))
        score_included = turn.get("score_included")
        if score_included is None:
            score_included = should_include_analysis_score(
                turn.get("judgment", ""), current_score, repaired_feature_scores
            )
        score_included = bool(score_included)

        if (
            score_included
            and parsed_from_reason["total"] > 0
            and (current_score == 0 or current_subtotal == 0)
        ):
            repaired_feature_scores = {
                "repetition": parsed_from_reason["repetition"],
                "memory": parsed_from_reason["memory"],
                "time_confusion": parsed_from_reason["time_confusion"],
                "incoherence": parsed_from_reason["incoherence"],
            }
            current_score = parsed_from_reason["total"]
        elif (
            score_included
            and current_subtotal > 0
            and current_score != clamp_score(current_subtotal)
        ):
            current_score = clamp_score(current_subtotal)

        judgment = str(turn.get("judgment", "")).strip()
        if judgment not in {JUDGMENT_NORMAL, JUDGMENT_SUSPECTED, JUDGMENT_UNCERTAIN}:
            judgment = infer_judgment_from_score(current_score)
        if score_included and (
            (judgment == JUDGMENT_NORMAL and current_score >= 20)
            or (judgment == JUDGMENT_SUSPECTED and current_score < 20)
        ):
            judgment = infer_judgment_from_score(current_score)

        turn["feature_scores"] = repaired_feature_scores
        turn["score"] = current_score if score_included else 0
        turn["judgment"] = judgment
        turn["score_included"] = score_included
        turn["excluded_reason"] = str(
            turn.get("excluded_reason")
            or build_score_exclusion_reason(
                judgment, turn["score"], turn.get("reason", ""), repaired_feature_scores
            )
        )
        turn["reason"] = normalize_reason_text(
            turn.get("reason", ""), repaired_feature_scores
        )

        if score_included:
            turn["confidence"] = calculate_confidence_from_feature_scores(
                repaired_feature_scores, current_score
            )
            turn["risk_level"] = get_risk_level_from_score(current_score)
            running_scores.append(current_score)
            turn["trend"] = calculate_trend_from_score_values(
                running_scores, recent_window
            )
        else:
            turn["confidence"] = 0
            turn["risk_level"] = RISK_EXCLUDED
            turn["trend"] = RISK_EXCLUDED

        if running_scores:
            turn["average_score"] = round(sum(running_scores) / len(running_scores), 1)
            recent_scores = running_scores[-recent_window:]
            turn["recent_average_score"] = round(
                sum(recent_scores) / len(recent_scores), 1
            )
        else:
            turn["average_score"] = 0.0
            turn["recent_average_score"] = 0.0

        if score_included:
            if index < len(existing_scores):
                time_value = existing_scores[index].get("time", turn.get("time", ""))
            else:
                time_value = turn.get("time", "")

            repaired_score_history.append(
                {"score": clamp_score(int(turn.get("score", 0))), "time": time_value}
            )

    return turns, repaired_score_history
