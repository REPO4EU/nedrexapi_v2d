import os
import subprocess
import tempfile
import traceback
from csv import DictReader
from itertools import product

import networkx as nx  # type: ignore

from nedrexapi.common import (
    _TRUSTRANK_COLL,
    _TRUSTRANK_COLL_LOCK,
    _TRUSTRANK_DIR_INTERNAL,
    _TRUSTRANK_SUFFIX,
    _STATIC_DIR,
    _STATIC_DIR_INTERNAL,
    _DATA_DIR_INTERNAL,
    generate_ranking_static_files,
)
from nedrexapi.config import config
from nedrexapi.logger import logger


def run_trustrank_wrapper(uid):
    try:
        run_trustrank(uid)
    except Exception as E:
        traceback.print_exc()
        with _TRUSTRANK_COLL_LOCK:
            _TRUSTRANK_COLL.update_one({"uid": uid}, {"$set": {"status": "failed", "error": f"{E}"}})


def run_trustrank(uid):
    ranking_file = f"{config['api.mode']}/PPDr-for-ranking.graphml"
    if not os.path.exists(os.path.join(_STATIC_DIR_INTERNAL, ranking_file)):
        generate_ranking_static_files()

    with _TRUSTRANK_COLL_LOCK:
        details = _TRUSTRANK_COLL.find_one({"uid": uid})
        if not details:
            raise Exception(f"No TrustRank job with UID {uid!r}")
        _TRUSTRANK_COLL.update_one({"uid": uid}, {"$set": {"status": "running"}})
        logger.info(f"starting TrustRank job {uid!r}")

    tmp = tempfile.NamedTemporaryFile(mode="wt")
    for seed in details["seed_proteins"]:
        tmp.write("uniprot.{}\n".format(seed))
    tmp.flush()

    outfile = _TRUSTRANK_DIR_INTERNAL / f"{uid}.txt"
    outfile_internal = _DATA_DIR_INTERNAL / _TRUSTRANK_SUFFIX / f"{uid}.txt"

    command = [
        f"{config['api.directories.scripts']}/run_trustrank.py",
        "-e", _STATIC_DIR_INTERNAL,
        "-i", _STATIC_DIR_INTERNAL,
        "-n",
        ranking_file,
        "-s",
        f"{tmp.name}",
        "-d",
        f"{details['damping_factor']}",
        "-o",
        f"{outfile}",
    ]
    print(command)

    if details["only_direct_drugs"]:
        command.append("--only_direct_drugs")
    if details["only_approved_drugs"]:
        command.append("--only_approved_drugs")

    res = subprocess.call(command)
    if res != 0:
        with _TRUSTRANK_COLL_LOCK:
            _TRUSTRANK_COLL.update_one(
                {"uid": uid},
                {
                    "$set": {
                        "status": "failed",
                        "error": f"Process exited with exit code {res} -- please contact API developer.",
                    }
                },
            )
        return

    if not details["N"]:
        with _TRUSTRANK_COLL_LOCK:
            _TRUSTRANK_COLL.update_one({"uid": uid}, {"$set": {"status": "completed"}})
        return

    results = {}

    with outfile_internal.open("r") as f:
        keep = []
        reader = DictReader(f, delimiter="\t")
        for _ in range(details["N"]):
            item = next(reader)
            if float(item["score"]) == 0:
                break
            keep.append(item)

        lowest_score = keep[-1]["score"]
        if float(lowest_score) != 0:
            while True:
                item = next(reader)
                if item["score"] != lowest_score:
                    break
                keep.append(item)

    results["drugs"] = keep
    results["edges"] = []

    drug_ids = {i["drug_name"] for i in results["drugs"]}
    seeds = {f"uniprot.{seed}" for seed in details["seed_proteins"]}

    g = nx.read_graphml(ranking_file)
    for edge in product(drug_ids, seeds):
        if g.has_edge(*edge):
            results["edges"].append(list(edge))

    with _TRUSTRANK_COLL_LOCK:
        _TRUSTRANK_COLL.update_one({"uid": uid}, {"$set": {"status": "completed", "results": results}})

    logger.success(f"finished TrustRank job {uid!r}")
