"""Extract conversation turns from Claude Code JSONL transcript files."""

import argparse
import json
import sys
from typing import TextIO


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _try_extract(obj: dict) -> tuple[str, str] | None:
    # Try: obj["message"]["role"] + obj["message"]["content"]
    msg = obj.get("message")
    if isinstance(msg, dict):
        role = msg.get("role")
        content = msg.get("content")
        if role and content is not None:
            return role, _extract_text(content)

    # Try: obj["role"] + obj["content"]
    role = obj.get("role")
    content = obj.get("content")
    if role and content is not None:
        return role, _extract_text(content)

    # Try: obj["type"] as role + obj["message"] as content
    type_val = obj.get("type")
    msg_val = obj.get("message")
    if type_val and msg_val is not None:
        if isinstance(msg_val, str):
            return type_val, msg_val
        return type_val, _extract_text(msg_val)

    return None


def _process(input_stream: TextIO, output_stream: TextIO, fmt: str, role_filter: str) -> None:
    turns = []

    for lineno, raw in enumerate(input_stream, 1):
        line = raw.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            print(f"Warning: skipping malformed JSON on line {lineno}", file=sys.stderr)
            continue

        if not isinstance(obj, dict):
            print(f"Warning: skipping non-object on line {lineno}", file=sys.stderr)
            continue

        result = _try_extract(obj)
        if result is None:
            continue

        role, content = result
        if role_filter != "all" and role != role_filter:
            continue

        turns.append({"role": role, "content": content})

    if fmt == "json":
        json.dump(turns, output_stream, indent=2)
        output_stream.write("\n")
    else:
        for turn in turns:
            output_stream.write(f"[{turn['role']}] {turn['content']}\n---\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract turns from Claude Code JSONL transcripts")
    parser.add_argument("--input", "-i", default="-", metavar="FILE",
                        help="Input JSONL file (default: stdin)")
    parser.add_argument("--output", "-o", default=None, metavar="FILE",
                        help="Output file (default: stdout)")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text",
                        dest="fmt", help="Output format (default: text)")
    parser.add_argument("--role", "-r", choices=["human", "assistant", "all"], default="all",
                        help="Filter by role (default: all)")
    args = parser.parse_args()

    try:
        if args.input == "-":
            input_stream = sys.stdin
        else:
            input_stream = open(args.input, encoding="utf-8")
    except OSError as e:
        print(f"Error opening input: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.output is None:
            output_stream = sys.stdout
        else:
            output_stream = open(args.output, "w", encoding="utf-8")
    except OSError as e:
        print(f"Error opening output: {e}", file=sys.stderr)
        if args.input != "-":
            input_stream.close()
        sys.exit(1)

    try:
        _process(input_stream, output_stream, args.fmt, args.role)
    except OSError as e:
        print(f"IO error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if args.input != "-":
            input_stream.close()
        if args.output is not None:
            output_stream.close()


if __name__ == "__main__":
    main()
