"""Timing 中枢：与其他 AppService 通过 hub 协作（可选单独挂载）。"""
from bollydog.models.service import AppService


class TimingApp(AppService):
    domain = "timing"
    alias = "TimingApp"
    commands = []
