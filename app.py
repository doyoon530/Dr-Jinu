from flask import Flask, render_template, request, jsonify
from google.cloud import speech
from langchain_core.prompts import ChatPromptTemplate
from langchain_classic.chains import LLMChain
from langchain_community.llms import LlamaCpp
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

import os
import re
import uuid
import random
import socket
import json
import tempfile
import urllib.request
import urllib.error
from datetime import datetime
from difflib import SequenceMatcher

try:
    from waitress import serve
except ImportError:
    serve = None


# =========================
# 기본 설정
# =========================
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DEFAULT_MODEL_PATH = os.path.join(
    BASE_DIR, "models", "EXAONE-3.5-7.8B-Instruct-Q8_0.gguf"
)
DEFAULT_GOOGLE_KEY_PATH = os.path.join(BASE_DIR, "stt-bot-489913-807430be631b.json")
DEFAULT_API_LLM_BASE_URL = "https://api.openai.com/v1"
SUPPORTED_LLM_PROVIDERS = {"local", "api"}

os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_HISTORY_TURNS = 12
MAX_SCORE_HISTORY = 30
MAX_ANALYSIS_RETRY = 3
RECENT_WINDOW = 5
ANALYSIS_CONTEXT_TURNS = 3
REPETITION_CONTEXT_TURNS = 4
REPETITION_SCORE_OPTIONS = [0, 8, 15, 20, 25]
MEMORY_STRONG_PATTERNS = [
    r"기억이\s*안\s*나",
    r"기억이\s*잘\s*안\s*나",
    r"기억이\s*안\s*나네",
    r"기억이\s*안\s*나요",
    r"기억이\s*나지\s*않",
    r"기억이\s*흐릿",
    r"기억이\s*가물가물",
    r"까먹",
    r"잊어버",
]
MEMORY_MILD_PATTERNS = [
    r"생각이\s*안\s*나",
    r"헷갈",
    r"잘\s*모르겠",
]
TIME_REFERENCE_PATTERNS = [
    r"언제",
    r"몇\s*시",
    r"몇시",
    r"시간",
    r"날짜",
    r"요일",
    r"오늘",
    r"어제",
    r"내일",
    r"오전",
    r"오후",
    r"수업",
    r"회의",
    r"약속",
    r"일정",
]
TIME_CONFUSION_PATTERNS = [
    r"언제였",
    r"언제인지",
    r"몇\s*시인지",
    r"몇시인지",
    r"모르겠",
    r"헷갈",
    r"기억이\s*안\s*나",
    r"까먹",
    r"잊어버",
]

RECALL_WORDS = [
    "사과",
    "버스",
    "바다",
    "연필",
    "시계",
    "나무",
    "기차",
    "고양이",
    "책상",
    "우산",
    "커피",
    "구름",
    "노트",
    "수건",
    "안경",
]


# =========================
# 메모리 저장소
# =========================
conversation_store = {}
score_store = {}
recall_store = {}
turn_store = {}
answer_chain = None
analysis_chain = None
analysis_retry_chain = None
analysis_repetition_chain = None
analysis_feature_chain = None
analysis_feature_retry_chain = None
analysis_llm_instance = None
role_analysis_chains = {}
role_analysis_retry_chains = {}
speech_client = None
temp_google_credentials_path = None
analysis_runtime_cache = {}


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
""",
        ),
        (
            "human",
            """
질문: {question}
""",
        ),
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
질문반복점수는 최근 대화에서 같은 의미의 질문이 다시 제시되었는지, 특히 직전 답변 직후 비슷한 질문이 반복되었는지를 우선 확인하여 평가하십시오.
단일 질문만으로 강한 판단을 내리지 말고 언어적 특징이 명확할 때만 높은 점수를 부여하십시오.

사용자의 질문에 나타난 언어적 특징을 분석하여 치매 의심 징후 점수를 계산하십시오.

{ANALYSIS_SCORING_GUIDE}

{ANALYSIS_OUTPUT_RULES}
""",
        ),
        (
            "human",
            """
최근 대화 맥락:
{conversation_context}

현재 질문: {question}

반드시 아래 형식만 출력하세요.

판단:
의심점수:
질문반복점수:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
""",
        ),
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
질문반복점수는 최근 대화에서 같은 의미의 질문이 다시 제시되었는지, 특히 직전 답변 직후 비슷한 질문이 반복되었는지를 우선 확인하여 평가하십시오.
단일 질문만으로 강한 판단을 내리지 말고 언어적 특징이 명확할 때만 높은 점수를 부여하십시오.

{ANALYSIS_SCORING_GUIDE}

{ANALYSIS_OUTPUT_RULES}
""",
        ),
        (
            "human",
            """
