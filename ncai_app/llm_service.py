import json
import os
import tempfile
import urllib.error
import urllib.request

from flask import request
from google.cloud import speech
from langchain_classic.chains import LLMChain
from langchain_community.llms import LlamaCpp
from langchain_core.prompts import ChatPromptTemplate

from . import runtime
from .common import normalize_text
from .config import (
    DEFAULT_GOOGLE_KEY_PATH,
    ROLE_ANALYSIS_META,
    ROLE_ANALYSIS_PROMPTS,
    ROLE_ANALYSIS_RETRY_PROMPTS,
    SUPPORTED_LLM_PROVIDERS,
    analysis_prompt,
    analysis_retry_prompt,
    answer_prompt,
    feature_analysis_prompt,
    feature_analysis_retry_prompt,
    get_analysis_max_tokens,
    get_analysis_n_batch,
    get_analysis_n_ctx,
    get_api_llm_analysis_model,
    get_api_llm_answer_model,
    get_api_llm_base_url,
    get_api_llm_timeout,
    get_default_llm_provider,
    get_model_path,
    is_api_llm_configured,
    normalize_llm_provider,
    repetition_prompt,
)


def setup_google_credentials() -> str | None:
    explicit_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if explicit_path:
        return explicit_path

    credentials_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if credentials_json:
        if runtime.temp_google_credentials_path and os.path.exists(
            runtime.temp_google_credentials_path
        ):
            return runtime.temp_google_credentials_path

        fd, temp_path = tempfile.mkstemp(prefix="gcp-creds-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            json.dump(json.loads(credentials_json), temp_file, ensure_ascii=False)

        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = temp_path
        runtime.temp_google_credentials_path = temp_path
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


def get_model_status() -> dict:
    model_path = get_model_path()
    return {"path": model_path, "exists": os.path.exists(model_path)}


def get_google_credentials_status() -> dict:
    credentials_path = setup_google_credentials()
    return {"path": credentials_path or "", "configured": bool(credentials_path)}


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
    api_key = os.getenv("API_LLM_API_KEY", "").strip()
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
        with urllib.request.urlopen(request_obj, timeout=get_api_llm_timeout()) as response:
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


def get_or_create_answer_chain():
    if runtime.answer_chain is not None:
        return runtime.answer_chain

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
    runtime.answer_chain = LLMChain(prompt=answer_prompt, llm=answer_llm)
    return runtime.answer_chain


def get_or_create_analysis_chains():
    if (
        runtime.analysis_chain is not None
        and runtime.analysis_retry_chain is not None
        and runtime.analysis_repetition_chain is not None
        and runtime.analysis_feature_chain is not None
        and runtime.analysis_feature_retry_chain is not None
    ):
        return runtime.analysis_chain, runtime.analysis_retry_chain

    model_path = get_model_path()
    if not os.path.exists(model_path):
        raise RuntimeError(
            f"Model file not found: {model_path}. Set MODEL_PATH to a valid GGUF model path."
        )

    if runtime.analysis_llm_instance is None:
        runtime.analysis_llm_instance = LlamaCpp(
            model_path=model_path,
            temperature=0.0,
            top_p=0.9,
            max_tokens=get_analysis_max_tokens(),
            n_ctx=get_analysis_n_ctx(),
            n_batch=get_analysis_n_batch(),
            verbose=False,
        )

    if runtime.analysis_chain is None:
        runtime.analysis_chain = LLMChain(
            prompt=analysis_prompt, llm=runtime.analysis_llm_instance
        )
    if runtime.analysis_retry_chain is None:
        runtime.analysis_retry_chain = LLMChain(
            prompt=analysis_retry_prompt, llm=runtime.analysis_llm_instance
        )
    if runtime.analysis_repetition_chain is None:
        runtime.analysis_repetition_chain = LLMChain(
            prompt=repetition_prompt, llm=runtime.analysis_llm_instance
        )
    if runtime.analysis_feature_chain is None:
        runtime.analysis_feature_chain = LLMChain(
            prompt=feature_analysis_prompt, llm=runtime.analysis_llm_instance
        )
    if runtime.analysis_feature_retry_chain is None:
        runtime.analysis_feature_retry_chain = LLMChain(
            prompt=feature_analysis_retry_prompt, llm=runtime.analysis_llm_instance
        )

    return runtime.analysis_chain, runtime.analysis_retry_chain


def get_or_create_repetition_chain():
    if runtime.analysis_repetition_chain is None:
        get_or_create_analysis_chains()
    return runtime.analysis_repetition_chain


def get_or_create_feature_analysis_chains():
    if runtime.analysis_feature_chain is None or runtime.analysis_feature_retry_chain is None:
        get_or_create_analysis_chains()
    return runtime.analysis_feature_chain, runtime.analysis_feature_retry_chain


def get_or_create_role_analysis_chains(role_key: str):
    if role_key not in ROLE_ANALYSIS_META:
        raise ValueError(f"Unsupported analysis role: {role_key}")

    if (
        role_key in runtime.role_analysis_chains
        and role_key in runtime.role_analysis_retry_chains
    ):
        return (
            runtime.role_analysis_chains[role_key],
            runtime.role_analysis_retry_chains[role_key],
        )

    if runtime.analysis_llm_instance is None:
        get_or_create_analysis_chains()

    if role_key not in runtime.role_analysis_chains:
        runtime.role_analysis_chains[role_key] = LLMChain(
            prompt=ROLE_ANALYSIS_PROMPTS[role_key],
            llm=runtime.analysis_llm_instance,
        )

    if role_key not in runtime.role_analysis_retry_chains:
        runtime.role_analysis_retry_chains[role_key] = LLMChain(
            prompt=ROLE_ANALYSIS_RETRY_PROMPTS[role_key],
            llm=runtime.analysis_llm_instance,
        )

    return (
        runtime.role_analysis_chains[role_key],
        runtime.role_analysis_retry_chains[role_key],
    )


def get_or_create_speech_client():
    if runtime.speech_client is not None:
        return runtime.speech_client

    credentials_path = setup_google_credentials()
    if not credentials_path:
        raise RuntimeError(
            "Google STT credentials are not configured. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_APPLICATION_CREDENTIALS_JSON."
        )

    runtime.speech_client = speech.SpeechClient()
    return runtime.speech_client


def transcribe_audio_file(file_path: str) -> str:
    client = get_or_create_speech_client()

    with open(file_path, "rb") as file_handle:
        content = file_handle.read()

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
