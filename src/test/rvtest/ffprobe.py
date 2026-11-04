"""ffprobe wrappers: independent verification of rvio output."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT = 60.0


@dataclass
class StreamInfo:
    codec: str
    r_frame_rate: str
    avg_frame_rate: str
    duration: float | None
    timecode: str | None


def _load(json_text: str) -> dict:
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"ffprobe output is not valid JSON: {json_text!r}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"ffprobe output is not a JSON object: {json_text!r}")
    return data


def parse_stream(json_text: str) -> StreamInfo:
    data = _load(json_text)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("no video stream found")
    s = streams[0]
    fmt = data.get("format") or {}
    tc = (s.get("tags") or {}).get("timecode") or (fmt.get("tags") or {}).get("timecode")
    dur = fmt.get("duration")
    try:
        duration = float(dur) if dur is not None else None
    except ValueError:
        duration = None
    return StreamInfo(s.get("codec_name", ""), s.get("r_frame_rate", ""),
                      s.get("avg_frame_rate", ""), duration, tc)


def _run(cmd: list[str], log_path: Path | None = None) -> str:
    """Run ffprobe; return stdout only. Raises ValueError on any failure."""
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"ffprobe timed out after {_TIMEOUT:g}s: {cmd}") from exc
    except OSError as exc:
        raise ValueError(f"ffprobe could not run: {exc}") from exc
    out = res.stdout.decode("utf-8", errors="replace")
    err = res.stderr.decode("utf-8", errors="replace")
    if log_path is not None:
        Path(log_path).write_text(f"$ {cmd}\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n",
                                  encoding="utf-8")
    if res.returncode != 0:
        raise ValueError(f"ffprobe failed (rc={res.returncode}): {err.strip() or out.strip()}")
    return out


def probe(ffprobe: Path, media: Path, log_path: Path | None = None) -> StreamInfo:
    cmd = [str(ffprobe), "-v", "error", "-select_streams", "v:0",
           "-show_entries",
           "stream=codec_name,r_frame_rate,avg_frame_rate:stream_tags=timecode"
           ":format=duration:format_tags=timecode",
           "-of", "json", str(media)]
    return parse_stream(_run(cmd, log_path))


def count_frames(ffprobe: Path, media: Path) -> int:
    cmd = [str(ffprobe), "-v", "error", "-count_frames", "-select_streams", "v:0",
           "-show_entries", "stream=nb_read_frames", "-of", "json", str(media)]
    out = _run(cmd)
    data = _load(out)
    streams = data.get("streams") or []
    try:
        return int(streams[0]["nb_read_frames"])
    except (IndexError, KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"nb_read_frames missing/invalid: {out!r}") from exc
