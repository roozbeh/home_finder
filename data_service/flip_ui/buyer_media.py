"""Validate re-encoded JPEGs before public delivery."""
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MLS_PHOTO_HOSTS = frozenset({
    'media.mlslmedia.com', 'sfarmedia.rapmls.com',
    'bridgemls-assets.cdn-connectmls.com', 'media.crmls.org',
})
NEXT_MARKER = re.compile(rb'\xff[\x01-\xcf\xd8-\xff]')


def approved_mls_photo_url(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if (parts.scheme != 'https' or parts.netloc not in MLS_PHOTO_HOSTS
            or parts.query or parts.fragment or not re.fullmatch(r'/[a-zA-Z0-9/_.-]+\.jpe?g', parts.path, re.I)
            or any(segment in ('.', '..') for segment in parts.path.split('/'))):
        return None
    return url


class NoPhotoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_mls_photo(url):
    if approved_mls_photo_url(url) is None:
        raise ValueError('Invalid public image URL')
    opener = build_opener(NoPhotoRedirect())
    with opener.open(Request(url, headers={'Accept': 'image/jpeg'}), timeout=4) as response:
        return validated_jpeg(response.read(5_000_001), strip_metadata=True)


def validated_jpeg(data, *, strip_metadata=False):
    """Permit basic JPEG data and a thumbnail-free JFIF header only."""
    if len(data) > 5_000_000 or not data.startswith(b'\xff\xd8'):
        raise ValueError('Expected an approved JPEG up to 5 MB')
    index = 2
    scanned = False
    kept = []
    start = 0
    while index < len(data):
        if data[index] != 0xff:
            break
        marker_start = index
        while index < len(data) and data[index] == 0xff:
            index += 1
        if index == len(data):
            break
        marker = data[index]
        index += 1
        if marker == 0xd9 and scanned and index == len(data):
            return b''.join((*kept, data[start:])) if strip_metadata else data
        if index + 2 >= len(data):
            break
        length = int.from_bytes(data[index:index + 2], 'big')
        end = index + length
        if length < 2 or end > len(data):
            break
        if 0xe0 <= marker <= 0xef or marker == 0xfe:
            jfif = marker == 0xe0 and length == 16 and data[index + 2:index + 7] == b'JFIF\x00'
            if not jfif or data[end - 2:end] != b'\x00\x00':
                if not strip_metadata:
                    raise ValueError('Re-encode media without metadata or thumbnails')
                kept.append(data[start:marker_start])
                start = end
        index = end
        if marker == 0xda:
            scanned = True
            next_marker = NEXT_MARKER.search(data, index)
            if next_marker is None:
                break
            index = next_marker.start()
    raise ValueError('Malformed JPEG')
