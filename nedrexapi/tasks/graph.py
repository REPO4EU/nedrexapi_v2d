from collections import defaultdict
from collections.abc import MutableMapping
from itertools import chain

import networkx as nx  # type: ignore

from nedrexapi.common import _GRAPH_COLL, _GRAPH_COLL_LOCK, _GRAPH_DIR, _GRAPH_DIR_INTERNAL, NODE_COLLECTIONS
from nedrexapi.db import MongoInstance
from nedrexapi.logger import logger

_NODE_TYPE_MAP = {
    "disorder": ["Disorder"],
    "drug": ["Drug", "BiotechDrug", "SmallMoleculeDrug"],
    "gene": ["Gene"],
    "pathway": ["Pathway"],
    "protein": ["Protein"],
    "phenotype": ["Phenotype"],
    "go": ["GO"],
}


def flatten(d, parent_key="", sep="_"):
    """Helper function to flatten dictionaries"""
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, MutableMapping):
            items.extend(flatten(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))

    rtrn = {}
    for k, v in items:
        if isinstance(v, list):
            rtrn[k] = ", ".join(v)
        elif v is None:
            rtrn[k] = "None"
        else:
            rtrn[k] = v

    return rtrn


def graph_constructor_wrapper(uid):
    try:
        graph_constructor(uid)
    except Exception as E:
        with _GRAPH_COLL_LOCK:
            _GRAPH_COLL.update_one({"uid": uid}, {"$set": {"status": "failed", "error": f"{E}"}})
        raise E


