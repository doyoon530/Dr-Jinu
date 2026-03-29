from collections import deque
from threading import Lock


conversation_store = {}
score_store = {}
recall_store = {}
turn_store = {}
session_generation_store = {}

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
analysis_llm_lock = Lock()
visitor_lock = Lock()
visitor_event_store = deque(maxlen=300)
visitor_snapshot_store = {}
visitor_hostname_cache = {}
