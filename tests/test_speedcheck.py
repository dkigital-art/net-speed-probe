"""Unit-тесты для speedcheck (стандартная библиотека)."""

from __future__ import annotations

import io
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

import speedcheck


def calc_speeds(total_bytes: int, total_sec: float) -> tuple[float, float]:
    """Те же формулы, что в run(): Mbps (decimal) и MB/s (binary)."""
    if total_sec <= 0:
        return 0.0, 0.0
    mbps = (total_bytes * 8) / 1_000_000 / total_sec
    mbs = (total_bytes / speedcheck.BYTES_PER_MB) / total_sec
    return mbps, mbs


class TestSpeedFormulas(unittest.TestCase):
    def test_average_duration(self) -> None:
        durations = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(sum(durations) / len(durations), 2.0)

    def test_total_bytes_and_mb(self) -> None:
        total = int(1.5 * speedcheck.BYTES_PER_MB) + int(0.5 * speedcheck.BYTES_PER_MB)
        self.assertEqual(total, 2 * speedcheck.BYTES_PER_MB)
        self.assertAlmostEqual(total / speedcheck.BYTES_PER_MB, 2.0)

    def test_speed_mbps_decimal(self) -> None:
        # 1_250_000 байт за 1 с → 10_000_000 бит/с → 10 Mbps
        mbps, _ = calc_speeds(1_250_000, 1.0)
        self.assertAlmostEqual(mbps, 10.0)

    def test_speed_mbs_binary(self) -> None:
        # 2 MiB за 2 с → 1 MB/s
        _, mbs = calc_speeds(2 * speedcheck.BYTES_PER_MB, 2.0)
        self.assertAlmostEqual(mbs, 1.0)

    def test_zero_duration(self) -> None:
        mbps, mbs = calc_speeds(1024, 0.0)
        self.assertEqual(mbps, 0.0)
        self.assertEqual(mbs, 0.0)


class FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.status = status
        self._buf = io.BytesIO(payload)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestDownloadOnce(unittest.TestCase):
    def test_reads_body_in_chunks(self) -> None:
        payload = b"x" * (speedcheck.CHUNK_SIZE + 100)
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            with mock.patch("time.perf_counter", side_effect=[10.0, 10.5]):
                nbytes, duration = speedcheck.download_once(
                    "https://example.com/data.bin", timeout=5.0
                )
        self.assertEqual(nbytes, len(payload))
        self.assertAlmostEqual(duration, 0.5)

    def test_adds_cache_bust_headers_and_query(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["headers"] = {
                k.lower(): v for k, v in request.header_items()
            }
            captured["timeout"] = timeout
            return FakeResponse(b"ok")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.perf_counter", side_effect=[1.0, 1.2]):
                speedcheck.download_once("https://example.com/file?x=1", timeout=7.0)

        self.assertIn("_=", captured["url"])
        self.assertEqual(captured["headers"]["cache-control"], "no-cache")
        self.assertEqual(captured["timeout"], 7.0)

    def test_http_error(self) -> None:
        err = HTTPError("https://example.com/x", 404, "Not Found", hdrs=None, fp=None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once("https://example.com/x", timeout=5.0)
        self.assertIn("404", str(ctx.exception))

    def test_non_200_status(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            return_value=FakeResponse(b"partial", status=206),
        ):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once("https://example.com/file", timeout=5.0)
        self.assertIn("206", str(ctx.exception))

    def test_network_error(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=URLError("Name or service not known"),
        ):
            with self.assertRaises(speedcheck.SpeedCheckError) as ctx:
                speedcheck.download_once("https://offline.invalid/file", timeout=5.0)
        self.assertIn("Сетевая ошибка", str(ctx.exception))


class TestRunAndCli(unittest.TestCase):
    def test_run_prints_summary(self) -> None:
        with mock.patch(
            "speedcheck.download_once",
            side_effect=[(1_250_000, 1.0), (1_250_000, 1.0)],
        ):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                speedcheck.run("https://example.com/file", requests_count=2, timeout=5.0)

        text = out.getvalue()
        self.assertIn("Среднее время", text)
        self.assertIn("10.00 Мбит/с", text)  # 2_500_000 B * 8 / 1e6 / 2s
        self.assertIn("[1/2]", text)
        self.assertIn("[2/2]", text)

    def test_main_nonzero_on_error(self) -> None:
        with mock.patch(
            "speedcheck.run",
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


if __name__ == "__main__":
    unittest.main()
