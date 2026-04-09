from fastapi import APIRouter as _APIRouter
from fastapi import HTTPException as _HTTPException
from fastapi import Query as _Query
from pydantic import BaseModel as _BaseModel
from pydantic import Field as _Field

from nedrexapi.common import _API_KEY_HEADER_ARG, check_api_key_decorator
from nedrexapi.config import config as _config
from nedrexapi.db import MongoInstance

DEFAULT_QUERY = _Query(None)

router = _APIRouter()

class PPIRequest(_BaseModel):
    nodes: list[str] = _Field(None, title="Primary domain IDs of nodes",
                              description="Primary domain IDs of the nodes the attributes are requested for")
    iid_evidence: list[str] = _Field(['exp'], title="Evidence types",description="The evidence types to filter the PPIs by")
    skip: int = _Field(0, title="Skip", description="The number of PPIs to skip")
    limit: int = _Field(10000, title="Limit", description="The number of PPIs to return")
    reviewed_proteins: list[bool] = _Field([True,False], title="Reviewed proteins", description="Whether to filter by reviewed proteins")
    skip_proteins: int = _Field(0, title="Skip proteins", description="The number of proteins to skip")
    limit_proteins: int = _Field(250000, title="Limit proteins", description="The number of proteins to return")
    sources: list[str] = _Field([], title="Sources",description="The sources to filter the PPIs by; if the list is empty, all sources will be considered")
    num_sources: int = _Field(1, title="Minimum Source Count", description="Minimum number of sources required in dataSources for a PPI")
    hippie_methods_score_cutoff: float = _Field(0.0, title="Hippie Methods Score Cutoff", description="Minimum value for the Hippie Methods score")

    class Config:
        extra = "forbid"


_DEFAULT_PPI_REQUEST = PPIRequest()


@router.post("/ppi", summary="Paginated PPI query")
@check_api_key_decorator
def get_paginated_protein_protein_interactions(
    ppi_request: PPIRequest = _DEFAULT_PPI_REQUEST,
    x_api_key: str = _API_KEY_HEADER_ARG,
):
    """
    Returns an array of protein protein interactions (PPIs in a paginated manner). A skip and a limit can be
    specified, defaulting to `0` and `10_000`, respectively, if not specified.
    """
    if not ppi_request.iid_evidence:
        return []

    if not ppi_request.skip:
        ppi_request.skip = 0
    if not ppi_request.limit:
        ppi_request.limit = _config["api.pagination_max"]
    elif ppi_request.limit > _config["api.pagination_max"]:
        raise _HTTPException(status_code=422, detail=f"Limit specified ({ppi_request.limit}) greater than maximum limit allowed")
    
    query = {"evidenceTypes": {"$in": ppi_request.iid_evidence}}
    if not (True in ppi_request.reviewed_proteins and False in ppi_request.reviewed_proteins) or ppi_request.skip_proteins > 0 or ppi_request.limit_proteins < 250000:
        protein_query = {"is_reviewed": {"$in": [str(r) for r in ppi_request.reviewed_proteins]}}
    
        filtered_proteins = list({
            protein["primaryDomainId"]
            for protein in MongoInstance.DB()["protein"].find(protein_query).sort('_id').skip(ppi_request.skip_proteins).limit(ppi_request.limit_proteins)
        })
        query.update({"memberOne": {"$in": filtered_proteins}, "memberTwo": {"$in": filtered_proteins}})

    if ppi_request.sources and len(ppi_request.sources)>0:
        query["dataSources"] = {"$in": ppi_request.sources}
        
    if ppi_request.num_sources > 1:
        query["$expr"] = {"$gte": [{"$size": "$dataSources"}, ppi_request.num_sources]}
        
    if ppi_request.hippie_methods_score_cutoff > 0:
        query["hippie_methods_score"] = {"$gte": ppi_request.hippie_methods_score_cutoff}
        
    coll_name = "protein_interacts_with_protein"

    return [
        {k: v for k, v in doc.items() if k != "_id"}
        # each entry is one document -> finds all documents by conditions
        for doc in MongoInstance.DB()[coll_name].find(query).sort('_id').skip(ppi_request.skip).limit(ppi_request.limit)
    ]
