"""Version groups used by branch discovery, preparation and PR cleanup."""
import re

from refresh_api_support_db import NIGHTLY_TABLE, support_table


def branch_group(basic, root):
    if 'venv' not in basic:  # Historical runs predate fixed-version environments.
        return 'nightly'
    table = support_table(basic, root)
    return 'nightly' if table == NIGHTLY_TABLE else table.removeprefix(NIGHTLY_TABLE + '_')


def grouped_mapping(raw):
    if not isinstance(raw, dict):
        raise ValueError('branch-mapping.json must contain an object')
    if all(isinstance(value, str) for value in raw.values()):
        raw = {'nightly': raw}
    for group, tasks in raw.items():
        if not re.fullmatch(r'nightly|[0-9]+\.[0-9]+', group) or not isinstance(tasks, dict):
            raise ValueError(f'Invalid branch mapping group: {group}')
        if any(not task.strip() or not isinstance(branch, str) or not branch.strip()
               for task, branch in tasks.items()):
            raise ValueError(f'Invalid branch mapping in {group}')
    return raw
