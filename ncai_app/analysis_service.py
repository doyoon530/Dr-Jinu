import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from difflib import SequenceMatcher

from . import runtime
from .analysis_format_service import (
    build_error_result,
    build_full_text,
    build_reason_from_scores,
    build_short_input_result,
    force_single_role_analysis_format,
    get_default_reason,
    get_role_analysis_fallback_text,
    infer_judgment_from_score,
    is_single_role_analysis_complete,
    merge_reason_text,
    parse_repetition_chain_response,
    parse_single_role_analysis,
    sanitize_answer_text,
)
from .common import clamp_score, clamp_subscore, normalize_text, validate_user_text
from .config import (
    ANSWER_STOP_SEQUENCES,
    MAX_ANALYSIS_RETRY,
    REPETITION_HEURISTIC_SKIP_THRESHOLD,
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
    get_analysis_runtime_state,
)
from .history_repair_service import (
    build_score_exclusion_reason,
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

    if previous_turns is None:
        previous_turns = get_analysis_runtime_state(session_id)["previous_turns"]

    if not validate_user_text(question) or not previous_turns:
        return {
            "role": "repetition",
            "score": 0,
            "reason": "",
        }

    # --- Heuristic short-circuit ---
    # Run fast similarity analysis first.  If it already yields a high-confidence
    # score (>= REPETITION_HEURISTIC_SKIP_THRESHOLD), the match is unambiguous and
    # calling the LLM adds no value — skip it and return immediately.
    # This also avoids the API-configured check entirely for obvious repeats.
    heuristic = analyze_repetition_by_similarity(question, previous_turns)
    if heuristic["score"] >= REPETITION_HEURISTIC_SKIP_THRESHOLD:
        logger.debug(
            "repetition heuristic short-circuit: score=%s (>= %s), skipping LLM",
            heuristic["score"],
            REPETITION_HEURISTIC_SKIP_THRESHOLD,
        )
        return {
            "role": "repetition",
            "score": clamp_subscore(heuristic["score"], 25),
            "reason": heuristic.get("reason", ""),
        }

    if normalized_provider == "api" and not is_api_llm_configured():
        raise RuntimeError(
            "API 모드가 아직 설정되지 않았습니다. API 키와 모델 이름을 먼저 설정해주세요."
        )

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
        llm_score = clamp_subscore(int(parsed.get("score", 0)), 25)
        # Take whichever source (LLM or heuristic) gave a higher score.
        # Prefer LLM reason when its score is strictly higher; keep heuristic reason otherwise.
        if llm_score >= heuristic["score"]:
            return {
                "role": "repetition",
                "score": llm_score,
                "reason": normalize_text(parsed.get("reason", "")),
            }
        return {
            "role": "repetition",
            "score": heuristic["score"],
            "reason": heuristic.get("reason", ""),
        }
    except Exception as e:
        logger.warning("repetition 분석 실패: %s", e, exc_info=True)
        if normalized_provider == "api":
            raise RuntimeError("질문 반복 API 분석에 실패했습니다.") from e
        # Fall back to heuristic result on local-mode error
        if heuristic["score"] > 0:
            return {
                "role": "repetition",
                "score": heuristic["score"],
                "reason": heuristic.get("reason", ""),
            }
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
    question: str,
    session_id: str | None = None,
    provider: str | None = None,
    progress_callback=None,
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

    if normalized_provider == "api":
        # API mode: all 4 role analyses are independent HTTP calls — run them in parallel
        analysis_context = runtime_state["analysis_context"]
        previous_turns = runtime_state["previous_turns"]
        role_tasks = {
            "repetition": lambda: generate_repetition_role_analysis(
                question,
                session_id=session_id,
                previous_turns=previous_turns,
                provider=normalized_provider,
            ),
            "memory": lambda: generate_single_role_analysis(
                "memory",
                question,
                session_id=session_id,
                conversation_context=analysis_context,
                provider=normalized_provider,
            ),
            "time_confusion": lambda: generate_single_role_analysis(
                "time_confusion",
                question,
                session_id=session_id,
                conversation_context=analysis_context,
                provider=normalized_provider,
            ),
            "incoherence": lambda: generate_single_role_analysis(
                "incoherence",
                question,
                session_id=session_id,
                conversation_context=analysis_context,
                provider=normalized_provider,
            ),
        }
        role_results = {}
        completed_count = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_role = {executor.submit(fn): role for role, fn in role_tasks.items()}
            for future in as_completed(future_to_role):
                role = future_to_role[future]
                role_results[role] = future.result()  # propagates exceptions naturally
                completed_count += 1
                if progress_callback:
                    progress_callback(
                        "analysis",
                        45 + completed_count * 10,
                        f"분석 중... ({completed_count}/4)",
                    )
    else:
        # Local mode: sequential execution required due to analysis_llm_lock
        _local_roles = [
            ("repetition", lambda: generate_repetition_role_analysis(
                question,
                session_id=session_id,
                previous_turns=runtime_state["previous_turns"],
                provider=normalized_provider,
            )),
            ("memory", lambda: generate_single_role_analysis(
                "memory",
                question,
                session_id=session_id,
                conversation_context=runtime_state["analysis_context"],
                provider=normalized_provider,
            )),
            ("time_confusion", lambda: generate_single_role_analysis(
                "time_confusion",
                question,
                session_id=session_id,
                conversation_context=runtime_state["analysis_context"],
                provider=normalized_provider,
            )),
            ("incoherence", lambda: generate_single_role_analysis(
                "incoherence",
                question,
                session_id=session_id,
                conversation_context=runtime_state["analysis_context"],
                provider=normalized_provider,
            )),
        ]
        role_results = {}
        for i, (role_name, role_fn) in enumerate(_local_roles):
            if progress_callback:
                progress_callback(
                    "analysis",
                    45 + i * 10,
                    f"분석 중... ({i + 1}/4)",
                )
            role_results[role_name] = role_fn()

    fields = build_fields_from_role_results(role_results)
    fields["llm_provider"] = normalized_provider
    return fields


def get_response_from_llama(
    question: str,
    session_id: str | None = None,
    provider: str | None = None,
    progress_callback=None,
) -> dict:
    normalized_provider = normalize_llm_provider(provider or get_default_llm_provider())
    if progress_callback:
        progress_callback("answer", 25, "답변 생성 중...")
    answer_result = generate_answer_result(question, provider=normalized_provider)

    if all(
        key in answer_result
        for key in ["full_text", "judgment", "score", "reason", "feature_scores"]
    ):
        answer_result["llm_provider"] = normalized_provider
        return answer_result

    if progress_callback:
        progress_callback("analysis", 45, "인지 기능 분석 중...")
    fields = generate_analysis_result(
        question,
        session_id=session_id,
        provider=normalized_provider,
        progress_callback=progress_callback,
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
