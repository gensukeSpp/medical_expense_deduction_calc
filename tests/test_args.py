"""Tests for app.args (Issue #58 [中-4])."""

from app.args import is_loopback_host


def test_is_loopback_host_accepts_loopback():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")


def test_is_loopback_host_rejects_non_loopback():
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("mypc")
