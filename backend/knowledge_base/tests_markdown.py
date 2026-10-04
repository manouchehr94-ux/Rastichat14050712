"""Security-focused unit tests for the closed allowlist Markdown renderer.

These exercise `render_markdown` directly (no DB fixtures needed) to prove
the specific claim the docstring in markdown_renderer.py makes: raw
author-supplied HTML/script is never interpreted as markup, and unsafe URL
schemes are never emitted into `href`/`src` attributes.
"""
from django.test import SimpleTestCase

from knowledge_base.markdown_renderer import render_markdown


class MarkdownRendererSecurityTests(SimpleTestCase):
    def test_raw_script_tag_is_escaped_not_executed(self):
        html = render_markdown('<script>alert(1)</script>')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_raw_html_event_handler_is_escaped(self):
        html = render_markdown('<img src=x onerror="alert(1)">')
        self.assertNotIn('<img src=x onerror', html)
        self.assertIn('&lt;img', html)

    def test_javascript_scheme_link_is_dropped(self):
        html = render_markdown('[click me](javascript:alert(1))')
        self.assertNotIn('javascript:', html)
        self.assertNotIn('<a ', html)
        self.assertIn('click me', html)

    def test_data_scheme_image_is_dropped(self):
        html = render_markdown('![x](data:text/html;base64,PHNjcmlwdD4=)')
        self.assertNotIn('data:', html)
        self.assertNotIn('<img', html)

    def test_https_link_is_preserved(self):
        html = render_markdown('[docs](https://example.com/page)')
        self.assertIn('<a href="https://example.com/page"', html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_bold_and_italic_inline_markup_still_works(self):
        html = render_markdown('**bold** and *italic*')
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<em>italic</em>', html)

    def test_heading_and_list_are_rendered(self):
        html = render_markdown('# Title\n\n- one\n- two')
        self.assertIn('<h1>Title</h1>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<li>one</li>', html)

    def test_script_tag_inside_code_fence_is_still_escaped(self):
        html = render_markdown('```\n<script>alert(1)</script>\n```')
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
