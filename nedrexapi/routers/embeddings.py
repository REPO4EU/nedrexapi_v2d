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
_LLM_BASE, _LLM_model, _LLM_path
)

from nedrexapi.config import config as _config

_NEO4J_PORT = _config[f'db.{_config["api.status"]}.neo4j_bolt_port_internal']
_NEO4J_HOST = _config[f'db.{_config["api.status"]}.neo4j_name']
_NEO4J_DRIVER = Neo4jGraph(f"bolt://{_NEO4J_HOST}:{_NEO4J_PORT}", username="", password="", database='neo4j')

router = _APIRouter()

class QueryEmbeddingRequest(_BaseModel):
    query: str = _Field("", title="Query that is used to search for hits in the knowledge graph.", description="This query will be embedded by the same technique as the data in the KG is, and then cosine similarity is used to identify the closest matches", examples=["What is AD5?", "What is a prion disease?"])
    collection: str = _Field(None, title="Collection in the knowledge graph that is searched for hits", description="Defines collection that will be searched using the query. If none is given, all embedded collections are searched and the top X best matches, based on the cosine similarity are returned.")
    top: int = _Field(10, title="Number of results to return")


DEFAULT_QUERY_EMBEDDING_REQUEST = QueryEmbeddingRequest()

def run_neo4j_query(neo4j_query, params={}):
    res = _NEO4J_DRIVER.query(query=neo4j_query, params=params)
    return res


def to_json(result):
    return json.dumps([json.loads(json.dumps(result, default=lambda o: dict(o)))]) + "\n"

def get_available_collections():
    query = "SHOW vector INDEXES WHERE state='ONLINE'"
    result = run_neo4j_query(query)
    map = dict()
    for r in result:
        for type in r["labelsOrTypes"]:
            map[type] = {"index_name": r["name"], "properties": set(), "entityType":r["entityType"]}
            for property in r["properties"]:
                map[type]["properties"].add(property)
    return map

@router.get("/available", summary="Lists collections, aka node and edge types, with an available embedding index.")
@check_api_key_decorator
def available_collections(x_api_key: str = _API_KEY_HEADER_ARG):
    """
    Returns a list of available collections in the knowledge graph where embeddings are available.
    """
    result = get_available_collections()
    return _Response(json.dumps([n for n in result.keys()]))

def get_properties(type, entity_type):
    if entity_type == "NODE":
        query = f"MATCH (n: {type}) RETURN PROPERTIES(n) AS props LIMIT 1"
    else:
        query = f"MATCH ()-[r: {type}]-() RETURN PROPERTIES(r) AS props LIMIT 1"
    result = run_neo4j_query(query)
    try:
        return {k for k,v in result[0]["props"].items()}
    except IndexError:
        return set()


def create_embedding_cypher_query(embedding, type, top):
    collections = get_available_collections()
    if type not in collections:
        raise _HTTPException(status_code=404, detail=f"Collection {type} not available or ready.")
    collection = collections[type]

    properties = get_properties(type, collection.get("entityType"))

    if collection.get("entityType") == "NODE":
        property_string = ""
        for property in properties:
            if property not in collection["properties"]:
                property_string = property_string + f",n.{property}"
        cypher = """CALL db.index.vector.queryNodes($node_name, $top_n, $embedding) YIELD node AS n, score RETURN score """+property_string+""";"""
        params = {"node_name":collection['index_name'], "top_n":top, "embedding": embedding}
    else:
        property_string = ""
        for property in properties:
            if property not in collection["properties"]:
                property_string = property_string + f",r.{property}"
        cypher = """CALL db.index.vector.queryRelationships($edge_name, $top_n, $embedding) YIELD relationship AS r, score MATCH (s)-[r]-(t) RETURN s.primaryDomainId+"->"+t.primaryDomainId as primaryDomainIds, score """ + property_string+ """,s,t;"""
        params = {"edge_name": collection['index_name'], "top_n": top, "embedding": embedding}
    return cypher, params


def get_query_hits(embedding,type, top):
    cypher_query, params = create_embedding_cypher_query(embedding, type, top)
    return run_neo4j_query(cypher_query,params=params)

def query_single_embedding(embedding, type, top):
    cypher, params = create_embedding_cypher_query(embedding, type, top)
    return run_neo4j_query(cypher, params=params)


def create_embedding(user_query):
    neo4j_query = """CALL apoc.ml.openai.embedding([$user_query], "no-key",
                             {
                                 endpoint: $llm_base,
                                 path: $llm_path,
                                 model: $llm_model
                             })
    yield index, embedding"""
    params = {"user_query":user_query, "llm_base":_LLM_BASE, "llm_path":_LLM_path, "llm_model":_LLM_model}

    response = run_neo4j_query(neo4j_query, params=params)
    for embedding in response:
        return embedding["embedding"]


def query_all_embeddings(query, top):
    collections = get_available_collections()

    results = dict()
    scores = dict()

    embedding = create_embedding(query)
    for type in collections.keys():
        try:
            if collections[type]["entityType"] == "NODE":
                best_hits = get_query_hits(embedding, type, top)
                for hit in best_hits:
                    if "embedding" in hit.keys():
                        del hit["embedding"]
                    results[hit["n.primaryDomainId"]] = hit
                    scores[hit["n.primaryDomainId"]] = hit["score"]
            else:
                best_hits = get_query_hits(embedding, type, top)
                for hit in best_hits:
                    if "embedding" in hit.keys():
                        del hit["embedding"]
                    if "s" in hit.keys():
                        s = hit["s"]
                        if "embedding" in s.keys():
                            del s["embedding"]
                        del hit["s"]
                        hit["source"]=s
                    if "t" in hit.keys():
                        t = hit["t"]
                        if "embedding" in t.keys():
                            del t["embedding"]
                        hit["target"]=t
                    results[hit["primaryDomainIds"]] = hit
                    scores[hit["primaryDomainIds"]] = hit["score"]

        except Exception as e:
            print(f"Error while getting hits for {type}")
            print(e)

    scores_sorted = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_hits = []
    for id,score in scores_sorted:
        if len(top_hits) >= top:
            break
        top_hits.append(results[id])
    return top_hits


@router.post("/query")
@check_api_key_decorator
def query_embeddings(request: QueryEmbeddingRequest = DEFAULT_QUERY_EMBEDDING_REQUEST):
    type = request.collection
    top = request.top if request.top else 5
    if type is None:
        results = query_all_embeddings(request.query, top)
    else:
        embedding = create_embedding(request.query)
        results = query_single_embedding(embedding, type, top)
    return _Response(to_json(results))



