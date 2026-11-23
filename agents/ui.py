"""Small terminal helpers for the human-in-the-loop steps."""
import textwrap


def hr(title: str = "") -> None:
    print("\n" + "=" * 70)
    if title:
        print(title)
        print("=" * 70)


def wrap(text: str | None, indent: str = "  ") -> str:
    return textwrap.fill(text or "-", width=90, initial_indent=indent, subsequent_indent=indent)


def ask_yes_no(question: str) -> bool:
    while True:
        ans = input(f"{question} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def ask_choice(question: str, valid: set[str]) -> str:
    while True:
        ans = input(f"{question}: ").strip().lower()
        if ans in valid:
            return ans


def bullets(title: str, items: list[str]) -> None:
    print(f"\n{title}:")
    for item in items or ["-"]:
        print(textwrap.fill(item, width=90, initial_indent="  • ", subsequent_indent="    "))
