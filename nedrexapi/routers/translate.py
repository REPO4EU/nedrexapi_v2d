from itertools import chain as _chain
from fastapi import APIRouter as _APIRouter
from fastapi import Query as _Query
from nedrexapi.common import _API_KEY_HEADER_ARG, check_api_key_decorator
from nedrexapi.db import MongoInstance
from pydantic import BaseModel as _BaseModel
from pydantic import Field as _Field

from itertools import chain
import re

router = _APIRouter()

class NodeListRequest(_BaseModel):
    nodes: list[str] = _Field(None, title="Gene Identifier: Gene name, Entrez ID, or Ensembl ID",
                              description="Gene Identifier which is used to translate to Ensembl ID or Entrez ID")

    class Config:
        extra = "forbid"

_DEFAULT_NODE_REQUEST = NodeListRequest()

def make_node_list_request(items: list[str]) -> NodeListRequest:
    request = NodeListRequest()
    request.nodes = items
    return request

@router.post("/translate_entrez")
@check_api_key_decorator
def get_entrez_id(genes: NodeListRequest = _DEFAULT_NODE_REQUEST, x_api_key: str = _API_KEY_HEADER_ARG):
    if not genes.nodes:
        return {}

    results: dict[str, list[str]] = {gene: [] for gene in genes.nodes}

    entrez_inputs = []
    non_entrez_inputs = []
    for i in genes.nodes:
        if re.fullmatch(r"\d+", i):
            results[i].append(i)
            entrez_inputs.append(i)
        else:
            non_entrez_inputs.append(i)

    if not non_entrez_inputs:
        return results

    normalized_genes = []
    for i in non_entrez_inputs:
        if re.fullmatch(r"ENS[GTP]\d+(\.\d+)?", i):
            normalized_genes.append(f"ensembl.{i}")
        elif re.fullmatch(r"[A-Z0-9]{5,}", i) and not i.startswith("ENS"):
            normalized_genes.append(f"uniprot.{i}")
        else:
            normalized_genes.append(i)

    entrez_ids, gene_ids, uniprot_ids, gene_names = sort_list(normalized_genes)

    gene_coll = MongoInstance.DB()["gene"]
    protein_coll = MongoInstance.DB()["protein"]
    edge_coll = MongoInstance.DB()["protein_encoded_by_gene"]

    input_to_normalized = dict(zip(non_entrez_inputs, normalized_genes))

    if uniprot_ids:
        protein_query = {
            "$or": [
                {"primaryDomainId": {"$in": uniprot_ids}},
                {"domainIds": {"$in": uniprot_ids}}
            ]
        }

        uniprot_to_genes = {}
        uniprot_to_gene_names = {}

        for protein_doc in protein_coll.find(protein_query):
            protein_id = protein_doc.get("primaryDomainId", "")
            if not protein_id:
                continue

            protein_id_clean = protein_id.replace("uniprot.", "")

            gene_ids_found = []
            for edge_doc in edge_coll.find({"sourceDomainId": protein_id}):
                gene_id = edge_doc.get("targetDomainId", "")
                if gene_id:
                    gene_ids_found.append(gene_id)

            if not gene_ids_found:
                gene_name = protein_doc.get("geneName")
                if gene_name:
                    uniprot_to_gene_names[protein_id_clean] = gene_name

            if gene_ids_found:
                uniprot_to_genes[protein_id_clean] = gene_ids_found

        for original_input in non_entrez_inputs:
            normalized = input_to_normalized[original_input]
            if normalized in uniprot_ids:
                clean_uniprot = original_input.upper()
                if normalized.startswith("uniprot."):
                    clean_uniprot = normalized.replace("uniprot.", "")

                gene_ids_for_protein = uniprot_to_genes.get(clean_uniprot, [])

                if not gene_ids_for_protein:
                    gene_name = uniprot_to_gene_names.get(clean_uniprot)
                    if gene_name:
                        gene_doc = gene_coll.find_one({
                            "$or": [
                                {"symbols": gene_name},
                                {"approvedSymbol": gene_name},
                                {"displayName": gene_name}
                            ]
                        })
                        if gene_doc:
                            gene_ids_for_protein = [gene_doc.get("primaryDomainId", "")]

                if gene_ids_for_protein:
                    for gene_doc in gene_coll.find({"primaryDomainId": {"$in": gene_ids_for_protein}}):
                        entrez_id = None
                        primary_id = gene_doc.get("primaryDomainId", "")
                        if primary_id.startswith("entrez."):
                            entrez_id = primary_id.replace("entrez.", "")
                        else:
                            for domain_id in gene_doc.get("domainIds", []):
                                if domain_id.startswith("entrez."):
                                    entrez_id = domain_id.replace("entrez.", "")
                                    break

                        if entrez_id and entrez_id not in results[original_input]:
                            results[original_input].append(entrez_id)

    query_conditions = []
    if entrez_ids or gene_ids:
        all_domain_ids = entrez_ids + gene_ids
        query_conditions.append({"primaryDomainId": {"$in": all_domain_ids}})
        query_conditions.append({"domainIds": {"$in": all_domain_ids}})

    if gene_names:
        query_conditions.append({"symbols": {"$in": gene_names}})
        query_conditions.append({"approvedSymbol": {"$in": gene_names}})
        query_conditions.append({"displayName": {"$in": gene_names}})

    if query_conditions:
        query = {"$or": query_conditions} if len(query_conditions) > 1 else query_conditions[0]

        for doc in gene_coll.find(query):
            entrez_id = None
            primary_id = doc.get("primaryDomainId", "")
            if primary_id.startswith("entrez."):
                entrez_id = primary_id.replace("entrez.", "")
            else:
                for domain_id in doc.get("domainIds", []):
                    if domain_id.startswith("entrez."):
                        entrez_id = domain_id.replace("entrez.", "")
                        break

            if not entrez_id:
                continue

            doc_domain_ids = {primary_id} | set(doc.get("domainIds", []))
            doc_symbols = set(doc.get("symbols", []))
            if doc.get("approvedSymbol"):
                doc_symbols.add(doc.get("approvedSymbol"))
            if doc.get("displayName"):
                doc_symbols.add(doc.get("displayName"))

            for original_input in non_entrez_inputs:
                normalized = input_to_normalized[original_input]
                if normalized in uniprot_ids:
                    continue

                matches = (
                        normalized in doc_domain_ids or
                        original_input in doc_symbols
                )

                if matches:
                    if entrez_id not in results[original_input]:
                        results[original_input].append(entrez_id)

    return results




