"""Offline structural validation; never executes content or retrieves remote schemas.

Requires Python 3.10+, jsonschema 4 and referencing. Schema bytes are generated
from the central VF contracts. This is not authorization or semantic validation.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys

LIMIT = 4 * 1024 * 1024
BUNDLE = Path(__file__).resolve().parents[1] / 'references/learning-contracts.generated.json'


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def reject_constant(_):
    raise ValueError('non-finite JSON number')


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('unsupported non-finite JSON number')
    return number


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('JSON exceeds structural validation size limit')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                      parse_constant=reject_constant, parse_float=finite_float)


def validator(name):
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource

    schemas = read_json(BUNDLE)

    def refuse_remote(uri):
        raise NoSuchResource(ref=uri)

    def normalize(node, base):
        if isinstance(node, dict):
            if '$ref' in node:
                ref = node['$ref']
                target, sep, fragment = ref.partition('#')
                if target:
                    if '://' not in target:
                        target = base.rsplit('/', 1)[0] + '/' + target.removesuffix('.schema.json')
                    if target not in schemas:
                        raise ValueError('unbundled schema reference')
                    node['$ref'] = target + (sep + fragment if sep else '')
            for value in node.values():
                normalize(value, base)
        elif isinstance(node, list):
            for value in node:
                normalize(value, base)

    resources = []
    normalized = copy.deepcopy(schemas)
    for key, schema in normalized.items():
        normalize(schema, key)
        Draft202012Validator.check_schema(schema)
        resources.append((key, Resource.from_contents(schema)))
    key = 'station://contracts/v2/' + name
    if key not in normalized:
        raise ValueError('unsupported local schema')
    registry = Registry(retrieve=refuse_remote).with_resources(resources)
    return Draft202012Validator(normalized[key], registry=registry, format_checker=FormatChecker())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--schema', choices=('reference-learning', 'world-draft-manifest'), default='reference-learning')
    args = parser.parse_args()
    try:
        validator(args.schema).validate(read_json(args.input))
    except ImportError:
        print('UNAVAILABLE: requires Python 3.10+, jsonschema 4 and referencing.', file=sys.stderr)
        return 2
    except Exception as error:
        # Validation errors may contain private prompts, paths or tokens.
        print('FAIL structure: ' + type(error).__name__, file=sys.stderr)
        return 1
    print('PASS structure only: ' + args.schema + '; permissions, references, coverage and execution remain unverified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