최근 대화 맥락:
{conversation_context}

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
""",
        ),
    ]
)

repetition_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
당신은 사용자의 현재 질문이 최근 사용자 질문의 반복인지 판정하는 보조 분석기입니다.

오직 질문 반복 여부만 판단하십시오.
기억 혼란, 시간 혼란, 문장 비논리성은 여기서 평가하지 마십시오.

판단 규칙:
- 단어 순서, 조사, 어미가 조금 달라도 핵심 요청이 같으면 같은 의미의 질문일 수 있습니다.
- "기억이 안 난다", "몇 시지", "몇 시일까"처럼 표현 위치만 달라도 핵심 질문이 같으면 반복으로 판단할 수 있습니다.
- 같은 주제만 공유하고 실제 요청이 다르면 반복으로 보지 마십시오.
- 최근 AI 답변 직후 같은 의미 질문이 다시 나오면 더 높은 점수를 줄 수 있습니다.

질문반복점수는 반드시 아래 다섯 값 중 하나만 사용하십시오:
0, 8, 15, 20, 25

출력 형식:
질문반복점수:
반복대상:
근거:
""",
        ),
        (
            "human",
            """
최근 사용자 질문과 당시 AI 답변:
{recent_user_questions}

현재 질문:
{question}

반드시 형식만 출력하세요.
""",
        ),
    ]
)

feature_analysis_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
당신은 대화 기반 인지 위험 징후 분석기입니다.

오직 기억 혼란, 시간/상황 혼란, 문장 비논리성만 평가하십시오.
질문 반복 여부는 이미 별도 분석기가 담당하므로 여기서 점수화하지 마십시오.

중요 규칙:
- 질문에 답변하지 마십시오.
- 조언하지 마십시오.
- 같은 질문이 반복되더라도 반복 자체를 이유로 기억혼란점수, 시간혼란점수, 문장비논리점수를 올리지 마십시오.
- 오직 현재 질문과 대화 맥락에서 실제로 드러난 기억 회상 어려움, 시간·상황 혼란, 문장 논리성만 평가하십시오.
- 근거는 반드시 한국어 문장 2문장 이상으로 작성하십시오.
- 형식 외 다른 문장을 출력하면 실패입니다.

점수 범위:
- 기억혼란점수: 0~25
- 시간혼란점수: 0~30
- 문장비논리점수: 0~20

출력 형식:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
""",
        ),
        (
            "human",
            """
최근 대화 맥락:
{conversation_context}

현재 질문:
{question}

반드시 위 형식만 출력하세요.
""",
        ),
    ]
)

feature_analysis_retry_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
당신은 대화 기반 인지 위험 징후 분석기입니다.

오직 기억 혼란, 시간/상황 혼란, 문장 비논리성만 평가하십시오.
질문 반복 여부는 이미 별도 분석기가 담당하므로 여기서 점수화하지 마십시오.

이전 응답은 형식이 잘못되었습니다.
형식을 정확히 맞추고, 반복 질문이라는 이유만으로 다른 점수를 자동으로 올리지 마십시오.

출력 형식:
기억혼란점수:
시간혼란점수:
문장비논리점수:
근거:
""",
        ),
        (
            "human",
            """
최근 대화 맥락:
{conversation_context}

현재 질문:
{question}

이전 응답:
{previous_response}

반드시 위 형식만 다시 출력하세요.
""",
        ),
    ]
)


ROLE_ANALYSIS_META = {
    "memory": {
        "title": "기억 혼란",
        "score_label": "기억혼란점수",
        "max_score": 25,
        "focus_rule": "기억이 안 난다, 떠오르지 않는다, 방금 들은 내용을 바로 회상하지 못한다는 식의 기억 회상 어려움만 평가하십시오.",
    },
    "time_confusion": {
        "title": "시간/상황 혼란",
        "score_label": "시간혼란점수",
        "max_score": 30,
        "focus_rule": "언제, 몇 시, 오늘 일정, 현재 상황 같은 시간·상황 정보를 혼동하거나 바로 떠올리지 못하는 표현만 평가하십시오.",
    },
    "incoherence": {
        "title": "문장 비논리성",
        "score_label": "문장비논리점수",
        "max_score": 20,
        "focus_rule": "문장 전개가 비약적이거나 앞뒤 논리가 약한 경우만 평가하십시오.",
    },
}

ROLE_ANALYSIS_ORDER = ["repetition", "memory", "time_confusion", "incoherence"]


def normalize_role_key(role_key: str) -> str:
    normalized = str(role_key or "").strip().lower()
    aliases = {
        "repetition": "repetition",
        "memory": "memory",
        "time_confusion": "time_confusion",
        "time": "time_confusion",
        "incoherence": "incoherence",
    }
    return aliases.get(normalized, "")


def build_role_prompt(role_key: str) -> ChatPromptTemplate:
    meta = ROLE_ANALYSIS_META[role_key]
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"""
당신은 대화 기반 인지 위험 징후 분석기입니다.

오직 {meta["title"]}만 평가하십시오.
질문 반복, 다른 역할 점수, 전체 판단은 여기서 평가하지 마십시오.

평가 규칙:
- 질문에 답변하지 마십시오.
- 조언하지 마십시오.
- {meta["focus_rule"]}
- 다른 특징이 보여도 {meta["title"]}과 직접 관련이 없으면 점수에 반영하지 마십시오.
- 근거는 반드시 한국어 문장 2문장 이상으로 작성하십시오.

점수 범위:
- {meta["score_label"]}: 0~{meta["max_score"]}

출력 형식:
{meta["score_label"]}:
근거:
""",
            ),
            (
                "human",
                """
최근 대화 맥락:
{conversation_context}

현재 질문:
{question}

반드시 위 형식만 출력하세요.
""",
            ),
        ]
    )


