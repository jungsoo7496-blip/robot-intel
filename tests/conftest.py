"""pytest 공통 설정: scripts/ 를 import 경로에 추가한다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
