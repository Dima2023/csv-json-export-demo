"""Пишет в тикет стоимость запуска исполнителя командой /расход, чтобы её учёл отчёт.

Читает файл выполнения claude-code-action (путь в EXEC_FILE) и ищет total_cost_usd.
При подписке Claude (OAuth) это условная оценка, а не списание.
Ничего не делает, если файла или цифры нет: учёт не должен ломать работу.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def find_cost(obj) -> float | None:
    found = None
    if isinstance(obj, dict):
        value = obj.get("total_cost_usd")
        if isinstance(value, (int, float)):
            found = float(value)
        for v in obj.values():
            inner = find_cost(v)
            if inner is not None:
                found = inner
    elif isinstance(obj, list):
        for v in obj:
            inner = find_cost(v)
            if inner is not None:
                found = inner
    return found


def comment_body(cost: float) -> str:
    return f"/расход {cost:.2f}\n\n_Авто: стоимость запуска исполнителя (при подписке — условная оценка)._"


def main() -> int:
    path, number = os.environ.get("EXEC_FILE", ""), os.environ.get("NUMBER", "")
    if not path or not os.path.exists(path) or not number:
        print("Нет файла выполнения — расход не записан.")
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            cost = find_cost(json.load(f))
    except (OSError, ValueError) as e:
        print(f"Файл выполнения не прочитан: {e}")
        return 0
    if not cost:
        print("В файле нет стоимости — расход не записан.")
        return 0
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{number}/comments",
        data=json.dumps({"body": comment_body(cost)}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "alliance-bot"},
    )
    with urllib.request.urlopen(req, timeout=30):
        pass
    print(f"Расход записан: ${cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
