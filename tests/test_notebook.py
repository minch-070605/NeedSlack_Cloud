# -*- coding: utf-8 -*-
"""구워진 Colab 노트북이 Colab 에 가기 전에 잡을 수 있는 오류를 여기서 잡는다 (GPU 불필요).

M3 첫 실행에서 마지막 셀이 `NameError: name 'true' is not defined` 로 죽었다 — 생성기가 오프라인 결과를
json.dumps 로 코드에 실어 JSON 의 true/false 가 파이썬 코드에 들어갔다. 학습·평가가 끝난 뒤에 드러나서
값이 컸다. 노트북은 Colab 에서만 돌지만, 셀의 문법과 셀에 실린 값은 여기서 검사할 수 있다.

    python -m unittest discover -s tests
"""
import ast
import json
import os
import unittest

NB = os.path.join(os.path.dirname(__file__), "..", "pipelines", "colab_train.ipynb")
JSON_NAMES = {"true", "false", "null"}


def code_cells():
    with open(NB, encoding="utf-8") as f:
        nb = json.load(f)
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] == "code":
            src = "".join(c["source"])
            # `!pip ...` 같은 셸 줄은 IPython 문법이라 파이썬 파서에서 뺀다
            yield i, "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("!"))


class TestNotebook(unittest.TestCase):
    def test_cells_parse(self):
        for i, src in code_cells():
            with self.subTest(cell=i):
                ast.parse(src)

    def test_no_json_names_in_code(self):
        """true/false/null 은 파이썬에서 정의되지 않은 이름이다."""
        for i, src in code_cells():
            names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
            with self.subTest(cell=i):
                self.assertFalse(names & JSON_NAMES, f"셀 {i} 에 {names & JSON_NAMES}")

    def test_baked_values_are_python_literals(self):
        """생성기가 구워 넣은 상수(EXPECTED · OFFLINE · TRAIN · FILES · PROVENANCE)가 리터럴로 읽힌다."""
        baked = {"EXPECTED", "OFFLINE", "TRAIN", "FILES", "PROVENANCE"}
        found = {}
        for _, src in code_cells():
            for node in ast.parse(src).body:
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                        and node.targets[0].id in baked:
                    found[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(set(found), baked)
        self.assertIn("DYN_B_vs_STA_B", found["OFFLINE"])
        self.assertIsInstance(found["OFFLINE"]["DYN_B_vs_STA_B"]["non_inferior"], bool)
        self.assertEqual(set(found["EXPECTED"]), {"eval-common.tar", "ds-STA_B.tar", "ds-DYN_B.tar"})
        for name, text in found["FILES"].items():          # 실린 rev.9 코드 자체도 파이썬이다
            with self.subTest(file=name):
                ast.parse(text)


if __name__ == "__main__":
    unittest.main()
