from fastapi import APIRouter as _APIRouter
from fastapi import HTTPException as _HTTPException
from fastapi import Response as _Response
from pydantic import BaseModel as _BaseModel
from pydantic import Field as _Field
from langchain_community.graphs import Neo4jGraph
import json

from nedrexapi.common import (
    _API_KEY_HEADER_ARG,
    check_api_key_decorator,
)

from nedrexapi.llm import (
_LLM_BASE, _LLM_model, _LLM_path, generate, chat
)

from nedrexapi.routers.embeddings import run_neo4j_query

from nedrexapi.config import config as _config

_NEO4J_PORT = _config[f'db.{_config["api.status"]}.neo4j_bolt_port_internal']
_NEO4J_HOST = _config[f'db.{_config["api.status"]}.neo4j_name']
_NEO4J_DRIVER = Neo4jGraph(f"bolt://{_NEO4J_HOST}:{_NEO4J_PORT}", username="", password="", database='neo4j')

class QuestionRequest(_BaseModel):
    query: str = _Field("", title="Query that is used to search for hits in the knowledge graph and subsequently answer the posed question.", description="This query will be used to find the closest matches in the KG and subsequently answer the question using an LLM", examples=["What is AD5?", "What is a prion disease?"])

DEFAULT_QUESTION_REQUEST=QuestionRequest()

router = _APIRouter()

def get_explain_match_query(id, type):
    from nedrexapi.routers.embeddings import get_available_collections
    collections = get_available_collections()
    if type not in collections:
        raise _HTTPException(status_code=404, detail=f"Collection {type} not available or ready.")
    collection = collections[type]
    from nedrexapi.routers.embeddings import get_properties
    properties = get_properties(type, collection["entityType"])
    property_string = ""
    for property in properties:
        if property not in collection["properties"]:
            property_string = property_string + f"n.{property},"
    if len(property_string) > 0:
        property_string = property_string[:-1] + ";"

    entry = f"MATCH (n:{type}) WHERE n.primaryDomainId='{id}' RETURN "+property_string
    return entry


@router.get("/explain/{collection}", summary="Explains an entry of the KG")
@check_api_key_decorator
def explain_entry(id:str, collection:str):
    messages = [("system","You are a system that helps explaining entries from a knowledge graph. The knowledge graph is containing molecular biological entities and relationships and you can identify the type by the type attribute. You will be handed a neo4j entry. Please create a summary that explains what can be inferred from the entries properties. Do not write any introductory sentences, just start explaining and summarizing the content!")]
    result = run_neo4j_query(get_explain_match_query(id, collection))
    human_message = f"I have the following ID: {id}. Can you create an explanatory summary of the returned entry? \n{result}"
    messages.append(("human",human_message))
    return _Response(chat(messages).content)


@router.post("/ask")
@check_api_key_decorator
def ask_kb(request:QuestionRequest=DEFAULT_QUESTION_REQUEST):
    query = request.query
    from nedrexapi.routers.embeddings import query_all_embeddings
    results = query_all_embeddings(query,10)
    print(results)
    messages = [("system",
                 "You are a system that helps explaining results from a knowledge graph stored in the NeDRex database. Please call it NeDRex or 'the NeDRex knowledge graph' and not 'the knowledge graph' NeDRex contains molecular biological entities and relationships and you can identify the type by the type attribute. You will be handed multiple neo4j entries and the question the user asked. The entries will be the closest matches that could be found in the database regarding the query. It's score is the cosine similarity of the query and entries in embedding space. Please factor especially in the type of each of the top 10 entries that is given and provide a detailed answer based on the users question! Also base your answer exclusively on the provided top matching entries and also explain these entries, especially in regards to their likelihood of being a valid answer to their question! First formulate a detailed answer to the question based on the given entries and afterwards make sure to add a ranked list of the entries with the respective displayName/label, primaryDomainId (e.g. mondo, drugbank, ...), and the score so the user can also get the results from NeDRexDB. You are allowed to convert the score, that is a long decimal into a percentage number and cut the decimal points.")]
    human_message = f"I have the following question: {query}?\n Here are the best matches from the NeDRex knowledge graph:\n{results}\n\nPlease answer in detail based on the provided information from NeDRex and your general instructions: {query}?"
    messages.append(("human",human_message))
    return _Response(chat(messages).content)