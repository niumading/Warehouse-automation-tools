"""OCR 上传立即返回、后台识别和状态查询的回归测试。"""
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.ocr import upload_image
from app.core.database import Base
from app.models import Tenant, User


class FakeBackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))


class OcrAsyncUploadTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.db.add(Tenant(id=1, name="测试客户"))
        self.user = User(id=1, tenant_id=1, username="operator", password_hash="unused", role="operator")
        self.db.add(self.user)
        self.db.commit()
        self.upload_dir = TemporaryDirectory(dir=".")

    def tearDown(self):
        self.upload_dir.cleanup()
        self.db.close()
        self.engine.dispose()

    def test_upload_schedules_recognition_without_calling_model_inline(self):
        tasks = FakeBackgroundTasks()
        file = UploadFile(filename="invoice.jpg", file=BytesIO(b"image"))
        with patch("app.api.ocr._configured", return_value=True), patch(
            "app.api.ocr.recognize_purchase_image"
        ) as recognize, patch("app.api.ocr.settings.upload_dir", self.upload_dir.name):
            result = upload_image(file=file, background_tasks=tasks, db=self.db, user=self.user)

        self.assertEqual(result["action"], "processing")
        self.assertEqual(len(tasks.calls), 1)
        recognize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
