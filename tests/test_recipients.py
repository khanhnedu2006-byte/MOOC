"""Test tách danh sách người nhận email (MAIL_TO / ALERT_MAIL_TO).

Chạy: pytest tests/test_recipients.py -v
"""

import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import send_report
from send_report import MailSendError, parse_recipients


def test_mot_nguoi():
    assert parse_recipients("a@fpt.com") == ["a@fpt.com"]


def test_dau_phay():
    assert parse_recipients("a@fpt.com, b@fpt.com") == ["a@fpt.com", "b@fpt.com"]


# Ca thật: copy danh sách từ Outlook ra dạng chấm phẩy
def test_cham_phay_kieu_outlook():
    assert parse_recipients("a@fpt.com; b@fpt.com;") == ["a@fpt.com", "b@fpt.com"]


def test_tron_dau_va_xuong_dong():
    assert parse_recipients("a@fpt.com,b@fpt.com;\nc@fpt.com") == [
        "a@fpt.com", "b@fpt.com", "c@fpt.com"]


def test_bo_trung_khong_phan_biet_hoa_thuong():
    assert parse_recipients("a@fpt.com, A@FPT.com, b@fpt.com") == [
        "a@fpt.com", "b@fpt.com"]


def test_rong():
    assert parse_recipients("") == []
    assert parse_recipients(None) == []
    assert parse_recipients(" , ; ") == []


class _FakeSMTP:
    sent = []

    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def starttls(self): pass
    def login(self, *a): pass
    def send_message(self, msg): _FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    for k, v in {"smtp_host": "smtp.test", "smtp_user": "me@fpt.com",
                 "smtp_password": "x", "mail_from": ""}.items():
        monkeypatch.setattr(send_report.settings, k, v)
    return _FakeSMTP


def test_gui_toi_nhieu_nguoi(smtp, monkeypatch):
    monkeypatch.setattr(send_report.settings, "mail_to", "a@fpt.com; b@fpt.com, c@fpt.com")
    send_report._send_smtp("t", "<p>h</p>", "t")
    assert smtp.sent[0]["To"] == "a@fpt.com, b@fpt.com, c@fpt.com"


def test_dia_chi_sai_bao_loi_ro(smtp, monkeypatch):
    monkeypatch.setattr(send_report.settings, "mail_to", "a@fpt.com, b.fpt.com")
    with pytest.raises(MailSendError, match="b.fpt.com"):
        send_report._send_smtp("t", "<p>h</p>", "t")
    assert smtp.sent == []
