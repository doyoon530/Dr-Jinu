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
from .common import (
    build_device_name,
    extract_client_ip_info,
    infer_browser,
    infer_device_type,
    infer_operating_system,
    normalize_text,
    safe_reverse_dns,
)
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
visitor_event_store = runtime.visitor_event_store
visitor_hostname_cache = runtime.visitor_hostname_cache
visitor_lock = runtime.visitor_lock
visitor_snapshot_store = runtime.visitor_snapshot_store


def register_routes(app: Flask) -> None:
    def should_track_request(path: str) -> bool:
        if not path:
            return False
        if path.startswith("/static/"):
            return False
        if path in {"/favicon.ico"}:
            return False
        return True

    def resolve_hostname(ip_address: str) -> str:
        ip = normalize_text(ip_address)
        if not ip or ip == "unknown":
            return ""

        with visitor_lock:
            cached = visitor_hostname_cache.get(ip)
        if cached is not None:
            return cached

        hostname = safe_reverse_dns(ip)
        with visitor_lock:
            visitor_hostname_cache[ip] = hostname
        return hostname

    def normalize_client_telemetry(raw_payload: dict | None) -> dict:
        payload = raw_payload or {}
        browser_brands = payload.get("brands") or []

        if isinstance(browser_brands, list):
            brands = [
                normalize_text(item.get("brand", ""))
                for item in browser_brands
                if isinstance(item, dict)
            ]
        else:
            brands = []

        return {
            "platform": normalize_text(payload.get("platform", "")),
            "platform_version": normalize_text(payload.get("platformVersion", "")),
            "model": normalize_text(payload.get("model", "")),
            "language": normalize_text(payload.get("language", "")),
            "languages": [
                normalize_text(item)
                for item in (payload.get("languages") or [])
                if normalize_text(item)
            ],
            "timezone": normalize_text(payload.get("timezone", "")),
            "screen": normalize_text(payload.get("screen", "")),
            "viewport": normalize_text(payload.get("viewport", "")),
            "device_memory": payload.get("deviceMemory"),
            "hardware_concurrency": payload.get("hardwareConcurrency"),
            "max_touch_points": payload.get("maxTouchPoints"),
            "is_mobile": bool(payload.get("isMobile")),
            "connection_type": normalize_text(payload.get("connectionType", "")),
            "effective_type": normalize_text(payload.get("effectiveType", "")),
            "referrer": normalize_text(payload.get("referrer", "")),
            "page_url": normalize_text(payload.get("pageUrl", "")),
            "user_agent": normalize_text(payload.get("userAgent", "")),
            "brands": [brand for brand in brands if brand],
        }

    def get_request_session_id() -> str:
        query_session_id = normalize_text(request.args.get("session_id", ""))
        body = request.get_json(silent=True) or {}
        body_session_id = normalize_text(body.get("session_id", ""))
        return query_session_id or body_session_id

    def build_request_visitor_context() -> dict:
        ip_info = extract_client_ip_info(request)
        user_agent = normalize_text(request.headers.get("User-Agent", ""))
        visitor_id = normalize_text(request.headers.get("X-Visitor-Id", ""))
        session_id = get_request_session_id()
        snapshot_key = visitor_id or uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{ip_info['ip']}|{user_agent[:120]}",
        ).hex[:16]

        with visitor_lock:
            snapshot = dict(visitor_snapshot_store.get(snapshot_key, {}))
            telemetry = dict(snapshot.get("telemetry", {}))

        platform_hint = telemetry.get("platform", "")
        max_touch_points = telemetry.get("max_touch_points")
        is_mobile_hint = telemetry.get("is_mobile")

        browser = infer_browser(user_agent or telemetry.get("user_agent", ""))
        operating_system = infer_operating_system(
            user_agent or telemetry.get("user_agent", ""),
            platform_hint=platform_hint,
        )
        device_type = infer_device_type(
            user_agent or telemetry.get("user_agent", ""),
            is_mobile_hint=is_mobile_hint,
            max_touch_points=max_touch_points,
        )
        hostname = resolve_hostname(ip_info["ip"])
        model = normalize_text(telemetry.get("model", ""))
        device_name = build_device_name(
            browser=browser,
            operating_system=operating_system,
            hostname=hostname,
            model=model,
        )

        return {
            "visitor_id": snapshot_key,
            "session_id": session_id or snapshot.get("session_id", ""),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "path": request.path,
            "method": request.method,
            "ip": ip_info["ip"],
            "ip_source": ip_info["source"],
            "remote_addr": ip_info["remote_addr"],
            "forwarded_chain": ip_info["forwarded_chain"],
            "hostname": hostname,
            "browser": browser,
            "operating_system": operating_system,
            "device_type": device_type,
            "device_name": device_name,
            "user_agent": user_agent,
            "cf_ip_country": normalize_text(request.headers.get("CF-IPCountry", "")),
            "cf_ray": normalize_text(request.headers.get("CF-Ray", "")),
            "telemetry": telemetry,
            "referrer": telemetry.get("referrer", ""),
            "page_url": telemetry.get("page_url", ""),
            "language": telemetry.get("language", ""),
            "screen": telemetry.get("screen", ""),
            "viewport": telemetry.get("viewport", ""),
            "timezone": telemetry.get("timezone", ""),
            "connection_type": telemetry.get("connection_type", ""),
            "effective_type": telemetry.get("effective_type", ""),
        }

    def record_visitor_event(context: dict) -> None:
        snapshot_key = context["visitor_id"]
        timestamp = context["timestamp"]
        path = context["path"]

        with visitor_lock:
            existing = dict(visitor_snapshot_store.get(snapshot_key, {}))
            path_history = list(existing.get("recent_paths", []))
            path_history.append(path)
            path_history = path_history[-8:]
            visit_count = int(existing.get("visit_count", 0)) + 1

            snapshot = {
                **existing,
                "visitor_id": snapshot_key,
                "session_id": context.get("session_id", "") or existing.get("session_id", ""),
                "first_seen": existing.get("first_seen", timestamp),
                "last_seen": timestamp,
                "visit_count": visit_count,
                "last_path": path,
                "recent_paths": path_history,
                "ip": context["ip"],
                "ip_source": context["ip_source"],
                "remote_addr": context["remote_addr"],
                "forwarded_chain": context["forwarded_chain"],
                "hostname": context["hostname"],
                "browser": context["browser"],
                "operating_system": context["operating_system"],
                "device_type": context["device_type"],
                "device_name": context["device_name"],
                "user_agent": context["user_agent"],
                "cf_ip_country": context["cf_ip_country"],
                "cf_ray": context["cf_ray"],
                "telemetry": context.get("telemetry", existing.get("telemetry", {})),
                "referrer": context.get("referrer", existing.get("referrer", "")),
                "page_url": context.get("page_url", existing.get("page_url", "")),
                "language": context.get("language", existing.get("language", "")),
                "screen": context.get("screen", existing.get("screen", "")),
                "viewport": context.get("viewport", existing.get("viewport", "")),
                "timezone": context.get("timezone", existing.get("timezone", "")),
                "connection_type": context.get(
                    "connection_type", existing.get("connection_type", "")
                ),
                "effective_type": context.get(
                    "effective_type", existing.get("effective_type", "")
                ),
            }
            visitor_snapshot_store[snapshot_key] = snapshot
            visitor_event_store.append(
                {
                    "timestamp": timestamp,
                    "visitor_id": snapshot_key,
                    "session_id": snapshot["session_id"],
                    "method": context["method"],
                    "path": path,
                    "ip": context["ip"],
                    "ip_source": context["ip_source"],
                    "hostname": context["hostname"],
                    "browser": context["browser"],
                    "operating_system": context["operating_system"],
                    "device_type": context["device_type"],
                    "device_name": context["device_name"],
                    "language": context.get("language", ""),
                    "screen": context.get("screen", ""),
                    "viewport": context.get("viewport", ""),
                    "timezone": context.get("timezone", ""),
                    "connection_type": context.get("connection_type", ""),
                    "effective_type": context.get("effective_type", ""),
                    "referrer": context.get("referrer", ""),
                    "page_url": context.get("page_url", ""),
                    "cf_ip_country": context["cf_ip_country"],
                    "cf_ray": context["cf_ray"],
                    "user_agent": context["user_agent"],
                }
            )

        app.logger.info(
            "VISITOR %s %s %s ip=%s source=%s device=%s browser=%s os=%s host=%s session=%s",
            timestamp,
            context["method"],
            path,
            context["ip"],
            context["ip_source"],
            context["device_name"],
            context["browser"],
            context["operating_system"],
            context["hostname"] or "-",
            context.get("session_id", "") or "-",
        )

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

    def build_visitors_payload(limit: int) -> dict:
        with visitor_lock:
            visitors = sorted(
                visitor_snapshot_store.values(),
                key=lambda item: item.get("last_seen", ""),
                reverse=True,
            )
            recent_events = list(visitor_event_store)[-limit:][::-1]

        return {
            "status": "ok",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "total_known_visitors": len(visitors),
            "total_recent_events": len(recent_events),
            "visitors": visitors[:limit],
            "recent_events": recent_events,
        }

    @app.before_request
    def track_visitor_request():
        if not should_track_request(request.path):
            return None

        record_visitor_event(build_request_visitor_context())
        return None

    @app.route("/client-telemetry", methods=["POST"])
    def client_telemetry():
        payload = request.get_json(silent=True) or {}
        raw_visitor_id = normalize_text(
            request.headers.get("X-Visitor-Id", "") or payload.get("visitor_id", "")
        )
        if not raw_visitor_id:
            return jsonify({"error": "visitor_id가 없습니다."}), 400

        telemetry = normalize_client_telemetry(payload)
        ip_info = extract_client_ip_info(request)
        request_ip = ip_info["ip"]
        request_user_agent = normalize_text(
            telemetry.get("user_agent", "") or request.headers.get("User-Agent", "")
        )

        with visitor_lock:
            merge_key = None
            for key, snapshot in visitor_snapshot_store.items():
                if key == raw_visitor_id:
                    continue
                if snapshot.get("ip") == request_ip and snapshot.get(
                    "user_agent"
                ) == request_user_agent:
                    merge_key = key
                    break

            existing = dict(
                visitor_snapshot_store.get(raw_visitor_id, {})
                or visitor_snapshot_store.get(merge_key, {})
            )
            hostname = existing.get("hostname", "")
            browser = infer_browser(telemetry.get("user_agent", ""))
            operating_system = infer_operating_system(
                telemetry.get("user_agent", ""),
                platform_hint=telemetry.get("platform", ""),
            )
            device_type = infer_device_type(
                telemetry.get("user_agent", ""),
                is_mobile_hint=telemetry.get("is_mobile"),
                max_touch_points=telemetry.get("max_touch_points"),
            )
            device_name = build_device_name(
                browser=browser,
                operating_system=operating_system,
                hostname=hostname,
                model=telemetry.get("model", ""),
            )

            visitor_snapshot_store[raw_visitor_id] = {
                **existing,
                "visitor_id": raw_visitor_id,
                "session_id": normalize_text(payload.get("session_id", ""))
                or existing.get("session_id", ""),
                "ip": existing.get("ip", request_ip),
                "ip_source": existing.get("ip_source", ip_info["source"]),
                "remote_addr": existing.get("remote_addr", ip_info["remote_addr"]),
                "forwarded_chain": existing.get(
                    "forwarded_chain", ip_info["forwarded_chain"]
                ),
                "hostname": hostname,
                "telemetry": telemetry,
                "browser": browser if browser != "Unknown Browser" else existing.get("browser", browser),
                "operating_system": operating_system
                if operating_system != "Unknown OS"
                else existing.get("operating_system", operating_system),
                "device_type": device_type or existing.get("device_type", ""),
                "device_name": device_name or existing.get("device_name", ""),
                "language": telemetry.get("language", "") or existing.get("language", ""),
                "screen": telemetry.get("screen", "") or existing.get("screen", ""),
                "viewport": telemetry.get("viewport", "") or existing.get("viewport", ""),
                "timezone": telemetry.get("timezone", "") or existing.get("timezone", ""),
                "connection_type": telemetry.get("connection_type", "")
                or existing.get("connection_type", ""),
                "effective_type": telemetry.get("effective_type", "")
                or existing.get("effective_type", ""),
                "page_url": telemetry.get("page_url", "") or existing.get("page_url", ""),
                "referrer": telemetry.get("referrer", "") or existing.get("referrer", ""),
            }

            if merge_key and merge_key in visitor_snapshot_store:
                visitor_snapshot_store.pop(merge_key, None)
                for event in list(visitor_event_store):
                    if event.get("visitor_id") == merge_key:
                        event["visitor_id"] = raw_visitor_id

        app.logger.info(
            "VISITOR-CLIENT visitor=%s device=%s browser=%s os=%s screen=%s viewport=%s tz=%s",
            raw_visitor_id,
            visitor_snapshot_store[raw_visitor_id].get("device_name", "-"),
            visitor_snapshot_store[raw_visitor_id].get("browser", "-"),
            visitor_snapshot_store[raw_visitor_id].get("operating_system", "-"),
            visitor_snapshot_store[raw_visitor_id].get("screen", "-"),
            visitor_snapshot_store[raw_visitor_id].get("viewport", "-"),
            visitor_snapshot_store[raw_visitor_id].get("timezone", "-"),
        )

        return jsonify(
            {
                "status": "ok",
                "visitor_id": raw_visitor_id,
                "device_name": visitor_snapshot_store[raw_visitor_id].get(
                    "device_name", ""
                ),
            }
        )

    @app.route("/admin/visitors", methods=["GET"])
    def admin_visitors():
        try:
            limit = int(request.args.get("limit", 25) or 25)
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 200))
        payload = build_visitors_payload(limit)

        if normalize_text(request.args.get("format", "")).lower() == "json":
            return jsonify(payload)

        return render_template(
            "admin_visitors.html",
            payload=payload,
            limit=limit,
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