def build_role_retry_prompt(role_key: str) -> ChatPromptTemplate:
    meta = ROLE_ANALYSIS_META[role_key]
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                f"""
당신은 대화 기반 인지 위험 징후 분석기입니다.

오직 {meta["title"]}만 평가하십시오.
이전 응답은 형식이 잘못되었습니다.

중요:
- 질문 반복이나 다른 점수는 고려하지 마십시오.
- 형식 외 다른 문장을 출력하지 마십시오.

출력 형식:
{meta["score_label"]}:
근거:
""",
            ),
            (
                "human",
                """
최근 대화 맥락:
{conversation_context}

현재 질문:
{question}

이전 응답:
{previous_response}

반드시 위 형식만 다시 출력하세요.
""",
            ),
        ]
    )


ROLE_ANALYSIS_PROMPTS = {
    role_key: build_role_prompt(role_key) for role_key in ROLE_ANALYSIS_META
}

ROLE_ANALYSIS_RETRY_PROMPTS = {
    role_key: build_role_retry_prompt(role_key) for role_key in ROLE_ANALYSIS_META
}


# =========================
# LLM 로드
# =========================
def get_model_path() -> str:
    return os.getenv("MODEL_PATH", DEFAULT_MODEL_PATH)


def normalize_llm_provider(provider: str | None) -> str:
    normalized = normalize_text(str(provider or "")).lower()
    if normalized == "api":
        return "api"
    return "local"


def get_default_llm_provider() -> str:
    return normalize_llm_provider(os.getenv("LLM_PROVIDER", "local"))


def get_api_llm_base_url() -> str:
    return os.getenv("API_LLM_BASE_URL", DEFAULT_API_LLM_BASE_URL).strip().rstrip("/")


def get_api_llm_api_key() -> str:
    return os.getenv("API_LLM_API_KEY", "").strip()


def get_api_llm_answer_model() -> str:
    return os.getenv("API_LLM_ANSWER_MODEL", "").strip()


def get_api_llm_analysis_model() -> str:
    return os.getenv("API_LLM_ANALYSIS_MODEL", "").strip() or get_api_llm_answer_model()


def get_api_llm_timeout() -> int:
    return get_positive_int_env("API_LLM_TIMEOUT", 60)


def is_api_llm_configured() -> bool:
    return bool(
        get_api_llm_api_key()
        and get_api_llm_answer_model()
        and get_api_llm_analysis_model()
    )


def get_positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
        if parsed < 1:
            raise ValueError
        return parsed
    except ValueError:
        print(f"[config] Invalid {name}={raw_value!r}; using default {default}")
        return default


def get_analysis_n_ctx() -> int:
    return get_positive_int_env("ANALYSIS_N_CTX", 8192)


def get_analysis_max_tokens() -> int:
    return get_positive_int_env("ANALYSIS_MAX_TOKENS", 384)


def get_analysis_n_batch() -> int:
    n_ctx = get_analysis_n_ctx()
    requested = get_positive_int_env("ANALYSIS_N_BATCH", 512)
    return max(64, min(requested, n_ctx))


def setup_google_credentials() -> str | None:
    global temp_google_credentials_path

    explicit_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit_path:
        return explicit_path

    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if credentials_json:
        if temp_google_credentials_path and os.path.exists(
            temp_google_credentials_path
        ):
            return temp_google_credentials_path

        fd, temp_path = tempfile.mkstemp(prefix="gcp-creds-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(json.loads(credentials_json), temp_file, ensure_ascii=False)

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_path
        temp_google_credentials_path = temp_path
        return temp_path

    if os.path.exists(DEFAULT_GOOGLE_KEY_PATH):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = DEFAULT_GOOGLE_KEY_PATH
        return DEFAULT_GOOGLE_KEY_PATH

    return None


def get_requested_llm_provider(data=None) -> str:
    requested = ""

    if isinstance(data, dict):
        requested = str(data.get("llm_provider", "") or "")

    header_value = request.headers.get("X-LLM-Provider", "")
    return normalize_llm_provider(
        requested or header_value or get_default_llm_provider()
    )


def get_llm_provider_status() -> dict:
    model_status = get_model_status()
    local_ready = bool(model_status["exists"])
    api_ready = is_api_llm_configured()

    return {
        "default": get_default_llm_provider(),
        "supported": sorted(SUPPORTED_LLM_PROVIDERS),
        "local": {
            "ready": local_ready,
            "label": "로컬 모델",
            "model_path": model_status["path"],
            "model_exists": local_ready,
        },
        "api": {
            "ready": api_ready,
            "label": "외부 API",
            "base_url": get_api_llm_base_url(),
            "answer_model": get_api_llm_answer_model(),
            "analysis_model": get_api_llm_analysis_model(),
        },
    }


def flatten_prompt_message_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def build_api_chat_messages(
    prompt_template: ChatPromptTemplate, variables: dict
) -> list[dict]:
    messages = []
    formatted_messages = prompt_template.format_messages(**variables)

    for message in formatted_messages:
        role = "user"
        if message.type == "system":
            role = "system"
        elif message.type in {"ai", "assistant"}:
            role = "assistant"

        messages.append(
            {
                "role": role,
                "content": flatten_prompt_message_content(message.content),
            }
        )

    return messages


def extract_api_message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content or "")


