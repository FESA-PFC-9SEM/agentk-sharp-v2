import sys
from datetime import datetime
from pathlib import Path

_LOGS_DIR = Path(__file__).parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

# One log file per day, e.g. logs/2026-05-03.log
_log_file = open(
    _LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log",
    "a",
    encoding="utf-8",
    buffering=1,  # line-buffered
)


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(tag: str, msg: str) -> None:
    line = f"[{_ts()}] [{tag}] {msg}"
    print(line, file=sys.stderr, flush=True)
    print(line, file=_log_file, flush=True)


_metrics_hook = None  # callable(label, model, in_tok, out_tok, load_ms, gen_ms) | None


def set_metrics_hook(fn) -> None:
    global _metrics_hook
    _metrics_hook = fn


def ollama_chat(client, label: str, **kwargs):
    """
    Drop-in wrapper for client.chat().
    Logs input/output tokens, load duration, and generation duration.
    Fires _metrics_hook if registered.
    """
    log(label, f"→ model={kwargs.get('model')}  stream={kwargs.get('stream', False)}")

    resp = client.chat(**kwargs)

    in_tok   = getattr(resp, "prompt_eval_count", None)
    out_tok  = getattr(resp, "eval_count", None)
    load_ms  = _ns_to_ms(getattr(resp, "load_duration", None))
    gen_ms   = _ns_to_ms(getattr(resp, "eval_duration", None))
    total_ms = _ns_to_ms(getattr(resp, "total_duration", None))

    parts = []
    if in_tok   is not None: parts.append(f"in={in_tok}tok")
    if out_tok  is not None: parts.append(f"out={out_tok}tok")
    if load_ms  is not None: parts.append(f"load={load_ms}ms")
    if gen_ms   is not None: parts.append(f"gen={gen_ms}ms")
    if total_ms is not None: parts.append(f"total={total_ms}ms")

    log(label, f"← {' '.join(parts)}")

    if _metrics_hook is not None:
        _metrics_hook(label, kwargs.get("model", ""), in_tok, out_tok, load_ms, gen_ms)

    return resp


def _ns_to_ms(ns) -> int | None:
    if ns is None:
        return None
    return round(ns / 1_000_000)
