#!/usr/bin/env python3
"""Замер скорости интернета: N последовательных HTTP GET."""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CHUNK_SIZE = 65536
BYTES_PER_MB = 1024 * 1024


class SpeedCheckError(Exception):
    """Сеть, таймаут или HTTP-статус ≠ 200."""


def with_cache_bust(url: str) -> str:
    """Добавляет уникальный query-параметр, чтобы CDN не отдал кэш."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_", str(time.time_ns())))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def download_once(url: str, timeout: float) -> tuple[int, float]:
    """Один GET: полностью вычитывает тело чанками. → (байты, секунды)."""
    request = urllib.request.Request(
        with_cache_bust(url),
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
        method="GET",
    )

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if resp.status != 200:
                raise SpeedCheckError(f"HTTP {resp.status}: нужен 200 ({url})")
            total = 0
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
    except SpeedCheckError:
        raise
    except urllib.error.HTTPError as exc:
        raise SpeedCheckError(f"HTTP {exc.code}: {exc.reason} ({url})") from exc
    except urllib.error.URLError as exc:
        raise SpeedCheckError(f"Сетевая ошибка: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SpeedCheckError(f"Таймаут {timeout} с ({url})") from exc

    return total, time.perf_counter() - started


def run(url: str, requests_count: int, timeout: float) -> None:
    """Делает запросы, печатает прогресс и итоговую сводку."""
    durations: list[float] = []
    total_bytes = 0

    for i in range(1, requests_count + 1):
        nbytes, duration = download_once(url, timeout)
        total_bytes += nbytes
        durations.append(duration)

        mb = nbytes / BYTES_PER_MB
        rate = (mb / duration) if duration > 0 else 0.0
        print(f"  [{i}/{requests_count}] {mb:.2f} MB за {duration:.3f} с ({rate:.2f} MB/s)")

    total_sec = sum(durations)
    avg_sec = total_sec / len(durations)
    total_mb = total_bytes / BYTES_PER_MB

    # Mbps (decimal, как у провайдеров) и MB/s (binary, как размер файлов)
    mbps = (total_bytes * 8) / 1_000_000 / total_sec if total_sec > 0 else 0.0
    mbs = total_mb / total_sec if total_sec > 0 else 0.0

    print()
    print("=== Итог ===")
    print(f"URL:              {url}")
    print(f"Запросов:         {requests_count}")
    print(f"Скачано:          {total_bytes:,} B ({total_mb:.2f} MB)")
    print(f"Среднее время:    {avg_sec:.3f} с ({avg_sec * 1000:.1f} мс)")
    print(f"Общее время:      {total_sec:.3f} с")
    print(f"Скорость:         {mbps:.2f} Мбит/с (Mbps)")
    print(f"                  {mbs:.2f} МБ/с (MB/s)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Замер скорости интернета через последовательные HTTP GET.",
    )
    parser.add_argument("url", help="URL файла для скачивания")
    parser.add_argument(
        "--requests",
        type=int,
        default=10,
        metavar="N",
        help="число запросов (по умолчанию: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        metavar="SEC",
        help="таймаут одного запроса, сек (по умолчанию: 15)",
    )
    args = parser.parse_args(argv)
    if args.requests < 1:
        parser.error("--requests должен быть >= 1")
    if args.timeout <= 0:
        parser.error("--timeout должен быть > 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Замер скорости: {args.url}")
    print(f"Запросов: {args.requests}, таймаут: {args.timeout} с")
    print()
    try:
        run(args.url, args.requests, args.timeout)
    except SpeedCheckError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
