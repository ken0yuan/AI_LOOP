"""Resolve the batch's latest torch-npu wheel, or register a successful rebuild."""
import argparse
from pathlib import Path
import shutil
import tempfile
import zipfile

from fetch_nightly_wheels import wheel_info
from preparation_state import load_basic, locked, relative_path, sha256, task_config, write_json

ROOT = Path(__file__).resolve().parents[1]
FIELD = 'last-torchnpu-wheel'


def task_wheel(basic_path, built_wheel=None, *, root=ROOT):
    basic_path, _ = load_basic(basic_path, root/'data')
    run = basic_path.parent
    with locked(run/'log/.step0-entry.lock'):
        _, basic = load_basic(basic_path, root/'data')
        archive = run/'wheels'
        if built_wheel is None:
            selected = basic.get(FIELD, basic.get('torch_npu_wheel'))
            if not selected:
                raise ValueError('No torch-npu wheel recorded for this batch')
            path = (root/selected).resolve(strict=True)
            if not path.is_file() or path.suffix != '.whl':
                raise ValueError(f'Invalid recorded wheel: {path}')
            if path.parent.parent == archive and sha256(path) != path.parent.name:
                raise ValueError(f'Recorded wheel checksum mismatch: {path}')
            return path

        source = Path(built_wheel).resolve(strict=True)
        archive.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=archive) as temporary:
            staged = Path(temporary)/source.name
            shutil.copyfile(source, staged)
            info = wheel_info(staged, task_config(basic))
            if info['name'] != 'torch-npu':
                raise ValueError(f'Expected a torch-npu wheel: {source.name}')
            destination = archive/info['sha256']/source.name
            destination.parent.mkdir(exist_ok=True)
            staged.replace(destination)
        basic[FIELD] = relative_path(destination, root)
        write_json(basic_path, basic)
        return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basic-information', required=True, type=Path)
    parser.add_argument('--built-wheel', type=Path, help='The exact .whl produced by this build')
    args = parser.parse_args()
    try:
        print(task_wheel(args.basic_information, args.built_wheel))
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f'{error}\n')
