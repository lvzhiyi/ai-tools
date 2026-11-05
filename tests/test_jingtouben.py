"""镜头本导入：确定性字段由代码保证，模型输出只用来补英文提示词；不访问真实模型。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import cli, jingtouben, storyboard

FIXTURE = Path(__file__).parent / "fixtures" / "jingtouben-export.json"


def export() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def model_output() -> dict:
    return {
        "characters": [
            {"name": "林夏", "id": "linxia", "look": "a 25-year-old East Asian woman with shoulder-length black hair"},
            {"name": "雨衣男", "id": "raincoat_man", "look": "a tall man in a dark hooded raincoat"},
        ],
        "shots": [
            {"id": 1, "image": "Cinematic vertical film still of an empty neon street, extreme long shot",
             "motion": "rain keeps falling, slow pan right", "voice": "Vivian", "emotion": ""},
            {"id": 2, "image": "Cinematic vertical film still of {雨衣男} under a streetlight, long shot",
             "motion": "{raincoat_man} stands still, camera pushes in", "voice": "Uncle_Fu", "emotion": "低沉"},
            {"id": 3, "image": "Cinematic vertical film still of {linxia} with {stranger} behind",
             "motion": "{linxia} presses the phone to her ear", "voice": "Vivian", "emotion": "急促"},
        ],
    }


class ParseTests(unittest.TestCase):
    def test_sorts_and_renumbers_shots_and_adds_cardless_characters(self):
        src = jingtouben.parse(export())
        self.assertEqual([s["scene"][:2] for s in src["shots"]], ["雨夜", "穿雨", "林夏"])
        self.assertEqual([s["id"] for s in src["shots"]], [1, 2, 3])
        self.assertEqual([(c["name"], c["card"]) for c in src["characters"]], [("林夏", True), ("雨衣男", False)])

    def test_accepts_drift_column_names(self):
        data = export()
        for s in data["shots"]:
            s["sortOrder"] = s.pop("order")
            s["characterNames"] = s.pop("characters")
            s["shotSize"] = {"特写": "closeUp", "大远景": "extremeLong", "远景": "long"}[s["shotSize"]]
        src = jingtouben.parse(data)
        self.assertEqual(src["shots"][2]["names"], ["林夏"])
        self.assertEqual(src["shots"][0]["size"], "extremeLong")

    def test_rejects_bad_input_with_readable_reasons(self):
        cases = {
            "format": lambda d: d.update(format="other"),
            "标题": lambda d: d["project"].update(title=" "),
            "没有镜头": lambda d: d.update(shots=[]),
            "景别": lambda d: d["shots"][0].update(shotSize="俯拍"),
            "scene": lambda d: d["shots"][0].update(scene=""),
            "重名": lambda d: d["characters"].append({"name": "林夏"}),
        }
        for message, mutate in cases.items():
            with self.subTest(message):
                data = export()
                mutate(data)
                with self.assertRaisesRegex(ValueError, message):
                    jingtouben.parse(data)


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.src = jingtouben.parse(export())

    def test_keeps_dialogue_and_cast_from_export_and_fixes_placeholders(self):
        sb = jingtouben.convert(self.src, model_output())
        self.assertEqual(set(sb["characters"]), {"linxia", "raincoat_man"})
        s1, s2, s3 = sb["shots"]
        self.assertEqual(s1["line"], "")
        self.assertNotIn("voice", s1)
        self.assertEqual(s2["line"], "那个人影，始终保持着二十米的距离。")
        self.assertIn("{raincoat_man}", s2["image"])  # 中文名占位符改成 id
        self.assertEqual(s3["characters"], ["linxia"])
        self.assertNotIn("{stranger}", s3["image"])  # 未知占位符去掉花括号
        self.assertEqual(s3["note"], "特写表现决心")
        self.assertTrue(s3["image"].endswith(", close-up."))  # 模型漏写的景别由代码补上
        self.assertEqual(s1["image"].lower().count("extreme long shot"), 1)  # 已写的不重复补

    def test_falls_back_to_generated_ids_when_model_ids_are_bad(self):
        out = model_output()
        out["characters"][0]["id"] = "林夏"
        out["characters"][1]["id"] = "linxia"
        sb = jingtouben.convert(self.src, out)
        self.assertEqual(list(sb["characters"]), ["char1", "linxia"])
        self.assertEqual(sb["shots"][2]["characters"], ["char1"])

    def test_matches_renamed_character_by_position(self):
        # 真实模型（gemma-4-e4b）出现过：把 name 从「林夏」改成 linxia
        out = model_output()
        out["characters"][0]["name"] = "linxia"
        sb = jingtouben.convert(self.src, out)
        self.assertEqual(sb["characters"]["linxia"]["name"], "林夏")
        self.assertIn("shoulder-length", sb["characters"]["linxia"]["look"])

    def test_missing_shot_is_an_error(self):
        out = model_output()
        out["shots"].pop()
        with self.assertRaisesRegex(ValueError, "漏了镜头"):
            jingtouben.convert(self.src, out)

    def test_result_passes_pipeline_validation(self):
        sb = jingtouben.convert(self.src, model_output())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
            loaded = storyboard.load(p)
        self.assertEqual(len(loaded["shots"]), 3)


class ImportTests(unittest.TestCase):
    def test_import_sends_structured_request_and_warns_about_cardless_character(self):
        with patch.object(jingtouben.llm, "chat", return_value=copy.deepcopy(model_output())) as chat:
            sb, warnings = jingtouben.import_storyboard(export(), {"model": "m"})
        payload = json.loads(chat.call_args.args[1][1]["content"])
        self.assertEqual(payload["shots"][0]["shot_size"], "extreme long shot")
        self.assertIs(chat.call_args.args[2], jingtouben.SCHEMA)
        self.assertEqual(sb["title"], "雨夜")
        self.assertTrue(any("雨衣男" in w for w in warnings))

    def test_cli_writes_storyboard_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "storyboards").mkdir()
            with patch.object(cli, "ROOT", root), \
                    patch.object(jingtouben.llm, "chat", side_effect=lambda *a, **k: copy.deepcopy(model_output())), \
                    patch.object(cli, "load_config", return_value={"lmstudio": {}, "comfy": {}}), \
                    patch("studio.web.projects.SB_DIR", root / "storyboards"):
                self.assertEqual(cli.main(["import", str(FIXTURE)]), 0)
                out = root / "storyboards" / "雨夜.json"
                self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["source"], jingtouben.FORMAT)
                self.assertEqual(cli.main(["import", str(FIXTURE)]), 1)
                self.assertEqual(cli.main(["import", str(FIXTURE), "--name", "雨夜-2"]), 0)
            self.assertEqual(sorted(p.name for p in (root / "storyboards").iterdir()), ["雨夜-2.json", "雨夜.json"])


if __name__ == "__main__":
    unittest.main()
