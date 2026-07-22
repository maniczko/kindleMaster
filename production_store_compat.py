from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any, Callable

from durable_job_queue import SQLiteJobMapping, utc_now_label

CommitCallback = Callable[[], None]
_DELETE = object()


def _to_plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _wrap(value: Any, commit: CommitCallback) -> Any:
    if isinstance(value, _WriteThroughDict | _WriteThroughList):
        return value
    if isinstance(value, Mapping):
        return _WriteThroughDict(value, commit)
    if isinstance(value, list):
        return _WriteThroughList(value, commit)
    if isinstance(value, tuple):
        return _WriteThroughList(list(value), commit)
    return value


def _changed_paths(old: Any, new: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        changes: list[tuple[tuple[str, ...], Any]] = []
        old_keys = {str(key) for key in old}
        new_keys = {str(key) for key in new}
        for key in sorted(old_keys - new_keys):
            changes.append((path + (key,), _DELETE))
        for key in sorted(new_keys - old_keys):
            changes.append((path + (key,), deepcopy(new[key])))
        for key in sorted(old_keys & new_keys):
            changes.extend(_changed_paths(old[key], new[key], path + (key,)))
        return changes
    if old != new:
        return [(path, deepcopy(new))]
    return []


def _apply_change(target: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    if not path:
        if value is _DELETE:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("A conversion job must remain a mapping.")
        return {str(key): deepcopy(item) for key, item in value.items()}

    parent: dict[str, Any] = target
    for key in path[:-1]:
        child = parent.get(key)
        if not isinstance(child, dict):
            child = {}
            parent[key] = child
        parent = child
    leaf = path[-1]
    if value is _DELETE:
        parent.pop(leaf, None)
    else:
        parent[leaf] = deepcopy(value)
    return target


class _WriteThroughDict(dict[str, Any]):
    def __init__(self, source: Mapping[str, Any], commit: CommitCallback) -> None:
        self._commit_callback = commit
        for key, value in source.items():
            dict.__setitem__(self, str(key), _wrap(value, commit))

    def _commit(self) -> None:
        self._commit_callback()

    def __setitem__(self, key: str, value: Any) -> None:
        dict.__setitem__(self, str(key), _wrap(value, self._commit_callback))
        self._commit()

    def __delitem__(self, key: str) -> None:
        dict.__delitem__(self, key)
        self._commit()

    def clear(self) -> None:
        dict.clear(self)
        self._commit()

    def pop(self, key: str, default: Any = ...):
        if default is ...:
            value = dict.pop(self, key)
        else:
            value = dict.pop(self, key, default)
        self._commit()
        return value

    def popitem(self):
        value = dict.popitem(self)
        self._commit()
        return value

    def setdefault(self, key: str, default: Any = None):
        if key in self:
            return dict.__getitem__(self, key)
        wrapped = _wrap(default, self._commit_callback)
        dict.__setitem__(self, str(key), wrapped)
        self._commit()
        return wrapped

    def update(self, *args: Any, **kwargs: Any) -> None:
        incoming = dict(*args, **kwargs)
        for key, value in incoming.items():
            dict.__setitem__(self, str(key), _wrap(value, self._commit_callback))
        self._commit()

    def __ior__(self, other: Mapping[str, Any]):
        self.update(other)
        return self


class _WriteThroughList(list[Any]):
    def __init__(self, source: Iterable[Any], commit: CommitCallback) -> None:
        self._commit_callback = commit
        list.__init__(self, (_wrap(value, commit) for value in source))

    def _commit(self) -> None:
        self._commit_callback()

    def __setitem__(self, index, value: Any) -> None:
        if isinstance(index, slice):
            wrapped = [_wrap(item, self._commit_callback) for item in value]
            list.__setitem__(self, index, wrapped)
        else:
            list.__setitem__(self, index, _wrap(value, self._commit_callback))
        self._commit()

    def __delitem__(self, index) -> None:
        list.__delitem__(self, index)
        self._commit()

    def append(self, value: Any) -> None:
        list.append(self, _wrap(value, self._commit_callback))
        self._commit()

    def extend(self, values: Iterable[Any]) -> None:
        list.extend(self, (_wrap(value, self._commit_callback) for value in values))
        self._commit()

    def insert(self, index: int, value: Any) -> None:
        list.insert(self, index, _wrap(value, self._commit_callback))
        self._commit()

    def pop(self, index: int = -1):
        value = list.pop(self, index)
        self._commit()
        return value

    def remove(self, value: Any) -> None:
        list.remove(self, value)
        self._commit()

    def clear(self) -> None:
        list.clear(self)
        self._commit()

    def reverse(self) -> None:
        list.reverse(self)
        self._commit()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        list.sort(self, *args, **kwargs)
        self._commit()

    def __iadd__(self, values: Iterable[Any]):
        self.extend(values)
        return self

    def __imul__(self, count: int):
        list.__imul__(self, count)
        self._commit()
        return self


def _persistent_proxy(mapping: SQLiteJobMapping, job_id: str, payload: Mapping[str, Any]) -> _WriteThroughDict:
    holder: dict[str, _WriteThroughDict] = {}
    baseline = {"value": deepcopy(_to_plain(payload))}

    def commit() -> None:
        root = holder.get("root")
        if root is None:
            return
        updated = _to_plain(root)
        changes = _changed_paths(baseline["value"], updated)
        if not changes:
            return

        now = utc_now_label()
        with mapping.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM conversion_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                current: dict[str, Any] = {"job_id": job_id}
            else:
                loaded = json.loads(str(row["payload_json"] or "{}"))
                current = dict(loaded) if isinstance(loaded, Mapping) else {"job_id": job_id}
            merged = deepcopy(_to_plain(current))
            for path, value in changes:
                merged = _apply_change(merged, path, value)
            merged["job_id"] = job_id
            merged["updated_at"] = now
            connection.execute(
                """
                INSERT INTO conversion_jobs(job_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    job_id,
                    json.dumps(merged, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    now,
                ),
            )
            connection.commit()
        baseline["value"] = deepcopy(updated)

    root = _WriteThroughDict(payload, commit)
    holder["root"] = root
    return root


def install_sqlite_mapping_write_through() -> None:
    """Make legacy direct mutations durable without overwriting fresh fields."""

    current = SQLiteJobMapping.__getitem__
    if getattr(current, "_kindlemaster_write_through", False):
        return

    original = current

    def write_through_getitem(self: SQLiteJobMapping, key: str):
        payload = original(self, key)
        return _persistent_proxy(self, str(key), payload)

    write_through_getitem._kindlemaster_write_through = True
    SQLiteJobMapping.__getitem__ = write_through_getitem
