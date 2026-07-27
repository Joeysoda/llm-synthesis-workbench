from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path


def load_modules(repo: Path):
    sys.path.insert(0, str(repo))
    from base.data import Data
    from games.tasks.arrow_maze.scripts.arrow_maze import ArrowMaze
    from games.tasks.arrow_maze.scripts.arrow_maze_verifier import ArrowMazeVerifier

    return Data, ArrowMaze, ArrowMazeVerifier


def mutate_solution(solution: list[list[str]]) -> list[list[str]]:
    replacements = {
        "↑": "↓",
        "↓": "↑",
        "←": "→",
        "→": "←",
        "↖": "↘",
        "↘": "↖",
        "↗": "↙",
        "↙": "↗",
    }
    copy = [row[:] for row in solution]
    for row in copy:
        for index, value in enumerate(row):
            if value in replacements:
                row[index] = replacements[value]
                return copy
    return copy


def generate(payload: dict, repo: Path, output: Path) -> None:
    _, ArrowMaze, ArrowMazeVerifier = load_modules(repo)
    game = ArrowMaze()
    rows = game.generate(
        num_of_questions=int(payload["num_of_data"]),
        max_attempts=int(payload.get("max_attempts", 10000)),
        width=int(payload["width"]),
        height=int(payload["height"]),
        arrow_fill_rate_min=float(payload["arrow_fill_rate_min"]),
        arrow_fill_rate_max=float(payload["arrow_fill_rate_max"]),
    )
    verifier = ArrowMazeVerifier()
    verified = 0
    serialized = []
    for item in rows:
        with contextlib.redirect_stdout(io.StringIO()):
            passed = bool(verifier.verify(item, item.answer))
        verified += int(passed)
        serialized.append({**item.to_json(), "verified": passed})

    raw_path = output.parent / "arrow_maze_raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as stream:
        for row in serialized:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    negative_rejected = False
    if rows:
        bad = mutate_solution(rows[0].metadata["solution"])
        with contextlib.redirect_stdout(io.StringIO()):
            negative_rejected = not bool(
                verifier.verify(rows[0], json.dumps(bad, ensure_ascii=False))
            )

    result = {
        "generated": len(rows),
        "verified": verified,
        "verification_rate": verified / len(rows) if rows else 0,
        "negative_rejected": negative_rejected,
        "raw_path": str(raw_path),
        "rows": serialized,
    }
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def verify(payload: dict, repo: Path, output: Path) -> None:
    Data, _, ArrowMazeVerifier = load_modules(repo)
    row = payload["row"]
    answer = payload["answer"]
    if not isinstance(answer, list) or not all(isinstance(line, list) for line in answer):
        raise ValueError("答案必须是二维 JSON 数组")
    data = Data.from_json_dict(row)
    with contextlib.redirect_stdout(io.StringIO()):
        passed = bool(
            ArrowMazeVerifier().verify(data, json.dumps(answer, ensure_ascii=False))
        )
    output.write_text(
        json.dumps({"passed": passed}, ensure_ascii=False), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    if payload["action"] == "generate":
        generate(payload, args.repo, args.output)
    elif payload["action"] == "verify":
        verify(payload, args.repo, args.output)
    else:
        raise ValueError(f"Unknown action: {payload['action']}")


if __name__ == "__main__":
    main()
