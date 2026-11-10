#!/usr/bin/env python3
"""Resolve every documented Python name without calling APIs or constructing objects.

Uses explicit module imports for modules not re-exported from their parent,
inspect.getattr_static for class members (including inherited descriptors), and
targeted getattr for module lazy attributes and dynamic operator namespaces.
Failed paths remain in the report. Missing attributes are not silently discarded.
This verifies object exposure, not execution, publicness or hardware support.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import types
import textwrap
from urllib.parse import urldefrag, unquote


def object_kind(obj):
    if inspect.isclass(obj): return 'class'
    if isinstance(obj, types.ModuleType): return 'module'
    if isinstance(obj, property): return 'property'
    if inspect.ismethoddescriptor(obj): return 'method_descriptor'
    if inspect.isdatadescriptor(obj): return 'data_descriptor'
    if callable(obj): return 'callable'
    return 'data'


class Resolver:
    def __init__(self):
        import torch
        self.cache = {'torch': torch}
        self.import_cache = {'torch': torch}
        self.import_errors = {}

    def declared_field(self, cls, member):
        for base in cls.__mro__:
            annotations = vars(base).get('__annotations__', {})
            if isinstance(annotations, dict) and member in annotations:
                return {'status': 'declared_field', 'field_evidence': 'class_annotation',
                        'declaring_class': base.__module__ + '.' + base.__qualname__,
                        'annotation': str(annotations[member])}
            try:
                source, first_line = inspect.getsourcelines(base)
                tree = ast.parse(textwrap.dedent(''.join(source)))
            except (OSError, TypeError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and isinstance(node.value, ast.Name) and node.value.id == 'self' and node.attr == member:
                    return {'status': 'declared_field', 'field_evidence': 'instance_assignment_in_source',
                            'source_file': inspect.getsourcefile(base), 'source_line': first_line + node.lineno - 1,
                            'declaring_class': base.__module__ + '.' + base.__qualname__}
        return None

    def resolve(self, name, role=None):
        parts = name.split('.')
        if parts[0] != 'torch':
            return {'status': 'outside_torch_scope', 'failed_path': parts[0]}
        if not all(p.isidentifier() for p in parts):
            return {'status': 'invalid_identifier'}
        obj = self.cache['torch']
        owner_class = None
        trace = []
        # A package can export a function with the same name as a submodule.
        # Import the longest module prefix explicitly instead of following the
        # function alias and then incorrectly treating its child as missing.
        start = 1
        last_module = len(parts) if role == 'py:module' else len(parts) - 1
        for count in range(last_module, 0, -1):
            prefix = '.'.join(parts[:count])
            if prefix in self.import_cache:
                obj = self.import_cache[prefix]; start = count
                break
            if prefix in self.import_errors:
                continue
            try:
                obj = importlib.import_module(prefix)
                self.import_cache[prefix] = obj
                start = count
                break
            except ModuleNotFoundError as exc:
                if exc.name and (prefix == exc.name or prefix.startswith(exc.name + '.')):
                    self.import_errors[prefix] = str(exc)
                    continue
                return {'status': 'missing_dependency', 'failed_path': prefix,
                        'error_type': type(exc).__name__, 'message': str(exc), 'missing_module': exc.name}
            except (Exception, SystemExit) as exc:
                return {'status': 'unresolved', 'failed_path': prefix,
                        'error_type': type(exc).__name__, 'message': str(exc)}
        trace.append({'path': '.'.join(parts[:start]), 'mechanism': 'longest_importable_module_prefix'})
        for index in range(start, len(parts)):
            part = parts[index]
            path = '.'.join(parts[:index + 1])
            try:
                if inspect.isclass(obj):
                    owner_class = '.'.join(parts[:index])
                    try:
                        value = inspect.getattr_static(obj, part)
                    except AttributeError:
                        if index == len(parts) - 1:
                            field = self.declared_field(obj, part)
                            if field:
                                return {**field, 'owner_class': owner_class, 'trace': trace}
                        raise
                    mechanism = 'static_class_member'
                else:
                    try:
                        value = getattr(obj, part)
                        mechanism = 'attribute'
                    except AttributeError as attr_error:
                        if not isinstance(obj, types.ModuleType) or not vars(obj).get('__path__'):
                            raise
                        try:
                            value = importlib.import_module(path)
                            mechanism = 'import_module'
                        except ModuleNotFoundError as module_error:
                            if module_error.name == path:
                                raise attr_error
                            raise
                if isinstance(value, (classmethod, staticmethod)):
                    value = value.__func__
                obj = value
                self.cache[path] = obj
                trace.append({'path': path, 'mechanism': mechanism})
            except (Exception, SystemExit) as exc:
                status = 'missing_dependency' if isinstance(exc, ModuleNotFoundError) else 'unresolved'
                return {'status': status, 'failed_path': path,
                        'error_type': type(exc).__name__, 'message': str(exc),
                        'missing_module': getattr(exc, 'name', None),
                        'owner_class': owner_class, 'trace': trace}
        return {'status': 'resolved', 'object_kind': object_kind(obj),
                'object_module': getattr(obj, '__module__', None),
                'owner_class': owner_class, 'trace': trace}


def audit(inventory, output, discovered=None):
    os.environ.setdefault('TORCH_DEVICE_BACKEND_AUTOLOAD', '0')
    import torch
    resolver = Resolver()
    records = [json.loads(line) for line in inventory.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len({r['name'] for r in records}) != len(records):
        raise ValueError('Duplicate document names require explicit handling')
    discovered_names = None
    if discovered:
        files = [discovered] if isinstance(discovered, Path) else discovered
        discovered_names = set()
        for file in files:
            with file.open(encoding='utf-8') as source:
                for line in source:
                    if line.strip():discovered_names.add(json.loads(line)['name'])
    rows = []
    for i, record in enumerate(records):
        row = {**record, **resolver.resolve(record['name'], record['role'])}
        if row['status'] == 'unresolved':
            anchor = unquote(urldefrag(record['url'])[1])
            if anchor != record['name'] and anchor.startswith('torch.') and all(p.isidentifier() for p in anchor.split('.')):
                mapped = resolver.resolve(anchor, record['role'])
                if mapped['status'] == 'resolved':
                    row.update(status='documented_alias', documented_target=anchor, target_result=mapped)
        if discovered_names is not None:
            row['independently_discovered'] = record['name'] in discovered_names
        rows.append(row)
        if (i + 1) % 500 == 0:
            print(f'Checked {i + 1}/{len(records)}', flush=True)
    summary = {'documented_names': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
               'status_by_role': {role: dict(Counter(r['status'] for r in rows if r['role'] == role)) for role in sorted({r['role'] for r in rows})}}
    if discovered_names is not None:
        summary.update(independently_discovered=sum(r['independently_discovered'] for r in rows),
                       not_independently_discovered=sum(not r['independently_discovered'] for r in rows))
    report = {'provenance': {'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest(),
                            'torch_version': str(torch.__version__), 'torch_git_version': torch.version.git_version,
                            'torch_file': torch.__file__, 'cuda_build': torch.version.cuda,
                            'python': sys.version, 'python_executable': sys.executable,
                            'torch_npu_installed': importlib.util.find_spec('torch_npu') is not None,
                            'created_at_utc': datetime.now(timezone.utc).isoformat(),
                            'scope': 'Name lookup only; no API invocation or class instantiation; public/private names retained.'},
              'summary': summary, 'rows': rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    output.with_suffix('.unresolved.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows if r['status'] not in ('resolved', 'declared_field', 'documented_alias')), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--discovered', type=Path, action='append', help='Independent traversal JSONL; repeat to compare with their union')
    args = parser.parse_args()
    audit(args.inventory, args.output, args.discovered)


if __name__ == '__main__':
    main()
