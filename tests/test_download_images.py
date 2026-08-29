"""download_images: skip TM placeholder images, download real ones."""

from pathlib import Path

import pytest

from download_images import _download, _is_default_image


class _FakeClient:
    def __init__(self, content=b"img", status=200):
        self.content = content
        self.status = status

    def get_binary(self, url, timeout=60):
        class _R:
            status_code = self.status
            content = self.content

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(self.status_code)

        return _R()


def test_is_default_image():
    assert _is_default_image("https://img.a.transfermarkt.technology/portrait/header/default-123.jpg")
    assert not _is_default_image("https://img.a.transfermarkt.technology/portrait/header/463600-1.jpg")
    assert not _is_default_image(None)


def test_download_skips_default_and_existing(tmp_path):
    default_url = "https://img.a.transfermarkt.technology/portrait/header/default-9.jpg?lm=1"
    assert _download(_FakeClient(), default_url, tmp_path / "9.jpg") is False
    assert not (tmp_path / "9.jpg").exists()

    real_url = "https://img.a.transfermarkt.technology/portrait/header/463600-1.jpg?lm=1"
    dest = tmp_path / "463600.jpg"
    assert _download(_FakeClient(b"photo"), real_url, dest) is True
    assert dest.read_bytes() == b"photo"
    assert _download(_FakeClient(b"photo2"), real_url, dest) is False  # already exists


def test_download_no_url():
    assert _download(_FakeClient(), None, Path("x")) is False