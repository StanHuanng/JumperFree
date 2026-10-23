#!/usr/bin/env python3
"""要求非 false 的板级 verified 段附有本次变更中的证据文件。"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from jumperfree.board import BoardProfile
from jumperfree.board.profile import DEFAULT_BOARD_DIR
from jumperfree.core import JfError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board-dir', type=Path, default=DEFAULT_BOARD_DIR)
    parser.add_argument('--base', help='PR 基线或 push 前的提交 SHA')
    args = parser.parse_args(argv)
    try:
        profile = BoardProfile.load(args.board_dir)
        verified = [section for section in profile.sections if section.verified is not False]
        if verified:
            if not args.base:
                raise ValueError('verified 段需要 --base 以检查证据是否由本次变更修改')
            changed = subprocess.check_output(
                ['git', 'diff', '--name-only', '--diff-filter=AM', f'{args.base}...HEAD'],
                cwd=REPO_ROOT, text=True,
            ).splitlines()
            files = {(REPO_ROOT / name).resolve() for name in changed}
            for section in verified:
                if (args.board_dir / section.evidence).resolve() not in files:
                    raise ValueError(f'{section.name}: 证据文件未在本次变更中修改')
        print('BOARD_VERIFICATION OK（未自动修改 verified）')
        return 0
    except (JfError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'BOARD_VERIFICATION FAIL: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
