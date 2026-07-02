from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ENGINE_ANALYSIS_SCHEMA = "kindlemaster.chess_engine.analysis.v1"
ENGINE_CACHE_SCHEMA = "kindlemaster.chess_engine.cache_record.v1"
DEFAULT_LIMIT_MS = 500
DEFAULT_DEPTH = 14
DEFAULT_MULTIPV = 3
MAX_MULTIPV = 3
DEFAULT_CACHE_PATH = Path("reports/chess_engine/cache/engine_analysis_cache.jsonl")


def resolve_engine_path(*, env: Mapping[str, str] | None = None, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Resolve a Stockfish-compatible UCI executable without failing conversion."""
    env_map = dict(os.environ if env is None else env)
    root = Path(repo_root or Path(__file__).resolve().parent)
    checked: list[dict[str, str]] = []

    env_path = str(env_map.get("KINDLEMASTER_STOCKFISH_PATH") or "").strip()
    if env_path:
        checked.append({"source": "env:KINDLEMASTER_STOCKFISH_PATH", "path": env_path})
        if Path(env_path).is_file():
            return _engine_path_result("ok", env_path, "env:KINDLEMASTER_STOCKFISH_PATH", checked)

    profile_path = _stockfish_path_from_user_profile(env=env_map)
    if profile_path:
        checked.append({"source": "user_profile", "path": profile_path})
        if Path(profile_path).is_file():
            return _engine_path_result("ok", profile_path, "user_profile", checked)

    for candidate in _local_stockfish_candidates(root):
        checked.append({"source": "local_tools", "path": str(candidate)})
        if candidate.is_file():
            return _engine_path_result("ok", str(candidate), "local_tools", checked)

    path_candidate = shutil.which("stockfish")
    checked.append({"source": "PATH", "path": path_candidate or "stockfish"})
    if path_candidate:
        return _engine_path_result("ok", path_candidate, "PATH", checked)

    return _engine_path_result("engine_unavailable", "", "none", checked)


def is_engine_available(
    *,
    engine_path: str | None = None,
    engine_command: Sequence[str] | None = None,
    timeout_ms: int = 500,
) -> dict[str, Any]:
    if engine_command:
        command = list(engine_command)
        path_payload = {"status": "ok", "path": command[0], "source": "explicit_command", "checked": []}
    elif engine_path:
        command = [engine_path]
        path_payload = {"status": "ok", "path": engine_path, "source": "explicit_path", "checked": []}
    else:
        path_payload = resolve_engine_path()
        if path_payload.get("status") != "ok":
            return {**path_payload, "available": False}
        command = [str(path_payload["path"])]

    session = _UciEngineSession(command)
    try:
        identity = session.initialize(timeout_ms=timeout_ms)
    except _EngineTimeout:
        return {**path_payload, "available": False, "status": "timeout"}
    except Exception as exc:
        return {**path_payload, "available": False, "status": "failed", "error": str(exc)}
    finally:
        session.close()

    return {
        **path_payload,
        "available": True,
        "status": "ok",
        "engine": "stockfish",
        "engine_version": identity.get("name") or "",
        "identity": identity,
    }


def analyze_fen(
    fen: str,
    *,
    limit_ms: int = DEFAULT_LIMIT_MS,
    depth: int | None = None,
    multipv: int = DEFAULT_MULTIPV,
    engine_path: str | None = None,
    engine_command: Sequence[str] | None = None,
    cache_enabled: bool = True,
    cache_path: str | Path | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    fen_value = str(fen or "").strip()
    board_payload = _validate_fen_for_engine(fen_value)
    if board_payload.get("status") != "ok":
        return _base_analysis(
            status=str(board_payload["status"]),
            fen=fen_value,
            limit_ms=limit_ms,
            depth=depth,
            multipv=multipv,
            started=started,
            warnings=list(board_payload.get("warnings") or []),
        )

    command_payload = _engine_command_payload(engine_path=engine_path, engine_command=engine_command)
    if command_payload.get("status") != "ok":
        return _base_analysis(
            status="engine_unavailable",
            fen=fen_value,
            limit_ms=limit_ms,
            depth=depth,
            multipv=multipv,
            started=started,
            warnings=["stockfish_not_found"],
            cache={"hit": False, "key": ""},
        )

    session = _UciEngineSession(list(command_payload["command"]))
    effective_depth = int(depth if depth is not None else DEFAULT_DEPTH)
    effective_limit_ms = max(1, int(limit_ms or DEFAULT_LIMIT_MS))
    effective_multipv = max(1, min(MAX_MULTIPV, int(multipv or DEFAULT_MULTIPV)))
    effective_timeout_ms = int(timeout_ms or max(1500, effective_limit_ms + 1200))

    try:
        identity = session.initialize(timeout_ms=effective_timeout_ms)
        cache_key = _engine_cache_key(
            engine_name=identity.get("name") or "stockfish",
            engine_version=identity.get("name") or "",
            fen=fen_value,
            depth=effective_depth if depth is not None else None,
            limit_ms=effective_limit_ms,
            multipv=effective_multipv,
        )
        resolved_cache_path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
        if cache_enabled:
            cached = _load_cache_record(resolved_cache_path, cache_key)
            if cached:
                payload = dict(cached.get("analysis") or {})
                payload["cache"] = {"hit": True, "key": cache_key}
                payload["elapsed_ms"] = _elapsed_ms(started)
                return payload

        raw = session.analyze(
            fen_value,
            limit_ms=effective_limit_ms,
            depth=effective_depth if depth is not None else None,
            multipv=effective_multipv,
            timeout_ms=effective_timeout_ms,
        )
    except _EngineTimeout:
        session.close(kill=True)
        return _base_analysis(
            status="timeout",
            fen=fen_value,
            limit_ms=effective_limit_ms,
            depth=effective_depth if depth is not None else None,
            multipv=effective_multipv,
            started=started,
            warnings=["engine_timeout"],
        )
    except Exception as exc:
        session.close(kill=True)
        return _base_analysis(
            status="failed",
            fen=fen_value,
            limit_ms=effective_limit_ms,
            depth=effective_depth if depth is not None else None,
            multipv=effective_multipv,
            started=started,
            warnings=[str(exc)],
        )
    finally:
        session.close()

    payload = _analysis_from_uci(
        fen_value,
        raw,
        identity=identity,
        limit_ms=effective_limit_ms,
        depth=effective_depth if depth is not None else None,
        multipv=effective_multipv,
        started=started,
        cache_key=cache_key,
    )
    if cache_enabled and payload.get("status") == "ok":
        _append_cache_record(resolved_cache_path, cache_key, payload)
    return payload


def normalize_engine_score(score_type: str, value: int | str | None) -> dict[str, Any]:
    if score_type == "mate":
        try:
            mate = int(value) if value is not None else None
        except (TypeError, ValueError):
            mate = None
        return {
            "score_cp": None,
            "mate": mate,
            "pov_score": f"#{mate}" if mate is not None else "",
        }
    try:
        score_cp = int(value) if value is not None else None
    except (TypeError, ValueError):
        score_cp = None
    return {
        "score_cp": score_cp,
        "mate": None,
        "pov_score": _format_cp_score(score_cp),
    }


class _EngineTimeout(RuntimeError):
    pass


class _UciEngineSession:
    def __init__(self, command: Sequence[str]) -> None:
        self.command = list(command)
        self.process: subprocess.Popen[str] | None = None
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader: threading.Thread | None = None

    def initialize(self, *, timeout_ms: int) -> dict[str, str]:
        self._start()
        self._send("uci")
        identity: dict[str, str] = {}
        for line in self._read_until("uciok", timeout_ms=timeout_ms):
            if line.startswith("id name "):
                identity["name"] = line[len("id name ") :].strip()
            elif line.startswith("id author "):
                identity["author"] = line[len("id author ") :].strip()
        self._send("isready")
        self._read_until("readyok", timeout_ms=timeout_ms)
        return identity

    def analyze(
        self,
        fen: str,
        *,
        limit_ms: int,
        depth: int | None,
        multipv: int,
        timeout_ms: int,
    ) -> dict[str, Any]:
        self._send(f"setoption name MultiPV value {multipv}")
        self._send("isready")
        self._read_until("readyok", timeout_ms=timeout_ms)
        self._send(f"position fen {fen}")
        if depth is not None:
            self._send(f"go depth {int(depth)}")
        else:
            self._send(f"go movetime {int(limit_ms)}")
        lines = self._read_until("bestmove", timeout_ms=timeout_ms)
        bestmove_line = next((line for line in reversed(lines) if line.startswith("bestmove")), "")
        return {
            "lines": lines,
            "bestmove": _parse_bestmove(bestmove_line),
            "infos": [_parse_info_line(line) for line in lines if line.startswith("info ")],
        }

    def close(self, *, kill: bool = False) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                if not kill:
                    try:
                        self._send("quit")
                        process.wait(timeout=0.4)
                    except Exception:
                        kill = True
                if kill and process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=0.8)
                    except Exception:
                        pass
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            self.process = None

    def _start(self) -> None:
        if self.process is not None:
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self.reader = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader.start()

    def _reader_loop(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.strip())

    def _send(self, command: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("engine process is not running")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def _read_until(self, terminal_prefix: str, *, timeout_ms: int) -> list[str]:
        deadline = time.monotonic() + max(0.001, timeout_ms / 1000.0)
        captured: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _EngineTimeout(f"Timed out waiting for {terminal_prefix}")
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise _EngineTimeout(f"Timed out waiting for {terminal_prefix}") from exc
            captured.append(line)
            if line.startswith(terminal_prefix):
                return captured


def _engine_command_payload(
    *,
    engine_path: str | None,
    engine_command: Sequence[str] | None,
) -> dict[str, Any]:
    if engine_command:
        return {"status": "ok", "command": list(engine_command), "source": "explicit_command"}
    if engine_path:
        if Path(engine_path).is_file() or shutil.which(engine_path):
            return {"status": "ok", "command": [engine_path], "source": "explicit_path"}
        return {"status": "engine_unavailable", "command": [], "source": "explicit_path"}
    resolved = resolve_engine_path()
    if resolved.get("status") != "ok":
        return {"status": "engine_unavailable", "command": [], "source": resolved.get("source"), "checked": resolved.get("checked", [])}
    return {"status": "ok", "command": [str(resolved["path"])], "source": resolved.get("source")}


def _validate_fen_for_engine(fen: str) -> dict[str, Any]:
    try:
        import chess  # type: ignore
    except Exception as exc:
        return {"status": "failed", "warnings": [f"python_chess_unavailable:{exc}"]}
    if len(fen.split()) < 2 or fen.split()[1] not in {"w", "b"}:
        return {"status": "invalid_fen", "warnings": ["fen_missing_side_to_move"]}
    try:
        board = chess.Board(fen)
    except Exception as exc:
        return {"status": "invalid_fen", "warnings": [str(exc)]}
    if not board.is_valid():
        return {"status": "invalid_fen", "warnings": ["invalid_board_state"]}
    return {"status": "ok", "side_to_move": "w" if board.turn else "b"}


def _analysis_from_uci(
    fen: str,
    raw: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    limit_ms: int,
    depth: int | None,
    multipv: int,
    started: float,
    cache_key: str,
) -> dict[str, Any]:
    best_move = str(raw.get("bestmove") or "")
    pv_rows = _pv_rows_from_infos(fen, raw.get("infos") or [], multipv=multipv)
    best_move_san = _moves_to_san(fen, [best_move])[0] if best_move and best_move != "0000" else ""
    best_row = pv_rows[0] if pv_rows else {}
    score_payload = normalize_engine_score(str(best_row.get("score_type") or "cp"), best_row.get("score_value"))
    return {
        "schema": ENGINE_ANALYSIS_SCHEMA,
        "status": "ok" if best_move else "failed",
        "engine": "stockfish",
        "engine_version": identity.get("name") or "",
        "fen": fen,
        "side_to_move": fen.split()[1] if len(fen.split()) > 1 else "",
        "limit_ms": limit_ms,
        "depth": depth,
        "multipv": multipv,
        "best_move_uci": best_move,
        "best_move_san": best_move_san,
        "score_cp": score_payload["score_cp"],
        "mate": score_payload["mate"],
        "pov_score": score_payload["pov_score"],
        "pv": [
            {
                "rank": int(row.get("rank") or 1),
                "score_cp": normalize_engine_score(str(row.get("score_type") or "cp"), row.get("score_value"))["score_cp"],
                "mate": normalize_engine_score(str(row.get("score_type") or "cp"), row.get("score_value"))["mate"],
                "moves_uci": row.get("moves_uci") or [],
                "moves_san": row.get("moves_san") or [],
            }
            for row in pv_rows
        ],
        "cache": {"hit": False, "key": cache_key},
        "elapsed_ms": _elapsed_ms(started),
        "warnings": [],
    }


def _pv_rows_from_infos(fen: str, infos: Iterable[Mapping[str, Any]], *, multipv: int) -> list[dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for info in infos:
        moves = list(info.get("pv") or [])
        if not moves:
            continue
        rank = int(info.get("multipv") or 1)
        latest[rank] = {
            "rank": rank,
            "score_type": info.get("score_type") or "cp",
            "score_value": info.get("score_value"),
            "moves_uci": moves,
            "moves_san": _moves_to_san(fen, moves),
        }
    return [latest[key] for key in sorted(latest)[:multipv]]


def _moves_to_san(fen: str, moves_uci: Sequence[str]) -> list[str]:
    try:
        import chess  # type: ignore

        board = chess.Board(fen)
        san_moves: list[str] = []
        for value in moves_uci:
            move = chess.Move.from_uci(str(value))
            san_moves.append(board.san(move))
            board.push(move)
        return san_moves
    except Exception:
        return []


def _parse_info_line(line: str) -> dict[str, Any]:
    tokens = line.split()
    payload: dict[str, Any] = {}
    if "multipv" in tokens:
        payload["multipv"] = _int_after(tokens, "multipv") or 1
    if "depth" in tokens:
        payload["depth"] = _int_after(tokens, "depth")
    if "score" in tokens:
        try:
            index = tokens.index("score")
            payload["score_type"] = tokens[index + 1]
            payload["score_value"] = int(tokens[index + 2])
        except (ValueError, IndexError):
            pass
    if "pv" in tokens:
        index = tokens.index("pv")
        payload["pv"] = tokens[index + 1 :]
    return payload


def _parse_bestmove(line: str) -> str:
    tokens = line.split()
    if len(tokens) >= 2 and tokens[0] == "bestmove":
        return tokens[1]
    return ""


def _int_after(tokens: Sequence[str], key: str) -> int | None:
    try:
        return int(tokens[tokens.index(key) + 1])
    except (ValueError, IndexError):
        return None


def _engine_cache_key(
    *,
    engine_name: str,
    engine_version: str,
    fen: str,
    depth: int | None,
    limit_ms: int,
    multipv: int,
) -> str:
    payload = {
        "engine_name": engine_name,
        "engine_version": engine_version,
        "fen": fen,
        "depth": depth,
        "limit_ms": limit_ms,
        "multipv": multipv,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _load_cache_record(path: Path, key: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    found: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("key") == key:
                    found = row
    except (OSError, json.JSONDecodeError):
        return None
    return found


def _append_cache_record(path: Path, key: str, analysis: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": ENGINE_CACHE_SCHEMA,
        "key": key,
        "created_at": int(time.time()),
        "analysis": {**dict(analysis), "cache": {"hit": False, "key": key}},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _base_analysis(
    *,
    status: str,
    fen: str,
    limit_ms: int,
    depth: int | None,
    multipv: int,
    started: float,
    warnings: Sequence[str] | None = None,
    cache: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    side = fen.split()[1] if len(fen.split()) > 1 and fen.split()[1] in {"w", "b"} else ""
    return {
        "schema": ENGINE_ANALYSIS_SCHEMA,
        "status": status,
        "engine": "stockfish",
        "engine_version": "",
        "fen": fen,
        "side_to_move": side,
        "limit_ms": int(limit_ms or DEFAULT_LIMIT_MS),
        "depth": depth,
        "multipv": int(multipv or DEFAULT_MULTIPV),
        "best_move_uci": "",
        "best_move_san": "",
        "score_cp": None,
        "mate": None,
        "pov_score": "",
        "pv": [],
        "cache": dict(cache or {"hit": False, "key": ""}),
        "elapsed_ms": _elapsed_ms(started),
        "warnings": list(warnings or []),
    }


def _stockfish_path_from_user_profile(*, env: Mapping[str, str]) -> str:
    candidates: list[Path] = []
    appdata = str(env.get("APPDATA") or "").strip()
    if appdata:
        candidates.append(Path(appdata) / "KindleMaster" / "engine.json")
    candidates.append(Path.home() / ".kindlemaster" / "engine.json")
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        value = payload.get("stockfish_path") or (payload.get("engine") or {}).get("path")
        if value:
            return str(value)
    return ""


def _local_stockfish_candidates(repo_root: Path) -> list[Path]:
    names = ["stockfish.exe", "stockfish"]
    return [repo_root / "tools" / "stockfish" / name for name in names]


def _engine_path_result(status: str, path: str, source: str, checked: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    return {
        "status": status,
        "engine": "stockfish",
        "path": path,
        "source": source,
        "checked": [dict(item) for item in checked],
    }


def _format_cp_score(score_cp: int | None) -> str:
    if score_cp is None:
        return ""
    return f"{score_cp / 100.0:+.2f}"


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))