@router.post("/translate_uniprot")
@check_api_key_decorator
def get_uniprot_id(genes: NodeListRequest = _DEFAULT_NODE_REQUEST, x_api_key: str = _API_KEY_HEADER_ARG):
    if not genes.nodes:
        return {}

    results: dict[str, list[str]] = {gene: [] for gene in genes.nodes}

    non_uniprot_inputs = []
    for i in genes.nodes:
        if re.fullmatch(r"[A-Z0-9]{5,}", i) and not i.startswith("ENS") and not re.fullmatch(r"\d+", i):
            results[i].append(i.upper())
        else:
            non_uniprot_inputs.append(i)

    if not non_uniprot_inputs:
        return results

    normalized_genes = []
    for i in non_uniprot_inputs:
        if re.fullmatch(r"\d+", i):
            normalized_genes.append(f"entrez.{i}")
        elif re.fullmatch(r"ENS[GTP]\d+(\.\d+)?", i):
            normalized_genes.append(f"ensembl.{i}")
        else:
            normalized_genes.append(i)

    entrez_ids, gene_ids, uniprot_ids, gene_names = sort_list(normalized_genes)

    gene_coll = MongoInstance.DB()["gene"]
    protein_coll = MongoInstance.DB()["protein"]
    edge_coll = MongoInstance.DB()["protein_encoded_by_gene"]

    input_to_normalized = dict(zip(non_uniprot_inputs, normalized_genes))

    if uniprot_ids:
        protein_query = {
            "$or": [
                {"primaryDomainId": {"$in": uniprot_ids}},
                {"domainIds": {"$in": uniprot_ids}}
            ]
        }

        uniprot_to_genes = {}
        uniprot_to_gene_names = {}

        for protein_doc in protein_coll.find(protein_query):
            protein_id = protein_doc.get("primaryDomainId", "")
            if not protein_id:
                continue

            protein_id_clean = protein_id.replace("uniprot.", "")

            gene_ids_found = []
            for edge_doc in edge_coll.find({"sourceDomainId": protein_id}):
                gene_id = edge_doc.get("targetDomainId", "")
                if gene_id:
                    gene_ids_found.append(gene_id)

            if not gene_ids_found:
                gene_name = protein_doc.get("geneName")
                if gene_name:
                    uniprot_to_gene_names[protein_id_clean] = gene_name

            if gene_ids_found:
                uniprot_to_genes[protein_id_clean] = gene_ids_found

        for original_input in non_uniprot_inputs:
            normalized = input_to_normalized[original_input]
            if normalized in uniprot_ids:
                clean_uniprot = original_input.upper()
                if normalized.startswith("uniprot."):
                    clean_uniprot = normalized.replace("uniprot.", "")

                gene_ids_for_protein = uniprot_to_genes.get(clean_uniprot, [])

                if not gene_ids_for_protein:
                    gene_name = uniprot_to_gene_names.get(clean_uniprot)
                    if gene_name:
                        gene_doc = gene_coll.find_one({
                            "$or": [
                                {"symbols": gene_name},
                                {"approvedSymbol": gene_name},
                                {"displayName": gene_name}
                            ]
                        })
                        if gene_doc:
                            gene_ids_for_protein = [gene_doc.get("primaryDomainId", "")]

                if gene_ids_for_protein:
                    for edge_doc in edge_coll.find({"targetDomainId": {"$in": gene_ids_for_protein}}):
                        protein_id = edge_doc.get("sourceDomainId", "")
                        if protein_id and protein_id.startswith("uniprot."):
                            protein_id_clean = protein_id.replace("uniprot.", "")
                            if protein_id_clean and protein_id_clean not in results[original_input]:
                                results[original_input].append(protein_id_clean)
                else:
                    if clean_uniprot and clean_uniprot not in results[original_input]:
                        if protein_coll.find_one({"$or": [
                            {"primaryDomainId": f"uniprot.{clean_uniprot}"},
                            {"domainIds": f"uniprot.{clean_uniprot}"}
                        ]}):
                            results[original_input].append(clean_uniprot)

    query_conditions = []
    if entrez_ids or gene_ids:
        all_domain_ids = entrez_ids + gene_ids
        query_conditions.append({"primaryDomainId": {"$in": all_domain_ids}})
        query_conditions.append({"domainIds": {"$in": all_domain_ids}})
    if gene_names:
        query_conditions.append({"symbols": {"$in": gene_names}})
        query_conditions.append({"approvedSymbol": {"$in": gene_names}})
        query_conditions.append({"displayName": {"$in": gene_names}})

    if query_conditions:
        query = {"$or": query_conditions} if len(query_conditions) > 1 else query_conditions[0]
        input_to_gene_id = {}

        for doc in gene_coll.find(query):
            primary_id = doc.get("primaryDomainId", "")
            doc_domain_ids = {primary_id} | set(doc.get("domainIds", []))
            doc_symbols = set(doc.get("symbols", []))
            if doc.get("approvedSymbol"):
                doc_symbols.add(doc.get("approvedSymbol"))
            if doc.get("displayName"):
                doc_symbols.add(doc.get("displayName"))

            for original_input in non_uniprot_inputs:
                normalized = input_to_normalized[original_input]
                if normalized in uniprot_ids:
                    continue

                matches = (normalized in doc_domain_ids or original_input in doc_symbols)

                if matches and original_input not in input_to_gene_id:
                    input_to_gene_id[original_input] = primary_id

        if input_to_gene_id:
            matched_gene_ids = set(input_to_gene_id.values())

            for doc in edge_coll.find({"targetDomainId": {"$in": list(matched_gene_ids)}}):
                protein_id = doc.get("sourceDomainId", "")
                if not protein_id:
                    continue

                if protein_id.startswith("uniprot."):
                    protein_id = protein_id.replace("uniprot.", "")

                gene_id = doc.get("targetDomainId", "")

                for original_input, mapped_gene_id in input_to_gene_id.items():
                    if mapped_gene_id == gene_id:
                        if protein_id and protein_id not in results[original_input]:
                            results[original_input].append(protein_id)

    return results


def sort_list(genes):
    entrez_ids = []
    gene_ids = []
    uniprot_ids = []
    gene_names = []

    for gene in genes:
        if re.fullmatch(r"entrez\.\d+", gene):
            entrez_ids.append(gene)
        elif re.fullmatch(r"ensembl\.ENS[GTP]\d+(\.\d+)?", gene):
            gene_ids.append(gene)
        elif re.fullmatch(r"uniprot\.[A-Za-z0-9]+", gene):
            uniprot_ids.append(gene)
        else:
            gene_names.append(gene)

    return entrez_ids, gene_ids, uniprot_ids, gene_names