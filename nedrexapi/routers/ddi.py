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

class DDIRequest(_BaseModel):
    nodes: list[str] = _Field(None, title="Primary domain IDs of nodes",
                              description="Primary domain IDs of the nodes the edges are requested for")
    skip: int = _Field(0, title="Skip", description="The number of DDIs to skip")
    limit: int = _Field(10000, title="Limit", description="The number of DDIs to return")
    sources: list[str] = _Field([], title="Sources",description="The sources to filter the DDIs by; if the list is empty, all sources will be considered")

    class Config:
        extra = "forbid"


_DEFAULT_DDI_REQUEST = DDIRequest()


@router.post("/ddi", summary="Paginated DDI query")
@check_api_key_decorator
def get_paginated_drug_drug_interactions(
    ddi_request: DDIRequest = _DEFAULT_DDI_REQUEST,
    x_api_key: str = _API_KEY_HEADER_ARG,
):
    """
    Returns an array of drug drug interactions (DDIs in a paginated manner). A skip and a limit can be
    specified, defaulting to `0` and `10_000`, respectively, if not specified.
    """

    if not ddi_request.skip:
        ddi_request.skip = 0
    if not ddi_request.limit:
        ddi_request.limit = _config["api.pagination_max"]
    elif ddi_request.limit > _config["api.pagination_max"]:
        raise _HTTPException(status_code=422, detail=f"Limit specified ({ddi_request.limit}) greater than maximum limit allowed")
    
    query = {}
    if ddi_request.nodes and len(ddi_request.nodes)>0:
        query = {
            "$or": [
                {"memberOne": {"$in": ddi_request.nodes}},
                {"memberTwo": {"$in": ddi_request.nodes}}
            ]
        }
    
    if ddi_request.sources and len(ddi_request.sources)>0:
        query["dataSources"] = {"$in": ddi_request.sources}

    coll_name = "drug_interacts_with_drug"

    return [
        {k: v for k, v in doc.items() if k != "_id"}
        # each entry is one document -> finds all documents by conditions
        for doc in MongoInstance.DB()[coll_name].find(query).sort('_id').skip(ddi_request.skip).limit(ddi_request.limit)
    ]
