"""Interactive terminal chat. Run with: python cli.py   (add --trace to see tool calls)"""

import json
import sys

from app.agent import run_agent


def main():
    show_trace = "--trace" in sys.argv
    history: list = []
    print("Flight agent. Ask about a flight or what's flying nearby. Ctrl+C to quit.\n")

    while True:
        try:
            question = input("you> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not question:
            continue
        if question in ("/reset", "/new"):
            history = []
            print("(history cleared)\n")
            continue

        result = run_agent(question, history)
        history = result.messages

        if show_trace:
            for t in result.trace:
                flag = " ERROR" if t["is_error"] else ""
                print(f"  [step {t['step']}] {t['tool']}({json.dumps(t['input'])}){flag}"
                      f"  {t['output_chars']} chars")
            print(f"  ({result.iterations} model calls, "
                  f"{result.input_tokens} in / {result.output_tokens} out tokens)")

        print(f"\nagent> {result.answer}\n")


if __name__ == "__main__":
    main()
