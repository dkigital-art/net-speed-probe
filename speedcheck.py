#!/usr/bin/env python3
"""Замер скорости интернета через последовательные HTTP GET."""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

CHUNK_SIZE = 65536
BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class RequestResult:
    index: int
    bytes_downloaded: int
    duration_sec: float


@dataclass(frozen=True)
class SpeedReport:
    url: str
    results: list[RequestResult]

    @property
    def request_count(self) -> int:
        return len(self.results)

    @property
    def total_bytes(self) -> int:
        return sum(r.bytes_downloaded for r in self.results)

    @property
    def total_mb(self) -> float:
        return self.total_bytes / BYTES_PER_MB

    @property
    def total_duration_sec(self) -> float:
        return sum(r.duration_sec for r in self.results)

    @property
    def average_duration_sec(self) -> float:
        if not self.results:
            return 0.0
        return self.total_duration_sec / len(self.results)

    @property
    def average_duration_ms(self) -> float:
        return self.average_duration_sec * 1000

    @property
    def speed_mbps(self) -> float:
        """Мбит/с (decimal): bits / 1_000_000 / sec."""
        if self.total_duration_sec <= 0:
            return 0.0
        bits = self.total_bytes * 8
        return bits / 1_000_000 / self.total_duration_sec

    @property
    def speed_mbs(self) -> float:
        """МБ/с (binary): bytes / (1024 * 1024) / sec."""
        if self.total_duration_sec <= 0:
            return 0.0
        return self.total_bytes / BYTES_PER_MB / self.total_duration_sec


class SpeedCheckError(Exception):
    """Ошибка замера: сеть, таймаут или неожиданный HTTP-статус."""


def apply_cache_busting(url: str) -> str:
    """Добавляет уникальный query-параметр, чтобы обойти CDN/прокси-кэш."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_", str(time.time_ns())))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def download_once(url: str, timeout: float, no_cache: bool = True) -> tuple[int, float]:
    """Один GET с полным вычитыванием тела. Возвращает (байты, секунды)."""
    target = apply_cache_busting(url) if no_cache else url
    headers: dict[str, str] = {}
    if no_cache:
        headers["Cache-Control"] = "no-cache"
        headers["Pragma"] = "no-cache"

    request = urllib.request.Request(target, headers=headers, method="GET")
    started = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                raise SpeedCheckError(
                    f"HTTP {resp.status}: ожидался статус 200 для {url}"
                )
            total = 0
            while True:
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
    except SpeedCheckError:
        raise
    except urllib.error.HTTPError as exc:
        raise SpeedCheckError(f"HTTP {exc.code}: {exc.reason} для {url}") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason if exc.reason is not None else exc
        raise SpeedCheckError(f"Сетевая ошибка: {reason}") from exc
    except TimeoutError as exc:
        raise SpeedCheckError(f"Таймаут ({timeout} с) при запросе к {url}") from exc

    duration = time.perf_counter() - started
    return total, duration


def run_speedcheck(
    url: str,
    requests_count: int = 10,
    timeout: float = 15.0,
    no_cache: bool = True,
    *,
    progress: bool = True,
) -> SpeedReport:
    """Выполняет N последовательных загрузок и собирает отчёт."""
    results: list[RequestResult] = []

    for index in range(1, requests_count + 1):
        nbytes, duration = download_once(url, timeout=timeout, no_cache=no_cache)
        result = RequestResult(
            index=index,
            bytes_downloaded=nbytes,
            duration_sec=duration,
        )
        results.append(result)

        if progress:
            mb = nbytes / BYTES_PER_MB
            if duration > 0:
                print(
                    f"  [{index}/{requests_count}] "
                    f"{mb:.2f} MB за {duration:.3f} с "
                    f"({mb / duration:.2f} MB/s)"
                )
            else:
                print(f"  [{index}/{requests_count}] {nbytes} B за {duration:.3f} с")

    return SpeedReport(url=url, results=results)


def print_summary(report: SpeedReport) -> None:
    print()
    print("=== Итог ===")
    print(f"URL:              {report.url}")
    print(f"Запросов:         {report.request_count}")
    print(f"Скачано:          {report.total_bytes:,} B ({report.total_mb:.2f} MB)")
    print(
        f"Среднее время:    {report.average_duration_sec:.3f} с "
        f"({report.average_duration_ms:.1f} мс)"
    )
    print(f"Общее время:      {report.total_duration_sec:.3f} с")
    print(f"Скорость:         {report.speed_mbps:.2f} Мбит/с (Mbps)")
    print(f"                  {report.speed_mbs:.2f} МБ/с (MB/s)")


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
        help="число последовательных запросов (по умолчанию: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        metavar="SEC",
        help="таймаут одного запроса в секундах (по умолчанию: 15)",
    )
    # Пара флагов: по умолчанию cache-busting включён (no_cache=True).
    # BooleanOptionalAction нельзя использовать с именем --no-*: в 3.12+ это ошибка.
    parser.add_argument(
        "--no-cache",
        dest="no_cache",
        action="store_true",
        default=True,
        help="cache-busting и Cache-Control: no-cache (по умолчанию: вкл.)",
    )
    parser.add_argument(
        "--cache",
        dest="no_cache",
        action="store_false",
        help="отключить cache-busting",
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
    print(
        f"Запросов: {args.requests}, "
        f"таймаут: {args.timeout} с, "
        f"no-cache: {args.no_cache}"
    )
    print()

    try:
        report = run_speedcheck(
            url=args.url,
            requests_count=args.requests,
            timeout=args.timeout,
            no_cache=args.no_cache,
        )
    except SpeedCheckError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
