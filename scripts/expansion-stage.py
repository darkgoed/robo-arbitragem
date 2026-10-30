"""Isolated real-data stage; online DB copies, fresh test auth; never production auth."""
import os
import shutil
import sqlite3
from pathlib import Path
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / 'data/expansion-stage'


def prepare():
    if HOME.exists():
        raise RuntimeError('Stage exists; use --serve to resume, never reset it')
    (HOME / 'data').mkdir(parents=True, mode=0o700)
    for name in ('research', 'discovery'):
        with sqlite3.connect(f"file:{ROOT / 'data' / (name + '.sqlite3')}?mode=ro", uri=True) as source, sqlite3.connect(HOME / 'data' / (name + '.sqlite3')) as target:
            source.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for name in ('archives', 'discovery-archives'):
        if (ROOT / 'data' / name).exists():
            shutil.copytree(ROOT / 'data' / name, HOME / 'data' / name)
    shutil.copy2(ROOT / 'VERSION', HOME / 'VERSION')
    print('Stage real-data copies integrity verified; new test-only authentication will be generated.')


if __name__ == '__main__':
    import sys
    if '--serve' not in sys.argv:
        prepare()
    os.environ['ROBO_HOME'] = str(HOME)
    from robo.app import create_app
    app = create_app(HOME, collect=True, test_origin='http://localhost:3216')
    uvicorn.run(app, host='127.0.0.1', port=3216, workers=1, access_log=False)
