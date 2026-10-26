"""Render local media links in TXT, Markdown and static HTML transcripts."""
import html
import re
from urllib.parse import quote


def _markdown(text):
    text = html.escape(text, quote=False)
    return re.sub(r'([\\`*_\[\]{}()#+.!|>~-])', r'\\\1', text)


def render(header, messages, output_format):
    """Each message is (formatted text, optional media-manifest entry)."""
    if output_format == 'html':
        yield ('<!doctype html><html><head><meta charset="utf-8">'
               '<title>WeChat transcript</title><style>'
               'body{max-width:900px;margin:2rem auto;padding:0 1rem;font:16px sans-serif}'
               'pre{white-space:pre-wrap;overflow-wrap:anywhere}article{border-top:1px solid #ddd}'
               'img{max-width:100%;height:auto}</style></head><body>\n')
        yield '<pre>' + html.escape(header) + '</pre>\n'
    else:
        yield (_markdown(header) if output_format == 'markdown' else header) + '\n'
    for line, entry in messages:
        if output_format == 'html':
            yield '<article><pre>' + html.escape(line) + '</pre>\n'
        elif output_format == 'markdown':
            yield '> ' + _markdown(line).replace('\n', '\n> ') + '\n\n'
        else:
            yield line
        if entry:
            if entry['status'] == 'resolved':
                for asset in entry['files']:
                    path = asset['path']
                    url = quote(path, safe='/')
                    if output_format == 'html':
                        yield f'<a href="{url}"><img src="{url}" alt="Image" loading="lazy"></a>\n'
                    elif output_format == 'markdown':
                        yield f'![Image]({url})\n\n'
                    else:
                        yield ' [resolved] -> ' + path
            else:
                detail = '' if entry['reason'] == entry['status'] else ': ' + entry['reason']
                status = '[' + entry['status'] + detail + ']'
                yield ('<p>' + html.escape(status) + '</p>\n' if output_format == 'html'
                       else '\n' + status + '\n' if output_format == 'markdown' else ' ' + status)
        yield '</article>\n' if output_format == 'html' else '\n'
    if output_format == 'html':
        yield '</body></html>\n'
