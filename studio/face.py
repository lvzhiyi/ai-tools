"""人脸一致性：OpenCV 的 YuNet（检测）+ SFace（识别），纯 CPU、Apache-2.0 协议。

思路：每个角色有一张定妆照，算出"人脸特征向量"；镜头里的每张脸也算一个向量，
两者的余弦相似度就是"像不像同一个人"的分数。OpenCV 官方给 SFace 的同人阈值是 0.363。
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

from .config import ROOT

MODEL_DIR = ROOT / "models"
MODELS = {
    "detector": (
        "face_detection_yunet_2023mar.onnx",
        [
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
            "https://hf-mirror.com/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        ],
    ),
    "recognizer": (
        "face_recognition_sface_2021dec.onnx",
        [
            "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
            "https://hf-mirror.com/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        ],
    ),
}


class FaceError(RuntimeError):
    pass


def ensure_models() -> dict[str, Path]:
    MODEL_DIR.mkdir(exist_ok=True)
    paths = {}
    for key, (name, urls) in MODELS.items():
        path = MODEL_DIR / name
        if not (path.exists() and path.stat().st_size > 100_000):
            errors = []
            for url in urls:
                try:
                    print(f"  下载人脸模型 {name} ...", flush=True)
                    with urllib.request.urlopen(url, timeout=120) as r:
                        data = r.read()
                    if len(data) < 100_000:  # 拿到的是错误页而不是模型
                        raise FaceError(f"文件太小（{len(data)} 字节）")
                    path.write_bytes(data)
                    break
                except Exception as e:  # 换下一个地址
                    errors.append(f"{url} → {e}")
            else:
                raise FaceError(
                    f"人脸模型 {name} 下载失败，可以手动下载后放到 {MODEL_DIR}/：\n  " + "\n  ".join(errors)
                )
        paths[key] = path
    return paths


def _read(path: Path):
    import cv2
    import numpy as np

    # 用 imdecode 而不是 imread：路径里有中文也没问题
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise FaceError(f"读不了图片：{path}")
    return img


class FaceMatcher:
    def __init__(self, score_threshold: float = 0.7):
        import cv2

        self.cv2 = cv2
        p = ensure_models()
        self.detector = cv2.FaceDetectorYN.create(str(p["detector"]), "", (320, 320), score_threshold, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(p["recognizer"]), "")

    def features(self, path: Path) -> list[dict]:
        """图里每张脸的特征向量，按脸的大小从大到小排。"""
        img = _read(path)
        h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, faces = self.detector.detect(img)
        if faces is None:
            return []
        out = []
        for row in faces:
            aligned = self.recognizer.alignCrop(img, row)
            out.append({
                "feature": self.recognizer.feature(aligned),
                "box": [int(v) for v in row[:4]],
                "area": float(row[2] * row[3]),
                "det_score": float(row[14]),
            })
        return sorted(out, key=lambda f: -f["area"])

    def reference(self, path: Path):
        faces = self.features(path)
        if not faces:
            raise FaceError(f"定妆照里没有检测到人脸：{path}")
        return faces[0]["feature"]

    def similarity(self, a, b) -> float:
        return float(self.recognizer.match(a, b, self.cv2.FaceRecognizerSF_FR_COSINE))

    def best_match(self, ref_feature, faces: list[dict]) -> float | None:
        """镜头里和定妆照最像的那张脸的相似度；没检测到脸返回 None。"""
        if not faces:
            return None
        return max(self.similarity(ref_feature, f["feature"]) for f in faces)
