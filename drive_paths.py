import unicodedata

# Drive names are free text, so a name can be '..', contain '/', or start
# with the spaces the tree uses for nesting. Each name is percent-encoded so
# it stays exactly one segment of a '/'-joined path and one line of the tree;
# urllib.parse.unquote recovers the original name. '%' is always escaped, so
# encoded output never collides with a literal name.
EMPTY_NAME = '%00'
_SEPARATORS = ('%', '/', '\\')


def _escape(text):
    return ''.join(f'%{byte:02X}' for byte in text.encode('utf-8'))


def _needs_escape(char):
    folded = unicodedata.normalize('NFKC', char)
    return not char.isprintable() or any(sep in folded for sep in _SEPARATORS)


def encode_path_segment(name):
    if not name:
        return EMPTY_NAME
    # Segments made only of dots and spaces ('.', '..', '...', ' ') are
    # dropped or resolved upwards by path normalizers.
    if set(unicodedata.normalize('NFKC', name)) <= {'.', ' '}:
        return _escape(name)
    encoded = ''.join(_escape(c) if _needs_escape(c) else c for c in name)
    body = encoded.lstrip(' ')
    return '%20' * (len(encoded) - len(body)) + body
