import os
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from . import runtime
from .analysis_service import (
    build_fields_from_role_results,
    generate_analysis_result,
    generate_answer_result,
    generate_role_analysis_result,
    get_response_from_llama,
    normalize_role_results_payload,
)
from .common import normalize_text
from .config import UPLOAD_DIR, normalize_role_key
from .history_service import (
    add_score_history,
    add_to_history,
    add_turn_history,
    bump_analysis_generation,
    build_chat_response,
    evaluate_recall_answer,
    finalize_analysis_response,
    get_analysis_generation,
    get_average_score,
    get_or_create_session_id,
    get_requested_analysis_generation,
    get_recent_average_score,
    get_risk_level_from_score,
    get_score_history,
    get_score_trend,
    get_turn_history,
    is_current_analysis_generation,
    maybe_advance_recall_test,
    serialize_recall_state,
)
from .llm_service import (
    get_google_credentials_status,
    get_llm_provider_status,
    get_model_status,
    get_requested_llm_provider,
    transcribe_audio_file,
)

analysis_runtime_cache = runtime.analysis_runtime_cache
conversation_store = runtime.conversation_store
recall_store = runtime.recall_store
score_store = runtime.score_store
turn_store = runtime.turn_store


def register_routes(app: Flask) -> None:
    def build_stale_generation_response(session_id: str, status_code: int = 409):
        return (
            jsonify(
                {
                    "stale": True,
                    "error": "초기화 이전 분석 요청이어서 현재 세션에는 반영하지 않습니다.",
                    "session_id": session_id,
                    "analysis_generation": get_analysis_generation(session_id),
                }
            ),
            status_code,
        )

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/health", methods=["GET"])
    def health():
        model_status = get_model_status()
        credentials_status = get_google_credentials_status()
        provider_status = get_llm_provider_status()
        llm_ready = provider_status["local"]["ready"] or provider_status["api"]["ready"]
        ready = llm_ready and credentials_status["configured"]

        return jsonify(
            {
                "status": "ok" if ready else "degraded",
                "service": "ncai-dementia-risk-monitor",
                "time": datetime.now().isoformat(),
                "ready": ready,
                "model": model_status,
                "google_credentials": credentials_status,
                "llm_provider": provider_status,
            }
        )

    @app.route("/transcribe-audio", methods=["POST"])
    def transcribe_audio():
        session_id = get_or_create_session_id()

        try:
            requested_generation = get_requested_analysis_generation()
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            if "audio" not in request.files:
                return jsonify({"error": "오디오 파일이 없습니다."}), 400

            audio_file = request.files["audio"]

            if audio_file.filename == "":
                return jsonify({"error": "오디오 파일이 없습니다."}), 400

            original_name = secure_filename(audio_file.filename) or "audio.wav"
            unique_name = f"{uuid.uuid4()}_{original_name}"
            file_path = os.path.join(UPLOAD_DIR, unique_name)
            audio_file.save(file_path)

            user_input = transcribe_audio_file(file_path)

            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            return jsonify(
                {
                    "session_id": session_id,
                    "analysis_generation": get_analysis_generation(session_id),
                    "user_speech": user_input,
                }
            )

        except Exception:
            app.logger.exception("STT 처리 오류")
            return jsonify({"error": "음성 인식 중 문제가 발생했습니다."}), 500

    @app.route("/generate-answer", methods=["POST"])
    def generate_answer():
        session_id = get_or_create_session_id()

        try:
            data = request.get_json(silent=True) or {}
            user_input = normalize_text(data.get("message", ""))
            llm_provider = get_requested_llm_provider(data)
            requested_generation = get_requested_analysis_generation(data)

            if not user_input:
                return jsonify({"error": "분석할 텍스트가 없습니다."}), 400
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            result = generate_answer_result(user_input, provider=llm_provider)

            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            return jsonify(
                {
                    "session_id": session_id,
                    "analysis_generation": get_analysis_generation(session_id),
                    "user_speech": user_input,
                    "answer": result.get("answer", ""),
                    "is_answer_only": True,
                    "llm_provider": llm_provider,
                }
            )

        except Exception:
            app.logger.exception("응답 사전 생성 오류")
            return jsonify({"error": "응답 생성 중 문제가 발생했습니다."}), 500

    @app.route("/analyze-role", methods=["POST"])
    def analyze_role():
        session_id = get_or_create_session_id()

        try:
            data = request.get_json(silent=True) or {}
            user_input = normalize_text(data.get("message", ""))
            role_key = normalize_role_key(data.get("role", ""))
            llm_provider = get_requested_llm_provider(data)
            requested_generation = get_requested_analysis_generation(data)

            if not user_input:
                return jsonify({"error": "분석할 텍스트가 없습니다."}), 400
            if role_key not in {
                "repetition",
                "memory",
                "time_confusion",
                "incoherence",
            }:
                return jsonify({"error": "지원하지 않는 분석 역할입니다."}), 400
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            role_result = generate_role_analysis_result(
                role_key,
                user_input,
                session_id=session_id,
                provider=llm_provider,
            )

            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            return jsonify(
                {
                    "session_id": session_id,
                    "analysis_generation": get_analysis_generation(session_id),
                    "role": role_key,
                    "score": int(role_result.get("score", 0)),
                    "reason": role_result.get("reason", ""),
                    "llm_provider": llm_provider,
                }
            )

        except Exception:
            app.logger.exception("역할별 분석 오류")
            return jsonify({"error": "역할별 분석 중 문제가 발생했습니다."}), 500

    @app.route("/finalize-analysis", methods=["POST"])
    def finalize_analysis():
        session_id = get_or_create_session_id()

        try:
            data = request.get_json(silent=True) or {}
            user_input = normalize_text(data.get("message", ""))
            precomputed_answer = normalize_text(data.get("answer", ""))
            llm_provider = get_requested_llm_provider(data)
            role_results = normalize_role_results_payload(data.get("role_results", {}))
            requested_generation = get_requested_analysis_generation(data)

            if not user_input:
                return jsonify({"error": "분석할 텍스트가 없습니다."}), 400
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            answer_result = (
                {"answer": precomputed_answer}
                if precomputed_answer
                else generate_answer_result(
                    user_input,
                    provider=llm_provider,
                )
            )
            fields = build_fields_from_role_results(role_results)
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)
            return finalize_analysis_response(
                session_id=session_id,
                user_input=user_input,
                answer_text=answer_result.get("answer", ""),
                fields=fields,
                llm_provider=llm_provider,
            )

        except Exception:
            app.logger.exception("최종 분석 반영 오류")
            return jsonify({"error": "최종 분석 반영 중 문제가 발생했습니다."}), 500

    @app.route("/analyze-text", methods=["POST"])
    def analyze_text():
        session_id = get_or_create_session_id()

        try:
            data = request.get_json(silent=True) or {}
            user_input = normalize_text(data.get("message", ""))
            precomputed_answer = normalize_text(data.get("answer", ""))
            llm_provider = get_requested_llm_provider(data)
            requested_generation = get_requested_analysis_generation(data)

            if not user_input:
                return jsonify({"error": "분석할 텍스트가 없습니다."}), 400
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)

            answer_result = (
                {"answer": precomputed_answer}
                if precomputed_answer
                else generate_answer_result(
                    user_input,
                    provider=llm_provider,
                )
            )
            fields = generate_analysis_result(
                user_input, session_id=session_id, provider=llm_provider
            )
            if not is_current_analysis_generation(session_id, requested_generation):
                return build_stale_generation_response(session_id)
            return finalize_analysis_response(
                session_id=session_id,
                user_input=user_input,
                answer_text=answer_result.get("answer", ""),
                fields=fields,
                llm_provider=llm_provider,
            )

        except Exception:
            app.logger.exception("텍스트 분석 오류")
            return jsonify({"error": "텍스트 분석 중 문제가 발생했습니다."}), 500

    @app.route("/chat", methods=["POST"])
    def chat():
        session_id = get_or_create_session_id()
        user_input = ""
        llm_provider = get_requested_llm_provider()

        try:
            if "audio" in request.files:
                audio_file = request.files["audio"]

                if audio_file.filename == "":
                    return jsonify({"error": "오디오 파일이 없습니다."}), 400

                original_name = secure_filename(audio_file.filename) or "audio.wav"
                unique_name = f"{uuid.uuid4()}_{original_name}"
                file_path = os.path.join(UPLOAD_DIR, unique_name)
                audio_file.save(file_path)

                user_input = transcribe_audio_file(file_path)

                if not user_input:
                    return build_chat_response(
                        session_id=session_id,
                        user_speech="",
                        sys_response="판단: 판단 어려움\n의심점수: 반영 제외\n질문반복점수: 0\n기억혼란점수: 0\n시간혼란점수: 0\n문장비논리점수: 0\n근거: 음성 인식 결과가 없어 분석할 수 없습니다. 다시 녹음해 주세요.\n점수반영: 음성 인식 결과가 없어 이번 대화는 점수 통계에서 제외했습니다.",
                        answer="",
                        judgment="판단 어려움",
                        score=0,
                        reason="음성 인식 결과가 없어 분석할 수 없습니다. 다시 녹음해 주세요.",
                        feature_scores={
                            "repetition": 0,
                            "memory": 0,
                            "time_confusion": 0,
                            "incoherence": 0,
                        },
                        score_included=False,
                        excluded_reason="음성 인식 결과가 없어 이번 대화는 점수 통계에서 제외했습니다.",
                    )
            else:
                data = request.get_json(silent=True) or {}
                user_input = normalize_text(data.get("message", ""))
                llm_provider = get_requested_llm_provider(data)

                if not user_input:
                    return build_chat_response(
                        session_id=session_id,
                        user_speech="",
                        sys_response="판단: 판단 어려움\n의심점수: 반영 제외\n질문반복점수: 0\n기억혼란점수: 0\n시간혼란점수: 0\n문장비논리점수: 0\n근거: 입력된 대화가 없습니다. 분석할 내용이 필요합니다.\n점수반영: 분석할 대화가 없어 이번 대화는 점수 통계에서 제외했습니다.",
                        answer="",
                        judgment="판단 어려움",
                        score=0,
                        reason="입력된 대화가 없습니다. 분석할 내용이 필요합니다.",
                        feature_scores={
                            "repetition": 0,
                            "memory": 0,
                            "time_confusion": 0,
                            "incoherence": 0,
                        },
                        score_included=False,
                        excluded_reason="분석할 대화가 없어 이번 대화는 점수 통계에서 제외했습니다.",
                    )

            recall_feedback = evaluate_recall_answer(session_id, user_input)
            result = get_response_from_llama(
                user_input, session_id=session_id, provider=llm_provider
            )

            if recall_feedback:
                result["answer"] = f"{result['answer']}\n\n{recall_feedback}"

            add_to_history(session_id, "user", user_input)
            add_to_history(session_id, "assistant", result["full_text"])
            if result.get("score_included", True):
                add_score_history(session_id, result["score"])
            follow_up_messages = []

            recall_prompt = maybe_advance_recall_test(session_id)
            if recall_prompt:
                result["answer"] = f"{result['answer']}\n\n{recall_prompt}"
                result["full_text"] = f"{result['full_text']}\n\n{recall_prompt}"
                follow_up_messages.append(recall_prompt)

            turn = add_turn_history(
                session_id=session_id,
                user_text=user_input,
                answer=result["answer"],
                judgment=result["judgment"],
                score=result["score"],
                reason=result["reason"],
                feature_scores=result["feature_scores"],
                follow_up_messages=follow_up_messages,
                score_included=result.get("score_included", True),
                excluded_reason=result.get("excluded_reason", ""),
                llm_provider=llm_provider,
            )

            return build_chat_response(
                session_id=session_id,
                user_speech=user_input,
                sys_response=result["full_text"],
                answer=result["answer"],
                judgment=result["judgment"],
                score=result["score"],
                reason=result["reason"],
                feature_scores=result["feature_scores"],
                follow_up_messages=follow_up_messages,
                turn=turn,
                score_included=result.get("score_included", True),
                excluded_reason=result.get("excluded_reason", ""),
                llm_provider=llm_provider,
            )

        except Exception:
            app.logger.exception("서버 오류")
            return jsonify({"error": "서버 처리 중 문제가 발생했습니다."}), 500

    @app.route("/score-history", methods=["GET"])
    def score_history():
        session_id = get_or_create_session_id()

        return jsonify(
            {
                "session_id": session_id,
                "analysis_generation": get_analysis_generation(session_id),
                "average_score": get_average_score(session_id),
                "recent_average_score": get_recent_average_score(session_id),
                "risk_level": get_risk_level_from_score(
                    get_recent_average_score(session_id)
                ),
                "trend": get_score_trend(session_id),
                "score_history": get_score_history(session_id),
                "turn_history": get_turn_history(session_id),
                "recall": serialize_recall_state(session_id),
            }
        )

    @app.route("/reset-history", methods=["POST"])
    def reset_history():
        session_id = get_or_create_session_id()
        next_generation = bump_analysis_generation(session_id)

        conversation_store[session_id] = []
        score_store[session_id] = []
        turn_store[session_id] = []
        analysis_runtime_cache.pop(session_id, None)
        recall_store[session_id] = {
            "status": "idle",
            "target_word": "",
            "prompt": "",
            "last_result": "없음",
            "introduced_turn": 0,
        }

        return jsonify(
            {
                "session_id": session_id,
                "analysis_generation": next_generation,
                "message": "기록이 초기화되었습니다.",
                "average_score": 0.0,
                "recent_average_score": 0.0,
                "risk_level": "Normal",
                "trend": "데이터 부족",
                "score_history": [],
                "turn_history": [],
                "recall": serialize_recall_state(session_id),
            }
        )
