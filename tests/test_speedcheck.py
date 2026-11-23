"""Unit-тесты для speedcheck (только стандартная библиотека)."""

from __future__ import annotations

import io
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import speedcheck


def _make_report(sizes: list[int], durations: list[float]) -> speedcheck.SpeedReport:
    results = [
        speedcheck.RequestResult(index=i + 1, bytes_downloaded=size, duration_sec=dur)
        for i, (size, dur) in enumerate(zip(sizes, durations))
    ]
    return speedcheck.SpeedReport(url="https://example.com/file.bin", results=results)


class TestSpeedReportCalculations(unittest.TestCase):
    def test_total_bytes_and_mb(self) -> None:
        # 1.5 MiB + 0.5 MiB = 2.0 MiB
        report = _make_report(
            sizes=[int(1.5 * speedcheck.BYTES_PER_MB), int(0.5 * speedcheck.BYTES_PER_MB)],
            durations=[1.0, 1.0],
        )
        self.assertEqual(report.total_bytes, 2 * speedcheck.BYTES_PER_MB)
        self.assertAlmostEqual(report.total_mb, 2.0)

    def test_average_duration(self) -> None:
        report = _make_report(sizes=[100, 200, 300], durations=[1.0, 2.0, 3.0])
        self.assertAlmostEqual(report.average_duration_sec, 2.0)
        self.assertAlmostEqual(report.average_duration_ms, 2000.0)

    def test_speed_mbps_decimal(self) -> None:
        # 1_250_000 байт за 1 с → 10_000_000 бит/с → 10 Mbps
        report = _make_report(sizes=[1_250_000], durations=[1.0])
        self.assertAlmostEqual(report.speed_mbps, 10.0)

    def test_speed_mbs_binary(self) -> None:
        # ровно 2 MiB за 2 с → 1 MB/s
        report = _make_report(
            sizes=[2 * speedcheck.BYTES_PER_MB],
            durations=[2.0],
        )
        self.assertAlmostEqual(report.speed_mbs, 1.0)

    def test_zero_duration_yields_zero_speed(self) -> None:
        report = _make_report(sizes=[1024], durations=[0.0])
        self.assertEqual(report.speed_mbps, 0.0)
        self.assertEqual(report.speed_mbs, 0.0)

    def test_empty_report(self) -> None:
        report = speedcheck.SpeedReport(url="https://example.com", results=[])
        self.assertEqual(report.total_bytes, 0)
        self.assertEqual(report.average_duration_sec, 0.0)
        self.assertEqual(report.speed_mbps, 0.0)


class FakeResponse:
    """Минимальный ответ urlopen: status + chunked read()."""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.status = status
        self._buffer = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestDownloadOnce(unittest.TestCase):
    def test_reads_body_in_chunks(self) -> None:
        payload = b"x" * (speedcheck.CHUNK_SIZE + 100)
        fake = FakeResponse(payload)

        with mock.patch("urllib.request.urlopen", return_value=fake) as urlopen:
            with mock.patch("time.perf_counter", side_effect=[10.0, 10.5]):
                nbytes, duration = speedcheck.download_once(
                    "https://example.com/data.bin",
                    timeout=5.0,
                    no_cache=False,
                )

        self.assertEqual(nbytes, len(payload))
        self.assertAlmostEqual(duration, 0.5)
        urlopen.assert_called_once()

    def test_no_cache_adds_headers_and_query(self) -> None:
        fake = FakeResponse(b"ok")
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return fake

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.perf_counter", side_effect=[1.0, 1.1]):
                speedcheck.download_once(
                    "https://example.com/file?x=1",
                    timeout=7.0,
                    no_cache=True,
                )

        self.assertIn("_=", captured["url"])
        headers = {k.lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
        self.assertEqual(headers.get("cache-control"), "no-cache")
        self.assertEqual(captured["timeout"], 7.0)

    def test_http_error_status(self) -> None:
        error = HTTPError(
            url="https://example.com/missing",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once(
                    "https://example.com/missing",
                    timeout=5.0,
                    no_cache=False,
                )
        self.assertIn("404", str(ctx.exception))

    def test_non_200_status_from_response(self) -> None:
        fake = FakeResponse(b"partial", status=206)
        with mock.patch("urllib.request.urlopen", return_value=fake):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once(
                    "https://example.com/file",
                    timeout=5.0,
                    no_cache=False,
                )
        self.assertIn("206", str(ctx.exception))

    def test_network_error(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=URLError("Name or service not known"),
        ):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once(
                    "https://offline.invalid/file",
                    timeout=5.0,
                    no_cache=False,
                )
        self.assertIn("Сетевая ошибка", str(ctx.exception))


class TestRunAndCli(unittest.TestCase):
    def test_run_speedcheck_aggregates(self) -> None:
        with mock.patch(
            "speedcheck.download_once",
            side_effect=[(1000, 0.5), (3000, 1.5)],
        ):
            report = speedcheck.run_speedcheck(
                "https://example.com/file",
                requests_count=2,
                progress=False,
            )

        self.assertEqual(report.total_bytes, 4000)
        self.assertAlmostEqual(report.average_duration_sec, 1.0)
        self.assertAlmostEqual(report.speed_mbps, (4000 * 8) / 1_000_000 / 2.0)

    def test_main_returns_nonzero_on_error(self) -> None:
        with mock.patch(
            "speedcheck.run_speedcheck",
            side_effect=speedcheck.SpeedCheckError("boom"),
        ):
            with mock.patch("sys.stdout", new_callable=io.StringIO):
                with mock.patch("sys.stderr", new_callable=io.StringIO):
                    code = speedcheck.main(
                        ["https://example.com/file", "--requests", "1"]
                    )
        self.assertEqual(code, 1)

    def test_parse_args_defaults(self) -> None:
        args = speedcheck.parse_args(["https://example.com/file"])
        self.assertEqual(args.url, "https://example.com/file")
        self.assertEqual(args.requests, 10)
        self.assertEqual(args.timeout, 15.0)
        self.assertTrue(args.no_cache)


if __name__ == "__main__":
    unittest.main()
