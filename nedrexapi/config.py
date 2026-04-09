from dataclasses import dataclass as _dataclass
from pprint import pformat as _pformat
from typing import Any as _Any
from typing import Mapping
from typing import Optional as _Optional

import toml as _toml  # type: ignore

from nedrexapi.exceptions import ConfigError as _ConfigError


@_dataclass
class _Config:
    data: _Optional[dict[_Any, _Any]] = None

    def __repr__(self) -> str:
        return _pformat(self.data)

    def from_file(self, infile: str) -> None:
        with open(infile, "r") as f:
            self.data = _toml.load(f)
            # from here on, set defaults for minimal config
            if "version_type" in self.data.keys():
                print("USING MINIMAL CONFIG MODE")
                vt = self.data["version_type"]
                defaults_dict = {'api': {'mode': f'{vt}',
                                         'status': 'live',
                                         'base': f'/{vt}',
                                         'rate_limiting_enabled': False,
                                         'rate_limit': '1/second',
                                         'require_api_keys': True if vt == "licensed" else False,
                                         'pagination_max': 10000,
                                         'redis_port_internal': 6379,
                                         'redis_host': f'{vt}-nedrex-redis',
                                         'redis_nedrex_db': 1,
                                         'redis_rate_limit_db': 2,
                                         'redis_queue_db': 3,
                                         'mongo_port_internal': 27017,
                                         'mongo_db': 'nedrexapi',
                                         'node_collections': ['disorder',
                                                              'drug',
                                                              'gene',
                                                              'genomic_variant',
                                                              'go',
                                                              'pathway',
                                                              'protein',
                                                              'phenotype',
                                                              'tissue',
                                                              'side_effect',
                                                              'signature'],
                                         'edge_collections': ['disorder_has_phenotype',
                                                              'disorder_is_subtype_of_disorder',
                                                              'drug_has_contraindication',
                                                              'drug_has_indication',
                                                              'drug_has_target',
                                                              'drug_has_side_effect',
                                                              'gene_associated_with_disorder',
                                                              'go_is_subtype_of_go',
                                                              'molecule_similarity_molecule',
                                                              'protein_encoded_by_gene',
                                                              'protein_has_go_annotation',
                                                              'protein_has_signature',
                                                              'protein_in_pathway',
                                                              'protein_interacts_with_protein',
                                                              'protein_expressed_in_tissue',
                                                              'gene_expressed_in_tissue',
                                                              'side_effect_same_as_phenotype',
                                                              'variant_affects_gene',
                                                              'variant_associated_with_disorder'],
                                         'directories': {'static': '/data/nedrex_files/nedrex_api/static',
                                                         'static_outside': self.data['static_outside'],
                                                         'data': '/data/nedrex_files/nedrex_api/data',
                                                         'data_outside': self.data['data_outside'],
                                                         'scripts': '/app/nedrexapi/scripts'}},
                                 'tools': {'bicon_python': '/opt/conda/envs/bicon/bin/python'},
                                 'db': {'version': f'{vt}',
                                        'neo4j_image': 'neo4j:3.5.34',
                                        'mongo_image': 'mongo:4.4.10',
                                        'mongo_express_image': 'mongo-express:0.54.0',
                                        'mongo_db': 'nedrex',
                                        'root_directory': '/data/nedrex_files/nedrex_data',
                                        'volume_root': f'{vt}_nedrex',
                                        'dev': {'mongo_port': 27017 if vt == "licensed" else 26017,
                                                'mongo_name': f'{vt}_nedrex_dev',
                                                'mongo_express_port': 8081 if vt == "licensed" else 7081,
                                                'neo4j_http_port': 7478 if vt == "licensed" else 6478,
                                                'neo4j_bolt_port': 7688 if vt == "licensed" else 6688,
                                                'container_name': f'{vt}_nedrex_dev',
                                                'express_container_name': f'{vt}_nedrex_dev_express'},
                                        'live': {'mongo_port': 27018 if vt == "licensed" else 26018,
                                                 'mongo_port_internal': 27017,
                                                 'mongo_express_port': 8082 if vt == "licensed" else 7082,
                                                 'mongo_name': f'{vt}_nedrex_live',
                                                 'neo4j_http_port': 7479 if vt == "licensed" else 6479,
                                                 'neo4j_bolt_port': 7689 if vt == "licensed" else 6689,
                                                 'neo4j_bolt_port_internal': 7687,
                                                 'neo4j_name': f'{vt}_nedrex_live_neo4j',
                                                 'container_name': f'{vt}_nedrex_live',
                                                 'express_container_name': f'{vt}_nedrex_live_express'}}}

                # overwrite defaults with config
                overwrite_set = {'neo4j_image', 'mongo_image', 'mongo_image'}
                for image in overwrite_set:
                    if image in self.data.keys():
                        defaults_dict["db"][image] = self.data[image]
                if vt == "open":
                    defaults_dict["api"]["redis_port"] = 5379
                    defaults_dict["db"]["dev"]["mongo_port_internal"] = 27017
                    defaults_dict["db"]["dev"]["neo4j_http_port_internal"] = 7474
                    defaults_dict["db"]["dev"]["neo4j_bolt_port_internal"] = 7687
                    defaults_dict["db"]["live"]["neo4j_http_port_internal"] = 7474

                if "config_deep_merge" in self.data.keys() and self.data["config_deep_merge"] == False:
                    self.data = defaults_dict | self.data
                else:
                    self.data = self.deep_merge(defaults_dict, self.data)

    def deep_merge(self, dict1, dict2):
        for key, value in dict2.items():
            if key in dict1:
                if isinstance(dict1[key], dict) and isinstance(value, dict):
                    # Recursively merge dictionaries
                    self.deep_merge(dict1[key], value)
                else:
                    # Overwrite the value in dict1 with the value from dict2
                    dict1[key] = value
            else:
                # Add the key-value pair from dict2 to dict1
                dict1[key] = value
        return dict1


    def __getitem__(self, path: str) -> _Any:
        if self.data is None:
            raise _ConfigError("config has not been parsed (currently None)")

        split_path = path.split(".")
        current: _Any = self.data

        for idx, val in enumerate(split_path):
            if isinstance(current, Mapping):
                current = current.get(val)
            else:
                failed_path = ".".join(split_path[: idx + 1])
                raise _ConfigError(f"{failed_path!r} is not in config")

            if current is None:
                failed_path = ".".join(split_path[: idx + 1])
                raise _ConfigError(f"{failed_path!r} is not in config")

        return current

    def get(self, path: str) -> _Any:
        try:
            return self[path]
        except _ConfigError:
            return None


config = _Config()


def parse_config(infile: str) -> None:
    global config
    config.from_file(infile)
