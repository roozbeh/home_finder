"""Issue a paced, signed-out burst to the buyer preview and report latency, queries, and traffic."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
import math
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen


@dataclass
class Sample:
    path: str
    code: int
    elapsed_ms: float
    size: int
    db_reads: int | None
    db_ms: float | None


class ImagePaths(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            url = dict(attrs).get('src', '')
            if url.startswith('/homes/'):
                self.paths.add(url)


def fetch(base, path, *, discovery=False):
    started = monotonic()
    try:
        with urlopen(urljoin(base, path), timeout=10) as response:
            content = response.read()
            metrics = {item.strip().split(';')[0]: item.split('dur=', 1)[-1]
                       for item in response.headers.get('Server-Timing', '').split(',')
                       if 'dur=' in item}
            sample = Sample(path, response.status, (monotonic() - started) * 1_000, len(content),
                            int(float(metrics['buyer-db-reads'])) if 'buyer-db-reads' in metrics else None,
                            float(metrics['buyer-db']) if 'buyer-db' in metrics else None)
            return content if discovery else sample
    except HTTPError as error:
        return Sample(path, error.code, (monotonic() - started) * 1_000, len(error.read()), None, None)
    except (URLError, TimeoutError):
        return Sample(path, 0, (monotonic() - started) * 1_000, 0, None, None)


def quantile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def burst(base, paths, *, rate, seconds):
    pending = []
    started = monotonic()
    with ThreadPoolExecutor(max_workers=32) as pool:
        for index in range(rate * seconds):
            pause = started + index / rate - monotonic()
            if pause > 0:
                sleep(pause)
            pending.append(pool.submit(fetch, base, paths[index % len(paths)]))
    return [sample.result() for sample in pending], monotonic() - started


def summary(samples):
    durations = [sample.elapsed_ms for sample in samples]
    db = [sample for sample in samples if sample.db_ms is not None and sample.db_reads is not None]
    errors = len([sample for sample in samples if sample.code != 200])
    return {
        'requests': len(samples), 'errors': errors,
        'error_percent': round(errors * 100 / len(samples), 2),
        'p50_ms': round(quantile(durations, .50), 2), 'p95_ms': round(quantile(durations, .95), 2),
        'p99_ms': round(quantile(durations, .99), 2),
        'body_bytes': sum(sample.size for sample in samples), 'db_instrumented_responses': len(db),
        'db_reads': sum(sample.db_reads for sample in db) if db else None,
        'db_p95_ms': round(quantile([sample.db_ms for sample in db], .95), 2) if db else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_url')
    parser.add_argument('home_id')
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--rate', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.rate <= 100 or not 1 <= args.seconds <= 300:
        parser.error('The rate must be 1–100 and duration must be 1–300 seconds')
    home = f'/homes/{args.home_id}'
    paths = ['/homes', home, home + '/contact']
    images = ImagePaths()
    for path in paths[:2]:
        content = fetch(args.base_url, path, discovery=True)
        if not isinstance(content, bytes):
            parser.error(f'Unable to warm {path} (status {content.code})')
        images.feed(content.decode('utf-8'))
    samples, elapsed = burst(args.base_url, paths, rate=args.rate, seconds=args.seconds)
    image_samples = [fetch(args.base_url, url) for url in sorted(images.paths)]
    results = {
        'requested_rps': args.rate, 'elapsed_seconds': round(elapsed, 2),
        'requests_per_elapsed_second': round(len(samples) / elapsed, 2),
        'overall': summary(samples),
        'paths': {path: summary(related) for path in paths
                  if (related := [sample for sample in samples if sample.path == path])},
        'image_traffic': {
            'unique_image_requests': len(image_samples),
            'successful_body_bytes': sum(sample.size for sample in image_samples if sample.code == 200),
            'responses': [asdict(sample) for sample in image_samples],
        },
    }
    print(json.dumps(results, indent=2))
    return 1 if results['overall']['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
