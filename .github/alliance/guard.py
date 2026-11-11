"""Сторож защищённых путей: проверка в CI для веток исполнителя.

Исполнитель работает в ветках `claude/...`. В них нельзя менять правила, проверки
и шаблоны и нельзя удалять тесты. Сторож не блокирует слияние сам — он делает
проверку красной и перечисляет файлы, чтобы владелец увидел это на приёмке.

CI запускает версию сторожа из базовой ветки, поэтому правка этого файла в ветке
исполнителя на проверку не влияет.

Запуск: guard.py <ветка> <файл с выводом `git diff --name-status`>
"""
from __future__ import annotations

import sys

EXECUTOR_PREFIX = "claude/"
PROTECTED_PREFIXES = (".github/",)
PROTECTED_FILES = ("CLAUDE.md", "AGENTS.md", "Makefile", "CODEOWNERS")
TEST_PREFIXES = ("tests/", "test/")


def parse_changes(text: str) -> list[tuple[str, str]]:
    """Вывод `git diff --name-status` → [(статус, путь)]. У переименований берём оба пути."""
    changes: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][:1]
        if status == "R" and len(parts) >= 3:
            changes.append(("D", parts[1]))
            changes.append(("A", parts[2]))
        else:
            changes.append((status, parts[1]))
    return changes


def is_protected(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIXES) or path in PROTECTED_FILES


def is_test(path: str) -> bool:
    """Тест — файл в папке тестов или файл test_*.py в любом месте."""
    name = path.rsplit("/", 1)[-1]
    return path.startswith(TEST_PREFIXES) or (name.startswith("test_") and name.endswith(".py"))


def violations(branch: str, changes: list[tuple[str, str]]) -> list[str]:
    """Что нарушено. Пустой список — всё в порядке."""
    if not branch.startswith(EXECUTOR_PREFIX):
        return []  # ветки владельца не ограничиваем
    found = []
    for status, path in changes:
        if is_protected(path):
            found.append(f"изменён защищённый путь: {path}")
        elif status == "D" and is_test(path):
            found.append(f"удалён тест: {path}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("Запуск: guard.py <ветка> <файл со списком изменений>")
        return 2
    branch = argv[1]
    try:
        with open(argv[2], encoding="utf-8") as f:
            changes = parse_changes(f.read())
    except OSError as e:
        print(f"Список изменений не прочитан: {e}")
        return 2
    found = violations(branch, changes)
    if not found:
        print("Защищённые пути не тронуты.")
        return 0
    print("Ветка исполнителя меняет то, что меняет только владелец:")
    for line in found:
        print(f"  - {line}")
    print("Если это Ваша осознанная правка — перенесите её в свою ветку.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
