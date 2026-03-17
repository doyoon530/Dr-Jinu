from flask import Flask, render_template, request, jsonify
from google.cloud import speech
from langchain_core.prompts import ChatPromptTemplate
from langchain_classic.chains import LLMChain
from langchain_community.llms import LlamaCpp
from werkzeug.utils import secure_filename

import os
import re
import uuid
import random
from datetime import datetime


# =========================
# 기본 설정
# =========================
app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
MODEL_PATH = os.path.join(BASE_DIR, "models", "EXAONE-3.5-7.8B-Instruct-Q8_0.gguf")
GOOGLE_KEY_PATH = os.path.join(BASE_DIR, "stt-bot-489913-807430be631b.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_KEY_PATH

MAX_HISTORY_TURNS = 12
MAX_SCORE_HISTORY = 30
MAX_ANALYSIS_RETRY = 3
RECENT_WINDOW = 5

RECALL_WORDS = [
    "사과", "버스", "바다", "연필", "시계", "나무", "기차", "고양이",
    "책상", "우산", "커피", "구름", "노트", "수건", "안경"
]


# =========================
# 메모리 저장소
# =========================
conversation_store = {}
score_store = {}
recall_store = {}


# =========================
# 프롬프트
# =========================
answer_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
당신은 사용자의 질문에 짧고 직접적으로 답변하는 AI입니다.

규칙:
- 반드시 한국어로만 답변하십시오.
- 질문에 없는 사실을 지어내지 마십시오.
- 상황, 장소, 시간, 인물, 사건을 임의로 추가하지 마십시오.
- 소설처럼 서술하지 마십시오.
- 사용자가 물은 내용에만 직접 답하십시오.
- 질문만으로 확실히 알 수 없는 내용은 추측하지 말고 확인이 필요하다고 답하십시오.
- 정보가 부족하면 부족하다고 짧게 설명하십시오.
- 답변은 2~4문장 이내로 간결하게 작성하십시오.
- 'AI:' 같은 접두어를 절대 붙이지 마십시오.
"""
        ),
        (
            "human",
            """
질문: {question}
"""
        )
    ]
)

ANALYSIS_SCORING_GUIDE = """
분석 항목 (총 4개)

1. 같은 질문 또는 의미가 같은 질문의 반복
2. 기억 혼란 또는 기억 상실 표현
3. 시간 또는 상황 인식 혼란
4. 문장 구조의 비논리성 또는 담화 응집성 저하

점수 산정 방식

각 항목을 아래 기준으로 평가하십시오.

[1] 질문반복점수 (0~25점)
0 : 반복 없음
8 : 표현이 일부 반복되지만 의미 반복은 아님
15 : 같은 의미의 질문이 1회 반복
20 : 같은 질문이 거의 동일하게 2회 이상 반복
25 : 답을 들은 직후에도 같은 질문을 반복하는 수준

[2] 기억혼란점수 (0~25점)
0 : 기억 혼란 없음
8 : "뭐였지", "기억이 안 나네" 같은 약한 표현 1회
15 : 기억 공백 또는 기억 혼란이 분명히 나타남
20 : 기억 혼란 표현이 여러 번 나타남
25 : 방금 들은 정보조차 기억하지 못하는 표현

[3] 시간혼란점수 (0~30점)
0 : 혼란 없음
10 : 날짜/요일 등을 잠깐 헷갈림
18 : 현재 시간 또는 상황을 잘못 인식
24 : 시간과 상황을 동시에 혼동
30 : 현재 연도 / 현재 상황에 대한 명백한 혼란

[4] 문장비논리점수 (0~20점)
0 : 문장 구조 자연스러움
5 : 약간 모호하지만 의미 이해 가능
10 : 문장 구조가 흔들려 해석이 불안정
15 : 주제가 자주 이탈하거나 연결이 약함
20 : 의미 응집성이 매우 낮고 비논리성이 강함

최종 점수 계산
의심점수 = 질문반복점수 + 기억혼란점수 + 시간혼란점수 + 문장비논리점수

총점 범위는 0~100입니다.

판단 기준
0~19 → 정상
20~39 → 약한 의심
40~59 → 중간 의심
60~79 → 높은 의심
80~100 → 매우 높은 의심

판단 규칙
- 0~19점이면 판단: 정상
- 20점 이상이면 판단: 의심
- 분석 근거가 부족하면 판단: 판단 어려움
"""

ANALYSIS_OUTPUT_RULES = """
중요 규칙

- 반드시 한국어로 작성하십시오.
- 질문에 대한 답변을 절대 하지 마십시오.
- 반드시 아래 형식만 출력하십시오.
- 다른 문장은 절대 출력하지 마십시오.
- 근거에는 실제 분석 내용만 작성하십시오.
- '한 문장 이상', '두 문장 이상', '작성하십시오' 같은 지시문을 그대로 출력하면 실패입니다.
- 근거는 반드시 실제 분석 문장 2문장 이상 작성하십시오.
- 의심점수는 반드시 0~100 사이 정수 하나만 작성하십시오.
- 각 세부 점수도 반드시 정수 하나만 작성하십시오.
- 의심점수는 네 개 세부 점수의 합과 같아야 합니다.

출력 형식

판단:
의심점수:
질문반복점수:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
"""

analysis_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            f"""
당신은 대화 기반 치매 징후 분석기입니다.

절대 질문에 답변하지 마십시오.
조언하지 마십시오.
설명하지 마십시오.

가능하면 현재 질문뿐 아니라 대화 맥락에서 나타나는 반복 질문이나 기억 혼란 패턴도 함께 고려하여 판단하십시오.
단일 질문만으로 강한 판단을 내리지 말고 언어적 특징이 명확할 때만 높은 점수를 부여하십시오.

사용자의 질문에 나타난 언어적 특징을 분석하여 치매 의심 징후 점수를 계산하십시오.

{ANALYSIS_SCORING_GUIDE}

{ANALYSIS_OUTPUT_RULES}
"""
        ),
        (
            "human",
            """
현재 질문: {question}

반드시 아래 형식만 출력하세요.

판단:
의심점수:
질문반복점수:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
"""
        )
    ]
)

analysis_retry_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            f"""
당신은 대화 기반 치매 징후 분석기입니다.

질문에 답변하지 마십시오.
조언하지 마십시오.
설명하지 마십시오.

이전 응답은 형식이 잘못되었습니다.
점수 기준에 따라 다시 분석하고 형식을 정확히 맞추십시오.

가능하면 현재 질문뿐 아니라 대화 맥락에서 나타나는 반복 질문이나 기억 혼란 패턴도 함께 고려하여 판단하십시오.
단일 질문만으로 강한 판단을 내리지 말고 언어적 특징이 명확할 때만 높은 점수를 부여하십시오.

{ANALYSIS_SCORING_GUIDE}

{ANALYSIS_OUTPUT_RULES}
"""
        ),
        (
            "human",
            """
현재 질문: {question}

이전 응답:
{previous_response}

위 응답은 형식이 불완전했습니다.
같은 오류를 반복하지 말고 다시 작성하십시오.

판단:
의심점수:
질문반복점수:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
"""
        )
    ]
)


# =========================
# LLM 로드
# =========================
answer_llm = LlamaCpp(
    model_path=MODEL_PATH,
    temperature=0.2,
    top_p=0.9,
    max_tokens=256,
    n_ctx=4096,
    verbose=False,
)

analysis_llm = LlamaCpp(
    model_path=MODEL_PATH,
    temperature=0.0,
    top_p=0.9,
    max_tokens=256,
    n_ctx=4096,
    verbose=False,
)

answer_chain = LLMChain(prompt=answer_prompt, llm=answer_llm)
analysis_chain = LLMChain(prompt=analysis_prompt, llm=analysis_llm)
analysis_retry_chain = LLMChain(prompt=analysis_retry_prompt, llm=analysis_llm)


# =========================
# Google STT
# =========================
speech_client = speech.SpeechClient()


# =========================
# 유틸
# =========================
def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[ㅋㅎㅠㅜ]{4,}", "", text)

    return text.strip()


def validate_user_text(text: str) -> bool:
    return bool(text and len(text.strip()) >= 2)


def clamp_score(score: int) -> int:
    return max(0, min(score, 100))


def clamp_subscore(score: int, max_value: int) -> int:
    return max(0, min(score, max_value))


def get_or_create_session_id() -> str:
    session_id = request.headers.get("X-Session-Id") or request.args.get("session_id")

    if not session_id:
        session_id = str(uuid.uuid4())

    conversation_store.setdefault(session_id, [])
    score_store.setdefault(session_id, [])
    recall_store.setdefault(
        session_id,
        {
            "status": "idle",
            "target_word": "",
            "prompt": "",
            "last_result": "없음",
            "introduced_turn": 0,
        }
    )

    return session_id


def add_to_history(session_id: str, role: str, content: str) -> None:
    conversation_store.setdefault(session_id, [])
    conversation_store[session_id].append({
        "role": role,
        "content": content
    })

    max_messages = MAX_HISTORY_TURNS * 2
    if len(conversation_store[session_id]) > max_messages:
        conversation_store[session_id] = conversation_store[session_id][-max_messages:]


def get_user_turn_count(session_id: str) -> int:
    history = conversation_store.get(session_id, [])
    return sum(1 for item in history if item["role"] == "user")


def add_score_history(session_id: str, score: int) -> None:
    score_store.setdefault(session_id, [])
    score_store[session_id].append({
        "score": clamp_score(score),
        "time": datetime.now().strftime("%H:%M:%S")
    })

    if len(score_store[session_id]) > MAX_SCORE_HISTORY:
        score_store[session_id] = score_store[session_id][-MAX_SCORE_HISTORY:]


def get_score_history(session_id: str):
    return score_store.get(session_id, [])


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


def get_score_trend(session_id: str, window: int = RECENT_WINDOW) -> str:
    history = get_score_history(session_id)
    if len(history) < 2:
        return "데이터 부족"

    recent = history[-window:]
    if len(recent) < 2:
        return "안정"

    diff = recent[-1]["score"] - recent[0]["score"]

    if diff >= 10:
        return "상승"
    if diff <= -10:
        return "하락"
    return "안정"


def transcribe_audio_file(file_path: str) -> str:
    with open(file_path, "rb") as f:
        content = f.read()

    audio = speech.RecognitionAudio(content=content)

    config = speech.RecognitionConfig(
        language_code="ko-KR",
        enable_automatic_punctuation=True,
        max_alternatives=3,
    )

    response = speech_client.recognize(config=config, audio=audio)

    transcripts = []
    for result in response.results:
        if result.alternatives:
            transcripts.append(result.alternatives[0].transcript)

    return normalize_text(" ".join(transcripts))


# =========================
# Recall Memory Test
# =========================
def get_recall_state(session_id: str) -> dict:
    return recall_store.setdefault(
        session_id,
        {
            "status": "idle",
            "target_word": "",
            "prompt": "",
            "last_result": "없음",
            "introduced_turn": 0,
        }
    )


def evaluate_recall_answer(session_id: str, user_input: str) -> str:
    state = get_recall_state(session_id)

    if state["status"] != "ask":
        return ""

    target_word = state["target_word"].strip()
    normalized_input = normalize_text(user_input)

    if target_word and target_word in normalized_input:
        feedback = f"Recall Test 결과: 정답입니다. 기억 단어는 '{target_word}'였습니다."
        state["last_result"] = "정답"
    else:
        feedback = f"Recall Test 결과: 오답입니다. 기억 단어는 '{target_word}'였습니다."
        state["last_result"] = "오답"

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

    if state["status"] == "memorize" and user_turn_count == state["introduced_turn"] + 1:
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
        "target_word": state["target_word"] if state["status"] == "memorize" else ""
    }


# =========================
# 분석 응답 검증/보정
# =========================
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

    blocked_phrases = [
        "한 문장 이상",
        "두 문장 이상",
        "작성하십시오",
        "출력 형식"
    ]

    return any(phrase in reason for phrase in blocked_phrases)


def parse_analysis_scores(text: str) -> dict:
    def extract_int(pattern: str, default: int = 0) -> int:
        match = re.search(pattern, text)
        return int(match.group(1)) if match else default

    repetition = clamp_subscore(extract_int(r"질문반복점수:\s*(\d+)"), 25)
    memory = clamp_subscore(extract_int(r"기억혼란점수:\s*(\d+)"), 25)
    time_confusion = clamp_subscore(extract_int(r"시간혼란점수:\s*(\d+)"), 30)
    incoherence = clamp_subscore(extract_int(r"문장비논리점수:\s*(\d+)"), 20)

    total = repetition + memory + time_confusion + incoherence

    return {
        "repetition": repetition,
        "memory": memory,
        "time_confusion": time_confusion,
        "incoherence": incoherence,
        "total": clamp_score(total)
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
        r"근거:\s*(.+)"
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

    if is_invalid_reason_text(reason_text) or len(split_sentences(reason_text)) < 2:
        reason_text = get_default_reason()

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
                "incoherence": 0
            }
        }

    parsed = parse_analysis_scores(response_text)
    reason_match = re.search(r"근거:\s*(.+)", response_text, re.DOTALL)
    judgment_match = re.search(r"판단:\s*(.+)", response_text)

    reason = reason_match.group(1).strip() if reason_match else get_default_reason()
    judgment = judgment_match.group(1).strip() if judgment_match else infer_judgment_from_score(parsed["total"])

    if is_invalid_reason_text(reason) or len(split_sentences(reason)) < 2:
        reason = get_default_reason()

    if judgment not in {"정상", "의심", "판단 어려움"}:
        judgment = infer_judgment_from_score(parsed["total"])

    return {
        "judgment": judgment,
        "score": parsed["total"],
        "reason": reason,
        "feature_scores": {
            "repetition": parsed["repetition"],
            "memory": parsed["memory"],
            "time_confusion": parsed["time_confusion"],
            "incoherence": parsed["incoherence"]
        }
    }


def generate_analysis_with_retry(question: str, max_attempts: int = MAX_ANALYSIS_RETRY) -> str:
    previous_response = ""

    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                response = analysis_chain.invoke({
                    "question": question
                })
            else:
                response = analysis_retry_chain.invoke({
                    "question": question,
                    "previous_response": previous_response
                })

            raw_text = response.get("text", "").strip()
            previous_response = raw_text

            if is_analysis_format_complete(raw_text):
                return force_analysis_format(raw_text)

        except Exception as e:
            print(f"[분석 재시도 {attempt + 1} 실패] {e}")

    if previous_response:
        return force_analysis_format(previous_response)

    return get_analysis_fallback_text()


# =========================
# 응답 생성
# =========================
def build_short_input_result() -> dict:
    reason = (
        "대화 내용이 너무 짧아 언어적 특징을 분석하기 어렵습니다. "
        "조금 더 구체적인 입력이 필요합니다."
    )

    return {
        "full_text": (
            "답변: 질문 내용이 너무 짧아 답변하기 어렵습니다.\n\n"
            "판단: 판단 어려움\n"
            "의심점수: 0\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}"
        ),
        "answer": "질문 내용이 너무 짧아 답변하기 어렵습니다.",
        "judgment": "판단 어려움",
        "score": 0,
        "reason": reason,
        "feature_scores": {
            "repetition": 0,
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0
        }
    }


def build_error_result() -> dict:
    reason = "응답 생성 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."

    return {
        "full_text": (
            "답변: 응답 생성 중 문제가 발생했습니다.\n\n"
            "판단: 판단 어려움\n"
            "의심점수: 0\n"
            "질문반복점수: 0\n"
            "기억혼란점수: 0\n"
            "시간혼란점수: 0\n"
            "문장비논리점수: 0\n"
            f"근거: {reason}"
        ),
        "answer": "응답 생성 중 문제가 발생했습니다.",
        "judgment": "판단 어려움",
        "score": 0,
        "reason": reason,
        "feature_scores": {
            "repetition": 0,
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0
        }
    }


def get_response_from_llama(question: str) -> dict:
    question = normalize_text(question)

    if not validate_user_text(question):
        return build_short_input_result()

    try:
        answer_response = answer_chain.invoke({"question": question})
        answer_text = answer_response.get("text", "").strip()
        answer_text = re.sub(r"^\s*AI:\s*", "", answer_text)

        if not answer_text:
            answer_text = "질문에 대한 답변을 생성하지 못했습니다."

        analysis_text = generate_analysis_with_retry(question)
        fields = extract_analysis_fields(analysis_text)

        full_text = (
            f"답변: {answer_text}\n\n"
            f"판단: {fields['judgment']}\n"
            f"의심점수: {fields['score']}\n"
            f"질문반복점수: {fields['feature_scores']['repetition']}\n"
            f"기억혼란점수: {fields['feature_scores']['memory']}\n"
            f"시간혼란점수: {fields['feature_scores']['time_confusion']}\n"
            f"문장비논리점수: {fields['feature_scores']['incoherence']}\n"
            f"근거: {fields['reason']}"
        )

        return {
            "full_text": full_text,
            "answer": answer_text,
            "judgment": fields["judgment"],
            "score": fields["score"],
            "reason": fields["reason"],
            "feature_scores": fields["feature_scores"]
        }

    except Exception as e:
        print(f"LLM 응답 생성 실패: {e}")
        return build_error_result()


# =========================
# 공통 JSON 응답
# =========================
def build_chat_response(
    session_id: str,
    user_speech: str,
    sys_response: str,
    answer: str,
    judgment: str,
    score: int,
    reason: str,
    feature_scores: dict
):
    recent_average_score = get_recent_average_score(session_id)
    risk_level = get_risk_level_from_score(recent_average_score)
    trend = get_score_trend(session_id)

    return jsonify({
        "session_id": session_id,
        "user_speech": user_speech,
        "sys_response": sys_response,
        "answer": answer,
        "judgment": judgment,
        "score": clamp_score(score),
        "reason": reason,
        "feature_scores": feature_scores,
        "average_score": get_average_score(session_id),
        "recent_average_score": recent_average_score,
        "risk_level": risk_level,
        "trend": trend,
        "score_history": get_score_history(session_id),
        "recall": serialize_recall_state(session_id)
    })


# =========================
# 라우트
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transcribe-audio", methods=["POST"])
def transcribe_audio():
    session_id = get_or_create_session_id()

    try:
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

        return jsonify({
            "session_id": session_id,
            "user_speech": user_input
        })

    except Exception as e:
        print(f"STT 처리 오류: {e}")
        return jsonify({"error": "음성 인식 중 문제가 발생했습니다."}), 500


@app.route("/analyze-text", methods=["POST"])
def analyze_text():
    session_id = get_or_create_session_id()

    try:
        data = request.get_json(silent=True) or {}
        user_input = normalize_text(data.get("message", ""))

        if not user_input:
            return jsonify({"error": "분석할 텍스트가 없습니다."}), 400

        recall_feedback = evaluate_recall_answer(session_id, user_input)
        result = get_response_from_llama(user_input)

        if recall_feedback:
            result["answer"] = f"{result['answer']}\n\n{recall_feedback}"

        add_to_history(session_id, "user", user_input)
        add_to_history(session_id, "assistant", result["full_text"])
        add_score_history(session_id, result["score"])

        recall_prompt = maybe_advance_recall_test(session_id)
        if recall_prompt:
            result["answer"] = f"{result['answer']}\n\n{recall_prompt}"
            result["full_text"] = f"{result['full_text']}\n\n{recall_prompt}"

        return build_chat_response(
            session_id=session_id,
            user_speech=user_input,
            sys_response=result["full_text"],
            answer=result["answer"],
            judgment=result["judgment"],
            score=result["score"],
            reason=result["reason"],
            feature_scores=result["feature_scores"]
        )

    except Exception as e:
        print(f"텍스트 분석 오류: {e}")
        return jsonify({"error": "텍스트 분석 중 문제가 발생했습니다."}), 500


@app.route("/chat", methods=["POST"])
def chat():
    session_id = get_or_create_session_id()
    user_input = ""

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
                    sys_response="판단: 판단 어려움\n의심점수: 0\n질문반복점수: 0\n기억혼란점수: 0\n시간혼란점수: 0\n문장비논리점수: 0\n근거: 음성 인식 결과가 없어 분석할 수 없습니다. 다시 녹음해 주세요.",
                    answer="",
                    judgment="판단 어려움",
                    score=0,
                    reason="음성 인식 결과가 없어 분석할 수 없습니다. 다시 녹음해 주세요.",
                    feature_scores={
                        "repetition": 0,
                        "memory": 0,
                        "time_confusion": 0,
                        "incoherence": 0
                    }
                )
        else:
            data = request.get_json(silent=True) or {}
            user_input = normalize_text(data.get("message", ""))

            if not user_input:
                return build_chat_response(
                    session_id=session_id,
                    user_speech="",
                    sys_response="판단: 판단 어려움\n의심점수: 0\n질문반복점수: 0\n기억혼란점수: 0\n시간혼란점수: 0\n문장비논리점수: 0\n근거: 입력된 대화가 없습니다. 분석할 내용이 필요합니다.",
                    answer="",
                    judgment="판단 어려움",
                    score=0,
                    reason="입력된 대화가 없습니다. 분석할 내용이 필요합니다.",
                    feature_scores={
                        "repetition": 0,
                        "memory": 0,
                        "time_confusion": 0,
                        "incoherence": 0
                    }
                )

        recall_feedback = evaluate_recall_answer(session_id, user_input)
        result = get_response_from_llama(user_input)

        if recall_feedback:
            result["answer"] = f"{result['answer']}\n\n{recall_feedback}"

        add_to_history(session_id, "user", user_input)
        add_to_history(session_id, "assistant", result["full_text"])
        add_score_history(session_id, result["score"])

        recall_prompt = maybe_advance_recall_test(session_id)
        if recall_prompt:
            result["answer"] = f"{result['answer']}\n\n{recall_prompt}"
            result["full_text"] = f"{result['full_text']}\n\n{recall_prompt}"

        return build_chat_response(
            session_id=session_id,
            user_speech=user_input,
            sys_response=result["full_text"],
            answer=result["answer"],
            judgment=result["judgment"],
            score=result["score"],
            reason=result["reason"],
            feature_scores=result["feature_scores"]
        )

    except Exception as e:
        print(f"서버 오류: {e}")
        return jsonify({"error": "서버 처리 중 문제가 발생했습니다."}), 500


@app.route("/score-history", methods=["GET"])
def score_history():
    session_id = get_or_create_session_id()

    return jsonify({
        "session_id": session_id,
        "average_score": get_average_score(session_id),
        "recent_average_score": get_recent_average_score(session_id),
        "risk_level": get_risk_level_from_score(get_recent_average_score(session_id)),
        "trend": get_score_trend(session_id),
        "score_history": get_score_history(session_id),
        "recall": serialize_recall_state(session_id)
    })


@app.route("/reset-history", methods=["POST"])
def reset_history():
    session_id = get_or_create_session_id()

    conversation_store[session_id] = []
    score_store[session_id] = []
    recall_store[session_id] = {
        "status": "idle",
        "target_word": "",
        "prompt": "",
        "last_result": "없음",
        "introduced_turn": 0,
    }

    return jsonify({
        "session_id": session_id,
        "message": "기록이 초기화되었습니다.",
        "average_score": 0.0,
        "recent_average_score": 0.0,
        "risk_level": "Normal",
        "trend": "데이터 부족",
        "score_history": [],
        "recall": serialize_recall_state(session_id)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)