def invoke_api_chat_completion(
    messages: list[dict], model: str, temperature: float = 0.0, max_tokens: int = 256
) -> str:
    api_key = get_api_llm_api_key()
    if not api_key:
        raise RuntimeError(
            "API 모드가 설정되지 않았습니다. API_LLM_API_KEY를 먼저 설정해주세요."
        )
    if not model:
        raise RuntimeError(
            "API 모드가 설정되지 않았습니다. 사용할 모델 이름을 먼저 설정해주세요."
        )

    endpoint = f"{get_api_llm_base_url()}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    request_obj = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request_obj, timeout=get_api_llm_timeout()
        ) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = ""
        try:
            error_body = error.read().decode("utf-8")
        except Exception:
            error_body = str(error)
        raise RuntimeError(f"외부 API 호출이 실패했습니다. {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("외부 API 서버에 연결하지 못했습니다.") from error

    choices = response_payload.get("choices") or []
    if not choices:
        raise RuntimeError("외부 API 응답에 choices가 없습니다.")

    message = choices[0].get("message", {})
    return extract_api_message_text(message.get("content", "")).strip()


def invoke_api_prompt(
    prompt_template: ChatPromptTemplate,
    variables: dict,
    model_kind: str,
    temperature: float = 0.0,
    max_tokens: int = 256,
) -> dict:
    model = (
        get_api_llm_answer_model()
        if model_kind == "answer"
        else get_api_llm_analysis_model()
    )
    messages = build_api_chat_messages(prompt_template, variables)
    return {
        "text": invoke_api_chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    }


def get_model_status() -> dict:
    model_path = get_model_path()
    return {"path": model_path, "exists": os.path.exists(model_path)}


def get_google_credentials_status() -> dict:
    credentials_path = setup_google_credentials()
    return {"path": credentials_path or "", "configured": bool(credentials_path)}


def get_or_create_answer_chain():
    global answer_chain

    if answer_chain is not None:
        return answer_chain

    model_path = get_model_path()
    if not os.path.exists(model_path):
        raise RuntimeError(
            f"Model file not found: {model_path}. Set MODEL_PATH to a valid GGUF model path."
        )

    answer_llm = LlamaCpp(
        model_path=model_path,
        temperature=0.2,
        top_p=0.9,
        max_tokens=256,
        n_ctx=4096,
        verbose=False,
    )
    answer_chain = LLMChain(prompt=answer_prompt, llm=answer_llm)
    return answer_chain


def get_or_create_analysis_chains():
    global analysis_chain, analysis_retry_chain, analysis_repetition_chain
    global analysis_feature_chain, analysis_feature_retry_chain, analysis_llm_instance

    if (
        analysis_chain is not None
        and analysis_retry_chain is not None
        and analysis_repetition_chain is not None
        and analysis_feature_chain is not None
        and analysis_feature_retry_chain is not None
    ):
        return analysis_chain, analysis_retry_chain

    model_path = get_model_path()
    if not os.path.exists(model_path):
        raise RuntimeError(
            f"Model file not found: {model_path}. Set MODEL_PATH to a valid GGUF model path."
        )

    if analysis_llm_instance is None:
        analysis_llm_instance = LlamaCpp(
            model_path=model_path,
            temperature=0.0,
            top_p=0.9,
            max_tokens=get_analysis_max_tokens(),
            n_ctx=get_analysis_n_ctx(),
            n_batch=get_analysis_n_batch(),
            verbose=False,
        )

    if analysis_chain is None:
        analysis_chain = LLMChain(prompt=analysis_prompt, llm=analysis_llm_instance)
    if analysis_retry_chain is None:
        analysis_retry_chain = LLMChain(
            prompt=analysis_retry_prompt, llm=analysis_llm_instance
        )
    if analysis_repetition_chain is None:
        analysis_repetition_chain = LLMChain(
            prompt=repetition_prompt, llm=analysis_llm_instance
        )
    if analysis_feature_chain is None:
        analysis_feature_chain = LLMChain(
            prompt=feature_analysis_prompt, llm=analysis_llm_instance
        )
    if analysis_feature_retry_chain is None:
        analysis_feature_retry_chain = LLMChain(
            prompt=feature_analysis_retry_prompt, llm=analysis_llm_instance
        )
    return analysis_chain, analysis_retry_chain


def get_or_create_repetition_chain():
    global analysis_repetition_chain

    if analysis_repetition_chain is None:
        get_or_create_analysis_chains()

    return analysis_repetition_chain


def get_or_create_feature_analysis_chains():
    global analysis_feature_chain, analysis_feature_retry_chain

    if analysis_feature_chain is None or analysis_feature_retry_chain is None:
        get_or_create_analysis_chains()

    return analysis_feature_chain, analysis_feature_retry_chain


def get_or_create_role_analysis_chains(role_key: str):
    global role_analysis_chains, role_analysis_retry_chains, analysis_llm_instance

    if role_key not in ROLE_ANALYSIS_META:
        raise ValueError(f"Unsupported analysis role: {role_key}")

    if role_key in role_analysis_chains and role_key in role_analysis_retry_chains:
        return role_analysis_chains[role_key], role_analysis_retry_chains[role_key]

    if analysis_llm_instance is None:
        get_or_create_analysis_chains()

    if role_key not in role_analysis_chains:
        role_analysis_chains[role_key] = LLMChain(
            prompt=ROLE_ANALYSIS_PROMPTS[role_key],
            llm=analysis_llm_instance,
        )

    if role_key not in role_analysis_retry_chains:
        role_analysis_retry_chains[role_key] = LLMChain(
            prompt=ROLE_ANALYSIS_RETRY_PROMPTS[role_key],
            llm=analysis_llm_instance,
        )

    return role_analysis_chains[role_key], role_analysis_retry_chains[role_key]


def get_or_create_speech_client():
    global speech_client

    if speech_client is not None:
        return speech_client

    credentials_path = setup_google_credentials()
    if not credentials_path:
        raise RuntimeError(
            "Google STT credentials are not configured. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON."
        )

    speech_client = speech.SpeechClient()
    return speech_client


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
    turn_store.setdefault(session_id, [])
    recall_store.setdefault(
        session_id,
        {
            "status": "idle",
            "target_word": "",
            "prompt": "",
            "last_result": "없음",
            "introduced_turn": 0,
        },
    )

    return session_id


def add_to_history(session_id: str, role: str, content: str) -> None:
    conversation_store.setdefault(session_id, [])
    conversation_store[session_id].append({"role": role, "content": content})

    max_messages = MAX_HISTORY_TURNS * 2
    if len(conversation_store[session_id]) > max_messages:
        conversation_store[session_id] = conversation_store[session_id][-max_messages:]


def build_analysis_context_from_turns(
    turns, max_turns: int = ANALYSIS_CONTEXT_TURNS
) -> str:
    if not turns:
        return "이전 대화 없음"

    recent_turns = turns[-max_turns:]
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

    if not context_lines:
        return "이전 대화 없음"

    return "\n".join(context_lines)


def build_analysis_context(
    session_id: str | None, max_turns: int = ANALYSIS_CONTEXT_TURNS
) -> str:
    if not session_id:
        return "이전 대화 없음"

    turns = turn_store.get(session_id, [])
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

    if len(recent_turns) > max_turns:
        recent_turns = recent_turns[-max_turns:]

    return recent_turns


def get_analysis_runtime_state(session_id: str | None) -> dict:
    if not session_id:
        return {
            "analysis_context": "이전 대화 없음",
            "previous_turns": [],
            "turn_count": 0,
        }

    turns = turn_store.get(session_id, [])
    turn_count = len(turns)
    cached = analysis_runtime_cache.get(session_id)
    if cached and cached.get("turn_count") == turn_count:
        return cached

    runtime_state = {
        "analysis_context": build_analysis_context_from_turns(turns),
        "previous_turns": get_recent_user_turns(turns),
        "turn_count": turn_count,
    }
    analysis_runtime_cache[session_id] = runtime_state
    return runtime_state


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


def contains_any_pattern(text: str, patterns) -> bool:
    if not text:
        return False

    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def detect_language_feature_signal(question: str) -> dict:
    normalized_question = normalize_text(question)
    if not normalized_question:
        return {
            "memory": 0,
            "time_confusion": 0,
            "incoherence": 0,
            "reason": "",
        }

    memory_score = 0
    time_confusion_score = 0
    observations = []

    if contains_any_pattern(normalized_question, MEMORY_STRONG_PATTERNS):
        memory_score = 15
        observations.append(
            "질문에 '기억이 안 나', '까먹었다'처럼 기억 회상을 직접 어려워하는 표현이 포함되어 기억 혼란 신호가 관찰됩니다."
        )
    elif contains_any_pattern(normalized_question, MEMORY_MILD_PATTERNS):
        memory_score = 8
        observations.append(
            "질문 표현에서 기억을 바로 떠올리지 못하거나 혼동하는 모습이 약하게 관찰됩니다."
        )

    has_time_reference = contains_any_pattern(
        normalized_question, TIME_REFERENCE_PATTERNS
    )
    has_time_confusion = contains_any_pattern(
        normalized_question, TIME_CONFUSION_PATTERNS
    )

    if has_time_reference and has_time_confusion:
        time_confusion_score = 12 if memory_score >= 15 else 8
        observations.append(
            "언제, 몇 시, 오늘 일정 같은 시간 정보를 바로 떠올리지 못하는 표현이 함께 나타나 시간·상황 혼란 신호도 관찰됩니다."
        )

    return {
        "memory": memory_score,
        "time_confusion": time_confusion_score,
        "incoherence": 0,
        "reason": " ".join(observations).strip(),
    }


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


def merge_repetition_reason(existing_reason: str, repetition_reason: str) -> str:
    return merge_reason_text(existing_reason, repetition_reason)


def detect_repetition_signal(
    question: str, previous_turns, use_llm: bool = True
) -> dict:
    heuristic_result = analyze_repetition_by_similarity(question, previous_turns)
    best_result = dict(heuristic_result)

    if not use_llm or not previous_turns:
        return best_result

    try:
        repetition_chain = get_or_create_repetition_chain()
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
        print(f"[질문 반복 전용 판별 실패] {e}")

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


def apply_repetition_guardrail(question: str, fields: dict, previous_turns) -> dict:
    if not fields or not previous_turns:
        return fields

    repetition_signal = detect_repetition_signal(question, previous_turns, use_llm=True)
    current_repetition = clamp_subscore(
        int(fields["feature_scores"].get("repetition", 0)), 25
    )
    boosted_repetition = max(current_repetition, repetition_signal.get("score", 0))

    if boosted_repetition <= current_repetition:
        return fields

    fields["feature_scores"]["repetition"] = boosted_repetition
    fields["score"] = clamp_score(
        boosted_repetition
        + int(fields["feature_scores"].get("memory", 0))
        + int(fields["feature_scores"].get("time_confusion", 0))
        + int(fields["feature_scores"].get("incoherence", 0))
    )

    if fields.get("judgment") != "판단 어려움":
        fields["judgment"] = infer_judgment_from_score(fields["score"])

    fields["reason"] = merge_reason_text(
        fields.get("reason", ""), repetition_signal.get("reason", "")
    )
    return fields


def apply_language_feature_guardrail(question: str, fields: dict) -> dict:
    if not fields:
        return fields

    language_signal = detect_language_feature_signal(question)
    feature_scores = fields.get("feature_scores", {})
    updated = False

    boost_targets = [
        ("memory", 25),
        ("time_confusion", 30),
        ("incoherence", 20),
    ]

    for key, max_value in boost_targets:
        current_value = clamp_subscore(int(feature_scores.get(key, 0)), max_value)
        boosted_value = max(current_value, int(language_signal.get(key, 0)))
        if boosted_value > current_value:
            feature_scores[key] = boosted_value
            updated = True

    if not updated:
        return fields

    fields["feature_scores"] = feature_scores
    fields["score"] = clamp_score(
        int(feature_scores.get("repetition", 0))
        + int(feature_scores.get("memory", 0))
        + int(feature_scores.get("time_confusion", 0))
        + int(feature_scores.get("incoherence", 0))
    )

    if fields.get("judgment") != "판단 어려움":
        fields["judgment"] = infer_judgment_from_score(fields["score"])

    fields["reason"] = merge_reason_text(
        fields.get("reason", ""), language_signal.get("reason", "")
    )
    return fields


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
    turn_store.setdefault(session_id, [])

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

    turn_store[session_id].append(turn)

    if len(turn_store[session_id]) > MAX_SCORE_HISTORY:
        turn_store[session_id] = turn_store[session_id][-MAX_SCORE_HISTORY:]

    turn["average_score"] = get_average_score(session_id)
    turn["recent_average_score"] = get_recent_average_score(session_id)

    if turn["score_included"]:
        turn["risk_level"] = get_risk_level_from_score(turn["score"])
        turn["trend"] = get_score_trend(session_id)
        turn["confidence"] = calculate_confidence_from_feature_scores(
            turn["feature_scores"], turn["score"]
        )
    else:
        turn["risk_level"] = "반영 제외"
        turn["trend"] = "반영 제외"
        turn["confidence"] = 0

    return turn


def calculate_trend_from_score_values(scores, window: int = RECENT_WINDOW) -> str:
    if len(scores) < 2:
        return "데이터 부족"

    recent = scores[-window:]
    if len(recent) < 2:
        return "안정"

    diff = recent[-1] - recent[0]

    if diff >= 10:
        return "상승"
    if diff <= -10:
        return "하락"
    return "안정"


def repair_session_analysis_history(session_id: str) -> None:
    turns = turn_store.get(session_id, [])
    if not turns:
        return

    existing_scores = score_store.get(session_id, [])
    running_scores = []

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
        if judgment not in {"정상", "의심", "판단 어려움"}:
            judgment = infer_judgment_from_score(current_score)
        if score_included and (
            (judgment == "정상" and current_score >= 20)
            or (judgment == "의심" and current_score < 20)
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
        normalized_reason = normalize_reason_text(
            turn.get("reason", ""), repaired_feature_scores
        )
        turn["reason"] = normalized_reason

        if score_included:
            turn["confidence"] = calculate_confidence_from_feature_scores(
                repaired_feature_scores, current_score
            )
            turn["risk_level"] = get_risk_level_from_score(current_score)
            running_scores.append(current_score)
            turn["trend"] = calculate_trend_from_score_values(running_scores)
        else:
            turn["confidence"] = 0
            turn["risk_level"] = "반영 제외"
            turn["trend"] = "반영 제외"

        if running_scores:
            turn["average_score"] = round(sum(running_scores) / len(running_scores), 1)
            recent_scores = running_scores[-RECENT_WINDOW:]
            turn["recent_average_score"] = round(
                sum(recent_scores) / len(recent_scores), 1
            )
        else:
            turn["average_score"] = 0.0
            turn["recent_average_score"] = 0.0

    repaired_score_history = []
    for index, turn in enumerate(turns):
        if not turn.get("score_included", True):
            continue

        if index < len(existing_scores):
            time_value = existing_scores[index].get("time", turn.get("time", ""))
        else:
            time_value = turn.get("time", "")

        repaired_score_history.append(
            {"score": clamp_score(int(turn.get("score", 0))), "time": time_value}
        )

    score_store[session_id] = repaired_score_history[-MAX_SCORE_HISTORY:]


def get_turn_history(session_id: str):
    repair_session_analysis_history(session_id)
    return turn_store.get(session_id, [])


def get_user_turn_count(session_id: str) -> int:
    history = conversation_store.get(session_id, [])
    return sum(1 for item in history if item["role"] == "user")


def add_score_history(session_id: str, score: int) -> None:
    score_store.setdefault(session_id, [])
    score_store[session_id].append(
        {"score": clamp_score(score), "time": datetime.now().strftime("%H:%M:%S")}
    )

    if len(score_store[session_id]) > MAX_SCORE_HISTORY:
        score_store[session_id] = score_store[session_id][-MAX_SCORE_HISTORY:]


def get_score_history(session_id: str):
    repair_session_analysis_history(session_id)
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
        for key in [
            "repetition",
            "memory",
            "time_confusion",
            "incoherence",
        ]
    )


def should_include_analysis_score(
    judgment: str, score: int, feature_scores: dict
) -> bool:
    normalized_judgment = str(judgment or "").strip()
    normalized_score = clamp_score(int(score or 0))

    if (
        normalized_judgment == "판단 어려움"
        and normalized_score == 0
        and not has_meaningful_feature_scores(feature_scores)
    ):
        return False

    return True


def build_score_exclusion_reason(
    judgment: str, score: int, reason: str, feature_scores: dict
) -> str:
    if should_include_analysis_score(judgment, score, feature_scores):
        return ""

    normalized_reason = str(reason or "").strip()

    if "너무 짧아" in normalized_reason or "입력이 필요" in normalized_reason:
        return "입력이 너무 짧아 이번 대화는 점수 통계에서 제외했습니다."
    if "음성 인식 결과" in normalized_reason:
        return "음성 인식 결과가 없어 이번 대화는 점수 통계에서 제외했습니다."
    if "입력된 대화가 없습니다" in normalized_reason:
        return "분석할 대화가 없어 이번 대화는 점수 통계에서 제외했습니다."
    if "문제가 발생" in normalized_reason or "오류" in normalized_reason:
        return "분석 중 오류가 발생해 이번 대화는 점수 통계에서 제외했습니다."

    return "분석 결과가 불안정하여 이번 대화는 점수 통계에서 제외했습니다."


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
    return calculate_trend_from_score_values(
        [item["score"] for item in history], window
    )


def transcribe_audio_file(file_path: str) -> str:
    client = get_or_create_speech_client()

    with open(file_path, "rb") as f:
        content = f.read()

    audio = speech.RecognitionAudio(content=content)

    config = speech.RecognitionConfig(
        language_code="ko-KR",
        enable_automatic_punctuation=True,
        max_alternatives=3,
    )

    response = client.recognize(config=config, audio=audio)

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
        },
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
                response = primary_chain.invoke(
                    {
                        "conversation_context": conversation_context,
                        "question": question,
                    }
                )
            else:
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
            print(f"[{role_key} 분석 재시도 {attempt + 1} 실패] {e}")

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
            response = repetition_chain.invoke(
                {
                    "recent_user_questions": build_repetition_context(previous_turns),
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
        print(f"[repetition 분석 실패] {e}")
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


def generate_analysis_with_retry(
    question: str,
    conversation_context: str = "이전 대화 없음",
    max_attempts: int = MAX_ANALYSIS_RETRY,
) -> str:
    previous_response = ""
    primary_chain, retry_chain = get_or_create_analysis_chains()

    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                response = primary_chain.invoke(
                    {"conversation_context": conversation_context, "question": question}
                )
            else:
                response = retry_chain.invoke(
                    {
                        "conversation_context": conversation_context,
                        "question": question,
                        "previous_response": previous_response,
                    }
                )

            raw_text = response.get("text", "").strip()
            previous_response = raw_text

            if is_analysis_format_complete(raw_text):
                return force_analysis_format(raw_text)

        except Exception as e:
            print(f"[분석 재시도 {attempt + 1} 실패] {e}")

    if previous_response:
        return force_analysis_format(previous_response)

    return get_analysis_fallback_text()


def generate_feature_analysis_with_retry(
    question: str,
    conversation_context: str = "이전 대화 없음",
    max_attempts: int = MAX_ANALYSIS_RETRY,
) -> str:
    previous_response = ""
    primary_chain, retry_chain = get_or_create_feature_analysis_chains()

    for attempt in range(max_attempts):
        try:
            if attempt == 0:
                response = primary_chain.invoke(
                    {"conversation_context": conversation_context, "question": question}
                )
            else:
                response = retry_chain.invoke(
                    {
                        "conversation_context": conversation_context,
                        "question": question,
                        "previous_response": previous_response,
                    }
                )

            raw_text = response.get("text", "").strip()
            previous_response = raw_text

            if is_feature_analysis_complete(raw_text):
                return force_feature_analysis_format(raw_text)

        except Exception as e:
            print(f"[비반복 특징 분석 재시도 {attempt + 1} 실패] {e}")

    if previous_response:
        return force_feature_analysis_format(previous_response)

    return get_feature_analysis_fallback_text()


# =========================
# 응답 생성
# =========================
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


# =========================
# 공통 JSON 응답
# =========================
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
            )
        else:
            answer_response = get_or_create_answer_chain().invoke(
                {"question": question}
            )

        answer_text = answer_response.get("text", "").strip()
        answer_text = re.sub(r"^\s*AI:\s*", "", answer_text)

        if not answer_text:
            answer_text = "질문에 대한 답변을 생성하지 못했습니다."

        return {
            "answer": answer_text,
            "is_answer_only": True,
            "llm_provider": normalized_provider,
        }

    except Exception as e:
        print(f"LLM 응답 생성 실패: {e}")
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
    recent_average_score = get_recent_average_score(session_id)
    risk_level = get_risk_level_from_score(recent_average_score)
    trend = get_score_trend(session_id)

    return jsonify(
        {
            "session_id": session_id,
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
            "average_score": get_average_score(session_id),
            "recent_average_score": recent_average_score,
            "risk_level": risk_level,
            "trend": trend,
            "score_history": get_score_history(session_id),
            "turn_history": get_turn_history(session_id),
            "turn": turn,
            "recall": serialize_recall_state(session_id),
        }
    )


def finalize_analysis_response(
    session_id: str,
    user_input: str,
    answer_text: str,
    fields: dict,
    llm_provider: str = "local",
):
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
    analysis_runtime_cache.pop(session_id, None)

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


def get_server_host() -> str:
    return os.getenv("HOST", "0.0.0.0")


def get_server_port() -> int:
    return int(os.getenv("PORT", "5000"))


def get_waitress_threads() -> int:
    return int(os.getenv("WAITRESS_THREADS", "8"))


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def print_server_urls(host: str, port: int) -> None:
    local_ip = get_local_ip()

    print("=" * 60)
    print("NCAI server is starting")
    print(f"Local URL:   http://127.0.0.1:{port}")
    print(f"LAN URL:     http://{local_ip}:{port}")
    if host not in {"0.0.0.0", "::"}:
        print(f"Bind Host:   {host}")
    else:
        print("Bind Host:   0.0.0.0 (all network interfaces)")
    print("=" * 60)


def run_server() -> None:
    host = get_server_host()
    port = get_server_port()

    print_server_urls(host, port)

    if serve is not None:
        serve(app, host=host, port=port, threads=get_waitress_threads())
        return

    print("waitress is not installed. Falling back to Flask development server.")
    app.run(host=host, port=port, debug=False)


# =========================
# 라우트
# =========================
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

        return jsonify({"session_id": session_id, "user_speech": user_input})

    except Exception as e:
        print(f"STT 처리 오류: {e}")
        return jsonify({"error": "음성 인식 중 문제가 발생했습니다."}), 500


@app.route("/generate-answer", methods=["POST"])
def generate_answer():
    session_id = get_or_create_session_id()

    try:
        data = request.get_json(silent=True) or {}
        user_input = normalize_text(data.get("message", ""))
        llm_provider = get_requested_llm_provider(data)

        if not user_input:
            return jsonify({"error": "분석할 텍스트가 없습니다."}), 400

        result = generate_answer_result(user_input, provider=llm_provider)

        return jsonify(
            {
                "session_id": session_id,
                "user_speech": user_input,
                "answer": result.get("answer", ""),
                "is_answer_only": True,
                "llm_provider": llm_provider,
            }
        )

    except Exception as e:
        print(f"응답 사전 생성 오류: {e}")
        return jsonify({"error": "응답 생성 중 문제가 발생했습니다."}), 500


@app.route("/analyze-role", methods=["POST"])
def analyze_role():
    session_id = get_or_create_session_id()

    try:
        data = request.get_json(silent=True) or {}
        user_input = normalize_text(data.get("message", ""))
        role_key = normalize_role_key(data.get("role", ""))
        llm_provider = get_requested_llm_provider(data)

        if not user_input:
            return jsonify({"error": "분석할 텍스트가 없습니다."}), 400
        if role_key not in {"repetition", "memory", "time_confusion", "incoherence"}:
            return jsonify({"error": "지원하지 않는 분석 역할입니다."}), 400

        role_result = generate_role_analysis_result(
            role_key,
            user_input,
            session_id=session_id,
            provider=llm_provider,
        )

        return jsonify(
            {
                "session_id": session_id,
                "role": role_key,
                "score": int(role_result.get("score", 0)),
                "reason": role_result.get("reason", ""),
                "llm_provider": llm_provider,
            }
        )

    except Exception as e:
        print(f"역할별 분석 오류: {e}")
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

        if not user_input:
            return jsonify({"error": "분석할 텍스트가 없습니다."}), 400

        answer_result = (
            {"answer": precomputed_answer}
            if precomputed_answer
            else generate_answer_result(
                user_input,
                provider=llm_provider,
            )
        )
        fields = build_fields_from_role_results(role_results)
        return finalize_analysis_response(
            session_id=session_id,
            user_input=user_input,
            answer_text=answer_result.get("answer", ""),
            fields=fields,
            llm_provider=llm_provider,
        )

    except Exception as e:
        print(f"최종 분석 반영 오류: {e}")
        return jsonify({"error": "최종 분석 반영 중 문제가 발생했습니다."}), 500


@app.route("/analyze-text", methods=["POST"])
def analyze_text():
    session_id = get_or_create_session_id()

    try:
        data = request.get_json(silent=True) or {}
        user_input = normalize_text(data.get("message", ""))
        precomputed_answer = normalize_text(data.get("answer", ""))
        llm_provider = get_requested_llm_provider(data)

        if not user_input:
            return jsonify({"error": "분석할 텍스트가 없습니다."}), 400

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
        return finalize_analysis_response(
            session_id=session_id,
            user_input=user_input,
            answer_text=answer_result.get("answer", ""),
            fields=fields,
            llm_provider=llm_provider,
        )

    except Exception as e:
        print(f"텍스트 분석 오류: {e}")
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

    except Exception as e:
        print(f"서버 오류: {e}")
        return jsonify({"error": "서버 처리 중 문제가 발생했습니다."}), 500


@app.route("/score-history", methods=["GET"])
def score_history():
    session_id = get_or_create_session_id()

    return jsonify(
        {
            "session_id": session_id,
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


if __name__ == "__main__":
    run_server()
