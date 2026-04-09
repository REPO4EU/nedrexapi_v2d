from langchain_community.embeddings.ollama import OllamaEmbeddings
from langchain_ollama.chat_models import ChatOllama
from nedrexapi.config import config as _config
from langchain_community.llms.ollama import Ollama

_LLM_BASE=_config["embeddings.server_base"]
_LLM_model=_config[f"embeddings.model"]
_LLM_path=_config[f"embeddings.path"]

# _LLM_user=_config[f"embeddings.user"]
# _LLM_pass=_config[f"embeddings.pass"]

_LLM_chat_model=_config["chat.model"]
_LLM_chat_base=_config["chat.server_base"]
_LLM_chat_api_key=_config["chat.api_key"]

headers = {"Authorization": "Bearer " + _LLM_chat_api_key}


def get_embedder():
    return OllamaEmbeddings(base_url=_LLM_BASE, model=_LLM_model, headers=headers)

def get_generator():
    return Ollama(base_url=_LLM_chat_base, model=_LLM_chat_model, temperature=0.0, headers=headers)

def get_chat():
    return ChatOllama(base_url=_LLM_chat_base, model=_LLM_chat_model, temperature=0.0, client_kwargs={'headers': headers})

def get_embedding(query):
    embedder = get_embedder()
    return embedder.embed(query)



def generate(query):
    ollama_llm = get_generator()
    response = ollama_llm.invoke(query)
    return response


def chat(messages):
    llm = get_chat()
    response = llm.invoke(input=messages)
    return response