def graph_constructor(uid):
    with _GRAPH_COLL_LOCK:
        query = _GRAPH_COLL.find_one({"uid": uid})
        if not query:
            raise Exception()
        _GRAPH_COLL.update_one({"uid": query["uid"]}, {"$set": {"status": "building"}})
        logger.info(f"starting graph build job {uid!r}")

    g = nx.DiGraph()
    node_types_filtered = set()  # Keep track of node types added to the graph
    logger.info(f"query: {query}")
    # Dynamically identify node types
    node_type_prefix_map = {}
    node_collections = query["nodes"]  # Assuming this is a list of node collection names

    # Sample one entry from each node collection to determine the prefix
    for coll in node_collections:
        sample_doc = MongoInstance.DB()[coll].find_one()
        if sample_doc:
            prefix = sample_doc["primaryDomainId"].split('.')[0]
            node_type_prefix_map[prefix] = coll

    node_ids = set()

    for coll in node_collections:
        node_query = {}
        if coll == "protein":
            node_query = {"taxid": {"$in": query["taxid"]}, "is_reviewed": {"$in": [str(q) for q in query["reviewed_proteins"]]}}
            node_types_filtered.add("protein")
        elif coll == "drug":
            node_query = {"drugGroups": {"$in": query["drug_groups"]}}
            node_types_filtered.add("drug")

        cursor = MongoInstance.DB()[coll].find(node_query)
        for doc in cursor:
            node_id = doc["primaryDomainId"]
            node_ids.add(node_id)


    def add_edges(node_types_are_present, node1, node2, nodes) -> bool:
        if node_types_are_present[0]:
            if node1 not in nodes:
                return False
        if node_types_are_present[1]:
            if node2 not in nodes:
                return False
        return True

    for coll in query["edges"]:
        node_types = [None, None]
        e = MongoInstance.DB()[coll].find_one()
        if "memberOne" in e:
            node_types[0] = node_type_prefix_map.get(e["memberOne"].split(".")[0])
            node_types[1] = node_type_prefix_map.get(e["memberTwo"].split(".")[0])
        elif "sourceDomainId" in e:
            node_types[0] = node_type_prefix_map.get(e["sourceDomainId"].split(".")[0])
            node_types[1] = node_type_prefix_map.get(e["targetDomainId"].split(".")[0])

        node_types_are_present = [i in node_types_filtered for i in node_types]
        # Apply filters (if given) on PPI edges.

        if coll == "protein_interacts_with_protein":
            cursor = MongoInstance.DB()[coll].find({"evidenceTypes": {"$in": query["ppi_evidence"]}})
            for doc in cursor:
                m1 = doc["memberOne"]
                m2 = doc["memberTwo"]
                if not add_edges(node_types_are_present, m1, m2, node_ids):
                    continue
                if not query["ppi_self_loops"] and (m1 == m2):
                    continue
                if query["concise"]:
                    g.add_edge(
                        m1,
                        m2,
                        memberOne=m1,
                        memberTwo=m2,
                        reversible=True,
                        type=doc["type"],
                        evidenceTypes=", ".join(doc["evidenceTypes"]),
                    )
                else:
                    for attribute in ("_id", "created", "updated"):
                        doc.pop(attribute)
                    g.add_edge(m1, m2, reversible=True, **flatten(doc))
            continue

        # Apply filters on gene-disorder edges.
        if coll == "gene_associated_with_disorder":
            if query["include_omim"]:
                c1 = MongoInstance.DB()[coll].find({"assertedBy": "omim"})
            else:
                c1 = []

            c2 = MongoInstance.DB()[coll].find({"score": {"$gte": query["disgenet_threshold"]}})

            for doc in chain(c1, c2):
                s = doc["sourceDomainId"]
                t = doc["targetDomainId"]

                if not add_edges(node_types_are_present, s, t, node_ids):
                    continue

                # There is no difference in attributes between concise and non-concise.
                # If / else in just to show that there is no difference.
                for attribute in ("_id", "created", "updated"):
                    doc.pop(attribute)
                if query["concise"]:
                    g.add_edge(s, t, reversible=False, **flatten(doc))
                else:
                    g.add_edge(s, t, reversible=False, **flatten(doc))
            continue

        cursor = MongoInstance.DB()[coll].find()
        for doc in cursor:
            # Check for memberOne/memberTwo syntax (undirected).
            if ("memberOne" in doc) and ("memberTwo" in doc):
                m1 = doc["memberOne"]
                m2 = doc["memberTwo"]
                if not add_edges(node_types_are_present, m1, m2, node_ids):
                    continue
                if query["concise"]:
                    g.add_edge(m1, m2, reversible=True, type=doc["type"], memberOne=m1, memberTwo=m2)
                else:
                    for attribute in ("_id", "created", "updated"):
                        doc.pop(attribute)
                    g.add_edge(m1, m2, reversible=True, **flatten(doc))

            # Check for source/target syntax (directed).
            elif ("sourceDomainId" in doc) and ("targetDomainId" in doc):
                s = doc["sourceDomainId"]
                t = doc["targetDomainId"]
                if not add_edges(node_types_are_present, s, t, node_ids):
                    continue

                if query["concise"]:
                    g.add_edge(s, t, reversible=False, sourceDomainId=s, targetDomainId=t, type=doc["type"])
                else:
                    for attribute in ("_id", "created", "updated"):
                        doc.pop(attribute)
                    g.add_edge(s, t, reversible=False, **flatten(doc))

            else:
                raise Exception("Assumption about edge structure violated.")
    # protein_query = {"taxid": {"$not": {"$in": query["taxid"]}}}
    # if not (True in query["reviewed_proteins"] and False in query["reviewed_proteins"]):
    #     protein_query.update({"is_reviewed": {"$in": [str(r) for r in query["reviewed_proteins"]]}})

    # cursor = MongoInstance.DB()["protein"].find(protein_query)
    # ids = [i["primaryDomainId"] for i in cursor]
    # g.remove_nodes_from(ids)
    #
    # cursor = MongoInstance.DB()["drug"].find({"drugGroups": {"$not": {"$in": query["drug_groups"]}}})
    # ids = [i["primaryDomainId"] for i in cursor]
    # g.remove_nodes_from(ids)

    ############################################
    # ADD ATTRIBUTES
    ############################################

    # Problem:
    #  We don't know what types the nodes are.

    # Solution:
    # Iterate over all collections (quick), see if the node / edge is in the graph (quick), and decorate with
    # attributes

    updates = {}
    g.add_nodes_from(node_ids)
    node_ids = node_ids.union(set(g.nodes()))

    for node in NODE_COLLECTIONS:
        cursor = MongoInstance.DB()[node].find()
        for doc in cursor:
            eid = doc["primaryDomainId"]
            if eid not in node_ids:
                continue

            if node == "drug" and query["split_drug_types"] is False:
                doc["type"] = "Drug"

            if query["concise"]:
                assert eid not in updates

                if doc["type"] == "Pathway":
                    attrs = ["primaryDomainId", "displayName", "type"]
                elif doc["type"] == "Drug":
                    attrs = [
                        "primaryDomainId",
                        "domainIds",
                        "displayName",
                        "synonyms",
                        "type",
                        "drugGroups",
                        "indication",
                    ]
                elif doc["type"] == "Disorder":
                    attrs = ["primaryDomainId", "domainIds", "displayName", "synonyms", "icd10", "type"]
                elif doc["type"] == "Gene":
                    attrs = ["primaryDomainId", "displayName", "synonyms", "approvedSymbol", "symbols", "type"]
                elif doc["type"] == "Protein":
                    attrs = ["primaryDomainId", "displayName", "geneName", "taxid", "type"]
                elif doc["type"] == "Signature":
                    attrs = ["primaryDomainId", "type"]
                elif doc["type"] == "Phenotype":
                    attrs = ["primaryDomainId", "displayName", "type"]
                elif doc["type"] == "GO":
                    attrs = ["primaryDomainId", "displayName", "type"]
                else:
                    raise Exception(f"Document type {doc['type']!r} does not have concise attribute defined")

                doc = {attr: doc.get(attr, "") for attr in attrs}
                updates[eid] = flatten(doc)

            else:
                assert eid not in updates
                for attribute in ("_id", "created", "updated"):
                    doc.pop(attribute)
                updates[eid] = flatten(doc)
    print(g)
    nx.set_node_attributes(g, updates)

    ############################################
    # SORTING LONE NODES
    ############################################
    nodes_requested = set(chain(*[_NODE_TYPE_MAP[coll] for coll in query["nodes"]]))
    to_remove = set()

    for node, data in g.nodes(data=True):
        # If the type of the node is one of the requested types, do nothing.
        if data["type"] in nodes_requested:
            continue
        # Otherwise, check the node is involved in at least one edge.
        elif g.in_edges(node) or g.out_edges(node):
            continue
        else:
            to_remove.add(node)

    g.remove_nodes_from(to_remove)

    ############################################
    # CUSTOM CHANGES
    ############################################

    if query["use_omim_ids"]:
        # We need nodes with unambiguous OMIM IDs.
        mondomim_map = defaultdict(list)
        for doc in MongoInstance.DB()["disorder"].find():
            omim_xrefs = [i for i in doc["domainIds"] if i.startswith("omim.")]
            if len(omim_xrefs) == 1:
                mondomim_map[omim_xrefs[0]].append(doc["primaryDomainId"])

        mondomim_map = {v[0]: k for k, v in mondomim_map.items() if (len(v) == 1) and v[0] in g.nodes}

        nx.set_node_attributes(g, {k: {"primaryDomainId": v} for k, v in mondomim_map.items()})
        G = nx.relabel_nodes(g, mondomim_map)
        updates = defaultdict(dict)
        for i, j, data in G.edges(data=True):
            if "memberOne" in data and data["memberOne"] != i:
                updates[(i, j)]["memberOne"] = i
            if "memberTwo" in data and data["memberTwo"] != j:
                updates[(i, j)]["memberTwo"] = j
            if "sourceDomainId" in data and data["sourceDomainId"] != i:
                updates[(i, j)]["sourceDomainId"] = i
            if "targetDomainId" in data and data["targetDomainId"] != j:
                updates[(i, j)]["targetDomainId"] = j

        nx.set_edge_attributes(G, updates)


    nx.write_graphml(g, f"{_GRAPH_DIR_INTERNAL}/{query['uid']}.graphml")
    # print(f"{_GRAPH_DIR_INTERNAL / query['uid']}.graphml")

    with _GRAPH_COLL_LOCK:
        _GRAPH_COLL.update_one({"uid": query["uid"]}, {"$set": {"status": "completed"}})

    logger.success(f"finished graph build job {uid!r}")
