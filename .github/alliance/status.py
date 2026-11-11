"""Статусы задания вокруг запуска исполнителя.

guard  — перед работой: не начинать, если задание уже «на приёмке» и его
         запустили меткой повторно. Иначе ставит «в работе».
finish — после работы: появились новые коммиты в ветках → «на приёмке»;
         исполнитель ничего не изменил (задал вопросы или упёрся) → «ждёт владельца»;
         запуск упал или отменён → статус «в работе» остаётся, повторная метка
         запускает работу заново.

Решения принимают чистые функции should_start и label_after — они покрыты тестами.
Только стандартная библиотека.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ST_WORK = "статус: в работе"
ST_REVIEW = "статус: на приёмке"
ST_WAIT_OWNER = "статус: ждёт владельца"
STATUSES = (ST_WORK, ST_REVIEW, ST_WAIT_OWNER)

SKIP_COMMENT = (
    "Задание уже на приёмке: повторный запуск по метке не начат, чтобы не появилась "
    "вторая ветка. Нужна доработка — напишите комментарий с `@claude` и что исправить."
)
WAIT_COMMENT = (
    "Исполнитель завершил работу без изменений в коде. Обычно это вопросы по критериям "
    "или блокер — они в комментарии выше. Задание ждёт Вашего ответа."
)


def should_start(event_name: str, labels: list[str]) -> tuple[bool, str]:
    """Можно ли запускать исполнителя. Возвращает (да/нет, причина)."""
    if event_name == "issues" and ST_REVIEW in labels:
        return False, "уже на приёмке"
    return True, "можно"


def parse_heads(text: str) -> dict[str, str]:
    """Вывод `git ls-remote --heads` → {ветка: sha}."""
    heads: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            heads[parts[1][len("refs/heads/"):]] = parts[0]
    return heads


def changed_branches(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Ветки, которые появились или получили новые коммиты."""
    return sorted(name for name, sha in after.items() if before.get(name) != sha)


def label_after(outcome: str, before: dict[str, str], after: dict[str, str]) -> str | None:
    """Какой статус поставить после работы. None — оставить «в работе»."""
    if outcome != "success":
        return None
    return ST_REVIEW if changed_branches(before, after) else ST_WAIT_OWNER


# ---- Обращения к GitHub: тонкая обвязка, логики в ней нет ----

def _api(method: str, path: str, body: dict | None = None):
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "alliance-bot"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if method == "DELETE" and e.code == 404:  # метки и так нет
            return None
        raise
    return json.loads(raw) if raw else None


def _labels(number: str) -> list[str]:
    issue = _api("GET", f"/issues/{number}")
    return [item["name"] for item in issue.get("labels", [])]


def _set_status(number: str, current: list[str], new: str | None) -> None:
    for name in STATUSES:
        if name in current and name != new:
            _api("DELETE", f"/issues/{number}/labels/{urllib.parse.quote(name)}")
    if new and new not in current:
        _api("POST", f"/issues/{number}/labels", {"labels": [new]})


def _comment(number: str, text: str) -> None:
    _api("POST", f"/issues/{number}/comments", {"body": text})


def _output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    print(f"{name}={value}")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def guard() -> int:
    number = os.environ["NUMBER"]
    labels = _labels(number)  # свежие: запуск мог ждать своей очереди
    ok, reason = should_start(os.environ.get("EVENT_NAME", ""), labels)
    if not ok:
        _comment(number, SKIP_COMMENT)
        _output("skip", "true")
        print(f"Не начинаем: {reason}.")
        return 0
    _set_status(number, labels, ST_WORK)
    _output("skip", "false")
    return 0


def finish() -> int:
    number = os.environ["NUMBER"]
    before = parse_heads(_read(os.environ.get("HEADS_BEFORE", "")))
    after = parse_heads(_read(os.environ.get("HEADS_AFTER", "")))
    new = label_after(os.environ.get("OUTCOME", ""), before, after)
    if new is None:
        print("Запуск не завершился успешно: статус «в работе» оставлен.")
        return 0
    _set_status(number, _labels(number), new)
    if new == ST_WAIT_OWNER:
        _comment(number, WAIT_COMMENT)
    print(f"Статус: {new}. Изменённые ветки: {changed_branches(before, after) or 'нет'}.")
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"guard": guard, "finish": finish}.get(command, lambda: 2)())
